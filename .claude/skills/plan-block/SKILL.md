---
name: plan-block
description: Build or adjust a multi-week trail training block toward a goal race. Use when the user wants to create a training plan, plan a block, restructure the weeks toward a race, or adapt the plan after an injury or a missed week. Writes one markdown file per week under coach/plan/.
---

# Plan-block — build or adjust a training block

You are the **Reason** layer. You produce the forward-looking plan; the CLI `vs`
reads only the numeric targets you set in each week's frontmatter. Read
`docs/coach-memory.md` for the full schema.

## Steps

1. **Get context.**
   - Race target: `[race]` in `config/athlete.toml` (date, distance, D+).
   - Physiology: `[profile]` and `[[hr_zones]]` in `config/athlete.toml`.
   - The runner: `coach/athlete.md` (strengths, weaknesses, injury history, gear).
   - Recent state: top of `coach/journal/YYYY-<race>.md`.
   - Existing block, if adjusting: the files already in `coach/plan/`.

2. **Design the block.** Decide number of weeks, phases (Adaptation / Build /
   Specific / Recovery / Taper), and progression. Bias the plan toward the
   athlete's reality: respect injury watch-points, don't stack the two hard
   sessions (hilly + quality) on back-to-back days, place the long run on the
   weekend, ramp load sensibly, end with a taper.
   - **Recurring routines.** If `coach/athlete.md` has a "Routines récurrentes"
     section (e.g. a weekly strength/tendon routine), schedule each one in
     *every* week's session table at its stated frequency and placement rules
     (e.g. same day as a hard session, never the eve of a quality day). Drop or
     halve them only in race week, and say so in the prose.

3. **Write one file per week.** `coach/plan/YYYY-MM-DD.md` named by the Monday of
   the week (template: `templates/plan-week.md`). Each file is:
   - **TOML frontmatter** between `+++` fences — the targets the CLI compares
     against: `week`, `start`, `end` (YYYYMMDD), `phase`, `target_hours`,
     `target_dplus`, `target_sessions`. These must be accurate; the CLI reads
     only these.
   - **Markdown body** — the session table (day, type, duration, D+, intensity)
     and any notes, in prose, for the human and for `/debrief`.
   - Update `coach/plan/README.md` for block-level overview and recurring advice.

4. **Verify.** Run `vs plan` (and `vs plan --week N`) to confirm the CLI parses
   every week's frontmatter and the totals look right.

## Rules

- One source of truth: targets live in the frontmatter, nowhere else. Don't
  duplicate them into prose where they can drift.
- When adjusting after an injury or a missed week, reduce load rather than
  cramming it forward; note the change reasoning in the week's prose and add a
  journal entry via `/debrief` if appropriate.
- Keep prose free-form and human — it's read by a person on a training day.
