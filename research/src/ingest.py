#!/usr/bin/env python3
"""
src/ingest.py — RobotEvents API ingestion for the VEX schedule-strength research project.

Pulls raw JSON for one season's Worlds (HS V5RC by default) plus the pre-Worlds event
participation needed to compute pre-Worlds team strength. Output is unmodified API
JSON, persisted to disk for reproducibility. A coverage report applies the canonical
three-gate quality check that decides whether the season makes the panel.

DESIGN PRINCIPLES
-----------------
1. Raw API responses are persisted verbatim. No munging during ingestion — that's the
   job of src/clean.py. This makes ingestion deterministic and the analysis fully
   reproducible from the raw drop.
2. Network calls are idempotent: re-running the script overwrites existing JSON, but
   skips entire endpoints if --resume is set and the file already exists.
3. Rate limiting matches the existing visualizer pipeline (0.3s between paginated
   requests, exponential backoff on 429).
4. Pre-Worlds event aggregation is deduplicated: many Worlds teams attend the same
   regional events, so unique events are fetched once and cached.

USAGE
-----
    # Single-season run (token via env var)
    ROBOTEVENTS_TOKEN=xxx python -m src.ingest \\
        --season 197 \\
        --worlds-event-id 54321 \\
        --output data/raw/2026_worlds_hs

    # Token via file (research workflow convenience)
    python -m src.ingest \\
        --season 197 \\
        --token-file "../Robot events token.txt" \\
        --output data/raw/2026_worlds_hs

    # Auto-detect Worlds event ID from season
    python -m src.ingest \\
        --season 197 \\
        --output data/raw/2026_worlds_hs

    # Skip the pre-Worlds aggregation (faster smoke test)
    python -m src.ingest --season 197 --worlds-event-id 54321 \\
        --output data/raw/2026_worlds_hs --skip-preworlds

    # Resume an interrupted run (skip endpoints whose JSON already exists)
    python -m src.ingest --season 197 --worlds-event-id 54321 \\
        --output data/raw/2026_worlds_hs --resume

OUTPUTS (all in --output directory)
-----------------------------------
    teams.json                 Raw /events/{id}/teams (flattened across pages)
    rankings.json              Raw /events/{id}/rankings (includes SP, WP, AP)
    skills.json                Raw /events/{id}/skills
    matches.json               Raw /events/{id}/matches (alliance composition, scores)
    awards.json                Raw /events/{id}/awards
    preworlds_team_events.json team_number -> list of pre-Worlds event IDs
    preworlds_rankings.json    event_id -> raw /events/{id}/rankings (cached)
    preworlds_skills.json      event_id -> raw /events/{id}/skills (cached)
    season_info.json           Metadata: season name, Worlds event, dates, parameters
    coverage.md                Three-gate quality report
    ingest_log.txt             Timestamped run log
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "https://www.robotevents.com/api/v2"
DEFAULT_PROGRAM_ID = 1  # 1 = V5RC (high school)
DEFAULT_PER_PAGE = 250
DEFAULT_RATE_LIMIT_SLEEP = 0.3  # seconds between paginated requests
DEFAULT_MAX_RETRIES = 4
DEFAULT_TIMEOUT_S = 30
USER_AGENT = "VEX-Research/0.1 (schedule-strength study; contact: arjneel@gmail.com)"

# Quality-gate thresholds (from Task #5; referenced by Tasks #7, #8, #9)
GATE_MATCH_COVERAGE_MIN = 0.95
GATE_SP_COVERAGE_MIN = 0.95
GATE_PREWORLDS_COVERAGE_MIN = 0.80
GATE_DIVISION_COVERAGE_MIN = 1.00  # must be 100%

# Known season IDs for the target panel.
# 197 (Push Back) is the only one confirmed from the existing pipeline.
# Other seasons are auto-discovered at runtime via /seasons?program[]=1.
KNOWN_SEASON_IDS = {
    197: "Push Back",  # 2025-26
    # 191: "High Stakes",   # 2024-25 — placeholder; verify via /seasons
    # 181: "Over Under",    # 2023-24 — placeholder
    # 173: "Spin Up",       # 2022-23 — placeholder
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(output_dir: Path, verbose: bool = False) -> logging.Logger:
    """Configure stdout + file logging in the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "ingest_log.txt"

    logger = logging.getLogger("vex_ingest")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()  # idempotent re-runs

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S UTC",
    )
    # Force UTC timestamps
    logging.Formatter.converter = time.gmtime

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def resolve_token(token_file: Optional[Path] = None) -> str:
    """
    Resolve API bearer token. Priority:
      1. ROBOTEVENTS_TOKEN env var
      2. --token-file argument (if provided)
      3. Token file at ./Robot events token.txt or ../Robot events token.txt
    Raises ValueError if no usable token is found.
    """
    token = os.environ.get("ROBOTEVENTS_TOKEN", "").strip()
    if token:
        return token

    candidates: list[Path] = []
    if token_file is not None:
        candidates.append(token_file)
    candidates.extend([
        Path("Robot events token.txt"),
        Path("../Robot events token.txt"),
        Path("../../Robot events token.txt"),
    ])

    for p in candidates:
        try:
            if p.exists() and p.stat().st_size > 0:
                tok = p.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except Exception:
            continue

    raise ValueError(
        "No RobotEvents API token found. Set ROBOTEVENTS_TOKEN env var or "
        "pass --token-file pointing to a file containing the bearer token."
    )


