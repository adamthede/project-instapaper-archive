---
title: "Controlled Vocabulary — normalized concepts + topics, curated once, applied both directions"
status: "In Progress"
priority: "P1"
project: "articles"
created: 2026-08-20
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/20"
# Phases A (PR #13), B (#17), C (#18) and E (#20) are DONE and live.
# ONLY PHASE D REMAINS — nightly per-article classification. It was never on
# the critical path for the pages, which is why they shipped without it: new
# articles simply carry no canonical entries until D lands, and the miss rate
# reports that honestly. linked_pr tracks the latest phase PR.
depends_on:
  - "Phase 5b measurement (PR #10) — the failure this plan answers"
  - "LM Studio: text-embedding-nomic-embed-text-v1.5 (verified available 2026-08-20)"
---

# Controlled Vocabulary for Concepts + Topics

## Problem

Free-text entity extraction produced a vocabulary larger than the corpus.
Measured on the live 16,346-row index at build time (PR #10, recomputed on
every build):

| Column | Vocabulary | Tagged | Singletons | Top-20 article coverage | Rankable |
|---|---|---|---|---|---|
| locations | 8,658 | 76.3% | 67.5% | 57.0% | yes |
| orgs | 27,479 | 92.2% | 72.6% | 45.2% | yes |
| topics | 29,306 | 99.7% | 74.2% | 25.3% | no |
| **concepts** | **50,601** | 99.7% | **74.0%** | **22.0%** | **no** |
| people | 41,514 | 86.8% | 79.4% | 18.0% | no |

Concepts and topics are tagged on ~100% of articles and are still unrankable,
because the model invented phrasing per article with no controlled vocabulary:
"AI" / "Artificial Intelligence" / "machine intelligence" never merge, and
"supply chains" / "supply chain resilience" never find each other. Proper
nouns self-normalize (everyone writes "Google" the same way), which is exactly
why orgs and locations clear the 40% bar and these two do not.

The cost is not cosmetic. The two richest interpretive fields in the archive
are currently unusable for ranking, for the trends heatmaps, and - the real
loss - for cross-era comparison. "What did I read about AI in 2012 versus
2024" is the question this archive exists to answer and cannot.

## Approach

A controlled vocabulary (the library-science answer: subject headings, a
curated thesaurus), derived bottom-up from the corpus, curated once by Adam,
then applied in **two different ways** depending on direction.

### The five design rules

1. **Derive, don't author.** The 50,601 strings are raw material, not garbage.
   Cluster them and the canonical set emerges with frequency and example
   articles already attached. A hand-authored taxonomy risks being elegant and
   not fitting - articles forced into ill-fitting buckets are worse than
   fragmentation, because the result looks clean.
2. **Backward is a vocabulary map, not 16,346 inferences.** Build the
   `raw string -> canonical` lookup ONCE, then join it against every article's
   existing strings. Minutes, not the ~60-90 GPU-hours a per-article
   re-classification would cost. It also makes taxonomy revision cheap: v2
   re-joins, it does not re-infer.
3. **Never force a pick.** Classification allows "none of these" and the
   **miss rate is recorded and published**. If 30% of articles match nothing,
   the taxonomy is wrong and that must surface, not be absorbed. Same rule as
   the never-guess time parser and the 40% ranking bar.
4. **Keep the raw strings.** Canonical fields sit BESIDE the originals, never
   replacing them. Free-text is fragmented but surprising; controlled is
   rankable but decides in advance what is interesting. Keeping both means
   every future revision re-derives from source. Same shape as the index-layer
   dedupe and the people quarantine: add a layer, disclose it, destroy nothing.
5. **Version the taxonomy.** Every classification records `taxonomy_version`,
   or the trend charts silently lie the day v2 lands.

### The axis question (decide at the gate)

Today's two fields are not two things. Topics came back as "Artificial
Intelligence", "Perception", "Societal Trends"; concepts as "Venture Capital",
"Innovation", "Market Share" - the same kind of noun, because the model was
asked for two lists and produced one list twice. Either merge them, or define
the axes so they cannot overlap. Proposed:

