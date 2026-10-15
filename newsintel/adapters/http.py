"""Polite HTTP client: declared User-Agent, per-source rate limit, domain allow-list, no off-domain redirects."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx

from newsintel.core.config import settings


class PoliteClient:
    def __init__(self, user_agent: str, max_rps: float, allowed_domains: list[str], timeout_s: float = 20.0):
        contact = settings().contact_email
        self.user_agent = user_agent.replace("set NEWSINTEL_CONTACT_EMAIL", contact) if contact else user_agent
        self.min_interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self.allowed = {d.lower() for d in allowed_domains}
        self._last = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout_s,
            follow_redirects=False,
        )

    def _check_domain(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if self.allowed and host not in self.allowed:
            raise PermissionError(f"{host} is not in the source's allowed domains")

    def get(self, url: str) -> httpx.Response:
        self._check_domain(url)
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        resp = self._client.get(url)
        if resp.is_redirect:
            target = resp.headers.get("location", "")
            self._check_domain(httpx.URL(url).join(target).__str__())
            resp = self._client.get(str(httpx.URL(url).join(target)))
        resp.raise_for_status()
        return resp
