"""The unread enrichment: the base schema, the unread template, and the bill.

Every test here names, in its docstring, the mutation it catches. No test calls
Gemini; the model is a fake that returns canned text and canned usage metadata.

The rule this file is mostly about: two of the unread template's fields are
deliberately NOT asked of the model. `abandonment` is arithmetic on read
progress and `topic_drift` is a corpus computation, and a model asked for
either would return a plausible number that nothing can check. `why_saved` and
`aged_out` are the two the model is genuinely for, and `why_saved` is the field
most likely to produce confident fiction, so it carries a confidence flag, it
is permitted to return nothing, and it is labelled as inference in the record
rather than in a footnote.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "core"))

import enrich_archive_gemini as base  # noqa: E402
from unread import derive  # noqa: E402
from unread import enrich  # noqa: E402
from unread import queue as q  # noqa: E402

GOLDEN = REPO / "tests" / "fixtures" / "base_prompt.golden.txt"


ANSWER = """CONTENT_VALID: YES
TOPICS: Attention, Media Criticism, Productivity
PEOPLE: Nicholas Carr
ORGANIZATIONS: The Atlantic
LOCATIONS: None
CONCEPTS: deep reading, distraction
SENTIMENT: Negative
EMOTION: Analytical
SUMMARY: An argument that the web rewires how we read.
WHY_SAVED: He was reading about attention in the year he stopped finishing things.
WHY_SAVED_CONFIDENCE: medium
AGED_OUT: NO
"""


class FakeModel:
    """Stands in for the Gemini client. Returns canned text and usage."""

    def __init__(self, answer=ANSWER, usage=None, fail_on=()):
        self.answer = answer
        self.usage = usage or {"input_tokens": 2990, "output_tokens": 180}
        self.fail_on = set(fail_on)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if len(self.prompts) in self.fail_on:
            raise RuntimeError("model unavailable")
        answer = self.answer(prompt) if callable(self.answer) else self.answer
        return answer, dict(self.usage)


def a_row(url_sha256="a" * 64, **extra):
    row = {
        "url_sha256": url_sha256,
        "bookmark_id": 7,
        "url": "https://theatlantic.com/is-google-making-us-stupid",
        "title": "Is Google Making Us Stupid?",
        "description": "What the internet is doing to our brains.",
        "saved_date": "2018-04-02",
        "saved_year": 2018,
        "folder": None,
        "starred": False,
        "read_progress": 0.0,
        "abandonment": "never_opened",
        "resolve_path": "instapaper",
        "outcome": "resolved",
        "body_path": "aa/" + "a" * 64 + ".txt",
        "body_words": 3200,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# the shared prompt: reused, parameterised, not forked
# ---------------------------------------------------------------------------

def test_the_read_corpus_prompt_has_not_drifted():
    """Mutation: any edit to the shared prompt the read corpus was built on.

    17,020 enriched rows were produced by this exact text. The unread run
    reaches it through two new keyword arguments and must not change what it
    says, so the default rendering is pinned to a golden file: a diff here is
    either a mistake or a decision, and both want to be seen.
    """
    assert base.build_prompt("ARTICLE BODY") == GOLDEN.read_text(encoding="utf-8")


def test_the_ten_thousand_character_cap_is_still_the_default():
    """Mutation: removing the cap globally instead of for the unread run.

    Adam removed the cap for the unread corpus on 2026-09-15. Removing it for
    the read corpus too would silently change what a re-enrichment of 17,020
    rows costs and what it sees.
    """
    long_body = "x" * 25_000
    assert "x" * 10_000 in base.build_prompt(long_body)
    assert "x" * 10_001 not in base.build_prompt(long_body)


def test_the_unread_run_sends_the_whole_article():
    """Mutation: the cap surviving into the unread prompt.

    28 of the 79 sampled bodies exceed 10,000 characters and the longest is
    66,476. Under the cap a third of the corpus would be summarised from its
    opening only, and the plan priced removing it at five cents.
    """
    long_body = "x" * 25_000
    prompt = enrich.build_unread_prompt(long_body, row=a_row())
    assert "x" * 25_000 in prompt


def test_a_pathological_body_is_still_bounded():
    """Mutation: no guard at all, so one broken scrape sends a megabyte.

    No cap is not the same as no guard. The longest real body in the sample is
    66,476 characters; anything past the guard is a scrape that went wrong, not
    an article.

    The bound is a literal rather than the module's own constant. Asserting
    against `MAX_BODY_CHARS` would move with it, so raising the guard to a
    hundred megabytes would still pass.
    """
    prompt = enrich.build_unread_prompt("y" * 400_000, row=a_row())
    assert len(prompt) < 200_000
    assert enrich.MAX_BODY_CHARS >= 66_476  # every real body still fits whole


def test_the_unread_template_keeps_every_base_field():
    """Mutation: forking the prompt and losing a base field.

    The plan is explicit: the base schema is the portfolio standard and the
    unread template adds to it. A fork that drops CONTENT_VALID would also drop
    the guard that catches junk scrapes from the direct-GET leg.
    """
    prompt = enrich.build_unread_prompt("body text", row=a_row())
    for marker in ("CONTENT_VALID:", "TOPICS:", "PEOPLE:", "ORGANIZATIONS:",
                   "LOCATIONS:", "CONCEPTS:", "SENTIMENT:", "EMOTION:", "SUMMARY:"):
        assert marker in prompt


def test_the_unread_instructions_come_before_the_article_text():
    """Mutation: appending the template after the body.

    The base prompt ends with the article. Instructions placed after it are
    read as part of the document, which is both a worse prompt and a small
    injection surface on text fetched from the open web.
    """
    prompt = enrich.build_unread_prompt("THE ARTICLE ITSELF", row=a_row())
    assert prompt.index("WHY_SAVED:") < prompt.index("THE ARTICLE ITSELF")


def test_the_prompt_permits_the_model_to_say_it_cannot_tell():
    """Mutation: dropping the escape hatch from the why_saved instruction.

    This is the rule that separates an inference from confident fiction. The
    plan requires the answer be allowed to be "cannot tell" and the field be
    allowed to be empty.
    """
    prompt = enrich.build_unread_prompt("body", row=a_row())
    assert "cannot tell" in prompt.lower()


def test_the_prompt_carries_the_saved_date_because_why_saved_depends_on_it():
    """Mutation: asking why it was saved without saying when.

    "One sentence, from the article plus the saved date" is the plan's
    definition. Without the date the model is guessing at a motive with the
    evidence removed.
    """
    prompt = enrich.build_unread_prompt("body", row=a_row(saved_date="2018-04-02"))
    assert "2018-04-02" in prompt


def test_the_model_is_never_asked_for_abandonment_or_topic_drift():
    """Mutation: moving a computed field into the prompt.

    Both are checkable arithmetic. Asking for them would return a plausible
    number nothing can check, and would make the two fields that ARE inference
    harder to tell apart from the ones that are not.
    """
    prompt = enrich.build_unread_prompt("body", row=a_row())
    assert "ABANDONMENT" not in prompt.upper()
    assert "TOPIC_DRIFT" not in prompt.upper()


# ---------------------------------------------------------------------------
# parsing the answer
# ---------------------------------------------------------------------------

def test_the_base_fields_parse_through_the_existing_parser():
    """Mutation: a second parser for the unread run that drifts from the first."""
    parsed = enrich.parse_unread_response(ANSWER)
    assert parsed["ai_topics"] == ["Attention", "Media Criticism", "Productivity"]
    assert parsed["ai_people"] == ["Nicholas Carr"]
    assert parsed["ai_sentiment"] == "Negative"
    assert parsed["ai_summary"].startswith("An argument")


def test_the_summary_does_not_swallow_the_unread_fields():
    """Mutation: handing the whole answer to the base parser unchanged.

    The base parser treats every line after SUMMARY: as a continuation of the
    summary, and the unread template's three fields are emitted after it. Left
    alone, every summary in the corpus would trail off into the words
    WHY_SAVED and the inference the schema keeps separate from it.
    """
    parsed = enrich.parse_unread_response(ANSWER)
    assert parsed["ai_summary"] == "An argument that the web rewires how we read."


def test_the_unread_fields_parse():
    """Mutation: dropping the three fields the unread template exists for."""
    parsed = enrich.parse_unread_response(ANSWER)
    assert parsed["why_saved"].startswith("He was reading about attention")
    assert parsed["why_saved_confidence"] == "medium"
    assert parsed["aged_out"] is False


def test_cannot_tell_becomes_an_empty_why_saved_at_low_confidence():
    """Mutation: storing the literal string "cannot tell" as the inference.

    The analysis treats an empty why_saved as a result rather than a failure. A
    literal "cannot tell" would be counted, aggregated and eventually printed.
    """
    parsed = enrich.parse_unread_response(
        ANSWER.replace("He was reading about attention in the year he stopped finishing things.",
                       "Cannot tell"))
    assert parsed["why_saved"] == ""
    assert parsed["why_saved_confidence"] == "low"


def test_a_missing_confidence_is_low_not_high():
    """Mutation: defaulting the confidence to high, or to the model's silence.

    Low-confidence rows are excluded from every aggregate, so the default has
    to be the one that excludes. A missing flag is not a confident answer.
    """
    without = "\n".join(l for l in ANSWER.splitlines()
                        if not l.startswith("WHY_SAVED_CONFIDENCE"))
    assert enrich.parse_unread_response(without)["why_saved_confidence"] == "low"


def test_a_confidence_the_prompt_never_offered_is_low():
    """Mutation: passing an unknown enum value through into the record.

    "Very high" or "certain" would sail past a membership check that only
    guards against empty, and then be counted as a high-confidence inference.
    """
    odd = ANSWER.replace("WHY_SAVED_CONFIDENCE: medium", "WHY_SAVED_CONFIDENCE: certain")
    assert enrich.parse_unread_response(odd)["why_saved_confidence"] == "low"


def test_a_missing_aged_out_is_unknown_rather_than_false():
    """Mutation: defaulting aged_out to False.

    "What aged out?" is one of the six analysis questions and the share is the
    number that makes the case a backlog is not a to-do list. Defaulting to
    False would quietly move every unanswered item into the still-current pile.
    """
    without = "\n".join(l for l in ANSWER.splitlines() if not l.startswith("AGED_OUT"))
    assert enrich.parse_unread_response(without)["aged_out"] is None


def test_content_valid_no_marks_the_record_corrupted():
    """Mutation: dropping the CONTENT_VALID guard on the direct-GET leg.

    The plan gates anything the direct fetch produces behind this check,
    because the word floor cannot tell an article from a JavaScript shell.
    """
    parsed = enrich.parse_unread_response(ANSWER.replace("CONTENT_VALID: YES",
                                                         "CONTENT_VALID: NO"))
    record = enrich.make_record(a_row(resolve_path="direct"), parsed)
    assert record["content_corrupted"] is True


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

def test_abandonment_is_computed_from_read_progress_not_taken_from_the_model():
    """Mutation: reading an ABANDONMENT line out of the model's answer.

    The bands are arithmetic. A model that volunteers the field must not be
    able to overwrite the arithmetic.
    """
    answer = ANSWER + "ABANDONMENT: nearly_finished\n"
    record = enrich.make_record(a_row(read_progress=0.0),
                                enrich.parse_unread_response(answer))
    assert record["abandonment"] == "never_opened"


@pytest.mark.parametrize("progress,band", [
    (0.0, derive.NEVER_OPENED),
    (0.02, derive.STARTED),
    (0.66, derive.STARTED),
    (0.79, derive.STARTED),
    (0.8, derive.NEARLY),
    (1.0, derive.NEARLY),
])
def test_the_abandonment_bands_sit_where_the_live_data_puts_them(progress, band):
    """Mutation: moving the nearly-finished floor, or an off-by-one at a boundary.

    The eight partially-read folder items sit at 0.02, 0.04, 0.07, 0.10, 0.18,
    0.18, 0.66 and 1.0. Exactly 0.0 is never opened and nothing else is.
    """
    assert derive.abandonment(progress) == band


def test_why_saved_is_labelled_as_inference_in_the_record_itself():
    """Mutation: storing the sentence as a bare field.

    The plan: every surface that shows it labels it as inference in the surface
    itself rather than in a footnote. A record whose field carries no label
    makes that a thing each surface has to remember.
    """
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER))
    assert record["why_saved_kind"] == "inference"


def test_a_record_with_an_unlabelled_inference_fails_validation():
    """Mutation: letting a surface receive why_saved without its label.

    Validation is where the rule is enforced rather than hoped for.
    """
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER))
    record.pop("why_saved_kind")
    with pytest.raises(enrich.SchemaError):
        enrich.validate_record(record)


def test_a_record_must_say_which_leg_produced_its_text():
    """Mutation: dropping resolve_path from the enriched record.

    Every other field has to be read through it. A summary built from a 2016
    Wayback snapshot and one built from today's live page are different
    evidence about the same intention.
    """
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER))
    assert record["resolve_path"] == "instapaper"
    record["resolve_path"] = None
    with pytest.raises(enrich.SchemaError):
        enrich.validate_record(record)


def test_a_bad_confidence_value_fails_validation():
    """Mutation: a validator that checks presence but not the enum."""
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER))
    record["why_saved_confidence"] = "quite sure"
    with pytest.raises(enrich.SchemaError):
        enrich.validate_record(record)


def test_the_record_does_not_carry_topic_drift():
    """Mutation: computing drift at enrichment time, per item, against nothing.

    Drift is a distance from the read corpus's distribution for the same saved
    year. It cannot be known while enriching one article, and a zero written
    here would read as "no drift" forever.
    """
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER))
    assert "topic_drift" not in record


def test_a_metadata_only_item_is_enriched_from_its_title_and_description():
    """Mutation: skipping the dead items, or sending the model an empty body.

    An item that resolves nowhere still has a title, a domain, a saved date and
    a word count, and it still counts as an intention. It is enriched from what
    is left and flagged.
    """
    row = a_row(resolve_path="metadata", outcome="metadata_only", body_path=None)
    prompt = enrich.build_unread_prompt("", row=row)
    assert "Is Google Making Us Stupid?" in prompt
    assert "What the internet is doing to our brains." in prompt
    record = enrich.make_record(row, enrich.parse_unread_response(ANSWER))
    assert record["enriched_from"] == "metadata"


def test_a_resolved_item_is_enriched_from_its_body():
    """Mutation: a flag that is always metadata, making the caveat meaningless."""
    record = enrich.make_record(a_row(), enrich.parse_unread_response(ANSWER),
                                body="a real body")
    assert record["enriched_from"] == "body"


# ---------------------------------------------------------------------------
# the bill
# ---------------------------------------------------------------------------

def test_the_ledger_prices_flash_lite_at_the_rate_the_plan_quotes():
    """Mutation: the stale $0.015/$0.06 pricing still in the read corpus script.

    Paid Flash-Lite is $0.10 per million input and $0.40 per million output.
    The older figures in `enrich_archive_gemini.py` predate the paid tier and
    understate the bill by nearly an order of magnitude.
    """
    ledger = enrich.CostLedger()
    ledger.add(1_000_000, 1_000_000)
    assert ledger.usd == pytest.approx(0.50, abs=1e-9)


def test_the_measured_corpus_line_lands_where_the_plan_priced_it():
    """Mutation: an arithmetic slip in the total.

    492 articles at the sample's mean is 1.47M input and 0.09M output tokens,
    which the plan prices at $0.18 for one full pass.
    """
    ledger = enrich.CostLedger()
    for _ in range(492):
        ledger.add(2_990, 180)
    assert ledger.usd == pytest.approx(0.18, abs=0.01)
    assert ledger.articles == 492


def test_the_ledger_reports_cost_per_article():
    """Mutation: a total with no per-article figure.

    The dispatch's gate is the measured cost per article on the first 25. A
    total alone cannot be compared against the plan's line before the rest of
    the corpus is spent.
    """
    ledger = enrich.CostLedger()
    ledger.add(2_990, 180)
    ledger.add(2_990, 180)
    assert ledger.per_article_usd == pytest.approx(ledger.usd / 2)


def test_the_ledger_prefers_the_api_token_counts_over_an_estimate():
    """Mutation: billing from a character estimate when the API reported truth.

    Gemini returns prompt and candidate token counts. An estimate that happens
    to be close is still a guess, and the whole point of the accounting is to
    check the plan's line against reality.
    """
    usage = enrich.usage_from({"input_tokens": 4_242, "output_tokens": 111},
                              prompt="short", answer="short")
    assert usage == (4_242, 111)


def test_the_ledger_falls_back_to_an_estimate_when_the_api_reports_nothing():
    """Mutation: recording zero tokens when usage metadata is absent.

    A run that silently bills zero is worse than one that estimates: it reports
    a cost of nothing and the plan's line can never be checked.
    """
    prompt = "x" * 4_000
    tokens_in, tokens_out = enrich.usage_from(None, prompt=prompt, answer="y" * 400)
    assert tokens_in > 500
    assert tokens_out > 50


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def make_corpus(tmp_path, n=3):
    queue_path = tmp_path / "unread_queue.jsonl"
    bodies = tmp_path / "bodies"
    rows = []
    for i in range(n):
        key = f"{i:064d}"
        row = a_row(url_sha256=key, bookmark_id=i,
                    url=f"https://example.com/article-{i}",
                    body_path=f"{key[:2]}/{key}.txt")
        target = bodies / row["body_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"Body of article {i}. " + "word " * 300, encoding="utf-8")
        rows.append(row)
    queue = q.Queue(queue_path)
    queue.upsert(rows)
    for row in rows:
        queue.mark(row["url_sha256"], outcome="resolved", resolve_path="instapaper",
                   body_path=row["body_path"])
    return queue, bodies


def test_the_run_writes_after_every_article(tmp_path):
    """Mutation: buffering enriched records and writing at the end.

    The enrichment is resumable at any point for the same reason the fetch is:
    a killed run costs one article, not a pass over the corpus.
    """
    queue, bodies = make_corpus(tmp_path, n=3)
    out = tmp_path / "unread_enriched.jsonl"
    model = FakeModel(fail_on={2})

    with pytest.raises(RuntimeError):
        enrich.run(queue, bodies_dir=bodies, out_path=out, model=model)

    written = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(written) == 1


def test_the_run_resumes_and_does_not_re_enrich(tmp_path):
    """Mutation: re-enriching the whole corpus on every run.

    At $0.18 a pass this is cheap to get wrong and easy to not notice. The
    resume check is also what makes a prompt-iteration pass affordable.
    """
    queue, bodies = make_corpus(tmp_path, n=3)
    out = tmp_path / "unread_enriched.jsonl"

    first = FakeModel(fail_on={2})
    with pytest.raises(RuntimeError):
        enrich.run(queue, bodies_dir=bodies, out_path=out, model=first)

    second = FakeModel()
    summary = enrich.run(queue, bodies_dir=bodies, out_path=out, model=second)
    assert len(second.prompts) == 2
    assert summary["enriched"] == 2
    assert len(enrich.load_records(out)) == 3


class RecordingGemini:
    """Stands in for the SDK's GenerativeModel, recording call keywords."""

    def __init__(self):
        self.calls = []

    def generate_content(self, prompt, **kwargs):
        self.calls.append(kwargs)

        class Response:
            text = "CONTENT_VALID: YES\nSUMMARY: fine.\n"
            usage_metadata = None

        return Response()


