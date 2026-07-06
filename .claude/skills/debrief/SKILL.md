---
name: debrief
description: Analyze recent trail training and update the coach memory. Use after the user trains, downloads activities, or asks to debrief a session/week, review how training is going, or log what happened. Runs the deterministic CLI for numbers, then interprets and writes the journal.
---

# Debrief — review training and update memory

You are the **Reason** layer. The CLI `vs` is the **Sense** layer (deterministic
metrics, never asks it to judge). `coach/` is the **Remember** layer you maintain.
Read `docs/coach-memory.md` if you need the full schema.

## Steps

1. **Get context.** Read `coach/athlete.md` (persistent profile) and the top of
   the current block's `coach/journal/YYYY-<race>.md` (recent entries, newest on
   top). Identify the active plan week file under `coach/plan/`.

2. **Get the numbers (deterministic).** Run the CLI with `--json` and parse it.
   Do not recompute metrics yourself — trust the CLI.
   - Single activity: `vs analyze <YYYYMMDD> --json` (gradient profile: `vs profile <YYYYMMDD> --json`).
   - A week: `vs week --start <YYYYMMDD> --end <YYYYMMDD> --json` — includes the
     summary **and** the comparison to the plan week's targets.
   - Strengths/weaknesses flags: `vs assess --json`. Treat `analysis.py`
     observations (strength/weakness/info) as **inputs to interpret**, not verdicts.
   - If files are missing, the user may need `vs download --start X --end Y` first.

3. **Interpret.** Compare actuals vs the plan week's targets (hours, D+, sessions),
   factor the athlete profile (e.g. poles → running cadence underestimated; HR drift
   is a strength; watch the known injuries). Say what it means, not just the numbers.

4. **Write the journal entry.** Prepend a dated entry to the top of
   `coach/journal/YYYY-<race>.md` (template: `templates/journal-entry.md`):
   `## YYYY-MM-DD — <short title>` then what happened, the key numbers, what it
   means, and ⚠️ anything to follow up (a pain, a load spike, a decision to revisit).

5. **Promote durable facts.** If something outlives this block (new reference
   performance, resolved/confirmed injury, validated gear, a corrected physiological
   value), update `coach/athlete.md`. If it's a deterministic number the CLI reads
   (HR max/zones, threshold pace, race target), update `config/athlete.toml`.

## Rules

- Never invent metrics — every number comes from the CLI JSON.
- Health claims are non-medical hypotheses; flag them as such and defer to a pro.
- Keep the journal honest: if a week missed targets, write that plainly.
