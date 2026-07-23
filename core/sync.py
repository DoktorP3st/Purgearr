import logging
from datetime import datetime

from sqlalchemy.orm import Session

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
    users = jf.get_users()
    total_movies = 0
    total_episodes = 0

    for user in users:
        uid   = user["Id"]
        uname = user["Name"]

        movies = jf.get_watched_with_details(uid, "Movie", limit=500)
        for item in movies:
            pct = item.get("UserData", {}).get("PlayedPercentage") or 100.0
            _upsert_watch_event(db, item, uid, uname, "Movie", pct)
        total_movies += len(movies)

        episodes = jf.get_watched_with_details(uid, "Episode", limit=2000)
        for item in episodes:
            pct = item.get("UserData", {}).get("PlayedPercentage") or 100.0
            _upsert_watch_event(db, item, uid, uname, "Episode", pct)
        total_episodes += len(episodes)

        logger.info(f"[Sync] {uname} — {len(movies)} films, {len(episodes)} épisodes importés")

    logger.info(f"[Sync] Terminé : {total_movies} films, {total_episodes} épisodes sur {len(users)} users")
    return {"users": len(users), "movies": total_movies, "episodes": total_episodes}
