---
name: daily-life-orchestrator
description: "Run and operate a Hermes daily-life orchestrator for a given profile: morning briefing, adaptive planning, Kanban dispatch, telemetry capture, EOD review, and 100-point hardening. Use when the user asks for morning briefing, evening review, replanning, wants to audit what daily-life is doing, or needs to extend the 100-point hardening."
---

# Daily Life Orchestrator — 13-step loop, 10 subsystems, 100 hardening points

This plugin orchestrates, not implements. Daily Life is the **Caller** in every pair: it
triggers, gates, and records. The actual browser, email, kanban, and brain work happens in
their own plugins. If a line of this skill says "Daily Life does X", the X is a **routing
call** to the target plugin's own engine, not a re-implementation of it.

Everything below assumes you have the `HERMES_HOME` and `HERMES_PROFILE` env vars set. The
plugin reads them through `data/plugins/common/profile_resolver.py`.

## Prerequisites

- `biomimetic-brain` is installed and its `brain-state.json` is seeded (see
  `biomimetic-brain/skills/mind-kit/`).
- `data/plugins/daily-life/plugin.yaml` exists at that path.
- `pytest` and `pytest-asyncio` are available for running the 134-test suite under
  `data/plugins/daily-life/tests/`.
- No external network calls are made at import time; OAuth tokens are read lazily.

## 1. Resolve the plugin

```bash
DAILY="${HERMES_HOME:-$HOME/.hermes}/plugins/daily-life"
ls "$DAILY/plugin.yaml" || echo "plugin missing — check $DAILY"
echo "Active profile: ${HERMES_PROFILE:-default}"
```

All relative paths below are relative to `$DAILY`. The SQLite database for the *active*
profile lands at `$HERMES_HOME/profiles/<profile>/daily-life/daily_life.db` — never share
one across profiles.

## 2. Register the boot hook (one-time)

```bash
mkdir -p "$DAILY/hooks/daily-life-boot"
cat > "$DAILY/hooks/daily-life-boot/HOOK.yaml" <<EOF
name: daily-life-boot
events: [gateway:startup]
handler: handler.py
on_failure: log_and_continue
EOF
```

`handler.py` calls `run_startup_catchup()` which:
- Skips if the daily cycle is already committed today (idempotent).
- Decays brain fatigue by 6 h on every startup (point 2).
- Only runs the full cycle after the configured `planning_hour`.

## 3. Daily operations

### Slash command `/daily`

| Sub-command | Effect | Admin-only? |
|---|---|---|
| `status` | Renders `format_daily_status_summary()` markdown | no |
| `trigger` | Runs `run_daily_cycle(trigger_source="slash")` now | yes (`/daily trigger`) |
| `replan` | Re-runs `build_plan` and refreshes the Kanban dispatch | yes (`/daily replan`) |
| `review` | Runs `run_daily_review` + `format_eod_review_markdown` | no |
| `trigger --force` | Bypasses idempotency check | yes |

Role gating is implemented in `core/partnership.py::gate_admin_slash_commands` (point 84).

### Dashboard tab

FastAPI endpoints under `dashboard/plugin_api.py`:

```
GET  /daily-life/status              # plan + brain + telemetry overview
GET  /daily-life/insights            # two-tier learned insights
GET  /daily-life/opportunities       # repetitive workflows to automate
GET  /daily-life/briefing            # cached or fresh briefing
POST /daily-life/trigger             # kick off a cycle
POST /daily-life/replan              # re-plan from the current brain state
GET  /daily-life/review              # today's EOD report
```

The dashboard is **read-mostly** — writes go through the slash commands or the plugin's
tool surface.

## 4. The 13-step lifecycle (what actually happens)