# ---------------------------------------------------------------------------
# API client (paginated GET with retry/backoff)
# ---------------------------------------------------------------------------

@dataclass
class ApiClient:
    """Stateful wrapper around the RobotEvents API with retries and logging."""
    token: str
    logger: logging.Logger
    base_url: str = API_BASE
    per_page: int = DEFAULT_PER_PAGE
    rate_limit_sleep: float = DEFAULT_RATE_LIMIT_SLEEP
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_s: int = DEFAULT_TIMEOUT_S
    _session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self._session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })

    # -- single GET with retry ------------------------------------------------
    def _get(self, url: str, params: Optional[dict] = None) -> dict:
        """Single GET with retry. Returns parsed JSON dict."""
        retries = 0
        while True:
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout_s)
            except requests.RequestException as e:
                retries += 1
                if retries > self.max_retries:
                    raise
                wait = 2 ** retries
                self.logger.warning(f"  Connection error: {e}; retry {retries} in {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retries += 1
                wait = min(60 * retries, 300)
                self.logger.warning(f"  Rate-limited; sleeping {wait}s (retry {retries})")
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Auth error {resp.status_code} from {url}. Token invalid or insufficient scope."
                )

            if resp.status_code == 404:
                self.logger.info(f"  404 from {url} — treating as empty")
                return {"data": [], "meta": {"last_page": 1}}

            if 500 <= resp.status_code < 600:
                retries += 1
                if retries > self.max_retries:
                    resp.raise_for_status()
                wait = 2 ** retries
                self.logger.warning(
                    f"  Server error {resp.status_code} from {url}; retry {retries} in {wait}s"
                )
                time.sleep(wait)
                continue

            # other 4xx
            resp.raise_for_status()
            return {"data": []}  # unreachable

    # -- paginated collection -------------------------------------------------
    def get_all(self, endpoint: str, params: Optional[dict] = None,
                max_pages: int = 100) -> list[dict]:
        """
        GET a paginated endpoint and return the concatenated list of `data` items
        across all pages. Endpoints like /events/{id}/rankings return objects shaped
        as {"data": [...], "meta": {...}}.
        """
        url = f"{self.base_url}{endpoint}"
        all_items: list[dict] = []
        page = 1
        local_params = dict(params or {})
        local_params["per_page"] = self.per_page

        while page <= max_pages:
            local_params["page"] = page
            body = self._get(url, params=local_params)
            page_data = body.get("data", []) if isinstance(body, dict) else []
            all_items.extend(page_data)

            meta = body.get("meta", {}) if isinstance(body, dict) else {}
            last_page = meta.get("last_page", 1)
            if page >= last_page:
                break
            page += 1
            time.sleep(self.rate_limit_sleep)

        return all_items

    # -- single object --------------------------------------------------------
    def get_one(self, endpoint: str) -> dict:
        """GET a single object endpoint like /seasons/{id} or /events/{id}."""
        url = f"{self.base_url}{endpoint}"
        return self._get(url)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def discover_seasons(client: ApiClient, program_id: int = DEFAULT_PROGRAM_ID) -> list[dict]:
    """Return all seasons for a program, ordered by start year descending."""
    seasons = client.get_all("/seasons", params={"program[]": program_id})
    seasons.sort(key=lambda s: (s.get("years_start") or 0), reverse=True)
    return seasons


