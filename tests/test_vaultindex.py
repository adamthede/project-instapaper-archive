"""Source selection for the cross-era dedupe index.

The thing under test is which of the three sources a given run reaches, and
whether it says so honestly. That mattered enough to cost the nightly ~50
minutes a night: the launchd interpreter has no pyarrow, so every run fell past
the Parquet index and walked 18,491 vault files over SMB instead.

Nothing here touches the real vault or the real Parquet index. The subprocess
tests run a stub interpreter -- a small Python script standing in for the venv
-- so the failure modes that matter at 04:45 (timeout, crash, garbage on
stdout) can be provoked deliberately rather than waited for.
"""

import json
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from matter.vaultindex import build_url_index  # noqa: E402

# build_index.py records file_path as an ABSOLUTE path, and the Matter
# exclusion below depends on that: it matches the fragment "/matter/", which a
# vault-relative "matter/x.md" would not contain. Real rows look like these.
VAULT_ROOT = "/Volumes/AST/Library/Articles/Instapaper-Matter-Archive"
PAIRS = [
    ["https://www.example.com/one?utm_source=news", f"{VAULT_ROOT}/2019-one.md"],
    ["https://example.com/two", f"{VAULT_ROOT}/2020-two.md"],
    ["", f"{VAULT_ROOT}/no-url.pdf.md"],              # ~10,560 real rows look like this
    ["https://example.com/mine", f"{VAULT_ROOT}/matter/2026-mine.md"],  # our own output
]


def write_vault_article(vault: Path, name: str, url: str) -> None:
    (vault / name).write_text(f"---\noriginal_url: \"{url}\"\ntitle: Whatever\n---\n\nBody.\n")


@pytest.fixture
def parquet(tmp_path):
    """A real Parquet index, or skip. Only the in-process test needs pyarrow."""
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa

    path = tmp_path / "archive_index.parquet"
    pq.write_table(
        pa.table({"url": [p[0] for p in PAIRS], "file_path": [p[1] for p in PAIRS]}),
        str(path),
    )
    return path


@pytest.fixture
def fake_parquet(tmp_path):
    """A file at the index's path. Its bytes never matter: the stub interpreters
    below answer from a canned payload, and the point of these tests is what the
    parent does with the subprocess's output, not what pyarrow does with bytes."""
    path = tmp_path / "archive_index.parquet"
    path.write_bytes(b"PAR1-not-really")
    return path


def make_stub_python(tmp_path, body: str, name: str = "stub-python") -> Path:
    """An executable standing in for the venv interpreter.

    It ignores the `-c` bootstrap it is handed and runs `body` instead, which is
    what lets a test produce a timeout or malformed stdout on demand.

    A /bin/sh trampoline rather than a `#!{sys.executable}` shebang: this repo
    lives under "Project - Instapaper Archive", and the kernel splits shebang
    lines on whitespace, so that interpreter path could never load. (The code
    under test passes the interpreter as argv[0] with no shell in between, which
    is why the spaces are only a problem for the stub.)
    """
    body_script = tmp_path / f"{name}-body.py"
    body_script.write_text(f"import json, sys, time\n{body}\n")

    path = tmp_path / name
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{body_script}"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- source 1: pyarrow in this process -------------------------------------

def test_pyarrow_in_process_reads_the_parquet_and_says_so(tmp_path, vault, parquet):
    index = build_url_index(vault, parquet_path=parquet, skip_dirs={"matter"},
                            write_cache=False)

    assert index.source == "parquet (2 urls)"
    assert index.degraded is False
    # utm_ stripped, www. dropped -- normalize_url ran in the parent.
    assert index.lookup("http://example.com/one") == f"{VAULT_ROOT}/2019-one.md"
    # Our own subdirectory is excluded, or an item would match itself.
    assert index.lookup("https://example.com/mine") is None


def test_in_process_path_never_walks_the_vault(tmp_path, vault, parquet, monkeypatch):
    """The fingerprint os.stats every file over SMB, so reaching Parquet has to
    mean not touching the vault at all -- not even for the cache key."""
    write_vault_article(vault, "elsewhere.md", "https://example.com/vault-only")
    monkeypatch.setattr("matter.vaultindex.os.walk",
                        lambda *a, **k: pytest.fail("the vault was walked"))

    index = build_url_index(vault, parquet_path=parquet, skip_dirs={"matter"},
                            write_cache=False)

    assert index.source == "parquet (2 urls)"


# --- source 2: the subprocess ----------------------------------------------

def test_subprocess_reads_the_parquet_when_pyarrow_is_missing_here(
    tmp_path, vault, fake_parquet, monkeypatch
):
    """The nightly's situation exactly: no pyarrow in this process, a venv that
    has it, and a vault that must not be walked."""
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "elsewhere.md", "https://example.com/vault-only")
    monkeypatch.setattr("matter.vaultindex.os.walk",
                        lambda *a, **k: pytest.fail("the vault was walked"))
    stub = make_stub_python(tmp_path, f"json.dump({json.dumps(PAIRS)}, sys.stdout)")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=stub)

    assert index.source == "parquet subprocess (2 urls)"
    assert index.degraded is False
    assert index.lookup("https://example.com/two") == f"{VAULT_ROOT}/2020-two.md"
    assert index.lookup("https://example.com/mine") is None


