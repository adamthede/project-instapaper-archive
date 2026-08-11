"""A client for Matter's public API v1.

Everything here follows https://docs.getmatter.com/openapi.yaml (OpenAPI 3.1.0),
fetched and read directly rather than recalled -- see docs/MATTER_SYNC.md for
which claims are spec-verified and which are assumptions awaiting a real token.

The three endpoints this sync needs:

    GET /v1/me                          account + the account's own rate limits
    GET /v1/items                       the library, with `updated_since` delta
    GET /v1/items/{id}?include=markdown the article body
    GET /v1/items/{item_id}/annotations highlights and notes for one item

Rate limits are enforced on our side as well as Matter's. The docs warn that
clients ignoring the limits "may have their tokens temporarily suspended", and
since Matter allows exactly one active token per account, a suspension would
take out the Matter CLI too.
"""

import json
import random
import time
from collections import deque
from typing import Any, Callable, Iterator

import requests

from .errors import MatterAPIError, MatterAuthError, MatterForbiddenError

BASE_URL = "https://api.getmatter.com/public/v1"

# Documented ceilings (GET /v1/me returns the account's actual values, which we
# adopt at runtime when they are lower). Verified from the RateLimit schema.
DEFAULT_READ_PER_MIN = 120
DEFAULT_MARKDOWN_PER_MIN = 20
DEFAULT_BURST_PER_SEC = 5

MAX_PAGE_SIZE = 100
USER_AGENT = "article-archive-matter-sync/1.0 (+https://github.com/adamthede/project-instapaper-archive)"


class SlidingWindow:
    """Allows at most `limit` events per `period` seconds, blocking when full.

    A sliding window rather than a token bucket because Matter's limits are
    stated as "N per minute" and reset on a rolling basis; a bucket that refills
    smoothly would let us exceed the stated ceiling right after a quiet period.
    """

    def __init__(self, limit: int, period: float, *, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep):
        self.limit = max(1, int(limit))
        self.period = float(period)
        self._clock = clock
        self._sleeper = sleeper
        self._events: deque[float] = deque()

    def acquire(self) -> float:
        """Block until an event is permitted. Returns seconds actually waited."""
        waited = 0.0
        while True:
            now = self._clock()
            while self._events and now - self._events[0] >= self.period:
                self._events.popleft()
            if len(self._events) < self.limit:
                self._events.append(now)
                return waited
            sleep_for = self.period - (now - self._events[0])
            # Guard against a zero/negative sleep spinning the loop if the clock
            # is coarse.
            sleep_for = max(sleep_for, 0.001)
            self._sleeper(sleep_for)
            waited += sleep_for


class MatterClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        timeout: float = 30.0,
        read_per_min: int = DEFAULT_READ_PER_MIN,
        markdown_per_min: int = DEFAULT_MARKDOWN_PER_MIN,
        burst_per_sec: int = DEFAULT_BURST_PER_SEC,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleeper = sleeper
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        self._read = SlidingWindow(read_per_min, 60.0, clock=clock, sleeper=sleeper)
        self._markdown = SlidingWindow(markdown_per_min, 60.0, clock=clock, sleeper=sleeper)
        self._burst = SlidingWindow(burst_per_sec, 1.0, clock=clock, sleeper=sleeper)
        self.request_count = 0
        self.throttled_seconds = 0.0

    # ---- plumbing ---------------------------------------------------------

    def _wait_for_slot(self, *, markdown: bool) -> None:
        self.throttled_seconds += self._read.acquire()
        if markdown:
            self.throttled_seconds += self._markdown.acquire()
        self.throttled_seconds += self._burst.acquire()

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:400]
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return f"{error.get('code', '?')}: {error.get('message', '')}".strip()
        return json.dumps(payload)[:400]

    def get(self, path: str, params: dict[str, Any] | None = None, *, markdown: bool = False) -> dict:
        """GET one URL with rate limiting, retries, and typed failures.

        Note that we branch on HTTP status, never on the `error.code` string:
        the live API returns `authentication_required` for an unauthenticated
        call where the prose docs promise `unauthorized`, so those strings are
        demonstrably not a stable contract.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        attempt = 0
        last_detail = ""

        while True:
            attempt += 1
            self._wait_for_slot(markdown=markdown)
            self.request_count += 1
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
                if attempt > self.max_retries:
                    raise MatterAPIError(
                        f"GET {url} failed after {attempt - 1} retries -- {last_detail}"
                    ) from exc
                self._backoff(attempt)
                continue

            status = response.status_code

            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise MatterAPIError(
                        f"GET {url} returned HTTP 200 with a body that is not JSON: "
                        f"{(response.text or '')[:200]!r}"
                    ) from exc

            if status == 401:
                raise MatterAuthError(
                    f"Matter rejected the API token (HTTP 401: {self._error_message(response)}).\n"
                    "Matter permits one active token per account, so this usually means the "
                    "token was regenerated somewhere else (the web settings page, or "
                    "`matter login`). Generate a fresh one at "
                    "https://web.getmatter.com/settings and write it to ~/.secrets/matter.token."
                )

            if status == 403:
                raise MatterForbiddenError(
                    f"Matter refused the request (HTTP 403: {self._error_message(response)}).\n"
                    "The public API requires an active Matter Pro subscription; a valid token "
                    "on a non-Pro account is rejected exactly this way."
                )

            if status == 404:
                raise MatterAPIError(f"GET {url} returned HTTP 404: {self._error_message(response)}")

            if status == 429:
                # Matter's own limiter disagreed with ours. Honour Retry-After.
                wait = self._retry_after_seconds(response)
                if wait is None:
                    wait = min(60.0, 2 ** attempt)
                last_detail = f"HTTP 429: {self._error_message(response)}"
                if attempt > self.max_retries:
                    raise MatterAPIError(f"GET {url} still rate-limited after {attempt - 1} retries")
                self.throttled_seconds += wait
                self._sleeper(wait)
                continue

            if 500 <= status < 600:
                last_detail = f"HTTP {status}: {self._error_message(response)}"
                if attempt > self.max_retries:
                    raise MatterAPIError(f"GET {url} failed after {attempt - 1} retries -- {last_detail}")
                self._backoff(attempt)
                continue

            raise MatterAPIError(f"GET {url} returned HTTP {status}: {self._error_message(response)}")

    def _backoff(self, attempt: int) -> None:
        delay = min(60.0, (2 ** attempt) * 0.5)
        delay += random.uniform(0, delay * 0.1)  # jitter, so retries don't lock step
        self.throttled_seconds += delay
        self._sleeper(delay)

    def _paginate(self, path: str, params: dict[str, Any], *, max_pages: int = 10_000) -> Iterator[dict]:
        """Follow `next_cursor` until exhausted.

        Two guards, because an opaque cursor that never advances would otherwise
        spin forever against a rate-limited API: a hard page ceiling, and a
        repeat-cursor check.
        """
        params = dict(params)
        seen_cursors: set[str] = set()
        pages = 0

        while True:
            payload = self.get(path, params)
            results = payload.get("results") or []
            if not isinstance(results, list):
                raise MatterAPIError(f"GET {path} returned a non-list `results` field")
            yield from results

            pages += 1
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")
            if not cursor:
                # has_more was true but no cursor came back; nothing sane to do
                # except stop rather than refetch page one forever.
                return
            if cursor in seen_cursors:
                raise MatterAPIError(
                    f"GET {path} repeated pagination cursor {cursor!r}; stopping to avoid a loop"
                )
            if pages >= max_pages:
                raise MatterAPIError(f"GET {path} exceeded {max_pages} pages; stopping")
            seen_cursors.add(cursor)
            params["cursor"] = cursor

    # ---- endpoints --------------------------------------------------------

    def me(self) -> dict:
        """GET /v1/me -- the auth check, and the account's real rate limits."""
        return self.get("/me")

    def adopt_account_rate_limits(self, account: dict, *, clock=time.monotonic, sleeper=time.sleep) -> dict:
        """Tighten our limiters to the account's own values when they are lower.

        Never loosens them: if the account reports a higher ceiling than the
        documented default we keep the conservative one, because being wrong in
        that direction costs a token suspension.
        """
        limits = account.get("rate_limit") or {}
        applied = {}
        for key, window in (("read", "_read"), ("markdown", "_markdown"), ("burst", "_burst")):
            value = limits.get(key)
            if isinstance(value, int) and value > 0:
                current = getattr(self, window)
                if value < current.limit:
                    current.limit = value
                    applied[key] = value
        return applied

    def iter_items(
        self,
        *,
        status: str | None = None,
        updated_since: str | None = None,
        order: str = "updated",
        page_size: int = MAX_PAGE_SIZE,
    ) -> Iterator[dict]:
        """GET /v1/items, paginated.

        `updated_since` is the whole reason a nightly delta is cheap: an item's
        `updated_at` advances on any change to it or its associated data,
        including new annotations, so one filtered call finds everything that
        needs re-reading.
        """
        params: dict[str, Any] = {"limit": min(page_size, MAX_PAGE_SIZE), "order": order}
        if status:
            params["status"] = status
        if updated_since:
            params["updated_since"] = updated_since
        yield from self._paginate("/items", params)

    def get_item(self, item_id: str, *, include_markdown: bool = False) -> dict:
        params = {"include": "markdown"} if include_markdown else None
        return self.get(f"/items/{item_id}", params, markdown=include_markdown)

    def iter_annotations(self, item_id: str, *, page_size: int = MAX_PAGE_SIZE) -> Iterator[dict]:
        """GET /v1/items/{item_id}/annotations.

        There is no global annotations feed and no `updated_since` here, so
        highlights can only be fetched per item. That is affordable precisely
        because a new highlight bumps the parent item's `updated_at`, so the
        item delta already tells us which items to ask about.
        """
        yield from self._paginate(f"/items/{item_id}/annotations", {"limit": min(page_size, MAX_PAGE_SIZE)})
