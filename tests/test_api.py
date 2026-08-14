"""The API client: pagination, rate limiting, retries, and typed failures.

No network. A fake session serves canned responses shaped like Matter's
documented payloads, and the clock and sleeper are injected so rate-limit
behaviour is asserted rather than waited for.
"""

import json

import pytest
import requests
from conftest import make_item

from matter.api import BASE_URL, MatterClient, SlidingWindow
from matter.errors import MatterAPIError, MatterAuthError, MatterForbiddenError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def build_client(responses, **kwargs):
    clock = FakeClock()
    client = MatterClient(
        "mat_test", session=FakeSession(responses),
        clock=clock, sleeper=clock.sleep, **kwargs,
    )
    return client, clock


def page(results, has_more=False, next_cursor=None):
    return FakeResponse(200, {
        "object": "list", "results": results,
        "has_more": has_more, "next_cursor": next_cursor,
    })


# ---- auth and errors ------------------------------------------------------

def test_bearer_header_is_set():
    client, _ = build_client([FakeResponse(200, {"object": "account"})])
    assert client._session.headers["Authorization"] == "Bearer mat_test"


def test_401_explains_the_one_active_token_rule():
    client, _ = build_client([FakeResponse(401, {"error": {"code": "authentication_required",
                                                           "message": "A valid API token is required."}})])
    with pytest.raises(MatterAuthError) as excinfo:
        client.me()
    message = str(excinfo.value)
    assert "one active token" in message
    assert "~/.secrets/matter.token" in message


def test_401_is_not_retried():
    """Retrying a rejected credential just burns the rate limit."""
    client, _ = build_client([FakeResponse(401, {"error": {}})])
    with pytest.raises(MatterAuthError):
        client.me()
    assert len(client._session.calls) == 1


def test_403_points_at_the_pro_subscription():
    client, _ = build_client([FakeResponse(403, {"error": {"code": "forbidden", "message": "nope"}})])
    with pytest.raises(MatterForbiddenError, match="Matter Pro"):
        client.me()


def test_error_codes_are_not_matched_as_strings():
    """The live API returns `authentication_required` where the docs promise
    `unauthorized`, so the client must branch on HTTP status alone."""
    client, _ = build_client([FakeResponse(401, {"error": {"code": "something_new_entirely"}})])
    with pytest.raises(MatterAuthError):
        client.me()


def test_html_error_body_does_not_crash_the_client():
    client, _ = build_client([FakeResponse(502, text="<html>bad gateway</html>", payload=None)] * 7)
    with pytest.raises(MatterAPIError):
        client.me()


def test_200_with_a_non_json_body_is_an_error_not_a_silent_empty_sync():
    client, _ = build_client([FakeResponse(200, payload=None, text="not json")])
    with pytest.raises(MatterAPIError, match="not JSON"):
        client.me()


# ---- retries --------------------------------------------------------------

def test_500_is_retried_then_succeeds():
    client, clock = build_client([
        FakeResponse(500, {"error": {"message": "boom"}}),
        FakeResponse(200, {"object": "account", "name": "Adam"}),
    ])
    assert client.me()["name"] == "Adam"
    assert len(clock.slept) == 1


def test_retries_are_bounded():
    client, _ = build_client([FakeResponse(500, {"error": {}})] * 10, max_retries=3)
    with pytest.raises(MatterAPIError, match="failed after"):
        client.me()


def test_connection_errors_are_retried():
    client, _ = build_client([
        requests.ConnectionError("network down"),
        FakeResponse(200, {"object": "account"}),
    ])
    assert client.me()["object"] == "account"


def test_429_honours_retry_after():
    client, clock = build_client([
        FakeResponse(429, {"error": {}}, headers={"Retry-After": "17"}),
        FakeResponse(200, {"object": "account"}),
    ])
    client.me()
    assert 17 in clock.slept


def test_429_without_retry_after_still_backs_off():
    client, clock = build_client([
        FakeResponse(429, {"error": {}}),
        FakeResponse(200, {"object": "account"}),
    ])
    client.me()
    assert clock.slept and clock.slept[0] > 0


# ---- pagination -----------------------------------------------------------