def find_worlds_event(client: ApiClient, season_id: int,
                      grade_level: str = "high") -> Optional[dict]:
    """
    Find the HS or MS V5RC Worlds event for a given season.
    grade_level: "high" (default) or "middle".
    Strategy mirrors update_data.py.find_worlds_event() with grade-level filter.
    """
    events = client.get_all(f"/seasons/{season_id}/events")
    grade_token = "middle" if grade_level == "middle" else "high"
    # Priority 1: level=World + grade match + championship in name
    for e in events:
        name = (e.get("name") or "").lower()
        level = (e.get("level") or "").lower()
        if level == "world" and grade_token in name and "championship" in name:
            return e
    # Priority 2: level=World + grade match (any)
    for e in events:
        name = (e.get("name") or "").lower()
        level = (e.get("level") or "").lower()
        if level == "world" and grade_token in name and "robot contest" not in name:
            return e
    # Priority 3: known SKU pattern (RE-V5RC-{yy}-4xxx is the Worlds SKU family)
    for e in events:
        sku = (e.get("sku") or "")
        level = (e.get("level") or "").lower()
        if level == "world" and sku.startswith("RE-V5RC") and grade_token in (e.get("name") or "").lower():
            return e
    return None


# ---------------------------------------------------------------------------
# Worlds-event fetchers (one call each, fully paginated)
# ---------------------------------------------------------------------------

def fetch_event_teams(client: ApiClient, event_id: int) -> list[dict]:
    client.logger.info(f"Fetching teams for event {event_id}...")
    items = client.get_all(f"/events/{event_id}/teams")
    client.logger.info(f"  {len(items)} teams")
    return items


def fetch_event_rankings(client: ApiClient, event_id: int) -> list[dict]:
    client.logger.info(f"Fetching rankings for event {event_id}...")
    items = client.get_all(f"/events/{event_id}/rankings")
    client.logger.info(f"  {len(items)} ranking rows")
    return items


def fetch_event_skills(client: ApiClient, event_id: int) -> list[dict]:
    client.logger.info(f"Fetching skills for event {event_id}...")
    items = client.get_all(f"/events/{event_id}/skills")
    client.logger.info(f"  {len(items)} skills entries")
    return items


def fetch_event_matches(client: ApiClient, event_id: int) -> list[dict]:
    client.logger.info(f"Fetching matches for event {event_id}...")
    items = client.get_all(f"/events/{event_id}/matches")
    client.logger.info(f"  {len(items)} matches")
    return items


def fetch_event_awards(client: ApiClient, event_id: int) -> list[dict]:
    client.logger.info(f"Fetching awards for event {event_id}...")
    items = client.get_all(f"/events/{event_id}/awards")
    client.logger.info(f"  {len(items)} awards")
    return items


# ---------------------------------------------------------------------------
# Pre-Worlds aggregation
# ---------------------------------------------------------------------------

def _team_id_from_team_object(t: dict) -> Optional[int]:
    """Extract the internal RobotEvents team ID from a team-shaped object."""
    if not isinstance(t, dict):
        return None
    tid = t.get("id")
    return int(tid) if tid else None


def fetch_team_pre_worlds_events(client: ApiClient, team_id: int, season_id: int,
                                  worlds_start: str) -> list[dict]:
    """
    Return events that this team attended during the season that ended BEFORE worlds_start.
    worlds_start: ISO date string ('YYYY-MM-DD').
    """
    events = client.get_all(
        f"/teams/{team_id}/events",
        params={"season[]": season_id},
    )
    cutoff = worlds_start[:10]
    pre = []
    for e in events:
        end = (e.get("end") or "")[:10]
        if end and end < cutoff:
            pre.append(e)
    return pre


