import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
import requests


def parse_tracker(comment: str) -> Tuple[str, str]:
    """Extrait (tracker_name, tracker_url) depuis un commentaire ou URL announce."""
    c = (comment or "").strip()
    if not c:
        return "", ""
    try:
        m = re.search(r'(https?://|udp://)\S+', c, re.IGNORECASE)
        if not m:
            return "", ""
        url = m.group(0).rstrip('.,;)')
        p = urlparse(url)
        domain = p.netloc.split(":")[0]
        if not domain:
            return "", ""
        if url.lower().startswith("udp://") or "/announce" in p.path.lower():
            # Sous-domaine privé tracker.* → pointer vers le site public www.*
            base_domain = re.sub(r'^tracker\.', '', domain)
            public_url = f"https://www.{base_domain}/" if base_domain != domain else f"https://{domain}/"
            return base_domain, public_url
        return domain, url
    except Exception:
        return "", ""


def get_tracker_info(torrent: dict) -> Tuple[str, str]:
    """Cherche le tracker dans le comment, puis fallback sur trackers[].announce."""
    comment = (torrent.get("comment") or "").strip()
    if comment:
        tname, turl = parse_tracker(comment)
        if tname:
            return tname, turl
    for tr_obj in (torrent.get("trackers") or []):
        announce = (tr_obj.get("announce") or "").strip()
        if announce:
            tname, turl = parse_tracker(announce)
            if tname:
                return tname, turl
    return "", ""

TORRENT_FIELDS = ["id", "name", "status", "files", "downloadDir", "percentDone", "isFinished", "hashString", "comment", "trackers"]

TORRENT_STATS_FIELDS = [
    "id", "name", "status", "files", "downloadDir", "percentDone", "isFinished", "hashString",
    "uploadRatio", "rateUpload", "uploadedEver", "peersGettingFromUs", "sizeWhenDone",
]

# status codes de Transmission
STATUS_STOPPED = 0
STATUS_SEEDING = 6


def _norm(s: str) -> str:
    """Normalise un nom de torrent : retire année, tags qualité, remplace séparateurs."""
    s = s.lower()
    s = re.sub(r"\(?\b(19|20)\d{2}\b\)?", "", s)
    s = re.sub(
        r"\b(bluray|bdrip|webrip|web-?dl|hdtv|4k|uhd|1080p|720p|480p"
        r"|x264|x265|hevc|aac|dts|hdr|remux|complete"
        r"|french|vff|vf|multi|truefrench|vostfr|mhd|custom)\b", "", s)
    s = re.sub(r"[._\-\[\]\(\)\s]+", " ", s)
    return s.strip()


class TransmissionClient:
    def __init__(self, host: str, port: int, username: Optional[str] = None, password: Optional[str] = None):
        self.url = f"http://{host}:{port}/transmission/rpc"
        self.auth = (username, password) if username else None
        self._session_id: Optional[str] = None

    def _request(self, method: str, arguments: Optional[Dict] = None) -> Dict:
        headers = {}
        if self._session_id:
            headers["X-Transmission-Session-Id"] = self._session_id

        payload = {"method": method, "arguments": arguments or {}}
        r = requests.post(self.url, json=payload, headers=headers, auth=self.auth, timeout=30)

        # Transmission renvoie 409 avec le session ID quand il faut s'authentifier
        if r.status_code == 409:
            self._session_id = r.headers.get("X-Transmission-Session-Id")
            headers["X-Transmission-Session-Id"] = self._session_id
            r = requests.post(self.url, json=payload, headers=headers, auth=self.auth, timeout=30)

        r.raise_for_status()
        result = r.json()

        if result.get("result") != "success":
            raise RuntimeError(f"Transmission RPC error: {result.get('result')}")

        return result.get("arguments", {})

    def get_all_torrents(self) -> List[Dict]:
        result = self._request("torrent-get", {"fields": TORRENT_FIELDS})
        return result.get("torrents", [])

    def get_all_torrents_with_stats(self) -> List[Dict]:
        result = self._request("torrent-get", {"fields": TORRENT_STATS_FIELDS})
        return result.get("torrents", [])

    def find_by_name(self, name: str) -> Optional[Dict]:
        """Recherche par nom normalisé (insensible aux points/tirets/année/qualité)."""
        needle = _norm(name)
        if not needle:
            return None
        for t in self.get_all_torrents():
            if needle in _norm(t.get("name", "")):
                return t
        return None

    def find_all_by_path_or_name(self, file_path: str = "", name: str = "") -> List[Dict]:
        """Retourne TOUS les torrents correspondant au chemin ou au nom (multi-tracker)."""
        needle = _norm(name) if name else ""
        matches: List[Dict] = []
        for t in self.get_all_torrents():
            matched = False
            if file_path:
                dl = t.get("downloadDir", "").rstrip("/")
                t_name = t.get("name", "")
                if file_path in f"{dl}/{t_name}" or t_name in file_path:
                    matched = True
                if not matched:
                    for f in t.get("files", []):
                        if file_path.endswith(f.get("name", "")) or f.get("name", "") in file_path:
                            matched = True
                            break
            if not matched and needle and needle in _norm(t.get("name", "")):
                matched = True
            if matched:
                matches.append(t)
        return matches

    def find_by_hash(self, hash_string: str) -> Optional[Dict]:
        for torrent in self.get_all_torrents():
            if torrent.get("hashString", "").lower() == hash_string.lower():
                return torrent
        return None

    def find_by_path(self, file_path: str) -> Optional[Dict]:
        """Cherche par downloadDir+name, par nom dans le chemin, ou dans les fichiers du torrent."""
        for t in self.get_all_torrents():
            dl   = t.get("downloadDir", "").rstrip("/")
            name = t.get("name", "")
            if file_path in f"{dl}/{name}" or name in file_path:
                return t
            # Vérifie chaque fichier du torrent
            for f in t.get("files", []):
                if file_path.endswith(f.get("name", "")) or f.get("name", "") in file_path:
                    return t
        return None

    def find_orphaned_torrents(self) -> List[Dict]:
        """Retourne les torrents dont le chemin n'existe plus sur le disque."""
        orphans = []
        for t in self.get_all_torrents():
            dl   = t.get("downloadDir", "").rstrip("/")
            name = t.get("name", "")
            path = f"{dl}/{name}"
            if not os.path.exists(path):
                orphans.append({**t, "expected_path": path})
        return orphans

    def stop(self, torrent_id: int) -> bool:
        self._request("torrent-stop", {"ids": [torrent_id]})
        return True

    def remove(self, torrent_id: int, delete_data: bool = True) -> bool:
        self._request("torrent-remove", {"ids": [torrent_id], "delete-local-data": delete_data})
        return True

    def stop_and_remove(self, torrent_id: int, delete_data: bool = True) -> bool:
        """Stop le seeding puis supprime le torrent (et les données si delete_data=True)."""
        self.stop(torrent_id)
        return self.remove(torrent_id, delete_data)

    def get_session_stats(self) -> Dict:
        return self._request("session-stats")

    def ping(self) -> bool:
        try:
            self._request("session-get")
            return True
        except Exception:
            return False