def test_both_parquet_paths_produce_the_same_index(tmp_path, vault, parquet):
    """The claim the fix rests on: swapping which process reads the file must
    not change what counts as a duplicate. Both routes share _pairs_to_urls, so
    normalization cannot drift between them."""
    in_process = build_url_index(vault, parquet_path=parquet, skip_dirs={"matter"},
                                 write_cache=False)

    pairs = json.dumps([[p[0], p[1]] for p in PAIRS])
    stub = make_stub_python(tmp_path, f"json.dump({pairs}, sys.stdout)")
    import matter.vaultindex as vaultindex
    real = vaultindex._read_parquet_pairs
    try:
        vaultindex._read_parquet_pairs = lambda path: None
        via_subprocess = build_url_index(vault, parquet_path=parquet, skip_dirs={"matter"},
                                         write_cache=False, helper_python=stub)
    finally:
        vaultindex._read_parquet_pairs = real

    assert via_subprocess.urls == in_process.urls
    assert via_subprocess.source != in_process.source  # ... and they still say which ran


# --- source 2's failure modes, all of which must degrade rather than raise --

@pytest.mark.parametrize("body, why", [
    ("time.sleep(30)", "timeout"),
    ("sys.exit(1)", "non-zero exit with no output"),
    # The case that actually exercises the returncode check. With `sys.exit(1)`
    # alone the payload is empty, so it is json.loads('') that rejects the run;
    # deleting the returncode guard entirely leaves that test green.
    ("json.dump([['https://example.com/one', '/v/a.md']], sys.stdout)\n"
     "sys.stdout.flush()\nsys.exit(3)", "valid JSON but a non-zero exit"),
    ("sys.stdout.write('Traceback: not json at all')", "malformed output"),
    ("json.dump({'url': 'https://example.com/one'}, sys.stdout)", "JSON of the wrong shape"),
    ("json.dump([['a', 'b'], ['just-one']], sys.stdout)", "a row of the wrong width"),
    # text=True decodes both streams, and UnicodeDecodeError is a ValueError --
    # caught by neither the TimeoutExpired nor the OSError handler, so it used
    # to escape build_url_index entirely.
    ("sys.stdout.buffer.write(b'[[\"https://e.com/a\", \"/v/a.md\"]]\\xff\\xfe')",
     "undecodable bytes on stdout"),
])
def test_subprocess_failures_fall_through_to_the_vault_scan(
    tmp_path, vault, fake_parquet, monkeypatch, body, why
):
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")
    stub = make_stub_python(tmp_path, body)

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=stub,
                            subprocess_timeout=1)

    assert index.source == "vault scan (1 urls from 1 files)", f"{why} should degrade, not raise"
    assert index.lookup("https://example.com/from-the-vault") == "fallback.md"


def test_undecodable_bytes_on_stderr_do_not_spoil_a_good_read(
    tmp_path, vault, fake_parquet, monkeypatch
):
    """The sharp version of the decoding hazard: the child SUCCEEDS, with
    complete valid JSON on stdout, and writes one non-UTF-8 byte to stderr.
    text=True decodes stderr too, so that used to raise UnicodeDecodeError out
    of build_url_index -- past main()'s `except MatterError`, skipping the
    heartbeat write and leaving yesterday's "ok" on disk. It must not merely
    degrade here; the read was good, so it must still be USED."""
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")
    stub = make_stub_python(tmp_path,
                            "json.dump([['https://example.com/one', '/v/a.md']], sys.stdout)\n"
                            "sys.stdout.flush()\n"
                            "sys.stderr.buffer.write(b'\\xff\\xfe')")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=stub)

    assert index.source == "parquet subprocess (1 urls)"
    assert index.lookup("https://example.com/one") == "/v/a.md"


def test_an_exception_reading_the_parquet_degrades_rather_than_escaping(
    tmp_path, vault, fake_parquet, monkeypatch
):
    """The guarantee under the specific handlers. pyarrow can raise from three
    unrelated branches of the exception hierarchy (ArrowNotImplementedError,
    ArrowMemoryError, ArrowTypeError), so the promise that the 04:45 run
    survives cannot rest on having enumerated them all."""
    def boom(path):
        raise MemoryError("pretend pyarrow could not allocate")
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", boom)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=None)

    assert index.source.startswith("vault scan")
    assert index.lookup("https://example.com/from-the-vault") == "fallback.md"


