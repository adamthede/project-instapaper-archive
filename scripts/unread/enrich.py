"""The unread enrichment: the base schema, plus the template the unread run adds.

The base schema is the portfolio standard and is not forked. The prompt in
`scripts/core/enrich_archive_gemini.py` already emits all of it including the
CONTENT_VALID guard, the local LM Studio variant shares it by import, and this
module reaches it through two keyword arguments rather than a copy.

On top of the base, three fields and one removal:

    WHY_SAVED              one sentence, from the article plus the saved date
    WHY_SAVED_CONFIDENCE   high, medium or low
    AGED_OUT               was this time bound, and has its moment passed

and the 10,000-character body cap comes off, so the model sees whole articles.

`why_saved` is the field most likely to produce confident fiction, so three
rules hold and all three are tested. It is permitted to return nothing, and an
empty answer is a result rather than a failure. It carries a confidence flag
the model sets, defaulting to low - the value that excludes a row from every
aggregate - whenever the model is silent or answers something the prompt never
offered. And the record labels it as inference in itself, so no surface can
show the sentence without the label being adjacent to it.

Two fields the model is never asked for. `abandonment` is arithmetic on read
progress and lives in `derive`; `topic_drift` is a distance from the read
corpus's distribution for the same saved year and cannot be known while
enriching one article, so it is computed in `analysis`.
"""
import datetime as dt
import json
import logging
import os
import re
import sys
from pathlib import Path

from . import derive
from .resolve import ItemStalled, deadline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import enrich_archive_gemini as base  # noqa: E402

log = logging.getLogger("unread.enrich")

MODEL_NAME = base.MODEL_NAME  # gemini-2.5-flash-lite

# Paid Flash-Lite, the tier this run is on. The figures still in
# `enrich_archive_gemini.run_enrichment` ($0.015 / $0.06) predate it and
# understate the bill by nearly an order of magnitude.
PRICE_INPUT_PER_M = 0.10
PRICE_OUTPUT_PER_M = 0.40

# A wall-clock ceiling on one article, for the same reason the fetch stage has
# one: `generate_content` takes no timeout, so a hung call freezes a pass over
# 492 articles with nothing in the log to say so. A stalled article is skipped
# rather than written - unlike a dead link, which is a finding, this is
# transient, so the next run picks it up.
ITEM_DEADLINE = 180

# The Gemini call's own timeout, and the one that actually bites.
#
# ITEM_DEADLINE above is SIGALRM, and Python runs a signal handler only when
# the interpreter regains control between bytecodes. The SDK blocks inside
# gRPC's C core, which never yields, so the alarm is raised and then never
# delivered: the live pass froze on one call for over ten minutes with the
# deadline armed and no stall recorded. The guard that works for `requests`
# does not work here. This is the one that does.
REQUEST_TIMEOUT = 120

# And the setting that makes the timeout reachable at all.
#
# On its default gRPC transport the SDK blocks in C: the interpreter never
# regains control, so SIGALRM is raised and never delivered, and on the live
# run the stall sat below the per-RPC timeout as well - the pass froze twice
# with `request_options` already set. On REST the SDK goes out through the
# ordinary HTTP stack, where the timeout is enforced and a signal can interrupt
# a blocked read.
TRANSPORT = "rest"

# No cap is not the same as no guard. The longest body in the measured sample
# is 66,476 characters; anything much past this is a scrape that went wrong.
MAX_BODY_CHARS = 120_000

CONFIDENCE = ("high", "medium", "low")
DEFAULT_CONFIDENCE = "low"

# Answers that mean "I could not tell", normalised to an empty inference.
NO_ANSWER = {"", "none", "n/a", "na", "unknown", "cannot tell", "can't tell",
             "cannot tell.", "unclear", "no idea"}

UNREAD_FIELDS = """WHY_SAVED: [One sentence on why this was likely saved on the saved date shown above, reading the article together with that date. If the article gives you no basis for an answer, respond with exactly: Cannot tell. Do not speculate to fill the field - an empty answer is a useful answer.]
WHY_SAVED_CONFIDENCE: [One word: High, Medium, or Low. How well the article and its saved date actually support the sentence above. Low if you are inferring more than reading.]
AGED_OUT: [Respond with ONLY one word: YES or NO. YES if the piece was tied to a moment that has passed - an election preview, a product launch, a conference writeup. NO if it would read the same today - an essay, a profile, a how-to, a piece of reporting with lasting subject.]"""


