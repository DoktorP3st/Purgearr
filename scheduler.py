import logging
from apscheduler.schedulers.background import BackgroundScheduler
from database import SessionLocal
from core import eventlog
from core.pipeline import process_queue
from core.sync import sync_watch_data
from config import get_scheduler_config

logger = logging.getLogger("purgearr.scheduler")

# job_defaults empêche l'empilement de jobs si un précédent traîne
# (coalesce=True fusionne les runs manqués en un seul, max_instances=1 = pas de doublon)
scheduler = BackgroundScheduler(
    timezone="UTC",
    job_defaults={
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 300,
    },
)

INTERVAL_OPTIONS = {
    30:   "30 minutes",
    60:   "1 heure",
    120:  "2 heures",
    180:  "3 heures",
    360:  "6 heures",
    720:  "12 heures",
    1440: "24 heures",
}


def _run_queue():
    db = SessionLocal()
    try:
        process_queue(db)
    except Exception as e:
        logger.error(f"Erreur scheduler queue: {e}")
        eventlog.error("scheduler", f"Job process_queue KO : {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _run_scan():
    db = SessionLocal()
    try:
        result = sync_watch_data(db)
        logger.info(f"[Scheduler] Scan auto terminé : {result}")
    except Exception as e:
        logger.error(f"Erreur scheduler scan: {e}")
        eventlog.error("scheduler", f"Job sync_data KO : {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _get_intervals():
    cfg = get_scheduler_config()
    queue = max(1, int(cfg.get("queue_interval_minutes", 360)))
    scan  = max(1, int(cfg.get("scan_interval_minutes",  360)))
    return queue, scan


def start_scheduler():
    queue_min, scan_min = _get_intervals()

    scheduler.add_job(_run_queue, "interval", minutes=queue_min, id="process_queue", replace_existing=True)
    scheduler.add_job(_run_scan,  "interval", minutes=scan_min,  id="sync_data",     replace_existing=True)
    scheduler.start()

    logger.info(f"Scheduler démarré — queue: {queue_min}min | scan: {scan_min}min")
    eventlog.info("scheduler", f"Démarré (queue={queue_min}min, scan={scan_min}min)")


def restart_jobs():
    """Recharge les intervalles depuis la config sans redémarrer l'app."""
    queue_min, scan_min = _get_intervals()
    if not scheduler.running:
        logger.info("Scheduler non démarré — pas de reschedule (les intervalles seront pris en compte au start)")
        return
    try:
        scheduler.reschedule_job("process_queue", trigger="interval", minutes=queue_min)
    except Exception as e:
        logger.warning(f"reschedule process_queue KO ({e}) — ajout du job")
        scheduler.add_job(_run_queue, "interval", minutes=queue_min, id="process_queue", replace_existing=True)
    try:
        scheduler.reschedule_job("sync_data", trigger="interval", minutes=scan_min)
    except Exception as e:
        logger.warning(f"reschedule sync_data KO ({e}) — ajout du job")
        scheduler.add_job(_run_scan, "interval", minutes=scan_min, id="sync_data", replace_existing=True)
    logger.info(f"Scheduler mis à jour — queue: {queue_min}min | scan: {scan_min}min")
    eventlog.info("scheduler", f"Reconfiguré (queue={queue_min}min, scan={scan_min}min)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
