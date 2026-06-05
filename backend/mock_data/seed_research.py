"""
Synthetic research demo seeder.

Populates `stop_score_history` and `accessibility_events` with three realistic
scenarios so the research endpoints return meaningful timelines out of the box.
Each scenario shows an accessibility *degradation* followed by a *recovery*, with
the disruption event(s) that caused it.

THIS DATA IS DEMONSTRATION-ONLY. The snapshot scores below are hand-authored to
illustrate the shape of a disruption-and-recovery timeline. Real timelines will
be built by `research.capture_snapshot()` over long-term CrowdSense observations
and TPG operational data. See RESEARCH_README.md.

Snapshot rows are tagged source='manual_demo'. The disruption events are tagged
with the standardized source channel a real instance would carry (weather,
tpg_operations, crowdsense) purely to illustrate the source taxonomy — the
underlying data is still synthetic.

Idempotent: only inserts if `stop_score_history` is empty.
"""

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import get_conn


# Each snapshot: days_ago, note, overall, safety, wheelchair, blind, deaf, cognitive_elderly, survey_count
# Each event:    event_type, description, severity, started_days_ago, resolved_days_ago (None = ongoing), source

SCENARIOS = [
    {
        # ── Cornavin — elevator/lift failure at the flagship hub ──────────────
        # A lift outage isolates the platform for wheelchair users and strongly
        # affects elderly riders; blind and deaf riders are largely unaffected.
        "stop": "Cornavin",
        "snapshots": [
            # days_ago, note,                                  ovr,  saf,  wc,   bl,   df,   ce,   n
            (70, "Baseline — all facilities operational",      0.71, 0.85, 0.96, 0.86, 1.00, 0.71, 23),
            (56, "Baseline holding",                           0.71, 0.85, 0.96, 0.86, 1.00, 0.71, 23),
            (42, "Lift out of service — platform access cut",  0.30, 0.85, 0.30, 0.86, 1.00, 0.42, 24),
            (35, "Lift still down — temporary signage only",   0.28, 0.85, 0.28, 0.84, 1.00, 0.40, 25),
            (21, "Temporary ramp in place — partial access",   0.55, 0.85, 0.55, 0.86, 1.00, 0.55, 26),
            (7,  "Lift repaired — access restored",            0.70, 0.85, 0.95, 0.86, 1.00, 0.70, 27),
            (1,  "Recovery confirmed",                         0.71, 0.85, 0.96, 0.86, 1.00, 0.71, 27),
        ],
        "events": [
            ("elevator_failure",
             "Platform lift out of service; wheelchair and reduced-mobility "
             "access to the boarding area lost.",
             "high", 42, 10, "tpg_operations"),
        ],
    },
    {
        # ── Hôpital Cantonal — construction works + temporary relocation ──────
        # Resurfacing works degrade the surface and add obstacles, then the stop
        # is temporarily moved, which breaks tactile guidance for blind riders.
        "stop": "Cantonal",
        "snapshots": [
            (63, "Baseline — strong accessibility focus",      0.71, 0.80, 0.96, 0.95, 0.86, 0.71, 19),
            (49, "Baseline holding",                           0.71, 0.80, 0.96, 0.95, 0.86, 0.71, 19),
            (42, "Construction begins — surface broken up",    0.45, 0.70, 0.45, 0.55, 0.86, 0.45, 20),
            (35, "Stop temporarily relocated 40 m north",      0.45, 0.70, 0.55, 0.45, 0.80, 0.50, 21),
            (21, "Works ongoing — relocation still active",    0.45, 0.70, 0.52, 0.45, 0.80, 0.48, 22),
            (7,  "Works complete — stop returned to position",  0.71, 0.80, 0.95, 0.95, 0.86, 0.70, 23),
            (1,  "Recovery confirmed",                         0.71, 0.80, 0.96, 0.95, 0.86, 0.71, 23),
        ],
        "events": [
            ("construction",
             "Footpath resurfacing works; broken surface and obstacles in the "
             "waiting area.",
             "medium", 42, 9, "tpg_operations"),
            ("temporary_stop_relocation",
             "Stop temporarily moved ~40 m north of its usual position; tactile "
             "guidance not yet reinstated at the temporary location.",
             "medium", 35, 9, "tpg_operations"),
        ],
    },
    {
        # ── Nations — winter weather then a real-time display failure ─────────
        # Ice degrades the surface (wheelchair/elderly), then the PID real-time
        # display fails, which hits deaf and blind riders who rely on it.
        "stop": "Nations",
        "snapshots": [
            (60, "Baseline — busy, well-equipped",             0.48, 0.82, 0.67, 0.48, 1.00, 0.71, 14),
            (48, "Baseline holding",                           0.48, 0.82, 0.67, 0.48, 1.00, 0.71, 14),
            (40, "Snow/ice — surface degraded",                0.40, 0.82, 0.40, 0.40, 1.00, 0.50, 15),
            (33, "Surface cleared; PID display now offline",   0.42, 0.82, 0.67, 0.42, 0.55, 0.71, 15),
            (19, "PID still offline — no real-time info",       0.42, 0.82, 0.67, 0.42, 0.55, 0.71, 16),
            (6,  "PID display repaired",                       0.48, 0.82, 0.67, 0.48, 1.00, 0.71, 17),
            (1,  "Recovery confirmed",                         0.48, 0.82, 0.67, 0.48, 1.00, 0.71, 17),
        ],
        "events": [
            ("weather_disruption",
             "Snow and ice on the waiting area and approach path; surface "
             "temporarily unsafe for wheels and unsteady footing.",
             "medium", 40, 36, "weather"),
            ("equipment_failure",
             "Real-time passenger information (PID) display offline; no live "
             "departure info for deaf and low-vision riders.",
             "high", 33, 8, "crowdsense"),
        ],
    },
]