class SchemaError(ValueError):
    """A record that must not reach the analysis or the public shape."""


# ---------------------------------------------------------------------------
# the prompt
# ---------------------------------------------------------------------------

def saved_context(row):
    """The lines that tell the model when and how this was saved.

    `why_saved` is defined as an inference from the article plus the saved
    date, so the date is evidence rather than decoration. The folder and the
    star are the rest of the saved context the plan names; read progress is
    included because an item someone got two thirds through is a different
    intention from one never opened.
    """
    lines = [f"SAVED_DATE: {row.get('saved_date') or 'unknown'}"]
    if row.get("title"):
        lines.append(f"SAVED_TITLE: {row['title']}")
    if row.get("description"):
        lines.append(f"SAVED_DESCRIPTION: {row['description']}")
    if row.get("folder"):
        lines.append(f"SAVED_FOLDER: {row['folder']}")
    if row.get("starred"):
        lines.append("SAVED_STARRED: yes")
    progress = float(row.get("read_progress") or 0.0)
    lines.append(f"READ_PROGRESS: {progress:.2f}")
    return "\n".join(lines)


def build_unread_prompt(body, *, row):
    """The base prompt, uncapped, with the unread template and saved context.

    The context and the extra field instructions both land after SUMMARY and
    before the article, which is where `build_prompt`'s `extra_fields` puts
    them.
    """
    extra = ("\nThis article was saved to a read-it-later queue and never "
             "read. Here is what is known about the saving:\n\n"
             + saved_context(row)
             + "\n\nAlso provide:\n\n" + UNREAD_FIELDS)

    text = (body or "").strip()
    if not text:
        # An item that resolved nowhere still carries an intention. It is
        # enriched from the title and description that reached the context
        # block above, and flagged.
        text = ("(No article text could be retrieved for this item. Answer "
                "from the title and description in the saved context above, "
                "and set WHY_SAVED_CONFIDENCE accordingly.)")
    return base.build_prompt(text, max_chars=MAX_BODY_CHARS, extra_fields=extra)


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------

UNREAD_PREFIXES = ("WHY_SAVED:", "WHY_SAVED_CONFIDENCE:", "AGED_OUT:")


def _split_unread_lines(answer):
    """Pull the unread fields out before the base parser sees the answer.

    The base parser treats every line after SUMMARY: as a continuation of the
    summary, and these three are emitted after it. Handing it the whole answer
    would end every summary in the corpus with the inference the schema keeps
    separate from it.
    """
    kept, taken = [], {}
    current = None
    for line in (answer or "").splitlines():
        stripped = line.strip()
        matched = None
        for prefix in UNREAD_PREFIXES:
            if stripped.startswith(prefix):
                matched = prefix
                break
        if matched:
            taken[matched] = stripped[len(matched):].strip()
            current = matched
            continue
        if current and stripped and not _looks_like_a_field(stripped):
            # A wrapped value continues its own field. Without this the tail of
            # a long WHY_SAVED falls through to the base parser, which treats
            # any unprefixed line after SUMMARY as more summary - putting the
            # inference back inside the field the schema keeps it out of.
            taken[current] = (taken[current] + " " + stripped).strip()
            continue
        current = None
        kept.append(line)
    return "\n".join(kept), taken


FIELD_LINE = re.compile(r"^[A-Z][A-Z_]{2,}:")


def _looks_like_a_field(line):
    return bool(FIELD_LINE.match(line))


def parse_unread_response(answer):
    """Base fields through the shared parser, plus the three unread ones."""
    body, taken = _split_unread_lines(answer)
    parsed = base.parse_llm_response(body) or {}

    why = taken.get("WHY_SAVED:", "").strip().strip('"')
    confidence = taken.get("WHY_SAVED_CONFIDENCE:", "").strip().lower()

    if why.casefold().rstrip(".") in {a.rstrip(".") for a in NO_ANSWER}:
        why = ""
    if not why:
        # An absent inference is not a confident one.
        confidence = DEFAULT_CONFIDENCE
    if confidence not in CONFIDENCE:
        confidence = DEFAULT_CONFIDENCE

    # The WHOLE answer, stripped of punctuation, must be exactly YES or NO.
    # The prompt asks for one word, so anything longer is a model declining to
    # answer rather than answering. `startswith("NO")` swallowed "No idea",
    # "None", "Not sure" and "Nope, timeless" as a confident False - the one
    # default the branch below exists to forbid, and the one that makes a row
    # eligible for the shortlist. Strictness fails toward "unknown", which is
    # excluded from the denominator and states the uncertainty honestly.
    aged = taken.get("AGED_OUT:", "").strip().upper().strip(".,:;!?\"' ")
    if aged == "YES":
        aged_out = True
    elif aged == "NO":
        aged_out = False
    else:
        # Unknown, not False: defaulting would quietly move every unanswered
        # item into the still-current pile, and the aged-out share is the
        # number that makes the case a backlog is not a to-do list.
        aged_out = None

    parsed["why_saved"] = why
    parsed["why_saved_confidence"] = confidence
    parsed["aged_out"] = aged_out
    return parsed


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

