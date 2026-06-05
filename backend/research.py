"""
Research data layer — historical accessibility tracking for tpgFlex stops.

This is a *data-collection only* branch prepared for future accessibility
vulnerability studies. It records how a stop's accessibility looks over time and
the disruption events that affect it, so that long-term analysis can be done
later. It deliberately does **not** perform any analysis itself: no network
propagation, no graph algorithms, no machine learning, no vulnerability maps.

What it provides
----------------
1. Historical accessibility snapshots per stop  (`stop_score_history`).
2. Accessibility disruption events per stop      (`accessibility_events`).
3. Population-impact metrics per stop            (read live from stop_evaluator).
4. Observation analytics per stop                (counts over stop_observations).

Scoring is **never** redefined here. Every score this module reports is read
straight from `backend.stop_evaluator`, which remains the single source of truth
for how a stop is scored. This module only stores those scores with a timestamp
and reads the observation table that already exists.
"""

from __future__ import annotations

from datetime import datetime

from backend.database import get_conn
from backend import stop_evaluator as _se


# ── Vocabulary ────────────────────────────────────────────────────────────────

# The disruption types we track. These mirror the kinds of operational events
# that degrade stop accessibility in the field. Stored as canonical keys; the
# human label is derived for display.
EVENT_TYPES = {
    "construction":              "Construction works",
    "elevator_failure":          "Elevator / lift failure",
    "weather_disruption":        "Weather disruption",
    "temporary_stop_relocation": "Temporary stop relocation",
    "equipment_failure":         "Equipment failure",
}

# Canonical provenance of an accessibility event. Every event records which of
# these four channels it came from, so a later study can weight or filter by
# data source. Kept deliberately small and standardized:
#   crowdsense      → surfaced by community CrowdSense reports (Epicollect)
#   tpg_operations  → reported through TPG operational channels
#   weather         → derived from weather conditions / alerts
#   manual_demo     → hand-entered or synthetic demonstration data
EVENT_SOURCES = {
    "crowdsense":     "Community CrowdSense report",
    "tpg_operations": "TPG operational channel",
    "weather":        "Weather condition / alert",
    "manual_demo":    "Manual or synthetic demo entry",
}

# The four disability population groups the research branch tracks, each mapped
# to the existing stop_evaluator profile that already scores that group. This is
# only a naming bridge — the scoring weights live in stop_evaluator and are not
# duplicated or changed here.
#   wheelchair             → wheelchair
#   blind / low vision     → blind
#   deaf / hard of hearing → deaf
#   cognitive / elderly    → elderly
POPULATION_GROUPS = {
    "wheelchair":         {"label": "Wheelchair users",        "profile": "wheelchair"},
    "blind_low_vision":   {"label": "Blind / low vision",      "profile": "blind"},
    "deaf_hard_hearing":  {"label": "Deaf / hard of hearing",  "profile": "deaf"},
    "cognitive_elderly":  {"label": "Cognitive / elderly",     "profile": "elderly"},
}


# ── Observation polarity index ──────────────────────────────────────────────────
# To count an observation as "positive" or "negative" we reuse the weight signs
# already defined in stop_evaluator — we do not invent new weights. An item's net
# weight across all of the scorer's weight dictionaries decides its polarity.

def _build_polarity_index() -> dict[str, str]:
    """Map each known survey item (normalized) → 'positive' | 'negative'.

    Net weight is summed across every weight dict in stop_evaluator so the
    classification stays consistent with how the live scorer treats the item.
    """
    nets: dict[str, float] = {}
    all_dicts = list(_se.PROFILE_MAP.values()) + [_se.SAFETY_ITEMS, _se.EXPERIENCE_ITEMS]
    for weights in all_dicts:
        for item, w in weights.items():
            key = _se.normalize_label(item)
            nets[key] = nets.get(key, 0) + w
    return {k: ("positive" if v >= 0 else "negative") for k, v in nets.items()}

_POLARITY_INDEX = _build_polarity_index()


def observation_polarity(checked_item: str) -> str:
    """Return 'positive', 'negative', or 'neutral' for a survey item.

    'neutral' is returned for items not present in any weight dict (unmatched
    vocabulary), so they are neither counted as a help nor a hindrance.
    """
    return _POLARITY_INDEX.get(_se.normalize_label(checked_item), "neutral")


# ── Table bootstrap — runs once on import ─────────────────────────────────────