def test_cursor_pagination_walks_every_page():
    client, _ = build_client([
        page([make_item(item_id="itm_1")], has_more=True, next_cursor="cur_2"),
        page([make_item(item_id="itm_2")], has_more=True, next_cursor="cur_3"),
        page([make_item(item_id="itm_3")]),
    ])
    ids = [item["id"] for item in client.iter_items()]
    assert ids == ["itm_1", "itm_2", "itm_3"]
    assert client._session.calls[1]["params"]["cursor"] == "cur_2"
    assert "cursor" not in client._session.calls[0]["params"]


def test_updated_since_is_passed_through_for_incremental_sync():
    client, _ = build_client([page([])])
    list(client.iter_items(status="archive,queue", updated_since="2026-08-10T04:45:00Z"))
    params = client._session.calls[0]["params"]
    assert params["updated_since"] == "2026-08-10T04:45:00Z"
    assert params["status"] == "archive,queue"
    assert params["order"] == "updated"


def test_page_size_is_capped_at_the_documented_maximum():
    client, _ = build_client([page([])])
    list(client.iter_items(page_size=5000))
    assert client._session.calls[0]["params"]["limit"] == 100


def test_a_repeating_cursor_raises_instead_of_looping_forever():
    client, _ = build_client([
        page([make_item()], has_more=True, next_cursor="same"),
        page([make_item()], has_more=True, next_cursor="same"),
    ])
    with pytest.raises(MatterAPIError, match="repeated pagination cursor"):
        list(client.iter_items())


def test_has_more_without_a_cursor_stops_rather_than_refetching_page_one():
    client, _ = build_client([page([make_item()], has_more=True, next_cursor=None)])
    assert len(list(client.iter_items())) == 1


def test_annotations_paginate_the_same_way():
    client, _ = build_client([
        page([{"id": "ann_1", "text": "a"}], has_more=True, next_cursor="c2"),
        page([{"id": "ann_2", "text": "b"}]),
    ])
    assert [a["id"] for a in client.iter_annotations("itm_1")] == ["ann_1", "ann_2"]
    assert "/items/itm_1/annotations" in client._session.calls[0]["url"]


def test_markdown_is_requested_via_the_include_parameter():
    client, _ = build_client([FakeResponse(200, make_item(markdown="# x"))])
    client.get_item("itm_1", include_markdown=True)
    assert client._session.calls[0]["params"] == {"include": "markdown"}
    assert client._session.calls[0]["url"] == f"{BASE_URL}/items/itm_1"


# ---- rate limiting --------------------------------------------------------

def test_sliding_window_blocks_once_the_limit_is_reached():
    clock = FakeClock()
    window = SlidingWindow(3, 60.0, clock=clock, sleeper=clock.sleep)
    for _ in range(3):
        assert window.acquire() == 0.0
    assert window.acquire() > 0, "the fourth call in the window has to wait"


def test_sliding_window_lets_through_again_after_the_period():
    clock = FakeClock()
    window = SlidingWindow(2, 60.0, clock=clock, sleeper=clock.sleep)
    window.acquire()
    window.acquire()
    clock.now += 61
    assert window.acquire() == 0.0


def test_markdown_requests_are_limited_more_tightly_than_plain_reads():
    """20/min for markdown vs 120/min for reads; the tighter one has to bind."""
    client, clock = build_client(
        [FakeResponse(200, make_item(markdown="# x")) for _ in range(4)],
        markdown_per_min=3, read_per_min=120, burst_per_sec=100,
    )
    for _ in range(4):
        client.get_item("itm_1", include_markdown=True)
    assert clock.slept, "the fourth markdown fetch in a minute must wait"


def test_plain_reads_are_not_charged_against_the_markdown_budget():
    client, clock = build_client(
        [page([]) for _ in range(5)], markdown_per_min=1, read_per_min=120, burst_per_sec=100,
    )
    for _ in range(5):
        list(client.iter_items())
    assert clock.slept == []


def test_burst_ceiling_applies_across_all_request_kinds():
    client, clock = build_client(
        [page([]) for _ in range(4)], read_per_min=1000, markdown_per_min=1000, burst_per_sec=2,
    )
    for _ in range(4):
        list(client.iter_items())
    assert clock.slept, "more than 2 requests in one second must be spaced out"


def test_account_rate_limits_tighten_but_never_loosen_ours():
    client, _ = build_client([], read_per_min=120, markdown_per_min=20)
    applied = client.adopt_account_rate_limits({"rate_limit": {"read": 60, "markdown": 500}})
    assert applied == {"read": 60}
    assert client._read.limit == 60
    assert client._markdown.limit == 20, "a higher advertised ceiling is not adopted"