def seed_research() -> None:
    conn = get_conn()

    # Idempotency check — skip if any history already exists.
    count = conn.execute("SELECT COUNT(*) FROM stop_score_history").fetchone()[0]
    if count > 0:
        conn.close()
        return

    # Build name → stop_id index (substring match, like seed_observations).
    rows = conn.execute("SELECT stop_id, name FROM stops").fetchall()
    stop_index = {r["name"].lower(): r["stop_id"] for r in rows}

    def resolve(fragment: str):
        frag = fragment.lower()
        for sname, sid in stop_index.items():
            if frag in sname:
                return sid
        return None

    now = datetime.now()
    created_at = now.isoformat()
    snap_total = evt_total = 0

    for scenario in SCENARIOS:
        stop_id = resolve(scenario["stop"])
        if not stop_id:
            print(f"[seed_research] no stop matched '{scenario['stop']}', skipping")
            continue

        for (days_ago, note, ovr, saf, wc, bl, df, ce, n) in scenario["snapshots"]:
            captured_at = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                """INSERT INTO stop_score_history
                   (stop_id, captured_at, source, overall_accessibility,
                    safety_score, wheelchair_score, blind_score, deaf_score,
                    cognitive_elderly_score, survey_count, note)
                   VALUES (?,?,'manual_demo',?,?,?,?,?,?,?,?)""",
                (stop_id, captured_at, ovr, saf, wc, bl, df, ce, n, note),
            )
            snap_total += 1

        for (etype, desc, sev, start_days, resolve_days, source) in scenario["events"]:
            started_at = (now - timedelta(days=start_days)).isoformat()
            resolved_at = (
                (now - timedelta(days=resolve_days)).isoformat()
                if resolve_days is not None else None
            )
            conn.execute(
                """INSERT INTO accessibility_events
                   (stop_id, event_type, description, severity, started_at,
                    resolved_at, source, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (stop_id, etype, desc, sev, started_at, resolved_at, source,
                 created_at),
            )
            evt_total += 1

    conn.commit()
    conn.close()
    print(f"[seed_research] inserted {snap_total} snapshots and {evt_total} "
          f"events across {len(SCENARIOS)} demo scenarios")


if __name__ == "__main__":
    seed_research()
