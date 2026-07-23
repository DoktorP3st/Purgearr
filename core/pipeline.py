import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from config import get_extra_paths, get_mode, get_rules
from core.fileops import run_cleanup
from database import DeletionHistory, DeletionQueue, WatchEvent
from services.factory import get_jellyfin, get_radarr, get_sonarr, get_transmission

logger = logging.getLogger("purgearr.pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_history(db: Session, item: Dict, services: List[str], triggered_by: str, error: Optional[str] = None):
    db.add(DeletionHistory(
        jellyfin_item_id=item.get("jellyfin_id"),
        item_type=item.get("type"),
        item_title=item.get("title"),
        series_title=item.get("series_title"),
        deleted_at=datetime.utcnow(),
        deleted_from=json.dumps(services),
        triggered_by=triggered_by,
        error=error,
    ))
    db.commit()


def _stop_torrent(file_path: str, title: str) -> Optional[str]:
    """Cherche et stoppe le torrent correspondant. Retourne le nom du torrent ou None."""
    try:
        tr = get_transmission()
        torrent = tr.find_by_path(file_path) if file_path else None
        if not torrent:
            torrent = tr.find_by_name(title)
        if torrent:
            # delete_data=False : on laisse Radarr/Sonarr gérer la suppression des fichiers
            tr.stop_and_remove(torrent["id"], delete_data=False)
            logger.info(f"[Transmission] Torrent supprimé : {torrent['name']}")
            return torrent["name"]
    except Exception as e:
        logger.warning(f"[Transmission] Erreur pour '{title}': {e}")
    return None


# ── Suppression film ──────────────────────────────────────────────────────────

def delete_movie(db: Session, item: Dict, triggered_by: str) -> Dict:
    """
    Pipeline complet de suppression d'un film.
    item = { jellyfin_id, title, tmdb_id, imdb_id, file_path }
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
            return result
    except Exception as e:
        logger.warning(f"[Pipeline] Impossible de vérifier les favoris: {e}")

    # 1. Transmission — stop seeding avant que Radarr efface les fichiers
    torrent_name = _stop_torrent(file_path, title)
    if torrent_name:
        result["services"].append("transmission")

    # 2. Radarr — supprime le film, les fichiers, et bloque le re-téléchargement
    try:
        radarr = get_radarr()
        movie = (
            radarr.find_by_tmdb_id(int(item["tmdb_id"])) if item.get("tmdb_id") else None
            or (radarr.find_by_imdb_id(item["imdb_id"]) if item.get("imdb_id") else None)
            or radarr.find_by_title(title)
        )
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

    # 2.5 Nettoyage des copies sur les chemins additionnels
    try:
        result["cleanup"] = run_cleanup(title, file_path, get_extra_paths())
    except Exception as e:
        logger.warning(f"[Fileops] Erreur nettoyage copies : {e}")

    # 3. Jellyfin — refresh bibliothèque
    try:
        get_jellyfin().refresh_library()
        result["services"].append("jellyfin")
    except Exception as e:
        result["errors"].append(f"Jellyfin refresh: {e}")

    _save_history(db, item, result["services"], triggered_by, "; ".join(result["errors"]) or None)
    return result


# ── Suppression épisode ───────────────────────────────────────────────────────

def delete_episode(db: Session, item: Dict, triggered_by: str) -> Dict:
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
            return result
    except Exception as e:
        logger.warning(f"[Pipeline] Impossible de vérifier les favoris: {e}")

    # 1. Transmission
    torrent_name = _stop_torrent(file_path, series_title)
    if torrent_name:
        result["services"].append("transmission")

    # 2. Sonarr
    try:
        sonarr = get_sonarr()
        series = (
            sonarr.find_by_tvdb_id(int(item["tvdb_id"])) if item.get("tvdb_id") else None
            or sonarr.find_by_title(series_title)
        )

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

    # 2.5 Nettoyage des copies sur les chemins additionnels
    try:
        cleanup_title = series_title if delete_mode == "series" else title
        result["cleanup"] = run_cleanup(cleanup_title, file_path, get_extra_paths())
    except Exception as e:
        logger.warning(f"[Fileops] Erreur nettoyage copies : {e}")

    # 3. Jellyfin — refresh
    try:
        get_jellyfin().refresh_library()
        result["services"].append("jellyfin")
    except Exception as e:
        result["errors"].append(f"Jellyfin refresh: {e}")

    _save_history(db, item, result["services"], triggered_by, "; ".join(result["errors"]) or None)
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

    for queue_item in pending:
        queue_item.status = "processing"
        db.commit()

        try:
            jf_item = jf.get_item(queue_item.jellyfin_item_id)
            provider_ids = jf_item.get("ProviderIds", {})

            item = {
                "jellyfin_id": queue_item.jellyfin_item_id,
                "type": queue_item.item_type,
                "title": queue_item.item_title,
                "series_title": queue_item.series_title,
                "tmdb_id": queue_item.tmdb_id or provider_ids.get("Tmdb"),
                "imdb_id": queue_item.imdb_id or provider_ids.get("Imdb"),
                "tvdb_id": queue_item.tvdb_id or provider_ids.get("Tvdb"),
                "file_path": queue_item.file_path or jf_item.get("Path", ""),
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
            queue_item.status = "failed"

        db.commit()


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

    # 4. Mode manuel → on enregistre uniquement, pas de suppression automatique
    if get_mode() != "auto":
        return

    # 5. Vérifier les règles multi-user
    if not should_queue(db, jellyfin_item_id, item_type, all_user_ids):
        return

    # 6. Ajouter à la queue
    db.add(DeletionQueue(
        jellyfin_item_id=jellyfin_item_id,
        item_type=item_type,
        item_title=item_title,
        series_title=series_title,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        imdb_id=imdb_id,
        file_path=file_path,
        scheduled_at=scheduled_time(item_type),
    ))
    db.commit()
    logger.info(f"[Queue] Ajouté à la queue de suppression : {item_title}")
