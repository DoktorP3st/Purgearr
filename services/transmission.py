from typing import Dict, List, Optional
import requests

TORRENT_FIELDS = ["id", "name", "status", "files", "downloadDir", "percentDone", "isFinished", "hashString"]

TORRENT_STATS_FIELDS = [
    "id", "name", "status", "files", "downloadDir", "percentDone", "isFinished", "hashString",
    "uploadRatio", "rateUpload", "uploadedEver", "peersGettingFromUs", "sizeWhenDone",
]

# status codes de Transmission
STATUS_STOPPED = 0
STATUS_SEEDING = 6


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
        name_lower = name.lower()
        for torrent in self.get_all_torrents():
            if name_lower in torrent.get("name", "").lower():
                return torrent
        return None

    def find_by_hash(self, hash_string: str) -> Optional[Dict]:
        for torrent in self.get_all_torrents():
            if torrent.get("hashString", "").lower() == hash_string.lower():
                return torrent
        return None

    def find_by_path(self, file_path: str) -> Optional[Dict]:
        """Cherche un torrent dont le chemin de téléchargement correspond."""
        for torrent in self.get_all_torrents():
            download_dir = torrent.get("downloadDir", "")
            torrent_name = torrent.get("name", "")
            if file_path in f"{download_dir}/{torrent_name}" or torrent_name in file_path:
                return torrent
        return None

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
