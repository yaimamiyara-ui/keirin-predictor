from datetime import datetime, timezone
import email.utils
import time
import logging
from typing import Any, Optional, Set
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)


class PoliteError(Exception):
    """Custom exception for PoliteClient HTTP/network errors."""
    pass


class PoliteRateLimitError(PoliteError):
    """Raised when HTTP 429 Retry-After exceeds remaining time or retries."""
    pass


def parse_retry_after(header_val: Optional[str]) -> Optional[float]:
    if not header_val:
        return None
    s = str(header_val).strip()
    if not s:
        return None
    try:
        return max(0.0, float(s))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class PoliteClient:
    USER_AGENT = "keirin-predictor/0.1 (personal research; non-commercial)"
    MAX_RETRY_AFTER = 60.0

    def __init__(
        self,
        base_url: str = "",
        min_interval: float = 3.0,
        timeout: float = 20.0,
        deadline: Optional[float] = None,
        max_retry_after: float = MAX_RETRY_AFTER,
    ):
        self.base_url = base_url.rstrip("/")
        self.min_interval = max(3.0, float(min_interval))
        self.timeout = float(timeout)
        self.deadline = deadline
        self.max_retry_after = float(max_retry_after)
        self.rate_limited: bool = False
        self.rate_limited_hosts: Set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.last_request_time: float = 0.0
        self.request_count: int = 0

    def _wait_interval(self, deadline: Optional[float] = None) -> None:
        eff_deadline = deadline if deadline is not None else self.deadline
        if eff_deadline is not None and time.monotonic() >= eff_deadline:
            raise PoliteError("Collection deadline exceeded before request")
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            wait_sec = self.min_interval - elapsed
            if eff_deadline is not None and time.monotonic() + wait_sec > eff_deadline:
                raise PoliteError("Collection deadline exceeded while waiting polite interval")
            time.sleep(wait_sec)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        deadline: Optional[float] = None,
    ) -> requests.Response:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

        eff_deadline = deadline if deadline is not None else self.deadline
        host = urlparse(url).netloc
        if (host and host in self.rate_limited_hosts) or (not host and self.rate_limited):
            raise PoliteRateLimitError(f"Source {host or url} is skipped due to earlier 429 rate limit")

        max_retries = 3

        for attempt in range(max_retries + 1):
            self._wait_interval(deadline=eff_deadline)
            self.last_request_time = time.time()
            self.request_count += 1

            try:
                response = self.session.request(method, url, params=params, timeout=self.timeout)
                status_code = response.status_code

                if status_code == 200:
                    return response
                elif 400 <= status_code < 500 and status_code != 429:
                    raise PoliteError(f"HTTP {status_code} Client Error for URL: {url}")
                elif status_code == 429:
                    headers = getattr(response, "headers", None) or {}
                    ra_raw = headers.get("Retry-After") or headers.get("retry-after")
                    ra_sec = parse_retry_after(ra_raw)
                    if ra_sec is not None:
                        if eff_deadline is not None and time.monotonic() + ra_sec > eff_deadline:
                            self.rate_limited = True
                            if host:
                                self.rate_limited_hosts.add(host)
                            raise PoliteRateLimitError(
                                f"HTTP 429 Retry-After ({ra_sec}s) exceeds remaining time for URL: {url}"
                            )
                        sleep_time = min(ra_sec, self.max_retry_after)
                    else:
                        sleep_time = float(2 ** attempt)

                    if eff_deadline is not None and time.monotonic() + sleep_time > eff_deadline:
                        self.rate_limited = True
                        if host:
                            self.rate_limited_hosts.add(host)
                        raise PoliteRateLimitError(
                            f"HTTP 429 retry wait ({sleep_time}s) exceeds remaining time for URL: {url}"
                        )

                    if attempt < max_retries:
                        logger.warning(
                            f"HTTP 429. Retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(sleep_time)
                        continue
                    else:
                        self.rate_limited = True
                        if host:
                            self.rate_limited_hosts.add(host)
                        raise PoliteRateLimitError(
                            f"HTTP 429 Server Error after {max_retries} retries for URL: {url}"
                        )
                elif 500 <= status_code < 600:
                    if attempt < max_retries:
                        sleep_time = 2 ** attempt
                        if eff_deadline is not None and time.monotonic() + sleep_time > eff_deadline:
                            raise PoliteError(
                                f"HTTP {status_code} retry wait ({sleep_time}s) exceeds deadline for URL: {url}"
                            )
                        logger.warning(
                            f"HTTP {status_code}. Retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise PoliteError(f"HTTP {status_code} Server Error after {max_retries} retries for URL: {url}")
                else:
                    response.raise_for_status()
                    return response

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < max_retries:
                    sleep_time = 2 ** attempt
                    if eff_deadline is not None and time.monotonic() + sleep_time > eff_deadline:
                        raise PoliteError(
                            f"Network retry wait ({sleep_time}s) exceeds deadline for URL: {url}"
                        ) from e
                    logger.warning(
                        f"Connection error: {e}. Retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    raise PoliteError(f"Network error after {max_retries} retries for URL: {url}") from e

        raise PoliteError(f"Failed to fetch {url}")

    def get_html(self, path: str, params: Optional[dict] = None, deadline: Optional[float] = None) -> str:
        res = self._request_with_retry("GET", path, params=params, deadline=deadline)
        return res.text

    def get_json(self, path: str, params: Optional[dict] = None, deadline: Optional[float] = None) -> Any:
        res = self._request_with_retry("GET", path, params=params, deadline=deadline)
        try:
            return res.json()
        except Exception as e:
            raise PoliteError(f"Invalid JSON response from {path}") from e
