# RESEARCH_README — Technical Research Supplement

This is the technical companion to the main [`README.md`](./README.md). It
documents the research data layer I added to AIBility, the scoring mechanics it
reads from, the synthetic demonstration scenarios, and the boundary between
implemented infrastructure and future research.

I have written it for three audiences — GitHub readers, academic reviewers, and
reviewers of a mathematical-biology application — so I am explicit about what is
built, what is synthetic, and what remains intended. No analytical results are
reported here: this branch collects and structures data; it does not yet
analyze it.

---

## Accessibility Scoring Reference

The research layer does not define its own scoring. Every number it stores is
read from `backend/stop_evaluator.py`, which remains the single source of truth.
This section documents that scorer for completeness.

**Survey vocabulary.** CrowdSense observations consist of 28 structured items
from the Epicollect "Observations du lieu" form (e.g. *Surface dure et stable*,
*Bandes podotactiles de guidage*, *Affichage temps réel*, *Zone sombre*). A
separate "Retour d'expérience / Quick Tags" form captures 19 ride-sentiment
items (e.g. *Bonne expérience*, *Accès difficile*, *Rampe utilisée si
nécessaire*).

**Profile weight dictionaries.** Each survey item carries a small integer weight
(positive = helpful, negative = harmful) in one or more of five disability
profiles:

| Profile | Key positive items (examples) | Key negative items (examples) |
|---|---|---|
| `wheelchair` | Level boarding (+4), wheelchair space (+3), hard surface (+3) | Steep slope (−4), soft/uneven surface (−3), obstacles (−2) |
| `blind` | Tactile guiding strips (+4), tactile edge warning (+4) | High-speed road (−3), obstacles (−2) |
| `deaf` | Real-time display (+4), stop name visible (+3), pictograms (+3) | *(no negative items in current dict)* |
| `elderly` | Bench (+3), shelter (+2), lighting (+2) | Steep slope (−3), soft surface (−2), obstacles (−2) |
| `low_digital` | Real-time display (+3), pictograms (+3), network map (+3) | *(no negative items in current dict)* |

A separate `safety` dictionary scores lighting, pedestrian crossings, nearby
services (positive) against darkness, isolation, high-speed roads, weak mobile
coverage (negative). Ride experience uses the sentiment tags.

**Scoring mechanics.** For a given stop and profile, matched item weights are
summed, then normalized to `[0, 1]` against the theoretical min–max span. The
result is labelled `Good` (≥ 0.70), `Fair` (≥ 0.40), or `Poor` (< 0.40). A
stop's overall accessibility is the **worst** of its per-profile scores.

**Data confidence** is a simple count-based heuristic: `high` if ≥ 3
observations, `medium` if ≥ 1, `low` if none.

**Punctuality and service regularity** are currently illustrative placeholders
pending a live TPG operational data feed.

---

## What has been implemented

The research branch is contained in `backend/research.py`,
`backend/mock_data/seed_research.py`, and a set of API routes appended to
`backend/main.py`. It adds two database tables and reuses the scorer above as
its only source of accessibility numbers.

### 1. Historical accessibility tracking

Table `stop_score_history`:

```
snapshot_id            INTEGER PRIMARY KEY
stop_id                TEXT    → stops(stop_id)
captured_at            TEXT    (ISO datetime)
source                 TEXT    ('live' | 'manual_demo')
overall_accessibility  REAL    (worst-of per-profile, [0,1])
safety_score           REAL
wheelchair_score       REAL
blind_score            REAL
deaf_score             REAL
cognitive_elderly_score REAL
survey_count           INTEGER
note                   TEXT
```

Each row is one timestamped snapshot. The dedicated per-group columns mean a
later study can read population-structured impact over time without
recomputation. `research.capture_snapshot(stop_id)` evaluates the stop *now*
via `stop_evaluator` and writes one such row — it derives nothing of its own.

### 2. Accessibility event model

Table `accessibility_events`:

```
event_id     INTEGER PRIMARY KEY
stop_id      TEXT    → stops(stop_id)
event_type   TEXT    (validated)
description  TEXT
severity     TEXT    ('low' | 'medium' | 'high')
started_at   TEXT    (ISO datetime)
resolved_at  TEXT    (NULL = ongoing)
source       TEXT    (validated)
created_at   TEXT
```

Two controlled vocabularies are enforced at write time:

**Event types:**

