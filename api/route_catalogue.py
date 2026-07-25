from math import ceil
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.templates import templates
from api.route_suggestions import (
    _cache_get, _cache_set, _age, _seed_info_aggregate, _norm,
    _find_all_matching, _torrent_base,
)
from config import get_config, get_protected
from services.factory import get_jellyfin, get_transmission

router = APIRouter(tags=["catalogue"])

PAGE_SIZE = 60


def _process_item(it: Dict, watched_per_user: Dict, jellyfin_url: str,
                  jellyfin_api_key: str, protected_ids: set,
                  protected_titles: set, trans_torrents: List[Dict],
                  now: datetime) -> Dict:
    item_id        = it["Id"]
    title          = it.get("Name", "?")
    original_title = it.get("OriginalTitle", "") or ""
    ud      = it.get("UserData", {})
    is_fav  = ud.get("IsFavorite", False)
    is_prot = item_id in protected_ids or title.lower() in protected_titles or is_fav
    watch_count = sum(1 for ids in watched_per_user.values() if item_id in ids)
    date_added, age = _age(it.get("DateCreated", ""), now)
    return {
        "id":           item_id,
        "title":        title,
        "type":         it.get("Type", "Movie"),
        "is_favorite":  is_fav,
        "is_protected": is_prot,
        "watch_count":  watch_count,
        "total_users":  len(watched_per_user),
        "date_added":   date_added,
        "age":          age,
        "image_url":    f"{jellyfin_url}/Items/{item_id}/Images/Primary?fillWidth=220&quality=80&api_key={jellyfin_api_key}",
        "seed":         _seed_info_aggregate(title, original_title, trans_torrents),
    }


def _build_catalogue() -> Dict:
    jf  = get_jellyfin()
    cfg = get_config()
    jellyfin_url     = cfg["jellyfin"]["url"].rstrip("/")
    jellyfin_api_key = cfg["jellyfin"]["api_key"]

    users = jf.get_users()
    if not users:
        return {"films": [], "series": []}

    admin_user          = users[0]["Id"]
    user_watched_movies = {u["Id"]: jf.get_played_item_ids(u["Id"], "Movie")   for u in users}
    user_watched_series = {u["Id"]: jf.get_played_item_ids(u["Id"], "Episode") for u in users}

    all_movies = jf.get_all_items_metadata(admin_user, "Movie",  limit=2000)
    all_series = jf.get_all_items_metadata(admin_user, "Series", limit=1000)

    protected_cfg    = get_protected()
    protected_titles = {t.lower() for t in protected_cfg.get("titles", [])}
    protected_ids    = set(protected_cfg.get("jellyfin_ids", []))
    now = datetime.now(timezone.utc)

    try:
        trans_torrents = get_transmission().get_all_torrents_with_stats()
    except Exception:
        trans_torrents = []

    films = sorted(
        [_process_item(it, user_watched_movies, jellyfin_url, jellyfin_api_key,
                       protected_ids, protected_titles, trans_torrents, now)
         for it in all_movies],
        key=lambda x: x["date_added"] or "0000", reverse=True,
    )
    series = sorted(
        [_process_item(it, user_watched_series, jellyfin_url, jellyfin_api_key,
                       protected_ids, protected_titles, trans_torrents, now)
         for it in all_series],
        key=lambda x: x["date_added"] or "0000", reverse=True,
    )
    return {"films": films, "series": series}


_STATUS_FILTERS = {
    "never":   lambda i: i["watch_count"] == 0,
    "partial": lambda i: 0 < i["watch_count"] < i["total_users"],
    "seen":    lambda i: i["total_users"] > 0 and i["watch_count"] == i["total_users"],
    "seeding": lambda i: i["seed"]["found"] and (i["seed"]["rate_up"] > 0 or i["seed"]["peers_up"] > 0),
    "idle":    lambda i: i["seed"]["found"] and i["seed"]["ratio"] > 0 and i["seed"]["rate_up"] == 0 and i["seed"]["peers_up"] == 0,
    "dead":    lambda i: i["seed"]["found"] and i["seed"]["ratio"] == 0 and i["seed"]["rate_up"] == 0,
    "noseed":  lambda i: not i["seed"]["found"],
}

