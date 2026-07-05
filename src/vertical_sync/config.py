"""Configuration loading for Vertical Sync.

The deterministic CLI reads two kinds of user data from files (never hardcoded):

- ``config/athlete.toml``  — physiological profile, HR zones, race goal.
  Falls back to ``config/athlete.example.toml`` so a fresh clone works
  out of the box. The real file is gitignored (personal data).
- ``coach/plan/*.md``      — the training plan, one markdown file per week.
  Each file carries a ``+++``-delimited TOML frontmatter with the numeric
  targets the CLI compares against; the body is human/LLM-facing prose.
"""

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FIT_DIR = DATA_DIR / "fit"
CONFIG_DIR = PROJECT_ROOT / "config"
COACH_DIR = PROJECT_ROOT / "coach"
PLAN_DIR = COACH_DIR / "plan"
PLAN_README = PLAN_DIR / "README.md"


# ---------------------------------------------------------------------------
# Athlete profile (config/athlete.toml, fallback to the committed example)
# ---------------------------------------------------------------------------

def _load_athlete() -> dict:
    """Load the athlete profile, preferring the real file over the example."""
    real = CONFIG_DIR / "athlete.toml"
    example = CONFIG_DIR / "athlete.example.toml"
    path = real if real.exists() else example
    with open(path, "rb") as f:
        return tomllib.load(f)


_athlete = _load_athlete()
_profile = _athlete.get("profile", {})

HR_MAX = _profile.get("hr_max")
HR_THRESHOLD = _profile.get("hr_threshold")
HR_REST = _profile.get("hr_rest")
THRESHOLD_PACE_S_PER_KM = _profile.get("threshold_pace_s_per_km")

HR_ZONES = _athlete.get("hr_zones", [])
RACE = _athlete.get("race", {})


# ---------------------------------------------------------------------------
# Training plan (coach/plan/*.md with TOML frontmatter)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a ``+++``-delimited TOML frontmatter from a markdown body.

    Returns ``(metadata, body)``. If no frontmatter is present, metadata is
    an empty dict and the whole text is returned as the body.
    """
    if text.startswith("+++"):
        _, raw, body = text.split("+++", 2)
        return tomllib.loads(raw), body.strip()
    return {}, text


def _load_plan_weeks() -> list[dict]:
    """Load weekly plan targets from the frontmatter of coach/plan/*.md."""
    weeks = []
    if PLAN_DIR.exists():
        for p in sorted(PLAN_DIR.glob("*.md")):
            if p.name.lower() == "readme.md":
                continue
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            if "week" in meta:
                weeks.append(meta)
    return sorted(weeks, key=lambda w: w.get("start", 0))


PLAN_WEEKS = _load_plan_weeks()


def get_plan_week(date: int) -> dict | None:
    """Find the plan week containing the given date (YYYYMMDD)."""
    for pw in PLAN_WEEKS:
        if pw["start"] <= date <= pw["end"]:
            return pw
    return None


def load_plan_markdown() -> str:
    """Concatenate the plan into a single markdown document (for PDF export).

    Starts with the general notes (coach/plan/README.md) if present, then each
    weekly file's prose body in chronological order (frontmatter stripped).
    """
    parts = []
    if PLAN_README.exists():
        parts.append(PLAN_README.read_text(encoding="utf-8").strip())
    for pw in _plan_files_sorted():
        _, body = parse_frontmatter(pw.read_text(encoding="utf-8"))
        if body:
            parts.append(body)
    return "\n\n---\n\n".join(parts)


def _plan_files_sorted() -> list[Path]:
    """Weekly plan files (excluding README) sorted by their start date."""
    if not PLAN_DIR.exists():
        return []
    files = [p for p in PLAN_DIR.glob("*.md") if p.name.lower() != "readme.md"]

    def _start(p: Path) -> int:
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        return meta.get("start", 0)

    return sorted(files, key=_start)
