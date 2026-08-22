#!/usr/bin/env python3
"""Phase A step 3 — one local-LLM call per cluster to NAME and DEFINE it.

    .venv/bin/python scripts/vocab/name_clusters.py [--top-n 250]

The division of labour is the whole point and it is enforced here rather than
trusted: membership was decided by the embeddings in cluster.py and this step
cannot change it. The model is shown the members and is asked only for a
canonical label, a one-sentence definition, and a suggested axis. Nothing it
returns is allowed to add, drop or move a string — if it hallucinates a member
list, that output is simply not read.

It also proposes ``axis`` (topic = subject domain, concept = the specific idea
at stake). That is a SUGGESTION carried to the gate as evidence, not a
decision: the plan reserves the topic-vs-concept split for Adam, and the gate
page labels the column "proposed" for exactly that reason.

Resilience, because ~250 sequential 35B calls will not all succeed:

  * results append to a JSONL after EVERY call, so a kill costs one call;
  * resume keys on a hash of the member list, not the cluster id, so
    re-running cluster.py at a different threshold does not silently reuse
    names written for a differently-shaped cluster;
  * a failed or unparseable call falls back to the cluster's highest-coverage
    member string as the name, records the error, and the run continues. The
    gate then shows that row as needing a human name rather than losing it.

The one exception is ModelMismatch: if LM Studio served something other than
the pinned Qwen, every subsequent call would be wrong the same way, so the run
aborts rather than half-naming the vocabulary with two models' judgment.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vocab import common  # noqa: E402

MAX_MEMBERS_SHOWN = 40
NAMES_FILE = "cluster-names.jsonl"


def cluster_key(members):
    """Content hash of the member list — the resume key.

    Cluster ids are positional and shift the moment the threshold changes;
    the members are what the name actually describes.
    """
    joined = "\n".join(members).encode("utf-8")
    return hashlib.sha1(joined).hexdigest()[:16]


def build_prompt(cluster, examples):
    shown = cluster["members"][:MAX_MEMBERS_SHOWN]
    hidden = len(cluster["members"]) - len(shown)
    lines = "\n".join(f"  - {m}" for m in shown)
    if hidden > 0:
        lines += f"\n  - ...and {hidden} more variants"
    titles = "\n".join(f"  - {x['title']}" for x in examples) or "  (none)"
    return f"""You are building a controlled vocabulary (a subject-heading thesaurus) for a personal reading archive of 22 years of saved articles.

An automated clustering step has already grouped these free-text tags, which were extracted per-article by a language model, into one group. The grouping is FINAL — you are not being asked to change it.

Tag variants in this group ({cluster['size']} distinct strings, appearing across {cluster['articles']} articles):
{lines}

Three articles from this group:
{titles}

Give this group ONE canonical vocabulary entry. Reply with a single JSON object and nothing else:

{{"name": "...", "definition": "...", "axis": "topic"}}

  name       The canonical label. Title Case, a noun phrase, as short as is
             still precise. Prefer the plain established term over jargon.
  definition ONE sentence saying what an article tagged with this entry is
             about. Write it as a definition, not as a description of the
             group. Do not start with "This group" or "Articles about".
  axis       "topic" if this names a broad subject domain (the field an
             article is in, e.g. Economics, Public Health). "concept" if it
             names a specific idea, mechanism or phenomenon at stake within a
             field (e.g. Network Effects, Regulatory Capture)."""


def parse_reply(text):
    """The JSON object out of a chat reply, tolerating fences and prose."""
    s = str(text).strip()
    if "```" in s:
        parts = s.split("```")
        for part in parts[1:]:
            body = part.split("\n", 1)[-1] if part[:8].lower().startswith("json") else part
            if "{" in body:
                s = body
                break
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {s[:160]!r}")
    data = json.loads(s[start:end + 1])
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("reply had no name")
    axis = str(data.get("axis") or "").strip().lower()
    return {
        "name": name,
        "definition": " ".join(str(data.get("definition") or "").split()),
        "axis": axis if axis in ("topic", "concept") else "",
    }


def load_named(path):
    """Every name already written, keyed by member-list hash."""
    out = {}
    if not Path(path).exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line from a kill mid-write
            if rec.get("key"):
                out[rec["key"]] = rec
    return out


def append_record(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def fallback_record(cluster, key, error):
    """Never lose a cluster to a bad call — degrade to its own best member."""
    return {
        "key": key, "id": cluster["id"],
        "name": cluster["members"][0],
        "definition": "",
        "axis": "",
        "error": str(error)[:300],
        "named_by": "fallback",
    }


def run(clusters, inventory, out_path, progress=print, session=None):
    named = load_named(out_path)
    todo = [c for c in clusters if cluster_key(c["members"]) not in named]
    progress(f"{len(clusters)} clusters in scope; {len(clusters) - len(todo)} "
             f"already named; {len(todo)} to name")
    ok = failed = 0
    started = time.time()
    for i, cluster in enumerate(todo, 1):
        key = cluster_key(cluster["members"])
        examples = common.example_articles(inventory, cluster["members"])
        try:
            reply, _ = common.chat(build_prompt(cluster, examples),
                                   session=session)
            parsed = parse_reply(reply)
            record = {"key": key, "id": cluster["id"], **parsed,
                      "named_by": common.CHAT_MODEL}
            ok += 1
        except common.ModelMismatch:
            raise  # every later call would mismatch too
        except (requests.ConnectionError, requests.Timeout) as exc:
            progress(f"  LM Studio unreachable: {exc}\n  {ok} named and "
                     f"checkpointed — re-run to resume.")
            break
        except Exception as exc:  # noqa: BLE001 - bad JSON, empty reply, HTTP 5xx
            record = fallback_record(cluster, key, exc)
            failed += 1
            progress(f"  FALLBACK cluster {cluster['id']}: {exc}")
        append_record(out_path, record)
        if i % 10 == 0 or i == len(todo):
            rate = i / max(time.time() - started, 1e-9)
            progress(f"  {i}/{len(todo)}  {rate * 60:.1f}/min  "
                     f"eta {(len(todo) - i) / rate / 60:.1f}m")
    progress(f"named {ok}, fell back {failed}, in "
             f"{(time.time() - started) / 60:.1f} min")
    return ok, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(common.DEFAULT_DATA_DIR))
    ap.add_argument("--index", default=str(common.INDEX_PATH))
    ap.add_argument("--clusters", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--top-n", type=int, default=250,
                    help="name only the top-N clusters by article coverage")
    ap.add_argument("--min-articles", type=int, default=2,
                    help="skip clusters touching fewer articles than this")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    clusters_path = Path(args.clusters or data_dir / "clusters.json")
    out_path = Path(args.out or data_dir / NAMES_FILE)
    if not clusters_path.exists():
        raise SystemExit(f"no clusters at {clusters_path} — run cluster.py")

    payload = json.loads(clusters_path.read_text(encoding="utf-8"))
    clusters = [c for c in payload["clusters"]
                if c["articles"] >= args.min_articles][:args.top_n]
    rows = common.load_rows(args.index)
    inv = common.Inventory(rows)
    print(f"model {common.CHAT_MODEL} -> {out_path}")
    run(clusters, inv, out_path)


if __name__ == "__main__":
    main()