```
1 Trigger           gateway:startup hook + cron grace-180m (point 4)
2 Idempotency       is_cycle_completed(pid, today) — skip if already done
3 Brain gate        evaluate_decision() — VETO silently aborts the cycle
4 Briefing          gather_briefing() — 36 h freshness, dedup, clickbait filter
5 Opportunity       scan_opportunities() — N>=3 detection of repetition
6 Calibration       get_calibrated_task_duration() — outlier-filtered (point 71)
7 Capacity          build_plan() — weekend/capacity ceiling (point 28)
8 Routing           route_task_execution_target() — pick connecting-dot adapter
9 Time allocation   calendar_adapter.allocate_focus_block()
10 Kanban commit    kanban_adapter.create_task(goal=True, completion_contract=...)
11 Telemetry        record_task_telemetry on completion
12 Dynamic replan   handle_replan() — mid-day re-allocation
13 EOD review       run_daily_review() + format_eod_review_markdown()
```

Every step is tested in `tests/unit/test_lifecycle.py` (8 tests) and the 5 hardening
batches (64 more tests).

## 5. 100-point hardening — quick reference

Full spec: `.plan/daily-life-100-hardening-plan.md`. All 100 points are implemented and
covered by 134 tests. Subsystem → point range:

| # | Subsystem | Points | File |
|---|---|---|---|
| 1 | Cognitive Recovery | 1–10 | `core/adapters/brain.py`, `core/review.py` |
| 2 | Smart Ingestion | 11–20 | `core/briefing.py` |
| 3 | Adaptive Planning | 21–30 | `core/planner.py` |
| 4 | Autonomous Browser | 31–40 | `core/adapters/camoufox.py` |
| 5 | External Comms | 41–50 | `core/adapters/gmail.py`, `core/adapters/tebar.py` |
| 6 | Two-Way Calendar | 51–60 | `core/adapters/calendar.py` |
| 7 | Kanban Lifecycle | 61–70 | `core/adapters/kanban.py` |
| 8 | Statistical Learning | 71–80 | `core/optimizer.py` |
| 9 | Human Partnership | 81–90 | `core/opportunity.py`, `core/partnership.py` |
| 10 | System Hardening | 91–100 | `db.py`, `config.py`, `core/prompt.py` |

Each point is documented by a `Point N` docstring next to its function; the audit script
counts 100/100 coverage.

## 6. Multi-profile operation

- Each profile owns its own `daily_life.db` + `.daily_life.lock` under
  `$HERMES_HOME/profiles/<profile>/daily-life/`.
- Settings are cached per-profile by `config.get_settings(profile_id)` — never use the
  legacy singleton.
- The atomic file lock in `core/lifecycle.py` uses `msvcrt` on Windows, `fcntl` on
  POSIX. Stale locks (>10 min) auto-release on next `acquire()`.

## 7. Verification (run this before claiming done)

```bash
cd "$DAILY"
python -m pytest -q            # expect: 134 passed
python -m pytest tests/unit/test_multiprofile_ecosystem.py -q   # 12 tests, profile isolation
python -m pytest tests/unit/test_batch_s[1-9]_*.py -q          # 65 hardening tests
```

## 8. Troubleshooting

- **`LockAcquisitionError`** → another process is mid-cycle or a stale lock wasn't
  released; wait 10 min or delete `.daily_life.lock`.
- **`DecisionGateVetoError`** → Brain VETO; check `MIND_TZ`, quiet-hours window, and
  fatigue state.
- **`Status: low_disk_space`** → `check_disk_space_healthy` guard fired; free 500 MB
  or the cycle will keep returning silently.
- **Dashboard shows `[SILENT]` everywhere** → cycle ran but everything was VETO/empty;
  inspect `daily_reviews` table.

## Pairs with

- `biomimetic-brain/skills/brain-decision-gate/` — how to run the brain gate directly
- `gmail/skills/gmail-ops/` & `google-calendar/skills/gcal-ops/` — credential flows
- `camoufox/skills/camoufox-ops/` — the target of CAMOUFOX_SCENARIO routing
- `calendar-hermes/skills/calendar-hermes-ops/` — the target of time-allocation
- `tebar/skills/tebar-ops/` — the target of TEBAR_PUBLISH routing
