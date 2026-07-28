import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.templates import templates
from config import get_config, get_protected
from services.factory import get_jellyfin, get_transmission

router = APIRouter(tags=["suggestions"])


# ── Cache fichier persistant (1h) ─────────────────────────────────────────────
_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
_CACHE_TTL  = 3600  # 1 heure

def _cache_get(key: str) -> Optional[Any]:
    path = _CACHE_DIR / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - raw.get("t", 0) < _CACHE_TTL:
            return raw["d"]
    except Exception:
        pass
    return None

def _cache_set(key: str, data: Any):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps({"d": data, "t": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

def _cache_clear():
    for p in _CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
        except Exception:
            pass


# ── Helpers titre/seed (page Suggestions) ────────────────────────────────────

_STOP = re.compile(
    r"\b(?:19|20)\d{2}\b|\bS\d{2}\b"
    r"|\b(?:1080p?|2160p?|4k|uhd|bluray|blu|webrip|web|hdtv|dvdrip"
    r"|french|english|vf|vostfr|multi|complete|saison|season)\b",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.\-_/\\:!?'()\[\]{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _torrent_base(name: str) -> str:
    n = _norm(name)
    m = _STOP.search(n)
    return n[:m.start()].strip() if m else n


def _age(date_str: str, now: datetime):
    if not date_str:
        return "", ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        days = (now - dt).days
        label = (
            f"{days // 365} an{'s' if days // 365 > 1 else ''}" if days >= 365
            else f"{days // 30} mois" if days >= 30
            else f"{days} jour{'s' if days > 1 else ''}"
        )
        return dt.strftime("%d/%m/%Y"), label
    except Exception:
        return "", ""


def _match_torrent(title: str, original_title: str, trans_torrents: List[Dict]) -> Optional[Dict]:
    candidates = list({_norm(t) for t in [title, original_title] if t})
    best = None
    for t in trans_torrents:
        tbase = _torrent_base(t.get("name", ""))
        tname = _norm(t.get("name", ""))
        for nt in candidates:
            if nt == tbase:
                return t
            words = [w for w in nt.split() if len(w) >= 4]
            if len(words) >= 2 and all(w in set(tbase.split()) for w in words):
                best = t
            elif nt in tname and len(nt) >= 6:
                best = best or t
    return best


def _seed_info(title: str, original_title: str, trans_torrents: List[Dict]) -> Dict:
    t = _match_torrent(title, original_title, trans_torrents)
    if not t:
        return {"found": False, "ratio": 0.0, "rate_up": 0, "peers_up": 0, "uploaded_gb": 0.0, "size_gb": 0.0}
    return {
        "found":       True,
        "ratio":       round(t.get("uploadRatio", 0) or 0, 2),
        "rate_up":     t.get("rateUpload", 0) or 0,
        "peers_up":    t.get("peersGettingFromUs", 0) or 0,
        "uploaded_gb": round((t.get("uploadedEver", 0) or 0) / (1024 ** 3), 2),
        "size_gb":     round((t.get("sizeWhenDone", 0) or 0) / (1024 ** 3), 2),
    }


def _find_all_matching(title: str, original_title: str, trans_torrents: List[Dict]) -> List[Dict]:
    """Retourne TOUS les torrents correspondant au titre, dédupliqués par (nom, taille).
    La déduplication évite de compter N fois le même fichier seedé sur N trackers."""
    candidates = list({_norm(t) for t in [title, original_title] if t})
    seen_content: set = set()
    results: List[Dict] = []
    for t in trans_torrents:
        tbase = _torrent_base(t.get("name", ""))
        tname = _norm(t.get("name", ""))
        for nt in candidates:
            matched = nt == tbase
            if not matched:
                words = [w for w in nt.split() if len(w) >= 4]
                if len(words) >= 2 and all(w in set(tbase.split()) for w in words):
                    matched = True
                elif nt in tname and len(nt) >= 6:
                    matched = True
            if matched:
                size_key = (t.get("sizeWhenDone") or 0) or (t.get("totalSize") or 0)
                content_key = (t.get("name", ""), size_key)
                if content_key not in seen_content:
                    seen_content.add(content_key)
                    results.append(t)
                break
    return results


def _seed_info_aggregate(title: str, original_title: str, trans_torrents: List[Dict]) -> Dict:
    """Agrège les stats de TOUS les torrents correspondants (somme taille, upload, peers)."""
    matches = _find_all_matching(title, original_title, trans_torrents)
    if not matches:
        return {"found": False, "ratio": 0.0, "rate_up": 0, "peers_up": 0, "uploaded_gb": 0.0, "size_gb": 0.0}
    def _size(t: Dict) -> int:
        return (t.get("sizeWhenDone") or 0) or (t.get("totalSize") or 0)
    total_size    = sum(_size(t) for t in matches)
    total_upload  = sum((t.get("uploadedEver", 0) or 0) for t in matches)
    total_rate_up = sum((t.get("rateUpload", 0) or 0) for t in matches)
    total_peers   = sum((t.get("peersGettingFromUs", 0) or 0) for t in matches)
    ratio = round(total_upload / total_size, 2) if total_size > 0 else 0.0
    return {
        "found":       True,
        "ratio":       ratio,
        "rate_up":     total_rate_up,
        "peers_up":    total_peers,
        "uploaded_gb": round(total_upload / (1024 ** 3), 2),
        "size_gb":     round(total_size / (1024 ** 3), 2),
    }


def _process_suggestion(it: Dict, watched_per_user: Dict, jellyfin_url: str,
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
        "seed":         _seed_info(title, original_title, trans_torrents),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/suggestions", response_class=HTMLResponse)
def suggestions_page(request: Request, refresh: int = 0):
    cache_key = "suggestions"
    if not refresh:
        cached = _cache_get(cache_key)
        if cached:
            return templates.TemplateResponse(request=request, name="suggestions.html", context=cached)

    jf  = get_jellyfin()
    cfg = get_config()
    jellyfin_url     = cfg["jellyfin"]["url"].rstrip("/")
    jellyfin_api_key = cfg["jellyfin"]["api_key"]

    users = jf.get_users()
    if not users:
        ctx = {"never_watched": [], "partial_watched": [], "dead_seed": [], "users": []}
        return templates.TemplateResponse(request=request, name="suggestions.html", context=ctx)

    admin_user          = users[0]["Id"]
    user_watched_movies = {u["Id"]: jf.get_played_item_ids(u["Id"], "Movie")   for u in users}
    user_watched_series = {u["Id"]: jf.get_played_item_ids(u["Id"], "Episode") for u in users}

    all_movies = jf.get_all_items_metadata(admin_user, "Movie",  limit=500)
    all_series = jf.get_all_items_metadata(admin_user, "Series", limit=300)

    protected_cfg    = get_protected()
    protected_titles = {t.lower() for t in protected_cfg.get("titles", [])}
    protected_ids    = set(protected_cfg.get("jellyfin_ids", []))
    now = datetime.now(timezone.utc)

    try:
        trans_torrents = get_transmission().get_all_torrents_with_stats()
    except Exception:
        trans_torrents = []

    items = (
        [_process_suggestion(it, user_watched_movies, jellyfin_url, jellyfin_api_key,
                              protected_ids, protected_titles, trans_torrents, now)
         for it in all_movies]
        +
        [_process_suggestion(it, user_watched_series, jellyfin_url, jellyfin_api_key,
                              protected_ids, protected_titles, trans_torrents, now)
         for it in all_series]
    )
    items.sort(key=lambda x: x["date_added"] or "9999")

    never_watched   = [i for i in items if i["watch_count"] == 0 and not i["is_protected"]]
    partial_watched = [i for i in items if 0 < i["watch_count"] < i["total_users"] and not i["is_protected"]]
    dead_seed = sorted(
        [i for i in items if i["seed"]["found"] and i["seed"]["ratio"] == 0
         and i["seed"]["rate_up"] == 0 and not i["is_protected"]],
        key=lambda x: x["seed"]["size_gb"], reverse=True,
    )

    ctx = {
        "never_watched":   never_watched[:80],
        "partial_watched": partial_watched[:40],
        "dead_seed":       dead_seed[:60],
        "users":           users,
    }
    _cache_set(cache_key, ctx)
    return templates.TemplateResponse(request=request, name="suggestions.html", context=ctx)


@router.post("/api/cache/refresh")
def refresh_cache():
    """Invalide le cache fichier de la page Suggestions/Catalogue."""
    _cache_clear()
    return JSONResponse({"ok": True})
