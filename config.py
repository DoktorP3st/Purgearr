import json
import yaml
from pathlib import Path
from typing import Dict, Any

# Dossier persistant — NE PAS écraser lors des mises à jour
DATA_DIR       = Path(__file__).parent / "data"
CONFIG_PATH    = DATA_DIR / "config.json"
PROTECTED_PATH = DATA_DIR / "protected.json"

DATA_DIR.mkdir(exist_ok=True)

_LEGACY_YAML = Path(__file__).parent / "config.yaml"
_config: Dict[str, Any] = {}


def _migrate_from_yaml():
    """Première installation : copie config.yaml → data/config.json."""
    if not _LEGACY_YAML.exists():
        return
    with open(_LEGACY_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.pop("protected", None)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("[config] Migration config.yaml → data/config.json effectuée")


def load_config() -> Dict[str, Any]:
    global _config
    if not CONFIG_PATH.exists():
        _migrate_from_yaml()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _config = json.load(f)
    return _config


def save_config(data: Dict[str, Any]):
    """Écrit data/config.json et met à jour le cache mémoire."""
    data.pop("protected", None)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    global _config
    _config = dict(data)


def get_config() -> Dict[str, Any]:
    if not _config:
        load_config()
    return _config


def get_rules() -> Dict[str, Any]:
    return get_config().get("rules", {})


def get_protected() -> Dict[str, Any]:
    """Lit data/protected.json. Migre depuis config.yaml au premier appel."""
    try:
        with open(PROTECTED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        legacy = get_config().get("protected", {"titles": [], "jellyfin_ids": []})
        save_protected(legacy)
        return legacy
    except Exception:
        return {"titles": [], "jellyfin_ids": []}


def save_protected(data: Dict[str, Any]):
    """Écrit data/protected.json."""
    with open(PROTECTED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_scheduler_config() -> Dict[str, Any]:
    return get_config().get("scheduler", {})


def get_mode() -> str:
    return get_rules().get("mode", "manual")


def get_extra_paths():
    return get_config().get("extra_paths", [])
