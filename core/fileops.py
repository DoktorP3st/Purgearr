import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("purgearr.fileops")

METADATA_EXTENSIONS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn", ".bmp", ".gif",
    ".srt", ".sub", ".idx", ".ass", ".ssa", ".sup",
    ".sfv", ".md5", ".sha256", ".txt",
}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".m2ts", ".iso"}


def _file_hash(path: str, chunk: int = 65536) -> str:
    """SHA-256 des premiers 64 Ko d'un fichier."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(chunk))
        return h.hexdigest()
    except Exception:
        return ""


def hash_file(path: str) -> str:
    """Empreinte SHA-256 (64 Ko) — à appeler AVANT toute suppression."""
    return _file_hash(path)


def _inode_key(path: str) -> Optional[Tuple[int, int]]:
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino)
    except Exception:
        return None


def _calc_size(path: str) -> Tuple[int, int]:
    if os.path.isfile(path):
        try:
            return os.path.getsize(path), 1
        except Exception:
            return 0, 1
    total, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except Exception:
                pass
    return total, count



def format_size(bytes_: int) -> str:
    if bytes_ >= 1_073_741_824:
        return f"{bytes_ / 1_073_741_824:.2f} Go"
    if bytes_ >= 1_048_576:
        return f"{bytes_ / 1_048_576:.1f} Mo"
    return f"{bytes_ / 1024:.1f} Ko"


# ── Scan par hash ─────────────────────────────────────────────────────────────

def scan_copies_smart(
    title: str,
    known_path: str,
    extra_paths: List[str],
    source_hash: str = "",
) -> Dict:
    """
    Trouve les copies dans extra_paths par inode (hardlinks) puis hash SHA-256.
    source_hash : hash pré-calculé avant suppression Radarr/Sonarr.
    Aucun matching par titre — uniquement contenu identique.
    """
    known_hash   = source_hash
    known_inode  = None
    known_real   = ""
    known_is_dir = False
    known_size: Optional[int] = None

    if known_path and os.path.isfile(known_path):
        known_real  = os.path.realpath(known_path)
        known_inode = _inode_key(known_path)
        known_hash  = _file_hash(known_path)
        try:
            known_size = os.path.getsize(known_path)
        except OSError:
            known_size = None
    elif known_path and os.path.isdir(known_path):
        known_real   = os.path.realpath(known_path)
        known_is_dir = True
        # Premier fichier vidéo dans le dossier — sert de référence inode/hash/taille
        for _r, _d, _f in os.walk(known_path):
            for _fn in sorted(_f):
                if Path(_fn).suffix.lower() in VIDEO_EXTENSIONS:
                    _fp = os.path.join(_r, _fn)
                    known_inode = _inode_key(_fp)
                    if not known_hash:
                        known_hash = _file_hash(_fp)
                    try:
                        known_size = os.path.getsize(_fp)
                    except OSError:
                        known_size = None
                    break
            if known_hash:
                break

    if not known_hash:
        return {
            "title": title, "known_path": known_path, "source_hash": "",
            "strategy": "indisponible", "copies": [], "total_copies": 0,
            "total_size_bytes": 0, "total_size_human": format_size(0),
            "has_inode_match": False, "skipped": True,
        }

    strategy = "inode+hash" if known_inode else "hash"
    found: List[Dict] = []

    for base in extra_paths:
        base = base.strip()
        if not base or not os.path.isdir(base):
            logger.warning("[Scan] Chemin ignoré (introuvable) : %s", base)
            continue

        base_depth = base.rstrip(os.sep).count(os.sep)
        reported: set = set()

        try:
            for root, dirs, files in os.walk(base, followlinks=False):
                depth = root.count(os.sep) - base_depth
                if depth >= 5:
                    dirs.clear()
                    continue

                for fname in files:
                    if Path(fname).suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    fpath = os.path.join(root, fname)

                    # Ignorer les fichiers appartenant au dossier/fichier source
                    try:
                        real_fpath = os.path.realpath(fpath)
                        if known_real:
                            if known_is_dir and real_fpath.startswith(known_real + os.sep):
                                continue
                            elif not known_is_dir and real_fpath == known_real:
                                continue
                    except Exception:
                        pass

                    match_method: Optional[str] = None
                    if known_inode and _inode_key(fpath) == known_inode:
                        match_method = "inode"
                    else:
                        # Skip hash si taille différente (rapide, évite d'ouvrir le fichier)
                        if known_size is not None:
                            try:
                                if os.path.getsize(fpath) != known_size:
                                    continue
                            except OSError:
                                continue
                        if _file_hash(fpath) == known_hash:
                            match_method = "hash"

                    if not match_method:
                        continue

                    # Dossier parent du fichier trouvé = dossier release
                    # (évite de remonter trop haut si base contient film/ ou serie/)
                    parent = os.path.dirname(fpath)
                    if os.path.normpath(parent) == os.path.normpath(base):
                        release = fpath   # fichier directement dans base
                    else:
                        release = parent  # dossier contenant le fichier

                    if release in reported:
                        continue
                    reported.add(release)

                    # Ignorer si c'est la source elle-même
                    try:
                        real_release = os.path.realpath(release)
                        if known_real:
                            if known_is_dir and real_release == known_real:
                                continue
                            elif not known_is_dir and real_release == os.path.dirname(known_real):
                                continue
                    except Exception:
                        pass

                    size_bytes, file_count = _calc_size(release)
                    found.append({
                        "path":         release,
                        "is_dir":       os.path.isdir(release),
                        "size_bytes":   size_bytes,
                        "size_human":   format_size(size_bytes),
                        "file_count":   file_count,
                        "match_method": match_method,
                        "is_hardlink":  match_method == "inode",
                    })
                    logger.info("[Scan] Copie trouvée (%s) : %s", match_method, release)

        except PermissionError as e:
            logger.warning("[Scan] Accès refusé à %s : %s", base, e)

    total = sum(e["size_bytes"] for e in found)
    return {
        "title":            title,
        "known_path":       known_path,
        "source_hash":      known_hash,
        "strategy":         strategy,
        "copies":           found,
        "total_copies":     len(found),
        "total_size_bytes": total,
        "total_size_human": format_size(total),
        "has_inode_match":  any(e["is_hardlink"] for e in found),
        "skipped":          False,
    }


# ── Suppression ───────────────────────────────────────────────────────────────

def delete_copy(entry: Dict) -> Dict:
    path = entry["path"]
    try:
        if entry["is_dir"]:
            shutil.rmtree(path)
        else:
            os.remove(path)
            _delete_companions(path)
        logger.info("[Fileops] Supprimé : %s", path)
        return {**entry, "success": True, "error": None}
    except Exception as e:
        logger.error("[Fileops] Erreur suppression %s : %s", path, e)
        return {**entry, "success": False, "error": str(e)}


def _delete_companions(file_path: str):
    p = Path(file_path)
    parent, stem = p.parent, p.stem
    for sibling in list(parent.iterdir()):
        if sibling == p:
            continue
        if sibling.stem.startswith(stem) and sibling.suffix.lower() in METADATA_EXTENSIONS:
            try:
                sibling.unlink()
            except Exception:
                pass
    try:
        remaining = list(parent.iterdir())
        if all(f.is_file() and f.suffix.lower() in METADATA_EXTENSIONS for f in remaining):
            for f in remaining:
                try:
                    Path(f).unlink()
                except Exception:
                    pass
            parent.rmdir()
    except Exception:
        pass


def run_cleanup_from_scan(scan_result: Dict) -> Dict:
    copies = scan_result.get("copies", [])
    if not copies:
        return {"skipped": True, "copies_found": 0}

    results     = [delete_copy(c) for c in copies]
    ok          = [r for r in results if r["success"]]
    total_bytes = sum(r["size_bytes"] for r in ok)
    total_files = sum(r["file_count"] for r in ok)

    return {
        "skipped":        False,
        "copies_found":   len(copies),
        "copies_deleted": len(ok),
        "copies_failed":  len(results) - len(ok),
        "total_files":    total_files,
        "size_bytes":     total_bytes,
        "size_human":     format_size(total_bytes),
        "details": [
            {
                "path":         r["path"],
                "success":      r["success"],
                "size":         format_size(r["size_bytes"]),
                "files":        r["file_count"],
                "match_method": r.get("match_method", "?"),
                "is_hardlink":  r.get("is_hardlink", False),
                "error":        r.get("error"),
            }
            for r in results
        ],
    }


def run_cleanup(title: str, file_path: str, extra_paths: List[str], source_hash: str = "") -> Dict:
    """Appelé depuis pipeline.py après suppression Radarr/Sonarr."""
    if not extra_paths:
        return {"skipped": True, "copies_found": 0}
    scan = scan_copies_smart(title, file_path, extra_paths, source_hash=source_hash)
    return run_cleanup_from_scan(scan)
