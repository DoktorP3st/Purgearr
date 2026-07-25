import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.templates import templates
from core import eventlog
from services.factory import get_transmission

logger = logging.getLogger("purgearr.routes.transmission")
router = APIRouter(tags=["transmission"])


@router.get("/transmission", response_class=HTMLResponse)
def transmission_page(request: Request):
    try:
        tr = get_transmission()
        orphans      = tr.find_orphaned_torrents()
        all_torrents = tr.get_all_torrents_with_stats()
    except Exception as e:
        orphans      = []
        all_torrents = []
        logger.error("[Transmission] Erreur : %s", e)
    return templates.TemplateResponse(request=request, name="transmission.html",
                                      context={"orphans": orphans, "all_torrents": all_torrents})


@router.get("/api/transmission/orphans")
def api_transmission_orphans():
    try:
        orphans = get_transmission().find_orphaned_torrents()
        return JSONResponse([{"id": t["id"], "name": t["name"],
                              "path": t["expected_path"]} for t in orphans])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/transmission/remove")
def api_transmission_remove(torrent_id: int = Form(...)):
    try:
        get_transmission().stop_and_remove(torrent_id, delete_data=False)
        eventlog.info("deletion", f"Torrent orphelin supprimé (id={torrent_id})")
        return JSONResponse({"success": True})
    except Exception as e:
        eventlog.error("service", f"Suppression torrent id={torrent_id} KO : {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/api/transmission/remove-all-orphans")
def api_remove_all_orphans():
    try:
        tr = get_transmission()
        orphans = tr.find_orphaned_torrents()
        removed, failed = 0, 0
        for t in orphans:
            try:
                tr.stop_and_remove(t["id"], delete_data=False)
                removed += 1
            except Exception:
                failed += 1
        level = "warning" if failed else "info"
        eventlog.log_event(level, "deletion",
                           f"Purge orphelins Transmission : {removed} supprimés, {failed} échec(s)")
        return JSONResponse({"success": True, "removed": removed, "failed": failed})
    except Exception as e:
        eventlog.error("service", f"Purge orphelins KO : {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