def _bootstrap() -> None:
    conn = get_conn()

    # Historical accessibility snapshots. One row per capture per stop. The
    # per-group columns let a later study read population impact over time
    # without recomputing anything.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stop_score_history (
            snapshot_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_id                TEXT NOT NULL,
            captured_at            TEXT NOT NULL,
            source                 TEXT DEFAULT 'live',
            overall_accessibility  REAL,
            safety_score           REAL,
            wheelchair_score       REAL,
            blind_score            REAL,
            deaf_score             REAL,
            cognitive_elderly_score REAL,
            survey_count           INTEGER,
            note                   TEXT,
            FOREIGN KEY (stop_id) REFERENCES stops(stop_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_stop ON stop_score_history(stop_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hist_time ON stop_score_history(captured_at)"
    )

    # Accessibility disruption events. resolved_at is NULL while a disruption is
    # ongoing. No severity scoring is derived from these — they are raw records.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accessibility_events (
            event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            stop_id      TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            description  TEXT,
            severity     TEXT DEFAULT 'medium',
            started_at   TEXT NOT NULL,
            resolved_at  TEXT,
            source       TEXT DEFAULT 'manual',
            created_at   TEXT,
            FOREIGN KEY (stop_id) REFERENCES stops(stop_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evt_stop ON accessibility_events(stop_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evt_type ON accessibility_events(event_type)"
    )

    conn.commit()
    conn.close()

_bootstrap()


def _maybe_seed_demo() -> None:
    """Auto-seed the synthetic research demo if the history table is empty."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM stop_score_history").fetchone()[0]
    conn.close()
    if count == 0:
        try:
            from backend.mock_data.seed_research import seed_research
            seed_research()
        except Exception as e:  # pragma: no cover - demo seeding is best-effort
            print(f"[research] demo seed skipped: {e}")

_maybe_seed_demo()


# ── Population impact (read live from the scorer) ─────────────────────────────

def population_impact(stop_id: str) -> dict:
    """Current accessibility for each disability population group at one stop.

    Reads the live observations for the stop and scores them per group using
    stop_evaluator. No new scoring is introduced — each group's number is exactly
    what the existing scorer returns for the corresponding profile.
    """
    conn = get_conn()
    stop = conn.execute(
        "SELECT stop_id, name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not stop:
        conn.close()
        return {"error": f"Stop '{stop_id}' not found"}
    items = [
        r["checked_item"]
        for r in conn.execute(
            "SELECT checked_item FROM stop_observations WHERE stop_id = ?", (stop_id,)
        )
    ]
    conn.close()

    groups = {}
    for key, meta in POPULATION_GROUPS.items():
        score = _se.evaluate_accessibility(items, meta["profile"])
        groups[key] = {
            "label":              meta["label"],
            "profile":            meta["profile"],
            "normalized":         score["normalized"],
            "rating":             score["label"],
            "color":              score["color"],
            "positives_matched":  score["positives_matched"],
            "negatives_matched":  score["negatives_matched"],
        }

    return {
        "stop_id":      stop["stop_id"],
        "stop_name":    stop["name"],
        "survey_count": len(items),
        "groups":       groups,
    }


# ── Observation analytics (counts over the observation table) ─────────────────

def observation_analytics(stop_id: str) -> dict:
    """Plain counts and date range of observations recorded for one stop.

    Reports: total reports, first/last report date, and how many of those
    observations were positive vs negative (polarity from the scorer's weights).
    """
    conn = get_conn()
    stop = conn.execute(
        "SELECT stop_id, name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not stop:
        conn.close()
        return {"error": f"Stop '{stop_id}' not found"}
    rows = conn.execute(
        "SELECT checked_item, submitted_at FROM stop_observations WHERE stop_id = ?",
        (stop_id,),
    ).fetchall()
    conn.close()

    dates = [r["submitted_at"] for r in rows if r["submitted_at"]]
    positives = sum(1 for r in rows if observation_polarity(r["checked_item"]) == "positive")
    negatives = sum(1 for r in rows if observation_polarity(r["checked_item"]) == "negative")

    return {
        "stop_id":               stop["stop_id"],
        "stop_name":             stop["name"],
        "total_reports":         len(rows),
        "first_report_date":     min(dates) if dates else None,
        "last_report_date":      max(dates) if dates else None,
        "positive_observations": positives,
        "negative_observations": negatives,
    }


# ── Historical snapshots ──────────────────────────────────────────────────────

def capture_snapshot(stop_id: str, source: str = "live", note: str | None = None) -> dict:
    """Compute the stop's current scores and store them as a history row.

    This is the data-collection entry point: going forward, calling it on a
    schedule builds the long-term accessibility timeline. The numbers stored are
    exactly what stop_evaluator returns right now — nothing is derived.
    """
    evaluation = _se.evaluate_stop(stop_id, "all")
    if "error" in evaluation:
        return evaluation

    conn = get_conn()
    items = [
        r["checked_item"]
        for r in conn.execute(
            "SELECT checked_item FROM stop_observations WHERE stop_id = ?", (stop_id,)
        )
    ]

    # Per-group normalized scores, straight from the scorer.
    per_group = {
        key: _se.evaluate_accessibility(items, meta["profile"])["normalized"]
        for key, meta in POPULATION_GROUPS.items()
    }

    captured_at = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO stop_score_history
           (stop_id, captured_at, source, overall_accessibility, safety_score,
            wheelchair_score, blind_score, deaf_score, cognitive_elderly_score,
            survey_count, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            stop_id,
            captured_at,
            source,
            evaluation["scores"]["accessibility"]["normalized"],
            evaluation["scores"]["safety"]["normalized"],
            per_group["wheelchair"],
            per_group["blind_low_vision"],
            per_group["deaf_hard_hearing"],
            per_group["cognitive_elderly"],
            evaluation["survey_count"],
            note,
        ),
    )
    conn.commit()
    snapshot_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return {
        "snapshot_id":           snapshot_id,
        "stop_id":               stop_id,
        "captured_at":           captured_at,
        "source":                source,
        "overall_accessibility": evaluation["scores"]["accessibility"]["normalized"],
        "safety_score":          evaluation["scores"]["safety"]["normalized"],
        "survey_count":          evaluation["survey_count"],
    }


def get_history(stop_id: str, limit: int = 100) -> dict:
    """Return stored accessibility snapshots for one stop, oldest first."""
    conn = get_conn()
    stop = conn.execute(
        "SELECT stop_id, name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not stop:
        conn.close()
        return {"error": f"Stop '{stop_id}' not found"}
    rows = conn.execute(
        """SELECT snapshot_id, captured_at, source, overall_accessibility,
                  safety_score, wheelchair_score, blind_score, deaf_score,
                  cognitive_elderly_score, survey_count, note
           FROM stop_score_history
           WHERE stop_id = ?
           ORDER BY captured_at ASC
           LIMIT ?""",
        (stop_id, limit),
    ).fetchall()
    conn.close()
    return {
        "stop_id":   stop["stop_id"],
        "stop_name": stop["name"],
        "count":     len(rows),
        "snapshots": [dict(r) for r in rows],
    }


# ── Accessibility events ──────────────────────────────────────────────────────

def add_event(
    stop_id: str,
    event_type: str,
    description: str | None = None,
    severity: str = "medium",
    started_at: str | None = None,
    resolved_at: str | None = None,
    source: str = "manual_demo",
) -> dict:
    """Record a disruption event for a stop. Raw record only — nothing derived."""
    if event_type not in EVENT_TYPES:
        return {"error": f"Unknown event_type '{event_type}'. "
                         f"Choose from: {list(EVENT_TYPES)}"}
    if source not in EVENT_SOURCES:
        return {"error": f"Unknown source '{source}'. "
                         f"Choose from: {list(EVENT_SOURCES)}"}
    conn = get_conn()
    stop = conn.execute(
        "SELECT stop_id FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not stop:
        conn.close()
        return {"error": f"Stop '{stop_id}' not found"}

    started_at = started_at or datetime.now().isoformat()
    created_at = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO accessibility_events
           (stop_id, event_type, description, severity, started_at, resolved_at,
            source, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (stop_id, event_type, description, severity, started_at, resolved_at,
         source, created_at),
    )
    conn.commit()
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "event_id":    event_id,
        "stop_id":     stop_id,
        "event_type":  event_type,
        "label":       EVENT_TYPES[event_type],
        "severity":    severity,
        "started_at":  started_at,
        "resolved_at": resolved_at,
        "source":      source,
    }


def _row_to_event(row) -> dict:
    e = dict(row)
    e["label"] = EVENT_TYPES.get(e["event_type"], e["event_type"])
    e["ongoing"] = e["resolved_at"] is None
    return e


def get_events(stop_id: str) -> dict:
    """Return all disruption events recorded for one stop, most recent first."""
    conn = get_conn()
    stop = conn.execute(
        "SELECT stop_id, name FROM stops WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if not stop:
        conn.close()
        return {"error": f"Stop '{stop_id}' not found"}
    rows = conn.execute(
        "SELECT * FROM accessibility_events WHERE stop_id = ? ORDER BY started_at DESC",
        (stop_id,),
    ).fetchall()
    conn.close()
    return {
        "stop_id":   stop["stop_id"],
        "stop_name": stop["name"],
        "count":     len(rows),
        "events":    [_row_to_event(r) for r in rows],
    }


def list_events(event_type: str | None = None, ongoing_only: bool = False) -> list[dict]:
    """Return recorded disruption events across all stops (plain retrieval)."""
    conn = get_conn()
    sql = "SELECT * FROM accessibility_events"
    clauses, params = [], []
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if ongoing_only:
        clauses.append("resolved_at IS NULL")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_event(r) for r in rows]
