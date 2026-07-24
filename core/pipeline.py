import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from config import get_extra_paths, get_mode, get_rules, get_scan_paths, resolve_real_path
from core import eventlog
from core.fileops import hash_file, run_cleanup
from database import DeletionHistory, DeletionQueue, WatchEvent
from services.factory import get_jellyfin, get_radarr, get_sonarr, get_transmission

logger = logging.getLogger("purgearr.pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_history(db: Session, item: Dict, services: List[str], triggered_by: str,
                  error: Optional[str] = None, details: Optional[Dict] = None):
    db.add(DeletionHistory(
        jellyfin_item_id=item.get("jellyfin_id"),
        item_type=item.get("type"),
        item_title=item.get("title"),
        series_title=item.get("series_title"),
        deleted_at=datetime.utcnow(),
        deleted_from=json.dumps(services),
        triggered_by=triggered_by,
        error=error,
        details_json=json.dumps(details) if details else None,
    ))
    db.commit()


def _stop_all_torrents(file_path: str, title: str) -> List[Dict]:
    """Cherche et stoppe TOUS les torrents correspondants. Retourne [{name, tracker_name, tracker_url}]."""
    from services.transmission import get_tracker_info
    results: List[Dict] = []
    try:
        tr = get_transmission()
        torrents = tr.find_all_by_path_or_name(file_path, title)
        if not torrents:
            return results
        for torrent in torrents:
            tr.stop_and_remove(torrent["id"], delete_data=False)
            logger.info(f"[Transmission] Torrent supprimé : {torrent['name']}")
            tname, turl = get_tracker_info(torrent)
            results.append({"name": torrent["name"], "tracker_name": tname, "tracker_url": turl})
    except Exception as e:
        logger.warning(f"[Transmission] Erreur pour '{title}': {e}")
        eventlog.warning("service", f"Transmission KO pour '{title}' : {e}")
    return results


# ── Suppression film ──────────────────────────────────────────────────────────