CARRIED = ("url_sha256", "bookmark_id", "saved_date", "saved_year", "folder",
           "starred", "read_progress", "resolve_path", "body_words")

REQUIRED = ("url_sha256", "resolve_path", "why_saved_confidence",
            "abandonment", "enriched_from", "model")


def make_record(row, parsed, *, body=None, usage=None):
    """One enriched row: the queue's facts, the model's answer, the provenance.

    `abandonment` is recomputed here from read progress rather than read out of
    the answer, so a model that volunteers the field cannot overwrite the
    arithmetic.
    """
    record = {key: row.get(key) for key in CARRIED}
    record["domain"] = _domain(row.get("url"))
    record["title"] = row.get("title") or ""
    record["url"] = row.get("url") or ""

    record["abandonment"] = derive.abandonment(row.get("read_progress"))

    for key in ("ai_topics", "ai_people", "ai_orgs", "ai_locations",
                "ai_concepts", "ai_sentiment", "ai_emotion", "ai_summary"):
        record[key] = parsed.get(key)

    record["content_corrupted"] = (parsed.get("content_valid", "YES") or "YES").upper() == "NO"

    record["why_saved"] = parsed.get("why_saved", "")
    record["why_saved_confidence"] = parsed.get("why_saved_confidence", DEFAULT_CONFIDENCE)
    # The label travels with the field. The plan's rule is that every surface
    # showing this sentence marks it as inference in the surface itself, and a
    # bare string makes that something each surface has to remember.
    record["why_saved_kind"] = "inference"
    record["aged_out"] = parsed.get("aged_out")

    has_body = bool((body if body is not None else "").strip())
    if body is None:
        has_body = row.get("resolve_path") not in (None, "metadata")
    record["enriched_from"] = "body" if has_body else "metadata"

    record["model"] = MODEL_NAME
    record["enriched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if usage:
        record["input_tokens"], record["output_tokens"] = usage
    return record


def _domain(url):
    from urllib.parse import urlsplit
    if not url:
        return ""
    host = urlsplit(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def validate_record(record):
    """Raise unless this row is safe for the analysis and the public shape."""
    for field in REQUIRED:
        if record.get(field) in (None, ""):
            raise SchemaError(f"missing required field {field!r}")
    if record["why_saved_confidence"] not in CONFIDENCE:
        raise SchemaError(
            f"why_saved_confidence {record['why_saved_confidence']!r} is not one of "
            + ", ".join(CONFIDENCE))
    if record.get("why_saved") and record.get("why_saved_kind") != "inference":
        raise SchemaError(
            "why_saved is present without its inference label; a surface could "
            "print the sentence as a record of what happened")
    if record["abandonment"] not in derive.BANDS:
        raise SchemaError(f"abandonment {record['abandonment']!r} is not a band")
    if record.get("aged_out") not in (True, False, None):
        raise SchemaError("aged_out must be True, False, or None for unknown")
    return record


# ---------------------------------------------------------------------------
# the bill
# ---------------------------------------------------------------------------

class CostLedger:
    """What the run actually spent, not what it was estimated to."""

    def __init__(self):
        self.articles = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, input_tokens, output_tokens):
        self.articles += 1
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)

    @property
    def usd(self):
        return (self.input_tokens * PRICE_INPUT_PER_M / 1e6
                + self.output_tokens * PRICE_OUTPUT_PER_M / 1e6)

    @property
    def per_article_usd(self):
        return self.usd / self.articles if self.articles else 0.0

    def summary(self):
        return {"enriched": self.articles,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "usd": self.usd,
                "usd_per_article": self.per_article_usd,
                "model": MODEL_NAME}


