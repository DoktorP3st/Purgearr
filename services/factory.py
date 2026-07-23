from config import get_config
from .radarr import RadarrClient
from .sonarr import SonarrClient
from .transmission import TransmissionClient
from .jellyfin import JellyfinClient


def get_radarr() -> RadarrClient:
    cfg = get_config()["radarr"]
    return RadarrClient(cfg["url"], cfg["api_key"])


def get_sonarr() -> SonarrClient:
    cfg = get_config()["sonarr"]
    return SonarrClient(cfg["url"], cfg["api_key"])


def get_transmission() -> TransmissionClient:
    cfg = get_config()["transmission"]
    return TransmissionClient(
        host=cfg["host"],
        port=cfg["port"],
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
    )


def get_jellyfin() -> JellyfinClient:
    cfg = get_config()["jellyfin"]
    return JellyfinClient(cfg["url"], cfg["api_key"])
