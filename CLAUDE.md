# Vertical Sync

Trail running training tool, three layers:

| Layer | Role | Where | LLM? |
|-------|------|-------|------|
| **Sense** | Deterministic FIT analysis, JSON output | CLI `vs` + `config/athlete.toml` | no |
| **Reason** | Interpretation, plans, debriefs, decisions | LLM via `.claude/skills/` | yes |
| **Remember** | Versioned markdown training memory | `coach/` | — |

The CLI **computes** (metrics, plan comparison, `analysis.py` flags). The LLM
**interprets and decides**. The CLI never calls an LLM; `analysis.py`
observations are *inputs* for the LLM, not final verdicts.

This repo is a **public template**: all personal data (the athlete profile, the
plan, the journal) is gitignored and rebuilt from `templates/`. The concrete race
goal lives in `config/athlete.toml` (`[race]`), not in this file.

## CLI (`vs`)

Entry point: `vs` (installed via `uv sync`, defined in `pyproject.toml` as `vertical_sync.cli:cli`).

All analysis commands support `--json` for structured AI-readable output.

| Command | Purpose |
|---------|---------|
| `vs list [--json]` | List available FIT files |
| `vs analyze <date> [--json]` | Analyze a single activity (date YYYYMMDD or file path) |
| `vs profile <date> [--json]` | Pace + HR profile by gradient bucket for one activity |
| `vs week --start X --end Y [--json]` | Weekly analysis with summary + plan comparison |
| `vs assess [--start X --end Y] [--json]` | Strengths/weaknesses assessment |
| `vs plan [--week N] [--json]` | Training plan targets (read from `coach/plan/*.md`) |
| `vs download --start X --end Y [--source coros\|garmin]` | Download FIT files from Coros or Garmin |
| `vs login [--source coros\|garmin]` | Test Coros / Garmin connection |
| `vs pdf` | Generate training plan PDF |

## Architecture

```
src/vertical_sync/
├── config.py       # Loads athlete.toml + coach/plan frontmatter, paths, constants
├── fit_parser.py   # FIT parsing, metric extraction, weekly summary
├── analysis.py     # Strengths/weaknesses assessment (activity + week level)
└── cli.py          # Click CLI commands + output formatting
```

- **config.py**: loads the physiological profile + HR zones + race target from
  `config/athlete.toml` (falls back to `athlete.example.toml` on a fresh clone),
  and the weekly plan targets from `coach/plan/*.md` TOML frontmatter. No
  hardcoded personal data.
- **fit_parser.py**: `parse_fit()`, `analyze_activity()`, `compute_week_summary()`, `find_fit_files()`
- **analysis.py**: `assess_activity()` and `assess_week()` return typed observations (strength/weakness/info)
- **cli.py**: Click commands, human + JSON output modes

## Coach memory & skills

`coach/` is the **Remember** layer (gitignored personal data; schema documented
in `docs/coach-memory.md`, public templates in `templates/`):

```
coach/
├── athlete.md          # persistent profile: strengths, weaknesses, injuries, gear, reference perfs
├── plan/YYYY-MM-DD.md  # one week each: TOML frontmatter targets (CLI reads these) + prose
├── journal/YYYY-race.md # dated log, newest on top, one file per block
└── races/<race>.md     # race-day cheatsheet + post-race debrief
```

Two skills orchestrate the loop (`.claude/skills/`):

- **`/debrief`** — run the CLI for numbers, interpret, write a dated journal
  entry, promote durable facts to `athlete.md` / `config/athlete.toml`.
- **`/plan-block`** — build or adjust the weekly plan files toward the race goal,
  reading the profile + journal.

Boundaries: deterministic numbers (HR zones, threshold pace, race target) →
`config/athlete.toml`. Plan targets → week-file frontmatter (single source of
truth). Interpretation and prose → the LLM and the markdown bodies.

## Adding Features

1. New **metric**: add extraction in `fit_parser.py:analyze_activity()`, it flows to JSON automatically
2. New **assessment criterion**: add `obs.append(...)` in `analysis.py`
3. New **CLI command**: add `@cli.command()` in `cli.py`

## Data Source

Training data comes from **Coros Training Hub** or **Garmin Connect**
(`--source garmin`). Both use reverse-engineered endpoints — there is no
official consumer API for either.

Key libraries:
- **coros_data_extractor** (Python) - structured activity data with pydantic models
- **garminconnect** (Python) - Garmin Connect mobile API wrapper (tokens cached in `~/.garminconnect`)
- **fitdecode** - .FIT file parsing
- **click** - CLI framework

## Stack

- Python 3.12+, uv
- FIT file analysis (fitdecode + pandas)
- Coros unofficial API (coros_data_extractor)
- Garmin unofficial API (garminconnect)
- CLI: click