def aggregate_pre_worlds(client: ApiClient, worlds_teams: list[dict],
                         season_id: int, worlds_start: str) -> dict[str, Any]:
    """
    For each Worlds team, find the events they attended pre-Worlds. Then collect
    rankings + skills for each unique pre-Worlds event (deduplicated cache).

    Returns:
        {
          "team_events": { team_number: [event_id, ...] },
          "rankings_by_event": { event_id: [raw ranking rows] },
          "skills_by_event": { event_id: [raw skills rows] },
          "events_metadata": { event_id: {raw event object} },
        }
    """
    client.logger.info(f"Aggregating pre-Worlds events for {len(worlds_teams)} teams...")
    team_events: dict[str, list[int]] = {}
    all_event_ids: set[int] = set()
    events_metadata: dict[int, dict] = {}

    # Phase 1: per-team event list
    for idx, t in enumerate(worlds_teams):
        team_number = t.get("number", "")
        team_id = _team_id_from_team_object(t)
        if not team_id or not team_number:
            continue
        if idx % 100 == 0:
            client.logger.info(f"  Team event list {idx + 1}/{len(worlds_teams)}...")
        pre = fetch_team_pre_worlds_events(client, team_id, season_id, worlds_start)
        team_events[team_number] = [e["id"] for e in pre if e.get("id")]
        for e in pre:
            eid = e.get("id")
            if eid and eid not in events_metadata:
                events_metadata[eid] = e
                all_event_ids.add(eid)
        time.sleep(client.rate_limit_sleep)

    client.logger.info(
        f"  Discovered {len(all_event_ids)} unique pre-Worlds events "
        f"across {len(team_events)} teams"
    )

    # Phase 2: rankings + skills for each unique event (cached)
    rankings_by_event: dict[int, list[dict]] = {}
    skills_by_event: dict[int, list[dict]] = {}

    for idx, eid in enumerate(sorted(all_event_ids)):
        if idx % 25 == 0:
            client.logger.info(
                f"  Pre-Worlds event {idx + 1}/{len(all_event_ids)}..."
            )
        try:
            rankings_by_event[eid] = client.get_all(f"/events/{eid}/rankings")
        except Exception as e:
            client.logger.warning(f"  Rankings failed for event {eid}: {e}")
            rankings_by_event[eid] = []
        try:
            skills_by_event[eid] = client.get_all(f"/events/{eid}/skills")
        except Exception as e:
            client.logger.warning(f"  Skills failed for event {eid}: {e}")
            skills_by_event[eid] = []
        time.sleep(client.rate_limit_sleep)

    return {
        "team_events": team_events,
        "rankings_by_event": {str(k): v for k, v in rankings_by_event.items()},
        "skills_by_event": {str(k): v for k, v in skills_by_event.items()},
        "events_metadata": {str(k): v for k, v in events_metadata.items()},
    }


# ---------------------------------------------------------------------------
# Coverage / quality gate
# ---------------------------------------------------------------------------