- **Topic** = the subject domain. What field is this in. ~30-40, possibly
  hierarchical (Technology > AI).
- **Concept** = the specific idea or mechanism at stake. Network effects,
  regulatory capture, spaced repetition. ~150.

An article on Uber pricing is Topic: Business; Concepts: two-sided markets,
surge pricing. Different questions, both searchable. **Adam decides this at
the curation gate, not before** - the clustering output is the evidence.

## Phases

### Phase A — Derivation (machine, unattended) — SHIPPED, PR #13

Run 2026-08-21 on the 16,346-row corpus. 73,099 distinct strings embedded in
8.9 min; 54,226 clusters at cosine similarity 0.89 in 9.5 s; top 250 named by
the pinned Qwen in 7.2 min with zero fallbacks. Gate artifact at
`data/vocab/curation-gate.html`.

**Two results that change the decisions below, both found in review:**

- The pooled free-text baseline is **34.1%** at top-20, not the 22.0% / 25.3%
  quoted above — those are per-column figures and this vocabulary is derived
  over both fields pooled. Case-folding and de-pluralising alone reach 38.2%.
  The derivation reaches 42.1%, so the honest gain is +8.0 over free text and
  **+3.9 over the no-embeddings option**.
- Measured against the individual columns Phase C builds, coverage is
  **28.9% (concepts) and 33.5% (topics)** — *neither clears the 40% bar*.
  Pooled clears it. So Phase E's "the pages turn on with no code change" holds
  only if the axes MERGE into one vocabulary. That makes open decision #1
  below load-bearing rather than aesthetic, and it now has numbers attached.

The threshold was chosen against a chaining tripwire, not by taste: at 0.78 a
single chained cluster held 41,980 strings, touched 16,293 of 16,346 articles,
and reported 99.7% coverage. Coverage alone cannot distinguish a working
vocabulary from one blob that ate the corpus.

Fragmentation is the method's accepted failure direction and it is large:
`privacy` survives as 54 separate clusters. Each of the top 300 entries
therefore carries its off-page look-alikes on the gate, tickable, folding
their aliases in on export — 291 of 300 entries have them.

### Phase A — as originally specified

Embeddings do the clustering; the LLM only names things. Split deliberately:
the deterministic step must not be a generation step.

1. Embed all distinct concept + topic strings with
   `text-embedding-nomic-embed-text-v1.5` (LM Studio, verified available
   2026-08-20). ~80k strings, batched, fleet flock honoured. Cache to disk -
   this is the expensive artifact and must never be recomputed casually.
2. Cluster (agglomerative/HDBSCAN on cosine distance). Deterministic given the
   embeddings; the tuning knob is distance threshold, chosen by inspection of
   cluster coherence, not by taste.
3. Rank clusters by **article coverage** (a set question, not a sum of
   mentions) and take the head that reaches a target coverage.
4. Per cluster, ONE local-LLM call (Qwen, pinned exact id, flock per call)
   proposes: canonical label + one-sentence definition, given the member
   strings with frequencies and 3 example article titles. The LLM names and
   defines; it does not decide membership.

### Phase B — DONE 2026-08-25

Adam read all 250 entries and accepted the list as a whole — "I like having the
diversity of topics" — with ten exceptions agreed individually. The result is
**248 entries / 2,469 aliases** in `data/taxonomy/v1.yaml`.

The axis question settled itself on Phase A's numbers (28.9% / 33.5% separately
against a 40% bar, pooled clears it), so the axes merge. Note the axis LABELS
are unreliable — Qwen called iPod, iPad, iTunes, Search Engines and Instant
Messaging "concepts" — but nothing downstream reads them, so they were left.

Every judgement used one test: **how many articles rely on this entry alone**,
measured against the live index. An entry that co-occurs with something more
specific ~96% of the time adds nothing to a ranking and puts noise in every
heatmap.

