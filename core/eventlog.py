"""
Journal événementiel léger et catégorisé.

Chaque appel `log_event(level, category, message, **context)` écrit une ligne
dans la table `event_logs` (SQLite WAL). Purge automatique périodique pour
éviter la croissance illimitée.

Désactivable via config `logs.enabled` (activé par défaut).
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func

from config import get_logs_config
from database import LogEntry, SessionLocal

logger = logging.getLogger("purgearr.eventlog")

# ── Constantes ────────────────────────────────────────────────────────────────

VALID_LEVELS = ("info", "warning", "error")

VALID_CATEGORIES = (
    "deletion",    # suppression film/épisode/torrent
    "watch",       # visionnage détecté qui atteint le seuil
    "queue",       # ajout/annulation/traitement de la queue
    "protection",  # ajout/retrait whitelist, blocage suppression
    "sync",        # synchronisation Jellyfin
    "scheduler",   # démarrage/reconfiguration/erreur du scheduler
    "webhook",     # événement webhook reçu
    "service",     # état des services (radarr/sonarr/…)
    "config",      # modification de configuration
    "error",       # erreur non catégorisée
)

CATEGORY_LABELS = {
    "deletion":   "Suppression",
    "watch":      "Visionnage",
    "queue":      "Queue",
    "protection": "Protection",
    "sync":       "Sync",
    "scheduler":  "Scheduler",
    "webhook":    "Webhook",
    "service":    "Service",
    "config":     "Configuration",
    "error":      "Erreur",
}

_MAX_MESSAGE_LEN = 500
_MAX_CONTEXT_LEN = 2000
_PURGE_EVERY_N_WRITES = 250    # purge périodique (pas à chaque écriture)

_write_counter = 0


# ── API principale ────────────────────────────────────────────────────────────

def log_event(level: str, category: str, message: str, **context: Any) -> None:
    """
    Écrit un événement dans le journal si activé.
    Ne lève jamais d'exception — un échec d'écriture est silencieux (log fallback).
    """
    global _write_counter

    cfg = get_logs_config()
    if not cfg.get("enabled", True):
        return

    if level not in VALID_LEVELS or category not in VALID_CATEGORIES:
        return

    db = SessionLocal()
    try:
        entry = LogEntry(
            level=level,
            category=category,
            message=(message or "")[:_MAX_MESSAGE_LEN],
            context=_serialize_context(context) if context else None,
        )
        db.add(entry)
        db.commit()

        _write_counter += 1
        if _write_counter >= _PURGE_EVERY_N_WRITES:
            _write_counter = 0
            _purge(db, cfg)
    except Exception as e:
        logger.warning(f"[EventLog] Écriture KO : {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def info(category: str, message: str, **context: Any) -> None:
    log_event("info", category, message, **context)


def warning(category: str, message: str, **context: Any) -> None:
    log_event("warning", category, message, **context)


def error(category: str, message: str, **context: Any) -> None:
    log_event("error", category, message, **context)


# ── Lecture / gestion ─────────────────────────────────────────────────────────

def query_logs(
    limit: int = 200,
    offset: int = 0,
    level: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Retourne une page de logs + total match."""
    db = SessionLocal()
    try:
        q = db.query(LogEntry)
        if level and level in VALID_LEVELS:
            q = q.filter(LogEntry.level == level)
        if category and category in VALID_CATEGORIES:
            q = q.filter(LogEntry.category == category)
        if search:
            like = f"%{search}%"
            q = q.filter(LogEntry.message.like(like))

        total = q.count()
        rows = q.order_by(LogEntry.timestamp.desc()).offset(offset).limit(limit).all()

        return {
            "total":   total,
            "limit":   limit,
            "offset":  offset,
            "entries": [_serialize_entry(r) for r in rows],
        }
    finally:
        db.close()


def purge_all() -> int:
    """Vide la table event_logs et retourne le nombre supprimé."""
    db = SessionLocal()
    try:
        n = db.query(LogEntry).delete()
        db.commit()
        return n
    except Exception as e:
        logger.error(f"[EventLog] Purge KO : {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_stats() -> Dict[str, Any]:
    """Compte total + par catégorie + par niveau (via GROUP BY)."""
    db = SessionLocal()
    try:
        total = db.query(LogEntry).count()

        by_cat = {c: 0 for c in VALID_CATEGORIES}
        for cat, cnt in db.query(LogEntry.category, func.count(LogEntry.id)).group_by(LogEntry.category).all():
            if cat in by_cat:
                by_cat[cat] = cnt

        by_lvl = {l: 0 for l in VALID_LEVELS}
        for lvl, cnt in db.query(LogEntry.level, func.count(LogEntry.id)).group_by(LogEntry.level).all():
            if lvl in by_lvl:
                by_lvl[lvl] = cnt

        return {"total": total, "by_category": by_cat, "by_level": by_lvl}
    except Exception:
        return {
            "total": 0,
            "by_category": {c: 0 for c in VALID_CATEGORIES},
            "by_level": {l: 0 for l in VALID_LEVELS},
        }
    finally:
        db.close()


# ── Internes ──────────────────────────────────────────────────────────────────

def _serialize_context(ctx: Dict[str, Any]) -> str:
    try:
        s = json.dumps(ctx, ensure_ascii=False, default=str)
    except Exception:
        s = json.dumps({"_repr": repr(ctx)[:_MAX_CONTEXT_LEN]}, ensure_ascii=False)
    if len(s) > _MAX_CONTEXT_LEN:
        s = s[:_MAX_CONTEXT_LEN - 3] + "..."
    return s


def _serialize_entry(row: LogEntry) -> Dict[str, Any]:
    context: Any = None
    if row.context:
        try:
            context = json.loads(row.context)
        except Exception:
            context = row.context
    return {
        "id":        row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "level":     row.level,
        "category":  row.category,
        "message":   row.message,
        "context":   context,
    }


def _purge(db, cfg: Dict[str, Any]) -> None:
    """Purge les entrées trop vieilles + limite le nombre total."""
    try:
        retention_days = max(1, int(cfg.get("retention_days", 30)))
        max_entries    = max(100, int(cfg.get("max_entries", 10000)))

        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        deleted_old = (
            db.query(LogEntry)
            .filter(LogEntry.timestamp < cutoff)
            .delete(synchronize_session=False)
        )

        count = db.query(LogEntry).count()
        deleted_excess = 0
        if count > max_entries:
            excess = count - max_entries
            oldest_ids = [
                row[0] for row in
                db.query(LogEntry.id)
                .order_by(LogEntry.timestamp)
                .limit(excess)
                .all()
            ]
            if oldest_ids:
                deleted_excess = (
                    db.query(LogEntry)
                    .filter(LogEntry.id.in_(oldest_ids))
                    .delete(synchronize_session=False)
                )

        db.commit()
        if deleted_old or deleted_excess:
            logger.info(
                f"[EventLog] Purge : {deleted_old} anciennes + {deleted_excess} excédentaires"
            )
    except Exception as e:
        logger.warning(f"[EventLog] Purge KO : {e}")
        try:
            db.rollback()
        except Exception:
            pass