def delete_movie(db: Session, item: Dict, triggered_by: str, source_hash: str = "") -> Dict:
    """
    Pipeline complet de suppression d'un film.
    item = { jellyfin_id, title, tmdb_id, imdb_id, file_path }
    source_hash : empreinte pré-calculée (scan manuel) ou calculée ici avant suppression Radarr
    """
    result = {"success": False, "services": [], "errors": [], "blocked_by_favorite": False, "cleanup": None}
    rules = get_rules()
    title = item.get("title", "?")
    file_path = item.get("file_path", "")

    logger.info(f"[Pipeline] Suppression film : {title}")

    # 0. Vérifier les favoris Jellyfin — item favori = intouchable
    try:
        if get_jellyfin().is_favorite_any_user(item.get("jellyfin_id", "")):
            logger.info(f"[Pipeline] Film en favori, suppression bloquée : {title}")
            result["errors"].append("Item en favori Jellyfin — suppression bloquée")
            result["blocked_by_favorite"] = True
            eventlog.warning("protection", f"Film en favori — suppression bloquée : {title}",
                             triggered_by=triggered_by, jellyfin_id=item.get("jellyfin_id"))
            return result
    except Exception as e:
        logger.warning(f"[Pipeline] Impossible de vérifier les favoris: {e}")
        eventlog.warning("service", f"Jellyfin favoris indisponibles : {e}", title=title)

    # 0.5 Empreinte SHA-256 AVANT suppression — pour retrouver les copies par hash
    # (si source_hash fourni par le scan manuel, on l'utilise directement)
    if not source_hash and file_path and os.path.isfile(file_path):
        source_hash = hash_file(file_path)
        logger.debug(f"[Pipeline] Hash source calculé avant suppression : {source_hash[:12]}…")

    # Taille du fichier source (avant que Radarr ne le supprime)
    file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0

    # 1. Transmission — stop TOUS les torrents seedant ce fichier (multi-tracker)
    torrents_info = _stop_all_torrents(file_path, title)
    torrent_names = [t["name"] for t in torrents_info]
    if torrents_info:
        result["services"].append("transmission")
        logger.info(f"[Transmission] {len(torrents_info)} torrent(s) supprimé(s) pour : {title}")

    # 1.5 Sauvegarder dans l'index cleanup AVANT que Radarr supprime les fichiers
    try:
        from core.cleanup_store import add_entry
        add_entry(
            item_title=title, item_type="Movie", source_hash=source_hash,
            file_path=file_path, jellyfin_item_id=item.get("jellyfin_id", ""),
            file_size_bytes=file_size, torrent_name=", ".join(torrent_names) if torrent_names else None,
            scan_paths=get_scan_paths("Movie"),
        )
    except Exception as e:
        logger.warning(f"[Cleanup] Erreur sauvegarde index : {e}")

    # 2. Radarr — supprime le film, les fichiers, et bloque le re-téléchargement
    try:
        radarr = get_radarr()
        movie = None
        if item.get("tmdb_id"):
            try:
                movie = radarr.find_by_tmdb_id(int(item["tmdb_id"]))
            except (TypeError, ValueError):
                movie = None
        if not movie and item.get("imdb_id"):
            movie = radarr.find_by_imdb_id(item["imdb_id"])
        if not movie:
            movie = radarr.find_by_title(title)
        if movie:
            radarr.delete(
                movie["id"],
                delete_files=rules.get("delete_files", True),
                add_exclusion=rules.get("add_to_exclusion", True),
            )
            result["services"].append("radarr")
            result["success"] = True
            logger.info(f"[Radarr] Film supprimé : {title} (id={movie['id']})")
        else:
            logger.warning(f"[Radarr] Film introuvable : {title}")
            result["errors"].append("Radarr: film introuvable")
    except Exception as e:
        result["errors"].append(f"Radarr: {e}")
        logger.error(f"[Radarr] Erreur pour '{title}': {e}")

    # 2.5 Nettoyage des copies (hash pré-calculé = pas de fallback titre hasardeux)
    try:
        result["cleanup"] = run_cleanup(title, file_path, get_scan_paths("Movie"), source_hash=source_hash)
    except Exception as e:
        logger.warning(f"[Fileops] Erreur nettoyage copies : {e}")

    # 3. Jellyfin — refresh bibliothèque
    try:
        get_jellyfin().refresh_library()
        result["services"].append("jellyfin")
    except Exception as e:
        result["errors"].append(f"Jellyfin refresh: {e}")

    # Détails pour l'historique
    from core.fileops import format_size
    cleanup_result = result.get("cleanup") or {}
    copies_size = cleanup_result.get("size_bytes", 0)
    details = {
        "file_path": file_path,
        "file_size_bytes": file_size,
        "file_size_human": format_size(file_size),
        "torrents": torrents_info,
        "copies_deleted": cleanup_result.get("copies_deleted", 0),
        "copies_size_bytes": copies_size,
        "copies_size_human": format_size(copies_size),
        "total_freed_bytes": file_size + copies_size,
        "total_freed_human": format_size(file_size + copies_size),
    }
    _save_history(db, item, result["services"], triggered_by,
                  "; ".join(result["errors"]) or None, details=details)

    # Log événementiel
    if result["success"]:
        eventlog.info("deletion", f"Film supprimé : {title}",
                      triggered_by=triggered_by,
                      services=result["services"],
                      copies_deleted=cleanup_result.get("copies_deleted", 0))
    elif result["errors"]:
        eventlog.error("deletion", f"Échec suppression film : {title}",
                       triggered_by=triggered_by, errors=result["errors"])

    return result


# ── Suppression épisode ───────────────────────────────────────────────────────