def compute_coverage_report(output_dir: Path, season_label: str,
                            preworlds_run: bool) -> dict[str, Any]:
    """
    Apply the canonical three-gate quality check and write coverage.md.

    Gates:
      (1) Match coverage: >= 95% of qual matches have complete alliance composition
          AND both alliance scores; SP populated for >= 95% of teams in rankings.
      (2) Pre-Worlds coverage: >= 80% of Worlds teams have >= 2 pre-Worlds events
          with non-null OPR AND non-null CCWM.
      (3) Division coverage: every Worlds team is assigned to a division in the API.
    """
    metrics: dict[str, Any] = {"season_label": season_label}

    teams = _load_json(output_dir / "teams.json")
    rankings = _load_json(output_dir / "rankings.json")
    matches = _load_json(output_dir / "matches.json")

    team_numbers = [t.get("number") for t in teams if t.get("number")]
    metrics["n_teams"] = len(team_numbers)

    # Gate 1a: match coverage
    qual_matches = [m for m in matches if (m.get("round") or 0) <= 2]
    complete_quals = 0
    for m in qual_matches:
        alliances = m.get("alliances") or []
        if len(alliances) < 2:
            continue
        team_count = sum(len(a.get("teams") or []) for a in alliances)
        scores_present = all(("score" in a and a["score"] is not None) for a in alliances)
        if team_count >= 4 and scores_present:
            complete_quals += 1
    metrics["n_qual_matches"] = len(qual_matches)
    metrics["match_coverage_pct"] = (
        complete_quals / len(qual_matches) if qual_matches else 0.0
    )
    metrics["gate1a_match_coverage"] = (
        metrics["match_coverage_pct"] >= GATE_MATCH_COVERAGE_MIN
    )

    # Gate 1b: SP coverage
    sp_present = sum(1 for r in rankings if r.get("sp") is not None)
    metrics["sp_coverage_pct"] = (
        sp_present / len(rankings) if rankings else 0.0
    )
    metrics["gate1b_sp_coverage"] = (
        metrics["sp_coverage_pct"] >= GATE_SP_COVERAGE_MIN
    )

    # Gate 3: division coverage
    teams_with_div = sum(
        1 for r in rankings
        if (r.get("division") or {}).get("id") is not None
    )
    unique_team_nums_in_rankings = {r.get("team", {}).get("number") for r in rankings}
    unique_team_nums_in_rankings.discard(None)
    metrics["division_coverage_pct"] = (
        teams_with_div / len(rankings) if rankings else 0.0
    )
    metrics["gate3_division_coverage"] = (
        metrics["division_coverage_pct"] >= GATE_DIVISION_COVERAGE_MIN
    )

    # Gate 2: pre-Worlds coverage
    if preworlds_run:
        pw_rankings_by_event = _load_json(output_dir / "preworlds_rankings.json")
        pw_team_events = _load_json(output_dir / "preworlds_team_events.json")
        # Count teams meeting "2+ events with non-null OPR and CCWM"
        teams_with_strength: set[str] = set()
        for team_number, event_ids in (pw_team_events or {}).items():
            n_good = 0
            for eid in event_ids:
                event_rankings = pw_rankings_by_event.get(str(eid)) or []
                # Did this team appear in this event's rankings with valid OPR/CCWM?
                for r in event_rankings:
                    if (r.get("team") or {}).get("number") != team_number:
                        continue
                    if r.get("opr") is not None and r.get("ccwm") is not None:
                        n_good += 1
                        break
            if n_good >= 2:
                teams_with_strength.add(team_number)
        metrics["preworlds_coverage_pct"] = (
            len(teams_with_strength) / metrics["n_teams"]
            if metrics["n_teams"] else 0.0
        )
        metrics["gate2_preworlds_coverage"] = (
            metrics["preworlds_coverage_pct"] >= GATE_PREWORLDS_COVERAGE_MIN
        )
    else:
        metrics["preworlds_coverage_pct"] = None
        metrics["gate2_preworlds_coverage"] = None

    # Verdict
    gate1 = bool(metrics["gate1a_match_coverage"] and metrics["gate1b_sp_coverage"])
    gate3 = bool(metrics["gate3_division_coverage"])
    if not (gate1 and gate3):
        verdict = "DROP from panel — failed gate (1) or (3)"
    elif metrics["gate2_preworlds_coverage"] is False:
        verdict = "KEEP with flagged uncertainty — gate (2) failed"
    elif metrics["gate2_preworlds_coverage"] is None:
        verdict = "INCOMPLETE — pre-Worlds aggregation was skipped"
    else:
        verdict = "INCLUDE in panel — all gates passed"
    metrics["verdict"] = verdict

    # Write the markdown report
    md = _format_coverage_md(metrics)
    (output_dir / "coverage.md").write_text(md, encoding="utf-8")

    return metrics


