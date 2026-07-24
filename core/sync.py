import logging
from datetime import datetime

from sqlalchemy.orm import Session

from core import eventlog
from database import WatchEvent
from services.factory import get_jellyfin

logger = logging.getLogger("purgearr.sync")


def _upsert_watch_event(db: Session, item: dict, user_id: str, user_name: str, item_type: str, percentage: float):
    item_id = item["Id"]
    existing = (
        db.query(WatchEvent)
        .filter(WatchEvent.jellyfin_item_id == item_id, WatchEvent.jellyfin_user_id == user_id)
        .first()
    )
    if existing:
        if percentage > existing.percentage:
            existing.percentage = percentage
            existing.watched_at = datetime.utcnow()
    else:
        db.add(WatchEvent(
            jellyfin_item_id=item_id,
            jellyfin_user_id=user_id,
            user_name=user_name,
            item_type=item_type,
            item_title=item.get("Name", "?"),
            series_title=item.get("SeriesName"),
            season=item.get("ParentIndexNumber"),
            episode=item.get("IndexNumber"),
            percentage=percentage,
        ))
    db.commit()


def sync_watch_data(db: Session) -> dict:
    """
    Scan Jellyfin et importe les données de visionnage en base.
    N'effectue aucune suppression — lecture seule côté Jellyfin.
    """
    jf = get_jellyfin()
    try:
        users = jf.get_users()
    except Exception as e:
        logger.error(f"[Sync] Impossible de récupérer les users Jellyfin : {e}")
        eventlog.error("sync", f"Jellyfin injoignable : {e}")
        return {"users": 0, "movies": 0, "episodes": 0, "errors": 1}

    total_movies = 0
    total_episodes = 0
    errors = 0

    for user in users:
        uid   = user.get("Id")
        uname = user.get("Name", "?")
        if not uid:
            continue

        try:
            movies = jf.get_watched_with_details(uid, "Movie", limit=500)
        except Exception as e:
            logger.warning(f"[Sync] Films KO pour {uname} : {e}")
            movies = []
            errors += 1

        for item in movies:
            try:
                pct = item.get("UserData", {}).get("PlayedPercentage") or 100.0
                _upsert_watch_event(db, item, uid, uname, "Movie", pct)
            except Exception as e:
                logger.warning(f"[Sync] Upsert film KO ({item.get('Name', '?')}) : {e}")
                db.rollback()
                errors += 1
        total_movies += len(movies)

        try:
            episodes = jf.get_watched_with_details(uid, "Episode", limit=2000)
        except Exception as e:
            logger.warning(f"[Sync] Épisodes KO pour {uname} : {e}")
            episodes = []
            errors += 1

        for item in episodes:
            try:
                pct = item.get("UserData", {}).get("PlayedPercentage") or 100.0
                _upsert_watch_event(db, item, uid, uname, "Episode", pct)
            except Exception as e:
                logger.warning(f"[Sync] Upsert épisode KO ({item.get('Name', '?')}) : {e}")
                db.rollback()
                errors += 1
        total_episodes += len(episodes)

        logger.info(f"[Sync] {uname} — {len(movies)} films, {len(episodes)} épisodes importés")

    logger.info(f"[Sync] Terminé : {total_movies} films, {total_episodes} épisodes sur {len(users)} users (errors={errors})")
    level = "warning" if errors else "info"
    eventlog.log_event(level, "sync",
                       f"Sync terminé : {total_movies} films / {total_episodes} épisodes ({len(users)} users)",
                       errors=errors)
    return {"users": len(users), "movies": total_movies, "episodes": total_episodes, "errors": errors}
