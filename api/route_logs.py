from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.templates import templates
from config import get_config
from core import eventlog

router = APIRouter(tags=["logs"])


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    stats    = eventlog.get_stats()
    cfg_logs = get_config().get("logs", {})
    return templates.TemplateResponse(
        request=request, name="logs.html",
        context={
            "stats":        stats,
            "categories":   eventlog.CATEGORY_LABELS,
            "levels":       eventlog.VALID_LEVELS,
            "logs_enabled": cfg_logs.get("enabled", True),
        },
    )


@router.get("/api/logs")
def api_logs(limit: int = 200, offset: int = 0,
             level: str = "", category: str = "", search: str = ""):
    limit  = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    return JSONResponse(eventlog.query_logs(
        limit=limit, offset=offset,
        level=level or None, category=category or None, search=search or None,
    ))


@router.get("/api/logs/stats")
def api_logs_stats():
    return JSONResponse(eventlog.get_stats())


@router.post("/api/logs/purge")
def api_logs_purge():
    n = eventlog.purge_all()
    eventlog.info("config", f"Journal purgé ({n} entrées)")
    return JSONResponse({"deleted": n})