| Key | Label |
|---|---|
| `construction` | Construction works |
| `elevator_failure` | Elevator / lift failure |
| `weather_disruption` | Weather disruption |
| `temporary_stop_relocation` | Temporary stop relocation |
| `equipment_failure` | Equipment failure |

**Standardized sources:**

| Key | Meaning |
|---|---|
| `crowdsense` | Surfaced by community CrowdSense reports |
| `tpg_operations` | Reported through TPG operational channels |
| `weather` | Derived from weather conditions / alerts |
| `manual_demo` | Manually entered or synthetic demonstration data |

### 3. Population impact metrics

`research.population_impact(stop_id)` returns the current accessibility score
for four disability groups, each mapped onto an existing scorer profile:

| Research group | Scorer profile |
|---|---|
| Wheelchair users | `wheelchair` |
| Blind / low vision | `blind` |
| Deaf / hard of hearing | `deaf` |
| Cognitive / elderly | `elderly` |

No new scoring is defined — these are the existing scorer's numbers.

### 4. Observation analytics

`research.observation_analytics(stop_id)` returns plain descriptive statistics
over `stop_observations`: total reports, first and last report date, and positive
vs negative counts. Polarity reuses the sign of each item's existing weight
across all scorer dictionaries.

The infrastructure has been validated through local testing. Historical accessibility snapshots can be captured and retrieved through the API, disruption events can be recorded and queried, and all research records remain fully separated from the underlying accessibility scoring logic.

---

## Why it was implemented

The platform scores a stop's *current* accessibility well, but it has no memory:
it cannot answer "how accessible was this stop last month, and what changed?".
The research question is fundamentally **temporal and group-structured**, so
before any modelling is possible I needed a faithful, well-typed record of three
things:

1. **State over time** — the accessibility score trajectory per stop.
2. **Perturbations** — the disruptions that move those trajectories.
3. **Who is affected** — the same disruption resolved per disability group.

I implemented only this data foundation, and deliberately stopped there, for two
reasons. First, the scoring logic is trust-sensitive and already validated, so I
read from it rather than re-deriving anything — the research branch cannot change
a single displayed score. Second, separating data collection from analysis keeps
the analysis honest later: a model is only as credible as the unbiased record it
is fitted to, and that record should exist and be inspected before any model is
built on it.

---

## Synthetic Demonstration Scenarios

To validate the research infrastructure before sufficient real-world observations are available, the branch includes a small number of hand-crafted demonstration scenarios (`backend/mock_data/seed_research.py`). These scenarios are not analytical results and do not describe the actual accessibility conditions of the referenced stops. Their purpose is solely to demonstrate how accessibility conditions, disruption events, and recovery processes can be represented and tracked over time within the data model.

**Scenario 1 — Cornavin: elevator failure** (source: `tpg_operations`). Wheelchair
score drops from 0.96 to 0.30 while the lift is out of service; elderly drops
similarly; blind and deaf scores stay flat. A temporary ramp brings partial
recovery (0.55), then the repair restores the baseline. Illustrates a
*single-group-dominant* disruption.

**Scenario 2 — Hôpital Cantonal: construction + temporary relocation** (source:
`tpg_operations`). Resurfacing degrades the surface (wheelchair and elderly
drop), then the stop is temporarily relocated 40 m north without tactile guidance
(blind drops too). Illustrates *compounding* disruptions affecting different
groups in sequence.

**Scenario 3 — Nations: winter weather then display failure** (sources: `weather`,
then `crowdsense`). Ice degrades the surface (wheelchair/elderly), then the
real-time PID display fails — surfaced by community reports — hitting deaf and
low-vision riders. Illustrates a disruption whose impact *shifts between groups*
over time and arrives via different provenance channels.

None of these describe the real stops. They demonstrate that the data model can
represent group-specific, time-varying, multi-source disruption-and-recovery
patterns.

---

## What is intentionally not implemented yet

To keep implemented work and research direction cleanly separated, the branch
contains **no analysis layer**. Specifically, it does not implement:

- network propagation of disruptions between stops;
- graph or network algorithms over stop topology;
- machine learning, prediction, or forecasting;
- vulnerability maps, vulnerability indices, or "most-vulnerable-stop" rankings;
- any cross-stop aggregation beyond plain retrieval.

These omissions are by design. They are the *research*, and I want the data layer
to be reviewed and the data to accumulate before any model is built on top of it.

---

## Current Data Sources

