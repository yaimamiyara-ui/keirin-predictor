import time
import logging
from typing import Any, Optional
import requests

logger = logging.getLogger(__name__)


class PoliteError(Exception):
    """Custom exception for PoliteClient HTTP/network errors."""
    pass


class PoliteClient:
    USER_AGENT = "keirin-predictor/0.1 (personal research; non-commercial)"

    def __init__(self, base_url: str = "", min_interval: float = 3.0, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.min_interval = max(3.0, float(min_interval))
        self.timeout = float(timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.last_request_time: float = 0.0
        self.request_count: int = 0

    def _wait_interval(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _request_with_retry(self, method: str, path: str, params: Optional[dict] = None) -> requests.Response:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

        max_retries = 3

        for attempt in range(max_retries + 1):
            self._wait_interval()
            self.last_request_time = time.time()
            self.request_count += 1

            try:
                response = self.session.request(method, url, params=params, timeout=self.timeout)
                status_code = response.status_code

                if status_code == 200:
                    return response
                elif 400 <= status_code < 500 and status_code != 429:
                    raise PoliteError(f"HTTP {status_code} Client Error for URL: {url}")
                elif status_code == 429 or 500 <= status_code < 600:
                    if attempt < max_retries:
                        sleep_time = 2 ** attempt
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
                    logger.warning(
                        f"Connection error: {e}. Retrying in {sleep_time}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    raise PoliteError(f"Network error after {max_retries} retries for URL: {url}") from e

        raise PoliteError(f"Failed to fetch {url}")

    def get_html(self, path: str, params: Optional[dict] = None) -> str:
        res = self._request_with_retry("GET", path, params=params)
        return res.text

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        res = self._request_with_retry("GET", path, params=params)
        try:
            return res.json()
        except Exception as e:
            raise PoliteError(f"Invalid JSON response from {path}") from e
