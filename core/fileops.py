import hashlib
import logging
import os
import re
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


# ── Helpers bas niveau ────────────────────────────────────────────────────────

def _inode_key(path: str) -> Optional[Tuple[int, int]]:
    """Retourne (device, inode) — deux hardlinks ont la même valeur."""
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino)
    except Exception:
        return None


def _file_hash(path: str, chunk: int = 65536) -> str:
    """SHA-256 des premiers 64 Ko d'un fichier (empreinte rapide)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(chunk))
        return h.hexdigest()
    except Exception:
        return ""


# Articles et prépositions ignorés dans la comparaison (mais PAS les chiffres/numéros de saga)
_STOP_WORDS = {
    "the", "and", "of", "in", "a", "an", "to", "or", "by", "at", "from",
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "au", "aux",
    "en", "sur", "dans", "par", "pour",
}


def _normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[''`]", "", name)            # apostrophes → rien
    name = re.sub(r"\(?\b(19|20)\d{2}\b\)?", "", name)
    name = re.sub(
        r"\b(bluray|bdrip|webrip|web-?dl|hdtv|4k|uhd|1080p|720p|480p"
        r"|x264|x265|hevc|aac|dts|hdr|remux|complete"
        r"|french|vff|vf|multi|truefrench|vostfr|mhd|custom)\b", "", name)
    name = re.sub(r"[._\-\[\]\(\)\s]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _sig_words(norm: str) -> list:
    """Mots significatifs : sans stop words, longueur >= 2 (garde 'ii', 'iii', '2'…)."""
    return [w for w in norm.split() if w not in _STOP_WORDS and len(w) >= 2]


def _matches(entry_name: str, title: str) -> bool:
    norm_entry = _normalize(entry_name)
    norm_title = _normalize(title)
    if not norm_title or not norm_entry:
        return False

    # Correspondance exacte
    if norm_title == norm_entry:
        return True

    title_sig = _sig_words(norm_title)
    entry_sig  = _sig_words(norm_entry)

    if not title_sig or len(entry_sig) < len(title_sig):
        return False

    # Les mots significatifs du titre doivent être les PREMIERS mots
    # significatifs de l'entrée — dans l'ordre, en tête.
    # "blade ii"   → ["blade","ii"] doit être en tête → matche "blade ii 2002…"
    # "blade ii"   → NE matche PAS "blade trinity…" (["blade","trinity"] ≠ ["blade","ii"])
    # "blade ii"   → NE matche PAS "blade 1998…"    (["blade"] trop court)
    return entry_sig[:len(title_sig)] == title_sig


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


def _video_files_in(directory: str, max_depth: int = 3) -> List[str]:
    """Liste les fichiers vidéo dans un dossier (profondeur limitée)."""
    base_depth = directory.rstrip(os.sep).count(os.sep)
    result = []
    for root, dirs, files in os.walk(directory):
        if root.count(os.sep) - base_depth >= max_depth:
            dirs.clear()
            continue
        for f in files:
            if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                result.append(os.path.join(root, f))
    return result


def format_size(bytes_: int) -> str:
    if bytes_ >= 1_073_741_824:
        return f"{bytes_ / 1_073_741_824:.2f} Go"
    if bytes_ >= 1_048_576:
        return f"{bytes_ / 1_048_576:.1f} Mo"
    return f"{bytes_ / 1024:.1f} Ko"


# ── Scan intelligent ──────────────────────────────────────────────────────────

def scan_copies_smart(title: str, known_path: str, extra_paths: List[str]) -> Dict:
    """
    Scan non-destructif : trouve toutes les copies dans extra_paths.

    Stratégie par priorité :
      1. Inode  — hardlinks sur le même filesystem (identique à 100%)
      2. Hash   — fichiers identiques cross-filesystem (SHA-256 64Ko)
      3. Titre  — correspondance floue sur le nom (fallback)
    """
    known_real   = ""
    known_inode  = None
    known_hash   = ""

    if known_path and os.path.isfile(known_path):
        known_real  = os.path.realpath(known_path)
        known_inode = _inode_key(known_path)
        known_hash  = _file_hash(known_path)
        strategy    = "inode+hash+titre"
    else:
        strategy = "titre"

    found: List[Dict] = []

    for base in extra_paths:
        base = base.strip()
        if not base or not os.path.isdir(base):
            logger.warning("[Scan] Chemin ignoré (introuvable) : %s", base)
            continue

        try:
            for entry in os.scandir(base):
                # Exclure le chemin géré par Radarr/Sonarr
                try:
                    entry_real = os.path.realpath(entry.path)
                    if known_real and entry_real in (known_real, os.path.dirname(known_real)):
                        continue
                except Exception:
                    pass

                match_method: Optional[str] = None

                if entry.is_file():
                    ext = Path(entry.name).suffix.lower()
                    if known_inode and _inode_key(entry.path) == known_inode:
                        match_method = "inode"
                    elif known_hash and ext in VIDEO_EXTENSIONS:
                        if _file_hash(entry.path) == known_hash:
                            match_method = "hash"
                    elif ext in VIDEO_EXTENSIONS and _matches(entry.name, title):
                        # Titre uniquement sur fichiers vidéo — les images compagnes
                        # sont supprimées automatiquement par _delete_companions()
                        match_method = "titre"

                elif entry.is_dir():
                    if known_inode or known_hash:
                        for vf in _video_files_in(entry.path):
                            if known_inode and _inode_key(vf) == known_inode:
                                match_method = "inode"
                                break
                            if known_hash and _file_hash(vf) == known_hash:
                                match_method = "hash"
                                break
                    if match_method is None and _matches(entry.name, title):
                        match_method = "titre"

                if match_method:
                    size_bytes, file_count = _calc_size(entry.path)
                    found.append({
                        "path":         entry.path,
                        "is_dir":       entry.is_dir(),
                        "size_bytes":   size_bytes,
                        "size_human":   format_size(size_bytes),
                        "file_count":   file_count,
                        "match_method": match_method,
                        "is_hardlink":  match_method == "inode",
                    })
                    logger.info("[Scan] Copie trouvée (%s) : %s", match_method, entry.path)

        except PermissionError as e:
            logger.warning("[Scan] Accès refusé à %s : %s", base, e)

    total = sum(e["size_bytes"] for e in found)
    return {
        "title":            title,
        "known_path":       known_path,
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
    """Supprime les entrées identifiées par scan_copies_smart."""
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


def run_cleanup(title: str, file_path: str, extra_paths: List[str]) -> Dict:
    """Point d'entrée appelé depuis pipeline.py après suppression Radarr/Sonarr."""
    if not extra_paths:
        return {"skipped": True, "copies_found": 0}
    scan = scan_copies_smart(title, file_path, extra_paths)
    return run_cleanup_from_scan(scan)