def test_the_gemini_call_carries_its_own_timeout():
    """Mutation: relying on the wall-clock deadline to bound a Gemini call.

    It cannot. The deadline is SIGALRM, and Python runs a signal handler only
    when the interpreter regains control between bytecodes. The Gemini SDK
    blocks inside gRPC's C core, which never yields, so the alarm is raised and
    then simply never delivered.

    Measured: the enrichment pass froze on one call and sat there for over ten
    minutes with the 180-second deadline armed and no stall recorded. The guard
    that works for `requests` does not work here, and the SDK's own
    `request_options` timeout is the thing that does.
    """
    recorder = RecordingGemini()
    model = enrich.GeminiModel(_model=recorder)
    model.generate("a prompt")
    assert recorder.calls
    assert recorder.calls[0]["request_options"]["timeout"] > 0


def test_one_stalled_article_cannot_stop_the_enrichment_pass(tmp_path):
    """Mutation: trusting the Gemini SDK to bound its own call.

    The same defect the fetch stage was caught by twice, in the next long
    network-bound loop. `generate_content` takes no timeout, so one hung call
    freezes a pass over 492 articles with nothing in the log to say so.

    A stalled article is skipped rather than written: unlike a dead link, which
    is a finding, this is transient, so it stays un-enriched and the next run
    picks it up.
    """
    import time as real_time

    queue, bodies = make_corpus(tmp_path, n=3)
    out = tmp_path / "unread_enriched.jsonl"

    class HangsOnce(FakeModel):
        def generate(self, prompt):
            if len(self.prompts) == 1:
                self.prompts.append(prompt)
                real_time.sleep(5)
            return super().generate(prompt)

    model = HangsOnce()
    summary = enrich.run(queue, bodies_dir=bodies, out_path=out, model=model,
                         item_deadline=1)

    assert summary["stalled"] == 1
    assert summary["enriched"] == 2
    assert len(enrich.load_records(out)) == 2


