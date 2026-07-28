from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from config import get_protected, get_rules
from database import DeletionQueue, WatchEvent


def is_protected(item_id: str, title: str) -> bool:
    protected = get_protected()
    if item_id in (protected.get("jellyfin_ids") or []):
        return True
    title_lower = title.lower()
    return any(t.lower() == title_lower for t in (protected.get("titles") or []))


def _threshold(item_type: str) -> float:
    rules = get_rules()
    section = "movies" if item_type == "Movie" else "series"
    value = rules.get(section, {}).get("watch_percentage_threshold")
    if value is None:
        value = rules.get("watch_percentage_threshold", 85)
    return value


def _delay_hours(item_type: str) -> int:
    rules = get_rules()
    section = "movies" if item_type == "Movie" else "series"
    value = rules.get(section, {}).get("deletion_delay_hours")
    if value is None:
        value = rules.get("deletion_delay_hours", 0)
    return value


def meets_percentage(percentage: float, item_type: str) -> bool:
    return percentage >= _threshold(item_type)


def already_queued(db: Session, item_id: str) -> bool:
    return (
        db.query(DeletionQueue)
        .filter(DeletionQueue.jellyfin_item_id == item_id, DeletionQueue.status == "pending")
        .first()
        is not None
    )


def should_queue(db: Session, item_id: str, item_type: str, all_jellyfin_user_ids: List[str]) -> bool:
    """
    Vérifie si les conditions sont remplies pour mettre l'item en queue de suppression.

    Logique prioritaire (si primary_user configuré) :
      - L'utilisateur principal DOIT avoir regardé
      - Tous les required_users doivent aussi avoir regardé
      - Les autres utilisateurs n'ont aucun impact

    Logique de fallback (multi_user_mode, rétrocompatibilité) :
      - "any"  : au moins un utilisateur a regardé
      - "all"  : tous les utilisateurs ont regardé
    """
    if already_queued(db, item_id):
        return False

    rules       = get_rules()
    primary_uid = rules.get("primary_user", "")
    required    = [u for u in (rules.get("required_users") or []) if u]

    events = db.query(WatchEvent).filter(WatchEvent.jellyfin_item_id == item_id).all()
    users_who_watched = {
        e.jellyfin_user_id
        for e in events
        if meets_percentage(e.percentage, item_type)
    }

    # ── Logique primary_user ────────────────────────────────────────────────
    if primary_uid:
        if primary_uid not in users_who_watched:
            return False
        for uid in required:
            if uid not in users_who_watched:
                return False
        return True

    # ── Fallback multi_user_mode ────────────────────────────────────────────
    mode               = rules.get("multi_user_mode", "any")
    watched_users_filter = rules.get("watched_users") or []
    target_users       = set(watched_users_filter) if watched_users_filter else set(all_jellyfin_user_ids)
    users_in_target    = users_who_watched & target_users

    if mode == "any":
        return bool(users_in_target)
    elif mode == "all":
        return target_users.issubset(users_who_watched)
    return False


def get_item_readiness(db: Session, item_id: str, item_type: str) -> dict:
    """
    Retourne le statut de readiness d'un item pour la suppression manuelle.
    { "primary_watched": bool, "required_statuses": [{id, name, watched}], "ready": bool }
    """
    rules       = get_rules()
    primary_uid = rules.get("primary_user", "")
    required    = [u for u in (rules.get("required_users") or []) if u]
    threshold   = _threshold(item_type)

    events = db.query(WatchEvent).filter(WatchEvent.jellyfin_item_id == item_id).all()
    watched_map = {e.jellyfin_user_id: e.percentage for e in events}

    primary_watched = watched_map.get(primary_uid, 0) >= threshold if primary_uid else True
    required_statuses = [
        {"id": uid, "watched": watched_map.get(uid, 0) >= threshold}
        for uid in required
    ]
    ready = primary_watched and all(s["watched"] for s in required_statuses)

    return {
        "primary_watched":   primary_watched,
        "required_statuses": required_statuses,
        "ready":             ready,
    }


def scheduled_time(item_type: str) -> datetime:
    return datetime.utcnow() + timedelta(hours=_delay_hours(item_type))
