import json
import logging
import os
import tempfile
import yaml
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("purgearr.config")

# Dossier persistant — NE PAS écraser lors des mises à jour
DATA_DIR       = Path(__file__).parent / "data"
CONFIG_PATH    = DATA_DIR / "config.json"
PROTECTED_PATH = DATA_DIR / "protected.json"

DATA_DIR.mkdir(exist_ok=True)

_LEGACY_YAML = Path(__file__).parent / "config.yaml"
_config: Dict[str, Any] = {}


def _atomic_write_json(path: Path, data: Any):
    """Écrit dans un fichier temporaire puis rename atomique — évite la corruption si crash."""
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _migrate_from_yaml():
    """Première installation : copie config.yaml → data/config.json."""
    if not _LEGACY_YAML.exists():
        return
    with open(_LEGACY_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.pop("protected", None)
    _atomic_write_json(CONFIG_PATH, data)
    logger.info("Migration config.yaml → data/config.json effectuée")


def load_config() -> Dict[str, Any]:
    global _config
    if not CONFIG_PATH.exists():
        _migrate_from_yaml()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config = json.load(f)
    except FileNotFoundError:
        _config = {}
    except json.JSONDecodeError as e:
        logger.error(f"config.json invalide ({e}) — utilisation d'une config vide")
        _config = {}
    return _config


def save_config(data: Dict[str, Any]):
    """Écrit data/config.json et met à jour le cache mémoire."""
    data.pop("protected", None)
    _atomic_write_json(CONFIG_PATH, data)
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
    except json.JSONDecodeError as e:
        logger.error(f"protected.json invalide ({e}) — utilisation d'une liste vide")
        return {"titles": [], "jellyfin_ids": []}


def save_protected(data: Dict[str, Any]):
    """Écrit data/protected.json."""
    _atomic_write_json(PROTECTED_PATH, data)


def get_scheduler_config() -> Dict[str, Any]:
    return get_config().get("scheduler", {})


def get_logs_config() -> Dict[str, Any]:
    """Config du journal événementiel — activé par défaut, rétention 30j / 10k entrées."""
    cfg = get_config().get("logs", {})
    return {
        "enabled":         cfg.get("enabled", True),
        "retention_days":  int(cfg.get("retention_days", 30)),
        "max_entries":     int(cfg.get("max_entries", 10000)),
    }


def get_language() -> str:
    return get_config().get("language", "fr")


def get_mode() -> str:
    return get_rules().get("mode", "manual")


def get_extra_paths():
    return get_config().get("extra_paths", [])


def get_scan_paths(item_type: str = "") -> list:
    """Chemins à scanner : extra_paths + racine films ou séries selon item_type."""
    cfg = get_config()
    paths = list(cfg.get("extra_paths", []))
    if item_type in ("Episode", "Series"):
        root = cfg.get("library_root_series", "").strip()
    else:
        root = cfg.get("library_root_movies", "").strip()
    if root and root not in paths:
        paths.append(root)
    return paths


def resolve_real_path(jf_path: str, item_type: str = "") -> str:
    """
    Jellyfin renvoie un chemin avec un préfixe de mount différent du filesystem réel.
    Teste toutes les racines connues (movies, series, extra_paths) dans l'ordre,
    en strippant progressivement les composants du chemin Jellyfin.
    """
    import os
    from pathlib import Path

    if not jf_path:
        return ""

    if os.path.isfile(jf_path) or os.path.isdir(jf_path):
        return jf_path

    cfg = get_config()

    # Racine du bon type en premier, l'autre en fallback, puis extra_paths
    if item_type in ("Episode", "Series"):
        roots = [
            cfg.get("library_root_series", "").strip(),
            cfg.get("library_root_movies", "").strip(),
        ]
    else:
        roots = [
            cfg.get("library_root_movies", "").strip(),
            cfg.get("library_root_series", "").strip(),
        ]
    roots += [p.strip() for p in cfg.get("extra_paths", [])]

    parts = Path(jf_path).parts
    for root in roots:
        if not root:
            continue
        for i in range(1, len(parts)):
            relative = os.path.join(*parts[i:])
            candidate = os.path.join(root, relative)
            if os.path.isfile(candidate) or os.path.isdir(candidate):
                return candidate

    return jf_path