def test_a_corrupt_cache_file_degrades_rather_than_escaping(vault, tmp_path):
    """Valid JSON, wrong type. `cached.get(...)` on a list throws AttributeError,
    which is neither OSError nor ValueError. Only reachable on a night where
    both Parquet routes already failed -- the one that must not also crash."""
    write_vault_article(vault, "one.md", "https://example.com/one")
    cache = tmp_path / "cache.json"
    cache.write_text("[1, 2]")

    index = build_url_index(vault, parquet_path=None, cache_path=cache, write_cache=False)

    assert index.source == "vault scan (1 urls from 1 files)"


def test_a_partial_payload_is_rejected_whole(tmp_path, vault, fake_parquet, monkeypatch):
    """One bad row must not yield an index missing only that row: a
    silently-short index reads as a clean dedupe and writes duplicates into the
    vault. The vault scan is slow; a half-accepted index is wrong."""
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")
    payload = json.dumps([["https://example.com/one", "a.md"], ["https://example.com/two"]])
    stub = make_stub_python(tmp_path, f"json.dump({payload}, sys.stdout)")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=stub)

    assert index.lookup("https://example.com/one") is None
    assert index.source.startswith("vault scan")


def test_a_missing_interpreter_falls_through(tmp_path, vault, fake_parquet, monkeypatch):
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=tmp_path / "no-such-python")

    assert index.source.startswith("vault scan")


def test_no_helper_interpreter_falls_through(tmp_path, vault, fake_parquet, monkeypatch):
    """The pre-fix nightly, and still the behaviour when nothing supplies one."""
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")

    index = build_url_index(vault, parquet_path=fake_parquet, skip_dirs={"matter"},
                            write_cache=False, helper_python=None)

    assert index.source.startswith("vault scan")


def test_the_subprocess_is_not_tried_when_there_is_no_parquet(tmp_path, vault, monkeypatch):
    called = []
    monkeypatch.setattr("matter.vaultindex._from_parquet_subprocess",
                        lambda *a, **k: called.append(1))
    write_vault_article(vault, "fallback.md", "https://example.com/from-the-vault")

    index = build_url_index(vault, parquet_path=tmp_path / "absent.parquet",
                            write_cache=False, helper_python=Path(sys.executable))

    assert called == []
    assert index.source.startswith("vault scan")


# --- source 3, and the degraded report -------------------------------------

def test_vault_scan_still_reports_its_own_source_and_file_count(vault):
    write_vault_article(vault, "one.md", "https://example.com/one")
    write_vault_article(vault, "two.md", "https://example.com/two")

    index = build_url_index(vault, parquet_path=None, write_cache=False)

    assert index.source == "vault scan (2 urls from 2 files)"
    assert index.degraded is False


def test_no_source_at_all_is_reported_as_degraded(vault, tmp_path):
    index = build_url_index(vault, parquet_path=tmp_path / "absent.parquet",
                            allow_vault_scan=False, write_cache=False)

    assert index.source == "unavailable"
    assert index.degraded is True
    assert len(index) == 0


def test_a_dry_run_writes_no_cache_into_the_vault(vault):
    write_vault_article(vault, "one.md", "https://example.com/one")

    build_url_index(vault, parquet_path=None, write_cache=False)

    assert not (vault / ".matter_url_index.json").exists()


def test_the_subprocess_bootstrap_actually_imports_and_runs(tmp_path, vault, parquet):
    """The stubs above never exercise the `-c` string itself. This one does,
    with the current interpreter standing in for the venv, so a typo in the
    bootstrap cannot pass the rest of the suite."""
    import matter.vaultindex as vaultindex
    real = vaultindex._read_parquet_pairs
    try:
        vaultindex._read_parquet_pairs = lambda path: None
        index = build_url_index(vault, parquet_path=parquet, skip_dirs={"matter"},
                                write_cache=False, helper_python=Path(sys.executable))
    finally:
        vaultindex._read_parquet_pairs = real

    assert index.source == "parquet subprocess (2 urls)"
    assert index.lookup("https://example.com/two") == f"{VAULT_ROOT}/2020-two.md"


def test_dump_parquet_pairs_emits_raw_unnormalized_rows(parquet, capsys):
    """Normalization belongs to the parent. If the child ever starts doing it,
    the two Parquet paths can disagree while both reporting success."""
    from matter.vaultindex import dump_parquet_pairs

    dump_parquet_pairs(str(parquet))

    assert json.loads(capsys.readouterr().out) == PAIRS


def test_dump_parquet_pairs_exits_non_zero_when_it_cannot_read(tmp_path, monkeypatch):
    """What the parent keys its fallback off: an unreadable index must be a
    failed exit, not an empty JSON array that reads as "no duplicates"."""
    from matter.vaultindex import dump_parquet_pairs
    monkeypatch.setattr("matter.vaultindex._read_parquet_pairs", lambda path: None)

    with pytest.raises(SystemExit):
        dump_parquet_pairs(str(tmp_path / "whatever.parquet"))
