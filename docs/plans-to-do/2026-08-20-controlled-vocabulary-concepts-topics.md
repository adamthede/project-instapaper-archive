---
title: "Controlled Vocabulary — normalized concepts + topics, curated once, applied both directions"
status: "In Progress"
priority: "P1"
project: "articles"
created: 2026-08-20
linked_pr: "https://github.com/adamthede/project-instapaper-archive/pull/13"
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

### Phase B — The curation gate (Adam, the only manual step)

Output an **HTML review artifact** in the house idiom (not a JSON dump): one
row per proposed concept showing canonical name, proposed definition, article
count and % coverage, the member strings folded into it, and 3 example article
titles linked to their week pages. Adam accepts / renames / merges / rejects /
splits, and settles the topic-vs-concept axis question above.

The approved result is committed as **`data/taxonomy/v1.yaml`** - canonical
name, definition, aliases (the member strings), axis, version. That file is
the source of truth from then on; it is human-readable, diffable, and editable
by hand.

### Phase C — Backward application (cheap, no per-article inference)

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

### Phase E — The pages that follow

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
