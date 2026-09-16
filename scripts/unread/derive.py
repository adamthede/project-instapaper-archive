"""The fields that are computed, not inferred.

The plan is explicit about this split. `abandonment` is arithmetic on read
progress and `topic_drift` is a corpus computation; asking a model for either
would produce a plausible number that nothing can check. `why_saved` and
`aged_out` are the two fields the model is actually for, and they live in
`enrich`.
"""

# Read progress bands. Instapaper stores progress as a float 0.0 to 1.0.
#
# Exactly 0.0 is never opened - and it is the only signal that says so. The
# `progress_timestamp` field is populated even where progress is 0: 73 of the
# 105 folder items share the single value 1375386433 (1 August 2013), a
# platform-side backfill that appears 283 times in the CSV export. Never infer
# "opened" from the presence of a timestamp.
#
# The nearly-finished floor is 0.8. The eight partially-read folder items sit
# at 0.02, 0.04, 0.07, 0.10, 0.18, 0.18, 0.66 and 1.0, so the band is set by
# the shape of the live data rather than by a round number that happens to
# split it somewhere uninformative.
NEARLY_FINISHED = 0.8

NEVER_OPENED = "never_opened"
STARTED = "started"
NEARLY = "nearly_finished"

BANDS = (NEVER_OPENED, STARTED, NEARLY)


def abandonment(read_progress):
    """Which abandonment band a read-progress value falls in.

    Arithmetic, not judgement. A missing or unparseable progress is treated as
    never opened, which is what Instapaper means by an absent field.
    """
    try:
        progress = float(read_progress)
    except (TypeError, ValueError):
        return NEVER_OPENED
    if progress <= 0.0:
        return NEVER_OPENED
    if progress >= NEARLY_FINISHED:
        return NEARLY
    return STARTED


def topic_drift(item_topics, year_distribution):
    """The share of one item's topics the read corpus did not carry that year.

    0.0 when every topic on the item is one he also read that year, 1.0 when
    none is. An item with no topics, or a year the read corpus never saw, has
    no measurable drift and returns None rather than a zero that would read as
    "no drift".

    Deliberately NOT a Jaccard against the year's vocabulary. That was the
    first implementation and it was useless: a year's read corpus carries
    several hundred distinct topics and one item carries three, so the union is
    the vocabulary, the overlap is a rounding error, and every year of the live
    corpus scored between 0.9967 and 0.9992. A metric whose every value is the
    same is not a measurement.

    The denominator is the item's own topics, so the answer means what it says:
    how much of what he saved was a subject he was not reading.
    """
    topics = [str(t).strip() for t in (item_topics or []) if str(t).strip()]
    year_topics = {str(t).strip().casefold()
                   for t in (year_distribution or []) if str(t).strip()}
    if not topics or not year_topics:
        return None
    unmatched = sum(1 for t in topics if t.casefold() not in year_topics)
    return round(unmatched / len(topics), 4)