def delete_episode(db: Session, item: Dict, triggered_by: str, source_hash: str = "") -> Dict:
    """
    Pipeline de suppression d'un épisode (ou d'une série entière selon config).
    item = { jellyfin_id, title, series_title, tvdb_id, file_path, season, episode }
    """
    result = {"success": False, "services": [], "errors": [], "blocked_by_favorite": False, "cleanup": None}
    rules = get_rules()
    delete_mode = rules.get("series", {}).get("delete_mode", "episode")
    series_title = item.get("series_title", "?")
    title = item.get("title", "?")
    file_path = item.get("file_path", "")

    logger.info(f"[Pipeline] Suppression épisode : {series_title} — {title}")

    # 0. Vérifier les favoris Jellyfin
    try:
        if get_jellyfin().is_favorite_any_user(item.get("jellyfin_id", "")):
            logger.info(f"[Pipeline] Épisode en favori, suppression bloquée : {title}")
            result["errors"].append("Item en favori Jellyfin — suppression bloquée")
            result["blocked_by_favorite"] = True
            eventlog.warning("protection", f"Épisode en favori — suppression bloquée : {series_title} — {title}",
                             triggered_by=triggered_by, jellyfin_id=item.get("jellyfin_id"))
            return result
    except Exception as e:
        logger.warning(f"[Pipeline] Impossible de vérifier les favoris: {e}")
        eventlog.warning("service", f"Jellyfin favoris indisponibles : {e}", title=title)

    # 0.5 Empreinte SHA-256 AVANT suppression
    if not source_hash and file_path and os.path.isfile(file_path):
        source_hash = hash_file(file_path)

    # Taille du fichier source (avant que Sonarr ne le supprime)
    file_size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else 0

    # 1. Transmission — stop TOUS les torrents seedant cet épisode (multi-tracker)
    torrents_info = _stop_all_torrents(file_path, series_title)
    torrent_names = [t["name"] for t in torrents_info]
    if torrents_info:
        result["services"].append("transmission")
        logger.info(f"[Transmission] {len(torrents_info)} torrent(s) supprimé(s) pour : {series_title}")

    # 1.5 Sauvegarder dans l'index cleanup AVANT que Sonarr supprime les fichiers
    try:
        from core.cleanup_store import add_entry
        add_entry(
            item_title=title, item_type="Episode", source_hash=source_hash,
            file_path=file_path, series_title=series_title,
            jellyfin_item_id=item.get("jellyfin_id", ""),
            file_size_bytes=file_size, torrent_name=", ".join(torrent_names) if torrent_names else None,
            scan_paths=get_scan_paths("Episode"),
        )
    except Exception as e:
        logger.warning(f"[Cleanup] Erreur sauvegarde index : {e}")

    # 2. Sonarr
    try:
        sonarr = get_sonarr()
        series = None
        if item.get("tvdb_id"):
            try:
                series = sonarr.find_by_tvdb_id(int(item["tvdb_id"]))
            except (TypeError, ValueError):
                series = None
        if not series:
            series = sonarr.find_by_title(series_title)

        if not series:
            logger.warning(f"[Sonarr] Série introuvable : {series_title}")
            result["errors"].append("Sonarr: série introuvable")
        elif delete_mode == "series":
            sonarr.delete_series(
                series["id"],
                delete_files=rules.get("delete_files", True),
                add_exclusion=rules.get("add_to_exclusion", True),
            )
            result["services"].append("sonarr")
            result["success"] = True
            logger.info(f"[Sonarr] Série entière supprimée : {series_title}")
        else:
            # Suppression épisode par épisode via le fichier
            file_path = item.get("file_path", "")
            ep_files = sonarr.get_episode_files(series["id"])
            deleted = False
            for ef in ep_files:
                if file_path and ef.get("path", "") == file_path:
                    sonarr.delete_episode_file(ef["id"])
                    deleted = True
                    break
            # Fallback : correspondance par saison/épisode si le path ne matche pas
            if not deleted and item.get("season") and item.get("episode"):
                episodes = sonarr.get_episodes(series["id"])
                for ep in episodes:
                    if ep.get("seasonNumber") == item["season"] and ep.get("episodeNumber") == item["episode"]:
                        if ep.get("episodeFileId"):
                            sonarr.delete_episode_file(ep["episodeFileId"])
                            deleted = True
                        break
            if deleted:
                result["services"].append("sonarr")
                result["success"] = True
                logger.info(f"[Sonarr] Épisode supprimé : {series_title} S{item.get('season', '?')}E{item.get('episode', '?')}")
            else:
                result["errors"].append("Sonarr: fichier épisode introuvable")
                logger.warning(f"[Sonarr] Fichier épisode introuvable pour : {title}")

    except Exception as e:
        result["errors"].append(f"Sonarr: {e}")
        logger.error(f"[Sonarr] Erreur pour '{series_title}': {e}")

    # 2.5 Nettoyage des copies (hash pré-calculé)
    try:
        cleanup_title = series_title if delete_mode == "series" else title
        result["cleanup"] = run_cleanup(cleanup_title, file_path, get_scan_paths("Episode"), source_hash=source_hash)
    except Exception as e:
        logger.warning(f"[Fileops] Erreur nettoyage copies : {e}")

    # 3. Jellyfin — refresh
    try:
        get_jellyfin().refresh_library()
        result["services"].append("jellyfin")
    except Exception as e:
        result["errors"].append(f"Jellyfin refresh: {e}")

    # Détails pour l'historique
    from core.fileops import format_size
    cleanup_result = result.get("cleanup") or {}
    copies_size = cleanup_result.get("size_bytes", 0)
    details = {
        "file_path": file_path,
        "file_size_bytes": file_size,
        "file_size_human": format_size(file_size),
        "torrents": torrents_info,
        "copies_deleted": cleanup_result.get("copies_deleted", 0),
        "copies_size_bytes": copies_size,
        "copies_size_human": format_size(copies_size),
        "total_freed_bytes": file_size + copies_size,
        "total_freed_human": format_size(file_size + copies_size),
    }
    _save_history(db, item, result["services"], triggered_by,
                  "; ".join(result["errors"]) or None, details=details)

    # Log événementiel
    label = f"{series_title} — {title}"
    if result["success"]:
        eventlog.info("deletion", f"Épisode supprimé : {label}",
                      triggered_by=triggered_by,
                      services=result["services"],
                      delete_mode=delete_mode,
                      copies_deleted=cleanup_result.get("copies_deleted", 0))
    elif result["errors"]:
        eventlog.error("deletion", f"Échec suppression épisode : {label}",
                       triggered_by=triggered_by, errors=result["errors"])

    return result


