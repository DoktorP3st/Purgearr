import requests
from typing import Any, Dict, Optional


class BaseAPIClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": api_key})

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{endpoint}"

    def get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        r = self.session.get(self._url(endpoint), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        r = self.session.post(self._url(endpoint), json=data, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    def put(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        r = self.session.put(self._url(endpoint), json=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def delete(self, endpoint: str, params: Optional[Dict] = None) -> bool:
        r = self.session.delete(self._url(endpoint), params=params, timeout=30)
        r.raise_for_status()
        return True

    def ping(self) -> bool:
        try:
            self.get("/api/v3/system/status")
            return True
        except Exception:
            return False