def usage_from(usage, *, prompt, answer):
    """Token counts: the API's own if it reported them, an estimate otherwise.

    An estimate is a guess, and the point of the accounting is to check the
    plan's cost line against reality - but a run that silently bills zero is
    worse, because then the line can never be checked at all.
    """
    if usage:
        got_in = usage.get("input_tokens")
        got_out = usage.get("output_tokens")
        if got_in and got_out:
            return int(got_in), int(got_out)
    return (max(1, len(prompt or "") // 4), max(1, len(answer or "") // 4))


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

class GeminiModel:
    """The project's existing Gemini plumbing, with the meter attached."""

    def __init__(self, api_key=None, model_name=MODEL_NAME,
                 timeout=REQUEST_TIMEOUT, _model=None):
        self.model_name = model_name
        self.timeout = timeout
        if _model is not None:
            # Injected for tests: the SDK is not importable without a key and
            # the point under test is which keywords the call carries.
            self._model = _model
            return

        import google.generativeai as genai
        from dotenv import load_dotenv

        load_dotenv()
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        genai.configure(api_key=key, transport=TRANSPORT)
        self._model = genai.GenerativeModel(model_name)

    def generate(self, prompt):
        response = self._model.generate_content(
            prompt, request_options={"timeout": self.timeout})
        usage = None
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage = {"input_tokens": getattr(meta, "prompt_token_count", None),
                     "output_tokens": getattr(meta, "candidates_token_count", None)}
        return response.text, usage


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _log(failure_log, row, message):
    if not failure_log:
        return
    Path(failure_log).parent.mkdir(parents=True, exist_ok=True)
    with open(failure_log, "a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} "
                     f"{row['url_sha256'][:12]}: {message}\n")


def load_records(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _already_done(path):
    return {r["url_sha256"] for r in load_records(path)}


def read_body(bodies_dir, row):
    if not row.get("body_path"):
        return ""
    target = Path(bodies_dir) / row["body_path"]
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


def run(queue, *, bodies_dir, out_path, model, limit=None, dry_run=False,
        progress=None, failure_log=None, item_deadline=ITEM_DEADLINE):
    """Enrich every settled row that has not been enriched yet.

    Appends one JSON object per article as it completes, so a killed run costs
    one article. Returns the meter reading: tokens and dollars actually spent.
    """
    out_path = Path(out_path)
    done = _already_done(out_path)
    rows = [r for r in queue.rows()
            if r.get("outcome") in ("resolved", "metadata_only")
            and r["url_sha256"] not in done]
    if limit:
        rows = rows[:limit]

    ledger = CostLedger()
    if dry_run:
        summary = ledger.summary()
        summary["candidates"] = len(rows)
        summary["stalled"] = 0
        summary["failed"] = 0
        summary["dry_run"] = True
        return summary

    failures = 0
    stalled = 0
    refused = 0
    for index, row in enumerate(rows, start=1):
        body = read_body(bodies_dir, row)
        prompt = build_unread_prompt(body, row=row)
        try:
            with deadline(item_deadline):
                answer, usage = model.generate(prompt)
        except ItemStalled as exc:
            stalled += 1
            _log(failure_log, row, f"stalled, {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            # One article's worth of data, not the pass. The SDK's own timeout
            # raises DeadlineExceeded, and a transient 500 or a safety refusal
            # raises too; a 492-article paid run that dies on article 12 and
            # waits for a human to notice is not a resumable pipeline.
            #
            # `Exception`, so KeyboardInterrupt and SystemExit still stop the
            # run: a kill should kill.
            refused += 1
            log.warning("article %s refused: %s", row["url_sha256"][:12], exc)
            _log(failure_log, row, f"{type(exc).__name__}: {exc}")
            continue
        tokens = usage_from(usage, prompt=prompt, answer=answer)
        ledger.add(*tokens)

        parsed = parse_unread_response(answer)
        record = make_record(row, parsed, body=body, usage=tokens)
        try:
            validate_record(record)
        except SchemaError as exc:
            failures += 1
            _log(failure_log, row, str(exc))
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        if progress:
            progress(index, len(rows), row, record, ledger)

    summary = ledger.summary()
    summary["invalid"] = failures
    summary["stalled"] = stalled
    summary["failed"] = refused
    summary["candidates"] = len(rows)
    return summary
