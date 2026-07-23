import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from core.pipeline import handle_watch_event
from database import get_db
from services.factory import get_jellyfin

logger = logging.getLogger("purgearr.webhook")
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _calc_percentage(position_ticks: Optional[int], run_time_ticks: Optional[int], played: bool) -> float:
    if played:
        return 100.0
    if run_time_ticks and run_time_ticks > 0 and position_ticks:
        return min((position_ticks / run_time_ticks) * 100, 100.0)
    return 0.0


@router.post("/jellyfin")
async def jellyfin_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Endpoint que Jellyfin appelle à chaque événement PlaybackStop.
    Configurer dans Jellyfin Webhook Plugin → POST http://<pi-ip>:<port>/webhook/jellyfin
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "payload invalide"}

    event_type = payload.get("NotificationType", "")
    if event_type != "PlaybackStop":
        return {"status": "ignored", "reason": f"event '{event_type}' non géré"}

    item_type = payload.get("ItemType", "")
    if item_type not in ("Movie", "Episode"):
        return {"status": "ignored", "reason": f"type '{item_type}' non géré"}

    jellyfin_item_id = payload.get("ItemId")
    user_id = payload.get("UserId")
    user_name = payload.get("UserName", "?")

    if not jellyfin_item_id or not user_id:
        return {"status": "ignored", "reason": "ItemId ou UserId manquant"}

    # Calcul du pourcentage visionné
    percentage = _calc_percentage(
        payload.get("PlaybackPositionTicks"),
        payload.get("RunTimeTicks"),
        payload.get("PlayedToCompletion", False),
    )

    # Récupérer les détails complets depuis l'API Jellyfin (pour le Path et les ProviderIds)
    try:
        jf = get_jellyfin()
        item_details = jf.get_item(jellyfin_item_id, user_id)
        provider_ids = item_details.get("ProviderIds", {})
        file_path = item_details.get("Path", "")
        all_users = [u["Id"] for u in jf.get_users()]
    except Exception as e:
        logger.warning(f"[Webhook] Impossible de récupérer les détails Jellyfin: {e}")
        provider_ids = {}
        file_path = ""
        all_users = [user_id]

    item_title = payload.get("Name") or item_details.get("Name", "?")
    series_title = payload.get("SeriesName") or item_details.get("SeriesName")

    logger.info(
        f"[Webhook] PlaybackStop — {user_name} — {item_title} "
        f"({'S{:02d}E{:02d}'.format(payload.get('SeasonNumber',0), payload.get('EpisodeNumber',0)) if item_type == 'Episode' else 'Film'}) "
        f"— {percentage:.0f}%"
    )

    handle_watch_event(
        db=db,
        jellyfin_item_id=jellyfin_item_id,
        user_id=user_id,
        user_name=user_name,
        item_type=item_type,
        item_title=item_title,
        series_title=series_title,
        season=payload.get("SeasonNumber"),
        episode=payload.get("EpisodeNumber"),
        percentage=percentage,
        tmdb_id=provider_ids.get("Tmdb") or payload.get("Provider_tmdb"),
        tvdb_id=provider_ids.get("Tvdb") or payload.get("Provider_tvdb"),
        imdb_id=provider_ids.get("Imdb") or payload.get("Provider_imdb"),
        file_path=file_path,
        all_user_ids=all_users,
    )

    return {"status": "ok", "item": item_title, "percentage": round(percentage, 1)}
