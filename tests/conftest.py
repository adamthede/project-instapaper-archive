"""Shared fixtures for the Matter sync tests.

Everything here is synthetic. None of these tests touch the real vault, the real
credential at ~/.secrets/matter.token, or the network -- the API client is
driven by a fake session that serves canned payloads shaped like Matter's
OpenAPI schema.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, tmp_path):
    """Point the credential loader at a temp path for every test.

    Autouse and unconditional: a test that accidentally read Adam's real token
    would pass on his machine and fail everywhere else, and worse, a test that
    accidentally *wrote* one would be a genuine hazard.
    """
    monkeypatch.setenv("MATTER_TOKEN_FILE", str(tmp_path / "unset-matter.token"))
    monkeypatch.delenv("INSTAPAPER_VAULT_PATH", raising=False)


@pytest.fixture
def vault(tmp_path):
    """An existing vault directory, as the sync requires."""
    path = tmp_path / "vault"
    path.mkdir()
    return path


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "matter.token"
    path.write_text("mat_testtoken0123456789abcdef")
    path.chmod(0o600)
    return path


def make_item(
    item_id="itm_abc123",
    title="How to Do Great Work",
    url="https://paulgraham.com/greatwork.html",
    status="archive",
    updated_at="2026-03-30T19:15:00Z",
    **overrides,
):
    """An item shaped like GET /v1/items returns.

    Field names and nesting follow components.schemas.Item in
    https://docs.getmatter.com/openapi.yaml -- notably `author` is an object,
    `url` is top level, and there is no created_at.
    """
    item = {
        "object": "item",
        "id": item_id,
        "title": title,
        "url": url,
        "site_name": "paulgraham.com",
        "author": {"object": "author", "id": "aut_p4w7q", "name": "Paul Graham"},
        "status": status,
        "processing_status": "completed",
        "is_favorite": False,
        "content_type": "article",
        "word_count": 11842,
        "reading_progress": 0.35,
        "image_url": None,
        "excerpt": "Paul Graham explores what it takes to do great work.",
        "library_position": 58974321000,
        "inbox_position": None,
        "tags": [{"object": "tag", "id": "tag_n5j2x", "name": "essays"}],
        "updated_at": updated_at,
    }
    item.update(overrides)
    return item


def make_annotation(annotation_id="ann_m2k8v", item_id="itm_abc123",
                    text="The way to figure out what to work on is by working.",
                    note=None, created_at="2026-03-30T18:32:00Z"):
    return {
        "object": "annotation",
        "id": annotation_id,
        "item_id": item_id,
        "text": text,
        "note": note,
        "created_at": created_at,
        "updated_at": created_at,
    }


class FakeClient:
    """Stands in for MatterClient in sync tests.

    Records which items had their body or annotations fetched, so tests can
    assert on the rate-limit-saving behaviour (not re-downloading article text
    that is already on disk) rather than just on the files produced.
    """

    def __init__(self, items, annotations=None, markdown=None, account=None):
        self._items = list(items)
        self._annotations = annotations or {}
        self._markdown = markdown or {}
        self._account = account or {
            "object": "account", "id": "act_test", "name": "Adam Thede",
            "email": "athede@example.com", "created_at": "2020-01-01T00:00:00Z",
            "rate_limit": {"read": 120, "markdown": 20, "burst": 5},
        }
        self.request_count = 0
        self.throttled_seconds = 0.0
        self.detail_fetches = []
        self.annotation_fetches = []
        self.list_calls = []

    def me(self):
        self.request_count += 1
        return self._account

    def adopt_account_rate_limits(self, account):
        return {}

    def iter_items(self, *, status=None, updated_since=None, order="updated", page_size=100):
        self.list_calls.append({"status": status, "updated_since": updated_since, "order": order})
        self.request_count += 1
        for item in self._items:
            yield dict(item)

    def get_item(self, item_id, *, include_markdown=False):
        self.detail_fetches.append(item_id)
        self.request_count += 1
        item = next((dict(i) for i in self._items if i["id"] == item_id), None)
        if item is None:
            raise AssertionError(f"unexpected get_item for {item_id}")
        if include_markdown:
            item["markdown"] = self._markdown.get(item_id, f"# {item['title']}\n\nBody of {item_id}.")
        return item

    def iter_annotations(self, item_id, *, page_size=100):
        self.annotation_fetches.append(item_id)
        self.request_count += 1
        yield from (dict(a) for a in self._annotations.get(item_id, []))