# ── Traitement de la queue ────────────────────────────────────────────────────

def process_queue(db: Session):
    """Traite tous les items de la queue dont l'heure planifiée est passée."""
    now = datetime.utcnow()
    pending = (
        db.query(DeletionQueue)
        .filter(DeletionQueue.status == "pending", DeletionQueue.scheduled_at <= now)
        .all()
    )

    if not pending:
        return

    logger.info(f"[Queue] {len(pending)} item(s) à traiter")
    jf = get_jellyfin()
    try:
        admin_uid = (jf.get_users() or [{}])[0].get("Id")
    except Exception as e:
        logger.warning(f"[Queue] Impossible de récupérer les users Jellyfin: {e}")
        admin_uid = None

    for queue_item in pending:
        queue_item.status = "processing"
        db.commit()

        try:
            jf_item = jf.get_item(queue_item.jellyfin_item_id, user_id=admin_uid) or {}
            provider_ids = jf_item.get("ProviderIds", {}) or {}

            raw_path = queue_item.file_path or jf_item.get("Path", "")
            item = {
                "jellyfin_id": queue_item.jellyfin_item_id,
                "type": queue_item.item_type,
                "title": queue_item.item_title,
                "series_title": queue_item.series_title,
                "tmdb_id": queue_item.tmdb_id or provider_ids.get("Tmdb"),
                "imdb_id": queue_item.imdb_id or provider_ids.get("Imdb"),
                "tvdb_id": queue_item.tvdb_id or provider_ids.get("Tvdb"),
                "file_path": resolve_real_path(raw_path, queue_item.item_type),
                "season": jf_item.get("ParentIndexNumber"),
                "episode": jf_item.get("IndexNumber"),
            }

            if queue_item.item_type == "Movie":
                result = delete_movie(db, item, triggered_by="scheduler")
            else:
                result = delete_episode(db, item, triggered_by="scheduler")

            queue_item.status = "done" if result["success"] else "failed"

        except Exception as e:
            logger.error(f"[Queue] Erreur sur '{queue_item.item_title}': {e}")
            eventlog.error("queue", f"Erreur traitement queue : {queue_item.item_title}", error=str(e))
            db.rollback()
            queue_item.status = "failed"

        try:
            db.commit()
        except Exception as e:
            logger.error(f"[Queue] Commit final échoué pour '{queue_item.item_title}': {e}")
            db.rollback()


