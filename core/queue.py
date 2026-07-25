import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from config import get_mode, get_rules, resolve_real_path
from core import eventlog
from core.rules import is_protected, meets_percentage, scheduled_time, should_queue
from database import DeletionQueue, WatchEvent

logger = logging.getLogger("purgearr.queue")


def process_queue(db: Session):
    """Traite tous les items de la queue dont l'heure planifiée est passée."""
    from services.factory import get_jellyfin
    from core.pipeline import delete_movie, delete_episode

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
            jf_item      = jf.get_item(queue_item.jellyfin_item_id, user_id=admin_uid) or {}
            provider_ids = jf_item.get("ProviderIds", {}) or {}
            raw_path     = queue_item.file_path or jf_item.get("Path", "")
            item = {
                "jellyfin_id":  queue_item.jellyfin_item_id,
                "type":         queue_item.item_type,
                "title":        queue_item.item_title,
                "series_title": queue_item.series_title,
                "tmdb_id":      queue_item.tmdb_id or provider_ids.get("Tmdb"),
                "imdb_id":      queue_item.imdb_id or provider_ids.get("Imdb"),
                "tvdb_id":      queue_item.tvdb_id or provider_ids.get("Tvdb"),
                "file_path":    resolve_real_path(raw_path, queue_item.item_type),
                "season":       jf_item.get("ParentIndexNumber"),
                "episode":      jf_item.get("IndexNumber"),
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
        .filter(WatchEvent.jellyfin_item_id == jellyfin_item_id,
                WatchEvent.jellyfin_user_id == user_id)
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

    # 4. Mode manuel → enregistrement uniquement, pas de suppression automatique
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