- **3 rejected** — Technology (1,137 tagged, 42 solo, 3.7%), Business (221/6),
  Design (268/4). Head coverage 80.5% → 80.3%; Business and Design did not move
  it at one decimal place. Capitalism (11.9% solo) and Economic Impact (9.1%)
  were measured as controls and kept, confirming the metric is not merely a
  proxy for "small vocabulary".
- **3 merged** — the IPO pair was the clearest defect in the head: one cluster
  held only abbreviations, the other only spelled-out forms, so the model had
  separated a term from its own acronym. Plus Climate Change / Global Warming
  and Data Analysis / Data Analytics.
- **2 split** — National Economies had flattened 20 countries into one label;
  China (64 articles) and the US (38) were pulled out and the singleton tail
  left in the remainder. Culture's real problem turned out not to be
  nationality but that "Arts and Culture" is a different subject entirely.
- **11 aliases moved or dropped** — iPad strings out of the iPhone entry
  (moved, so the articles keep a home), audio-recorder strings out of Digital
  Photography.

**The gate's SPLIT/MERGE controls record intent, they do not execute it** — the
export writes `review: split: <note>` and moves on. So the decisions were
encoded directly in `data/taxonomy/decisions.yaml` and applied by
`scripts/vocab/apply_curation.py`. The decisions are hand-authored; only the
taxonomy is generated. Editing v1.yaml by hand is a mistake the drift test
catches.

Also fixed here: `data/` was gitignored wholesale, so the file this plan calls
"human-readable, diffable and editable" could not be committed at all.

### Phase B — as originally specified

Output an **HTML review artifact** in the house idiom (not a JSON dump): one
row per proposed concept showing canonical name, proposed definition, article
count and % coverage, the member strings folded into it, and 3 example article
titles linked to their week pages. Adam accepts / renames / merges / rejects /
splits, and settles the topic-vs-concept axis question above.

The approved result is committed as **`data/taxonomy/v1.yaml`** - canonical
name, definition, aliases (the member strings), axis, version. That file is
the source of truth from then on; it is human-readable, diffable, and editable
by hand.

### Phase C — DONE 2026-08-26

The join runs at index-build time and adds four columns:
`canonical_entries`, `canonical_concepts`, `canonical_topics`,
`taxonomy_unmatched` and `taxonomy_version`. No inference — a dictionary
lookup, as specified.

**80.2% of articles are tagged** (13,880 of 17,317), and the pooled column
reaches **41.2% top-20 coverage against the 40% bar**.

Three things this phase got wrong on the first attempt, all caught by
measuring rather than assuming:

1. **The canonical columns must POOL.** Routing them by source field leaves
   `canonical_concepts` at 29.7% and `canonical_topics` at 32.3% — both under
   the bar, and both within a point of the 28.9% / 33.5% Phase A measured for
   the *raw* axes. Phase A settled this by pooling; a split canonical output
   walks back into the same failure with extra steps. `canonical_entries` is
   the vocabulary; the per-field columns are provenance only.
2. **The miss rate counted our own rejections as gaps.** Technology alone was
   1,125 unmatched articles and led the most-missed list — which is supposed
   to be the *v2 candidate* list. `v1.yaml` now carries `excluded_aliases`,
   counted separately.
3. **The raw miss rate is a poor v2 trigger.** 75% of gap strings are used by
   exactly one article and will never deserve an entry, so the number barely
   moves. The usable metric is gap strings reaching ≥25 articles: **357 today,
   worth ~12,800 article-tags**. Current v2 candidates lead with Leverage,
   Crime, Evolution, Journalism, Agriculture.

**Correction to Phase E below:** the claim that "no code change should be
needed to turn the pages on" is false, and it is not a bug in the gate.
`deepdives.concepts_verdict` reads the raw `concepts` column, which was
correct when written — no canonical column existed. Phase E must point it at
`canonical_entries`. That is a one-line change, and until it lands the pages
stay off despite the taxonomy clearing the bar.

### Phase C — as originally specified