The current implementation includes a seeded dataset that is used to validate the historical tracking, event logging, and accessibility scoring infrastructure. During development, this dataset enables the complete research workflow—from observation storage and accessibility evaluation to historical snapshot generation and disruption tracking—to be tested and demonstrated locally.

The long-term objective is to replace these seeded observations with live accessibility reports collected through the CrowdSense–Epicollect pipeline. Combined with operational information from transport providers and accessibility-related disruption reports, these observations would create a continuously growing longitudinal dataset describing accessibility conditions, disruption events, recovery processes, and population-specific impacts across the transport network.

---

## How future longitudinal data could support vulnerability and resilience studies

With **longitudinal CrowdSense observations** accumulating in
`stop_observations` and **TPG operational data** populating
`accessibility_events`, the following becomes possible — as future work, not as a
claim about this code:

**Real trajectories.** Scheduling `capture_snapshot` alongside the Epicollect
sync replaces synthetic data with genuine, dated score trajectories per stop and
per group.

**Disruption characterization.** Aligning events with the history table would
allow measuring, per disability group, the depth of an accessibility drop and the
time-to-recovery after a disruption — the empirical inputs any resilience
analysis needs.

**Potential connections to mathematical biology.** My interest is not only in accessibility itself, but also in understanding how accessibility conditions evolve over time across a population. Once longitudinal observations are available, I am interested in exploring whether concepts commonly used to study population-level adaptation, resilience, and response to perturbations can provide useful perspectives on accessibility challenges experienced by different disability groups.

Rather than viewing accessibility as a static property of individual stops, this perspective treats it as a dynamic system shaped by infrastructure conditions, service disruptions, environmental factors, and human behaviour. The research infrastructure implemented in this branch was designed specifically to support the collection of the longitudinal observations required for such investigations.

**Partner validation.** Any "vulnerability" a model proposes would be checked
against the lived experience of Procap Genève and Fondation Foyer-Handicap
before being treated as meaningful.

This repository provides the **typed, provenance-tracked, group-resolved,
time-stamped data foundation** for such a study. The modelling — and any
conclusions about vulnerability or propagation — remains future work that this
branch is built to make possible, not work that has been done.

---
## Validation Status

The research infrastructure has been validated locally using both seeded observations and live API interactions.

The following capabilities have been successfully tested:

- Historical accessibility snapshot creation and storage
- Historical accessibility snapshot retrieval
- Accessibility event creation and retrieval
- Population-specific accessibility metrics
- Integration with the existing accessibility scoring framework
- Compatibility with the existing AIBility platform and booking workflow

At present, validation included successful creation and retrieval of accessibility snapshots, creation and retrieval of disruption events, and compatibility testing with the existing booking workflow and accessibility scoring pipeline.
The underlying observations originate from the seeded demonstration dataset. Future deployments are intended to populate the same infrastructure with live CrowdSense observations collected through Epicollect.

---

## API Reference

### Platform endpoints

| Endpoint | Purpose |
|---|---|
| `POST /voice/process` | Conversational booking (intent + reply) |
| `POST /quick-book` | One-shot tap-to-book |
| `GET /stops`, `GET /stops/nearest` | Stop lookups |
| `POST /ramp-request` | Request ramp deployment → driver panel |
| `GET /api/route` | Pedestrian routing (OSRM) with crossing hints |
| `GET /api/stops/evaluate/{id}` | Live 5-dimension stop evaluation |
| `GET /api/stops/evaluate-all` | All active stops with scores |
| `POST /api/stop-evaluator/sync` | Refresh scores from CrowdSense |
| `GET /api/ride-experience` | Network-wide ride experience score |
| `WS /ws/driver` | Driver panel real-time alerts |
| `WS /ws/navigate/{role}` | Navigation relay (user ↔ display) |

### Research endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/research/event-types` | Tracked disruption types |
| `GET /api/research/event-sources` | Standardized provenance sources |
| `GET /api/research/population-groups` | Disability groups and scorer profiles |
| `GET /api/research/stops/{id}/history` | Historical snapshots, oldest first |
| `POST /api/research/stops/{id}/snapshot` | Capture current scores into history |
| `GET /api/research/stops/{id}/population-impact` | Per-group accessibility now |
| `GET /api/research/stops/{id}/observation-analytics` | Report counts + date range |
| `GET /api/research/stops/{id}/events` | Disruption events for one stop |
| `POST /api/research/stops/{id}/events` | Record a disruption event |
| `GET /api/research/events` | All events (`event_type`, `ongoing_only` filters) |
