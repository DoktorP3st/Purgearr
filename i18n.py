import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("purgearr.i18n")

LOCALES_DIR = Path(__file__).parent / "locales"
FALLBACK = "fr"

SUPPORTED_LANGUAGES: dict = {
    "fr": "Français",
    "en": "English",
}


@lru_cache(maxsize=8)
def _load(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        path = LOCALES_DIR / f"{FALLBACK}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("i18n: cannot load %s: %s", lang, e)
        return {}


def translate(lang: str, key: str) -> str:
    """Traduit key (notation pointée) dans lang, avec fallback fr, puis key elle-même."""
    locale = _load(lang)
    val = locale
    for part in key.split("."):
        if isinstance(val, dict):
            val = val.get(part)
        else:
            val = None
        if val is None:
            break
    if val is None or not isinstance(val, str):
        if lang != FALLBACK:
            return translate(FALLBACK, key)
        return key
    return val


def get_js_strings(lang: str) -> dict:
    """Retourne la section 'js' du locale pour injection côté client."""
    return _load(lang).get("js", {})
