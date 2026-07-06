# Athlete profile

> **Persistent** memory: what stays true from one training block to the next.
> Copy this file to `coach/athlete.md` (gitignored) and fill it in.
>
> - Deterministic numbers (HR max, zones, threshold pace) live in
>   `config/athlete.toml` — the CLI reads those.
> - The dated chronological log lives in `coach/journal/`.
> - This file is the distilled "who is this runner" the LLM coach reads for
>   context. Update it when a *durable* fact changes (new reference performance,
>   resolved injury, validated gear), not for day-to-day observations.

**Home base:** <town / typical altitude / massif>
**Profile:** <e.g. trail runner, Garmin watch, mountain terrain>

---

## Strengths

- <e.g. cardiac stability — flat or negative HR drift on long runs>
- <e.g. climbing — sustained vertical speed at easy HR>

## Weaknesses / watch points

- <e.g. untested endurance beyond N hours>
- <e.g. tendency to drift into Z3 when fresh → pay for it late in the race>

## Injury history

- <body part — what happened, when, what manages it>

## Validated gear

- <poles, shoes, watch screen layout, anything tested and kept>

## Nutrition / hydration (tested)

- <carbs per hour, fluid per hour, what works>

## Reference performances

| Date | Race | Volume | Time | /km-eff | Avg HR | Asc. | Drift | Note |
|------|------|--------|------|---------|--------|------|-------|------|
| YYYY-MM-DD | <race> | <km / D+> | <h:mm> | <m:ss> | <bpm> | <m/h> | <±%> | <context> |
