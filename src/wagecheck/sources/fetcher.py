"""A polite HTTP fetcher that obeys robots.txt.

Every automated download goes through here. If a site's robots.txt disallows a URL, the
fetcher does not download it; it returns `NeedsUser` with the link, so a person can open it
in their browser and upload the file. That keeps WageCheck a well-behaved client of
government sites, whatever their rules are on the day it runs.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

DEFAULT_UA = "WageCheck/0.1 (+https://github.com/santhosh5113/wagecheck)"


@dataclass(frozen=True)
class Fetched:
    url: str
    path: Path
    sha256: str
    retrieved_at: str
    from_cache: bool


@dataclass(frozen=True)
class NeedsUser:
    """Automated download isn't allowed; a person has to fetch this file."""

    url: str
    reason: str
    instructions: str = "Open the link in your browser, save the file, then upload it to WageCheck."


class PoliteFetcher:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str = DEFAULT_UA,
        min_interval_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self._http = httpx.Client(headers={"User-Agent": user_agent}, transport=transport,
                                  timeout=30.0, follow_redirects=True)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._clock, self._sleep = clock, sleep

    # ---- robots.txt ----

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """Parsed robots for the host, or None meaning 'disallow everything' (fail closed)."""
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = RobotFileParser()
        try:
            self._throttle(p.netloc)
            r = self._http.get(f"{origin}/robots.txt")
            if r.status_code in (401, 403):
                rp.parse(["User-agent: *", "Disallow: /"])
            elif 400 <= r.status_code < 500:
                rp.parse([])  # no robots.txt: crawling allowed by convention
            elif r.status_code >= 500:
                self._robots[origin] = None
                return None
            else:
                rp.parse(r.text.splitlines())
        except httpx.HTTPError:
            self._robots[origin] = None
            return None
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is not None and rp.can_fetch(self.user_agent, url)

    # ---- fetching ----

    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        suffix = Path(urlparse(url).path).suffix or ".bin"
        return self.cache_dir / f"{key}{suffix}", self.cache_dir / f"{key}.json"

    def fetch(self, url: str) -> Fetched | NeedsUser:
        if not self.allowed(url):
            rp = self._robots.get(f"{urlparse(url).scheme}://{urlparse(url).netloc}")
            why = ("robots.txt could not be read, so automated download is not attempted"
                   if rp is None else "the site's robots.txt disallows automated download of this path")
            return NeedsUser(url=url, reason=why)

        body_path, meta_path = self._cache_paths(url)
        headers = {}
        meta = json.loads(meta_path.read_text()) if meta_path.exists() and body_path.exists() else None
        if meta and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta and meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

        self._throttle(urlparse(url).netloc)
        r = self._http.get(url, headers=headers)
        if r.status_code == 304 and meta:
            return Fetched(url, body_path, meta["sha256"], meta["retrieved_at"], from_cache=True)
        r.raise_for_status()
        body_path.write_bytes(r.content)
        meta = {
            "url": url,
            "sha256": hashlib.sha256(r.content).hexdigest(),
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        return Fetched(url, body_path, meta["sha256"], meta["retrieved_at"], from_cache=False)

    def close(self) -> None:
        self._http.close()
