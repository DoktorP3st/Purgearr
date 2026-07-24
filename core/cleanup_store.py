import json
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR  = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "cleanup_index.json"


def load_index() -> list:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_index(entries: list):
    DATA_DIR.mkdir(exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def add_entry(
    item_title: str,
    item_type: str,
    source_hash: str,
    file_path: str = "",
    series_title: str = None,
    jellyfin_item_id: str = "",
    file_size_bytes: int = 0,
    torrent_name: str = None,
    scan_paths: list = None,
):
    if not source_hash:
        return
    entries = load_index()
    entries.append({
        "id":                 str(uuid.uuid4())[:8],
        "item_title":         item_title,
        "series_title":       series_title,
        "item_type":          item_type,
        "jellyfin_item_id":   jellyfin_item_id,
        "source_hash":        source_hash,
        "file_path":          file_path,
        "file_size_bytes":    file_size_bytes,
        "torrent_name":       torrent_name,
        "scan_paths":         scan_paths or [],
        "deleted_at":         datetime.utcnow().isoformat(),
        "remains_checked_at": None,
        "remains_found":      None,
    })
    save_index(entries)