def test_the_run_reports_the_bill_it_actually_ran_up(tmp_path):
    """Mutation: a summary that reports an estimate rather than the meter.

    The plan's cost line is a prediction. The run has to be able to say whether
    it held.
    """
    queue, bodies = make_corpus(tmp_path, n=4)
    out = tmp_path / "unread_enriched.jsonl"
    summary = enrich.run(queue, bodies_dir=bodies, out_path=out, model=FakeModel())
    assert summary["enriched"] == 4
    assert summary["input_tokens"] == 4 * 2990
    assert summary["usd"] == pytest.approx(4 * (2990 * 1e-7 + 180 * 4e-7))
    assert summary["usd_per_article"] == pytest.approx(summary["usd"] / 4)


def test_a_dry_run_spends_nothing(tmp_path):
    """Mutation: a dry run that still calls the model.

    The dispatch's fallback when no key is present is a dry run, and a dry run
    that bills is not one.
    """
    queue, bodies = make_corpus(tmp_path, n=3)
    out = tmp_path / "unread_enriched.jsonl"
    model = FakeModel()
    summary = enrich.run(queue, bodies_dir=bodies, out_path=out, model=model,
                         dry_run=True)
    assert model.prompts == []
    assert summary["usd"] == 0
    assert not out.exists()


def test_every_written_record_validates(tmp_path):
    """Mutation: validation that runs on a sample, or not at all.

    The analysis and the public shape both read this file. A record that fails
    the schema should never reach either of them.
    """
    queue, bodies = make_corpus(tmp_path, n=3)
    out = tmp_path / "unread_enriched.jsonl"
    enrich.run(queue, bodies_dir=bodies, out_path=out, model=FakeModel())
    for record in enrich.load_records(out):
        enrich.validate_record(record)