_SORT_KEYS = {
    "date_desc":  (lambda i: i["date_added"] or "0000", True),
    "date_asc":   (lambda i: i["date_added"] or "9999", False),
    "alpha_asc":  (lambda i: i["title"].lower(), False),
    "alpha_desc": (lambda i: i["title"].lower(), True),
    "size_desc":  (lambda i: i["seed"]["size_gb"], True),
    "size_asc":   (lambda i: i["seed"]["size_gb"], False),
}


def _fmt_gb(gb: float) -> str:
    if gb >= 1024:
        return f"{round(gb / 1024, 2)} To"
    return f"{round(gb, 1)} Go"


@router.get("/catalogue", response_class=HTMLResponse)
def catalogue_page(request: Request, tab: str = "films", page: int = 1,
                   q: str = "", sort: str = "date_desc", status: str = "all",
                   refresh: int = 0):
    cache_key = "catalogue_all"
    cached = None if refresh else _cache_get(cache_key)

    if cached is None:
        cached = _build_catalogue()
        _cache_set(cache_key, cached)

    all_films  = cached["films"]
    all_series = cached["series"]

    # Totaux sur l'ensemble du catalogue (avant tout filtre)
    films_size  = _fmt_gb(sum(i["seed"]["size_gb"] for i in all_films  if i["seed"]["size_gb"] > 0))
    series_size = _fmt_gb(sum(i["seed"]["size_gb"] for i in all_series if i["seed"]["size_gb"] > 0))

    # Recherche
    if q:
        q_norm     = _norm(q)
        all_films  = [i for i in all_films  if q_norm in _norm(i["title"])]
        all_series = [i for i in all_series if q_norm in _norm(i["title"])]

    # Filtre statut
    if status in _STATUS_FILTERS:
        fn = _STATUS_FILTERS[status]
        all_films  = [i for i in all_films  if fn(i)]
        all_series = [i for i in all_series if fn(i)]

    # Tri
    sort_fn, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["date_desc"])
    all_films  = sorted(all_films,  key=sort_fn, reverse=reverse)
    all_series = sorted(all_series, key=sort_fn, reverse=reverse)

    source      = all_films if tab == "films" else all_series
    total_items = len(source)
    total_pages = max(1, ceil(total_items / PAGE_SIZE))
    page        = max(1, min(page, total_pages))
    items       = source[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    ctx = {
        "tab":          tab,
        "page":         page,
        "total_pages":  total_pages,
        "total_items":  total_items,
        "total_films":  len(all_films),
        "total_series": len(all_series),
        "films_size":   films_size,
        "series_size":  series_size,
        "items":        items,
        "q":            q,
        "sort":         sort,
        "status":       status,
        "page_size":    PAGE_SIZE,
    }
    return templates.TemplateResponse(request=request, name="catalogue.html", context=ctx)


@router.get("/api/debug/size")
def debug_size(title: str = ""):
    """Debug : affiche les torrents matchés pour un titre et leurs tailles brutes."""
    if not title:
        return JSONResponse({"error": "Passe ?title=NomDuFilm"})
    try:
        trans_torrents = get_transmission().get_all_torrents_with_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)})

    title_norm = _norm(title)
    matched = _find_all_matching(title, "", trans_torrents)
    candidates_debug = []
    for t in trans_torrents[:5]:
        candidates_debug.append({
            "name": t.get("name"),
            "tbase": _torrent_base(t.get("name", "")),
            "tnorm": _norm(t.get("name", "")),
        })

    return JSONResponse({
        "title_input":    title,
        "title_norm":     title_norm,
        "total_torrents": len(trans_torrents),
        "matched_count":  len(matched),
        "matched": [
            {
                "name":          t.get("name"),
                "tbase":         _torrent_base(t.get("name", "")),
                "sizeWhenDone":  t.get("sizeWhenDone"),
                "totalSize":     t.get("totalSize"),
                "size_gb":       round(((t.get("sizeWhenDone") or 0) or (t.get("totalSize") or 0)) / (1024**3), 2),
                "uploadRatio":   t.get("uploadRatio"),
                "status":        t.get("status"),
            }
            for t in matched
        ],
        "sample_torrents": candidates_debug,
    })