Join `taxonomy v1` aliases against every article's existing free-text strings
at index-build time. New index columns: `canonical_concepts`,
`canonical_topics`, `taxonomy_version`, plus a per-article
`taxonomy_unmatched` count. Build prints total coverage and miss rate - no
silent drops, same discipline as the dedupe and quarantine reporting.

Strings that match nothing stay in the raw fields and are counted; the
aggregate miss rate is the taxonomy's health metric and is published on the
site.

### Phase D — Forward application (nightly, per article)

New articles get classified against the taxonomy by the local model as part of
the nightly enrichment chain: given the article's summary + title + its own
free-text strings, select zero or more canonical entries, with "none" allowed
and recorded. A handful of articles a day makes per-article inference trivial.

Drift watch: when the running miss rate crosses a threshold, that is the
signal to cut taxonomy v2 (re-cluster the unmatched tail, curate the delta,
re-join - Phase C is cheap by design).

### Phase E — DONE 2026-08-26

Two pages, both live on the build: **/concepts/** (the Cascade, then the
Collapse) and **/together/** (the co-occurrence matrix). The gate reads
`canonical_entries` and returns RANKABLE at 41.2% top-20, 248 entries, 0.0%
singletons.

**The plan's prediction was wrong in a useful way.** "No code change should be
needed; if one is, that is a bug in the gate" — the repoint was needed, and it
was not a bug: `concepts_verdict` read the raw column, which was correct when
written because no canonical column existed. The real lesson is the one that
followed: gating the pages on `rankable` ALONE meant that on an index with no
canonical column the verdict fell back to the raw field, trivially cleared the
bar on a small corpus, and then died on `KeyError` inside the deep-dive
try/except — silently taking /trends/, /orgs/, /people/ and /locations/ down
with it. One missing column cost five pages. The column's presence is now a
precondition in its own right.

**Design decisions, for whoever revisits.** The Cascade is ordered by peak year,
not volume — that sort IS the visualization, and by volume the same 1,320 cells
say nothing. Intensity is per-entry, so a quiet entry's peak reads as clearly as
a loud one's. Orange is promoted from accent to encoding on these two pages
only. The ramp was validated rather than chosen (monotonic, every step ≥3:1
against stone-900); three earlier orange ramps failed that floor because orange
sits darker than amber at equal chroma. Both properties are pinned by tests.

**Not done:** trends heatmap rows using the vocabulary, and Phase D (nightly
per-article classification), which was never on the critical path for the pages.

### Phase E — as originally specified

- `/concepts/` and `/topics/` build automatically once coverage clears the
  existing `RANKABLE_HEAD_COVERAGE = 40%` constant, which is already
  recomputed on every build. No code change should be needed to turn them on;
  if one is, that is a bug in the gate.
- Each entry gets its **definition** on the page - a defined vocabulary is
  more useful than a bare ranked list, and the definitions are already written
  by then.
- The `/trends/` heatmaps gain concept and topic rows that mean something.
- The prize: cross-era comparison in one vocabulary ("AI in 2012 vs 2024"),
  which is the memoir/Big Letter thread.

## Verification

- Phase A: cluster coherence spot-checked; embedding cache reproducible; the
  clustering step deterministic across two runs on the same cache.
- Phase B: the gate is Adam's approval - nothing classifies before
  `data/taxonomy/v1.yaml` exists and is committed.
- Phase C: coverage and miss rate printed at build; tests for the alias join,
  the unmatched counter, version stamping, and that raw fields survive intact.
- Phase D: tests for the "none" path and miss-rate recording; the nightly must
  stay non-fatal if LM Studio is down (same posture as enrichment).
- Standard adversarial review by a different agent before any PR is ready;
  reviewers work in their own checkout.

## Open decisions for Adam (at the gate, with evidence in hand)

1. One taxonomy or two axes (topic vs concept), and the definitions of each.
2. Target size - ~100 concepts was the starting instinct; the coverage curve
   from Phase A should inform the real number.
3. Whether locations/orgs get the same treatment later (they rank fine today,
   but "Google" vs "Google LLC" vs "Alphabet" is the same disease, milder).