def _format_coverage_md(m: dict) -> str:
    """Render the coverage report as markdown."""
    def pct(x: Optional[float]) -> str:
        return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "—"

    def yn(x: Optional[bool]) -> str:
        if x is True:
            return "PASS"
        if x is False:
            return "FAIL"
        return "N/A"

    lines = [
        f"# Data Quality Report — {m['season_label']}",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Summary",
        "",
        f"- Teams at Worlds: **{m['n_teams']}**",
        f"- Qualification matches: **{m['n_qual_matches']}**",
        "",
        "## Quality Gates",
        "",
        "| Gate | Metric | Threshold | Observed | Verdict |",
        "|------|--------|-----------|----------|---------|",
        f"| 1a | Match coverage (alliance + score complete) | "
        f"≥ {GATE_MATCH_COVERAGE_MIN:.0%} | {pct(m['match_coverage_pct'])} | "
        f"{yn(m['gate1a_match_coverage'])} |",
        f"| 1b | SP populated in rankings | "
        f"≥ {GATE_SP_COVERAGE_MIN:.0%} | {pct(m['sp_coverage_pct'])} | "
        f"{yn(m['gate1b_sp_coverage'])} |",
        f"| 2  | Pre-Worlds coverage (≥2 events w/ OPR & CCWM) | "
        f"≥ {GATE_PREWORLDS_COVERAGE_MIN:.0%} | {pct(m['preworlds_coverage_pct'])} | "
        f"{yn(m['gate2_preworlds_coverage'])} |",
        f"| 3  | Division assignment | "
        f"= {GATE_DIVISION_COVERAGE_MIN:.0%} | {pct(m['division_coverage_pct'])} | "
        f"{yn(m['gate3_division_coverage'])} |",
        "",
        f"## Verdict",
        "",
        f"**{m['verdict']}**",
        "",
        "Gates (1) and (3) are hard requirements. Gate (2) is soft: failure means the "
        "season is kept but its schedule-strength estimates carry flagged uncertainty "
        "(reported in Limitations).",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _skip_if_resuming(path: Path, resume: bool, logger: logging.Logger) -> bool:
    """Return True if this endpoint should be skipped (file exists + resume=True)."""
    if resume and path.exists() and path.stat().st_size > 0:
        logger.info(f"  --resume: skipping {path.name} (already present)")
        return True
    return False


def ingest_worlds(client: ApiClient, season_id: int,
                  worlds_event_id: Optional[int],
                  output_dir: Path, grade_level: str = "high",
                  skip_preworlds: bool = False, resume: bool = False) -> dict:
    """
    Top-level orchestration. Returns the coverage metrics dict.
    """
    logger = client.logger
    output_dir.mkdir(parents=True, exist_ok=True)

    # -- Resolve season + Worlds event ----------------------------------------
    logger.info(f"=== Ingest run: season {season_id}, grade {grade_level} ===")
    season_meta = client.get_one(f"/seasons/{season_id}")
    season_name = season_meta.get("name", f"Season {season_id}")
    logger.info(f"Season: {season_name}")

    if worlds_event_id is None:
        logger.info("Auto-detecting Worlds event...")
        worlds = find_worlds_event(client, season_id, grade_level=grade_level)
        if not worlds:
            raise RuntimeError(
                f"Could not auto-detect Worlds event for season {season_id}. "
                f"Pass --worlds-event-id explicitly."
            )
        worlds_event_id = worlds["id"]
        logger.info(f"  Detected: {worlds.get('name')} (id={worlds_event_id})")
        worlds_event = worlds
    else:
        worlds_event = client.get_one(f"/events/{worlds_event_id}")
        logger.info(f"Using event: {worlds_event.get('name')} (id={worlds_event_id})")

    worlds_start = worlds_event.get("start") or ""
    if not worlds_start:
        raise RuntimeError(f"Worlds event {worlds_event_id} has no start date.")

    # Persist run metadata
    season_info = {
        "season_id": season_id,
        "season_name": season_name,
        "worlds_event_id": worlds_event_id,
        "worlds_event_name": worlds_event.get("name"),
        "worlds_start": worlds_start,
        "worlds_end": worlds_event.get("end"),
        "grade_level": grade_level,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "rate_limit_sleep": client.rate_limit_sleep,
        "per_page": client.per_page,
    }
    _save_json(output_dir / "season_info.json", season_info)

    season_label = f"{season_name} {grade_level.upper()} (season {season_id})"

    # -- Phase A: Worlds-event data -------------------------------------------
    if not _skip_if_resuming(output_dir / "teams.json", resume, logger):
        teams = fetch_event_teams(client, worlds_event_id)
        _save_json(output_dir / "teams.json", teams)
    else:
        teams = _load_json(output_dir / "teams.json") or []

    if not _skip_if_resuming(output_dir / "rankings.json", resume, logger):
        rankings = fetch_event_rankings(client, worlds_event_id)
        _save_json(output_dir / "rankings.json", rankings)

    if not _skip_if_resuming(output_dir / "skills.json", resume, logger):
        skills = fetch_event_skills(client, worlds_event_id)
        _save_json(output_dir / "skills.json", skills)

    if not _skip_if_resuming(output_dir / "matches.json", resume, logger):
        matches = fetch_event_matches(client, worlds_event_id)
        _save_json(output_dir / "matches.json", matches)

    if not _skip_if_resuming(output_dir / "awards.json", resume, logger):
        awards = fetch_event_awards(client, worlds_event_id)
        _save_json(output_dir / "awards.json", awards)

    # -- Phase B: pre-Worlds aggregation --------------------------------------
    if skip_preworlds:
        logger.info("Skipping pre-Worlds aggregation (--skip-preworlds).")
        preworlds_run = False
    else:
        pw_team_events_path = output_dir / "preworlds_team_events.json"
        pw_rankings_path = output_dir / "preworlds_rankings.json"
        pw_skills_path = output_dir / "preworlds_skills.json"
        pw_meta_path = output_dir / "preworlds_events_metadata.json"

        if (resume and all(p.exists() and p.stat().st_size > 0
                           for p in [pw_team_events_path, pw_rankings_path,
                                     pw_skills_path, pw_meta_path])):
            logger.info("  --resume: skipping pre-Worlds aggregation (already present)")
        else:
            agg = aggregate_pre_worlds(client, teams, season_id, worlds_start)
            _save_json(pw_team_events_path, agg["team_events"])
            _save_json(pw_rankings_path, agg["rankings_by_event"])
            _save_json(pw_skills_path, agg["skills_by_event"])
            _save_json(pw_meta_path, agg["events_metadata"])
        preworlds_run = True

    # -- Phase C: quality gate -----------------------------------------------
    logger.info("Computing coverage report...")
    metrics = compute_coverage_report(output_dir, season_label, preworlds_run)
    logger.info(f"Verdict: {metrics['verdict']}")
    return metrics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="src.ingest",
        description="Ingest RobotEvents API data for one season's Worlds "
                    "into a research-friendly raw JSON drop.",
    )
    p.add_argument("--season", type=int, required=True,
                   help="RobotEvents season ID (e.g. 197 for Push Back).")
    p.add_argument("--worlds-event-id", type=int, default=None,
                   help="Worlds event ID. If omitted, auto-detect from season.")
    p.add_argument("--grade", choices=["high", "middle"], default="high",
                   help="Grade level for Worlds event (default: high).")
    p.add_argument("--output", type=Path, required=True,
                   help="Output directory for raw JSON dump.")
    p.add_argument("--token-file", type=Path, default=None,
                   help="Path to file containing the RobotEvents bearer token. "
                        "If omitted, ROBOTEVENTS_TOKEN env var is used.")
    p.add_argument("--skip-preworlds", action="store_true",
                   help="Skip the per-team pre-Worlds event aggregation (faster smoke test).")
    p.add_argument("--resume", action="store_true",
                   help="Skip endpoints whose output JSON already exists.")
    p.add_argument("--rate-limit-sleep", type=float, default=DEFAULT_RATE_LIMIT_SLEEP,
                   help=f"Seconds between paginated requests (default: {DEFAULT_RATE_LIMIT_SLEEP}).")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose (DEBUG) logging.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir: Path = args.output
    logger = setup_logging(output_dir, verbose=args.verbose)

    try:
        token = resolve_token(args.token_file)
    except ValueError as e:
        logger.error(str(e))
        return 2

    client = ApiClient(
        token=token,
        logger=logger,
        rate_limit_sleep=args.rate_limit_sleep,
    )

    try:
        metrics = ingest_worlds(
            client=client,
            season_id=args.season,
            worlds_event_id=args.worlds_event_id,
            output_dir=output_dir,
            grade_level=args.grade,
            skip_preworlds=args.skip_preworlds,
            resume=args.resume,
        )
    except Exception as e:
        logger.exception(f"Ingest failed: {e}")
        return 1

    verdict = metrics["verdict"]
    if verdict.startswith("INCLUDE"):
        return 0
    if verdict.startswith("KEEP") or verdict.startswith("INCOMPLETE"):
        return 0  # not a failure — analyst decides downstream
    return 3  # DROP


if __name__ == "__main__":
    sys.exit(main())