# ── Enregistrement d'un événement de visionnage ───────────────────────────────

def handle_watch_event(
    db: Session,
    jellyfin_item_id: str,
    user_id: str,
    user_name: str,
    item_type: str,
    item_title: str,
    series_title: Optional[str],
    season: Optional[int],
    episode: Optional[int],
    percentage: float,
    tmdb_id: Optional[str],
    tvdb_id: Optional[str],
    imdb_id: Optional[str],
    file_path: Optional[str],
    all_user_ids: List[str],
):
    """
    Point d'entrée principal : appelé à chaque événement PlaybackStop de Jellyfin.
    Enregistre le visionnage et décide si l'item doit être mis en queue.
    """
    from core.rules import is_protected, meets_percentage, scheduled_time, should_queue

    # 1. Vérifier la liste de protection
    if is_protected(jellyfin_item_id, item_title):
        logger.info(f"[Event] Item protégé ignoré : {item_title}")
        eventlog.info("protection", f"Visionnage ignoré (protégé) : {item_title}",
                      user=user_name, jellyfin_id=jellyfin_item_id)
        return

    # 2. Vérifier le seuil de visionnage
    if not meets_percentage(percentage, item_type):
        logger.info(f"[Event] Seuil non atteint ({percentage:.0f}%) : {item_title}")
        return

    # 3. Enregistrer l'événement (upsert : mettre à jour si meilleur pourcentage)
    existing = (
        db.query(WatchEvent)
        .filter(WatchEvent.jellyfin_item_id == jellyfin_item_id, WatchEvent.jellyfin_user_id == user_id)
        .first()
    )
    if existing:
        if percentage > existing.percentage:
            existing.percentage = percentage
            existing.watched_at = datetime.utcnow()
        db.commit()
    else:
        db.add(WatchEvent(
            jellyfin_item_id=jellyfin_item_id,
            jellyfin_user_id=user_id,
            user_name=user_name,
            item_type=item_type,
            item_title=item_title,
            series_title=series_title,
            season=season,
            episode=episode,
            percentage=percentage,
        ))
        db.commit()

    logger.info(f"[Event] Visionnage enregistré : {user_name} — {item_title} ({percentage:.0f}%)")
    eventlog.info(
        "watch",
        f"{user_name} — {item_title} vu à {percentage:.0f}%",
        user=user_name, item_type=item_type,
        series=series_title, percentage=round(percentage, 1),
    )

    # 4. Mode manuel → on enregistre uniquement, pas de suppression automatique
    if get_mode() != "auto":
        return

    # 5. Vérifier les règles multi-user
    if not should_queue(db, jellyfin_item_id, item_type, all_user_ids):
        return

    # 6. Ajouter à la queue
    scheduled = scheduled_time(item_type)
    db.add(DeletionQueue(
        jellyfin_item_id=jellyfin_item_id,
        item_type=item_type,
        item_title=item_title,
        series_title=series_title,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
        file_path=file_path,
        scheduled_at=scheduled,
    ))
    db.commit()
    logger.info(f"[Queue] Ajouté à la queue de suppression : {item_title}")
    eventlog.info("queue", f"Ajouté à la queue : {item_title}",
                  scheduled_at=scheduled.isoformat(), triggered_by=user_name)
