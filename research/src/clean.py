#!/usr/bin/env python3
"""
src/clean.py — Transform raw RobotEvents JSON drops into typed parquet tables.

Reads the output of src/ingest.py for a single season and writes the seven canonical
parquet tables that downstream analysis (features, models, figures) consumes. The
parquet schemas are identical across seasons so that multi-year panels can be built
by simply concatenating the per-season outputs (or globbing with DuckDB).

DESIGN PRINCIPLES
-----------------
1. Idempotent and pure. clean.py reads raw JSON only — it never calls the API. Re-runs
   are safe and produce byte-equivalent parquet.
2. Schemas are declared explicitly. Every output parquet has an enforced pyarrow
   schema; mismatched columns raise on write, not at analysis time.
3. Match data is expanded to long format. One row per (team, match) makes
   schedule-strength feature engineering trivial.
4. Lossy operations are flagged. Anything we discard from the raw JSON is logged so
   nothing is silently dropped.

USAGE
-----
    # Single-season clean
    python -m src.clean \\
        --input data/raw/2026_worlds_hs \\
        --output data/processed/2026_worlds_hs

    # Resume — only re-write tables that don't exist yet
    python -m src.clean --input data/raw/2026_worlds_hs \\
        --output data/processed/2026_worlds_hs --resume

OUTPUTS (all in --output directory)
-----------------------------------
    teams.parquet              One row per Worlds team
    rankings.parquet           One row per (team, event) — Worlds rankings with SP
    skills.parquet             One row per (team, skill_type) at Worlds
    matches.parquet            One row per (team, match) — long format, with partners/opponents
    awards.parquet             One row per (event, award, team)
    events.parquet             One row per pre-Worlds event referenced (+ the Worlds event)
    worlds_teams.parquet       The analytical panel: one row per Worlds team for this season
    preworlds_rankings.parquet One row per (team, pre-Worlds event)
    preworlds_skills.parquet   One row per (team, skill_type, pre-Worlds event)

Each parquet contains `season_id` and `season_name` columns so concatenation across
seasons is trivial.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(output_dir: Path, verbose: bool = False) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "clean_log.txt"

    logger = logging.getLogger("vex_clean")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S UTC")
    logging.Formatter.converter = time.gmtime

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# JSON loading helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def safe_get(obj: Any, *keys: Any, default: Any = None) -> Any:
    """Nested .get() with safe fallback for missing nodes or non-dicts."""
    cur = obj
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        elif isinstance(cur, list):
            try:
                cur = cur[k]
            except (IndexError, TypeError):
                return default
        else:
            return default
    return cur if cur is not None else default


# ---------------------------------------------------------------------------
# Parquet schemas
# ---------------------------------------------------------------------------
# Declared up front so writes are validated. Each table has a `season_id` and
# `season_name` column so multi-season concats are trivial.

SCHEMA_TEAMS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("team_id", pa.int64()),
    ("team_number", pa.string()),
    ("team_name", pa.string()),
    ("robot_name", pa.string()),
    ("organization", pa.string()),
    ("grade", pa.string()),
    ("program_code", pa.string()),
    ("registered", pa.bool_()),
    ("region", pa.string()),
    ("country", pa.string()),
    ("city", pa.string()),
    ("postcode", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
])

SCHEMA_RANKINGS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("team_id", pa.int64()),
    ("team_number", pa.string()),
    ("division_id", pa.int32()),
    ("division_name", pa.string()),
    ("rank", pa.int32()),
    ("wins", pa.int32()),
    ("losses", pa.int32()),
    ("ties", pa.int32()),
    ("wp", pa.int32()),
    ("ap", pa.int32()),
    ("sp", pa.int32()),
    ("high_score", pa.int32()),
    ("average_points", pa.float64()),
    ("total_points", pa.int32()),
    ("opr", pa.float64()),
    ("dpr", pa.float64()),
    ("ccwm", pa.float64()),
])

SCHEMA_SKILLS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("team_id", pa.int64()),
    ("team_number", pa.string()),
    ("skill_type", pa.string()),
    ("score", pa.int32()),
    ("attempts", pa.int32()),
    ("rank", pa.int32()),
])

SCHEMA_MATCHES = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("match_id", pa.int64()),
    ("round", pa.int32()),
    ("round_label", pa.string()),
    ("instance", pa.int32()),
    ("matchnum", pa.int32()),
    ("scheduled", pa.string()),
    ("started", pa.string()),
    ("field", pa.string()),
    ("scored", pa.bool_()),
    ("division_id", pa.int32()),
    ("division_name", pa.string()),
    ("team_number", pa.string()),
    ("team_id", pa.int64()),
    ("alliance_color", pa.string()),
    ("is_sitting", pa.bool_()),
    ("my_alliance_score", pa.int32()),
    ("opp_alliance_score", pa.int32()),
    ("won", pa.bool_()),
    ("tied", pa.bool_()),
    ("is_qual", pa.bool_()),
    ("partners", pa.list_(pa.string())),
    ("opponents", pa.list_(pa.string())),
])

SCHEMA_AWARDS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("award_id", pa.int64()),
    ("award_title", pa.string()),
    ("classification", pa.string()),
    ("qualifies_for", pa.list_(pa.string())),
    ("team_number", pa.string()),
    ("team_id", pa.int64()),
])

SCHEMA_EVENTS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("event_sku", pa.string()),
    ("event_name", pa.string()),
    ("event_level", pa.string()),
    ("event_start", pa.string()),
    ("event_end", pa.string()),
    ("event_city", pa.string()),
    ("event_region", pa.string()),
    ("event_country", pa.string()),
    ("is_worlds", pa.bool_()),
])

SCHEMA_WORLDS_TEAMS = pa.schema([
    ("season_id", pa.int32()),
    ("season_name", pa.string()),
    ("event_id", pa.int64()),
    ("team_id", pa.int64()),
    ("team_number", pa.string()),
    ("team_name", pa.string()),
    ("region", pa.string()),
    ("country", pa.string()),
    ("city", pa.string()),
    ("postcode", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("division_id", pa.int32()),
    ("division_name", pa.string()),
    ("final_rank", pa.int32()),
    ("final_wins", pa.int32()),
    ("final_losses", pa.int32()),
    ("final_ties", pa.int32()),
    ("final_wp", pa.int32()),
    ("final_ap", pa.int32()),
    ("final_sp", pa.int32()),
    ("driver_max", pa.int32()),
    ("auto_max", pa.int32()),
    ("skills_total_max", pa.int32()),
])


# ---------------------------------------------------------------------------
# Cleaning functions
# ---------------------------------------------------------------------------

def _coalesce_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _coalesce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coalesce_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def _round_label(round_int: int) -> str:
    """Map RobotEvents round integer to a human-readable label."""
    mapping = {1: "Qual", 2: "Qual", 3: "R16", 4: "QF", 5: "SF", 6: "Final"}
    return mapping.get(int(round_int) if round_int else 0, f"R{round_int}")


def clean_teams(raw_teams: list[dict], season_id: int,
                season_name: str) -> pd.DataFrame:
    """Convert raw /events/{id}/teams to the teams table."""
    rows = []
    for t in raw_teams or []:
        loc = t.get("location") or {}
        coords = loc.get("coordinates") or {}
        rows.append({
            "season_id": season_id,
            "season_name": season_name,
            "team_id": _coalesce_int(t.get("id")),
            "team_number": str(t.get("number") or ""),
            "team_name": str(t.get("team_name") or ""),
            "robot_name": str(t.get("robot_name") or ""),
            "organization": str(t.get("organization") or ""),
            "grade": str(t.get("grade") or ""),
            "program_code": str(safe_get(t, "program", "code", default="") or ""),
            "registered": _coalesce_bool(t.get("registered"), default=False),
            "region": str(loc.get("region") or ""),
            "country": str(loc.get("country") or ""),
            "city": str(loc.get("city") or ""),
            "postcode": str(loc.get("postcode") or ""),
            "latitude": _coalesce_float(coords.get("lat")),
            "longitude": _coalesce_float(coords.get("lon")),
        })
    return pd.DataFrame(rows)


def clean_rankings(raw_rankings: list[dict], season_id: int,
                   season_name: str, default_event_id: int) -> pd.DataFrame:
    """
    Convert raw rankings list to the rankings table. Works for both Worlds-event
    rankings and pre-Worlds event rankings — the only difference is event_id.
    """
    rows = []
    for r in raw_rankings or []:
        team = r.get("team") or {}
        div = r.get("division") or {}
        event = r.get("event") or {}
        # event_id can come from the row itself (per-team or pre-Worlds calls)
        # or fall back to the default (Worlds event call)
        event_id = _coalesce_int(event.get("id")) or default_event_id
        rows.append({
            "season_id": season_id,
            "season_name": season_name,
            "event_id": event_id,
            "team_id": _coalesce_int(team.get("id")),
            "team_number": str(team.get("number") or ""),
            "division_id": _coalesce_int(div.get("id")),
            "division_name": str(div.get("name") or ""),
            "rank": _coalesce_int(r.get("rank")),
            "wins": _coalesce_int(r.get("wins")),
            "losses": _coalesce_int(r.get("losses")),
            "ties": _coalesce_int(r.get("ties")),
            "wp": _coalesce_int(r.get("wp")),
            "ap": _coalesce_int(r.get("ap")),
            "sp": _coalesce_int(r.get("sp")),
            "high_score": _coalesce_int(r.get("high_score")),
            "average_points": _coalesce_float(r.get("average_points")) or 0.0,
            "total_points": _coalesce_int(r.get("total_points")),
            "opr": _coalesce_float(r.get("opr")) or 0.0,
            "dpr": _coalesce_float(r.get("dpr")) or 0.0,
            "ccwm": _coalesce_float(r.get("ccwm")) or 0.0,
        })
    return pd.DataFrame(rows)


def clean_skills(raw_skills: list[dict], season_id: int,
                 season_name: str, default_event_id: int) -> pd.DataFrame:
    """Convert raw /events/{id}/skills to the skills table."""
    rows = []
    for s in raw_skills or []:
        team = s.get("team") or {}
        event = s.get("event") or {}
        event_id = _coalesce_int(event.get("id")) or default_event_id
        rows.append({
            "season_id": season_id,
            "season_name": season_name,
            "event_id": event_id,
            "team_id": _coalesce_int(team.get("id")),
            "team_number": str(team.get("number") or ""),
            "skill_type": str(s.get("type") or ""),
            "score": _coalesce_int(s.get("score")),
            "attempts": _coalesce_int(s.get("attempts")),
            "rank": _coalesce_int(s.get("rank")),
        })
    return pd.DataFrame(rows)


def clean_matches(raw_matches: list[dict], season_id: int, season_name: str,
                  event_id: int) -> pd.DataFrame:
    """
    Expand match-level JSON to long format — one row per (team, match).

    For each match, each team is materialized once with:
        - their alliance color and score
        - the opposing alliance's score
        - won / tied flags
        - the list of their PARTNERS (other teams on their alliance)
        - the list of their OPPONENTS (teams on the other alliance)
        - is_sitting flag (true if the team was a no-show in this match)
    """
    rows = []
    for m in raw_matches or []:
        alliances = m.get("alliances") or []
        if not alliances:
            continue

        # Resolve common match attributes once
        match_id = _coalesce_int(m.get("id"))
        round_int = _coalesce_int(m.get("round"))
        instance = _coalesce_int(m.get("instance"))
        matchnum = _coalesce_int(m.get("matchnum"))
        scheduled = str(m.get("scheduled") or "")
        started = str(m.get("started") or "")
        field = str(m.get("field") or "")
        scored = _coalesce_bool(m.get("scored"), default=False)
        div = m.get("division") or {}
        division_id = _coalesce_int(div.get("id"))
        division_name = str(div.get("name") or "")
        is_qual = round_int <= 2

        # Build a lookup: alliance index → score
        alliance_scores = [_coalesce_int(a.get("score"), default=0) for a in alliances]

        # Build a lookup: alliance index → list of team numbers (excluding sittings
        # for partner/opponent semantics, but we keep sitting teams as their own rows)
        alliance_team_numbers: list[list[str]] = []
        for a in alliances:
            nums = []
            for te in (a.get("teams") or []):
                tnum = str(safe_get(te, "team", "number", default="") or "")
                if tnum:
                    nums.append(tnum)
            alliance_team_numbers.append(nums)

        # Now emit one row per (team, alliance)
        for my_idx, alliance in enumerate(alliances):
            color = str(alliance.get("color") or "")
            my_score = alliance_scores[my_idx]
            # opp = aggregate of all non-self alliances (handles >2 alliance edge cases)
            opp_score = max(
                (alliance_scores[i] for i in range(len(alliances)) if i != my_idx),
                default=0,
            )
            opponents_list: list[str] = []
            for i, nums in enumerate(alliance_team_numbers):
                if i != my_idx:
                    opponents_list.extend(nums)

            for te in (alliance.get("teams") or []):
                t_obj = te.get("team") or {}
                tnum = str(t_obj.get("number") or "")
                if not tnum:
                    continue
                tid = _coalesce_int(t_obj.get("id"))
                is_sitting = _coalesce_bool(te.get("sitting"), default=False)
                # Partners = my alliance minus me; only meaningful when not sitting
                partners = [n for n in alliance_team_numbers[my_idx] if n != tnum]

                won = (my_score > opp_score) if scored else False
                tied = (my_score == opp_score) if scored else False

                rows.append({
                    "season_id": season_id,
                    "season_name": season_name,
                    "event_id": event_id,
                    "match_id": match_id,
                    "round": round_int,
                    "round_label": _round_label(round_int),
                    "instance": instance,
                    "matchnum": matchnum,
                    "scheduled": scheduled,
                    "started": started,
                    "field": field,
                    "scored": scored,
                    "division_id": division_id,
                    "division_name": division_name,
                    "team_number": tnum,
                    "team_id": tid,
                    "alliance_color": color,
                    "is_sitting": is_sitting,
                    "my_alliance_score": my_score,
                    "opp_alliance_score": opp_score,
                    "won": won,
                    "tied": tied,
                    "is_qual": is_qual,
                    "partners": partners,
                    "opponents": opponents_list,
                })
    return pd.DataFrame(rows)


def clean_awards(raw_awards: list[dict], season_id: int, season_name: str,
                 default_event_id: int) -> pd.DataFrame:
    """
    Convert raw awards to long format — one row per (award, team-winner).
    A single award (e.g. Excellence) can have multiple recipient teams.
    """
    rows = []
    for a in raw_awards or []:
        event = a.get("event") or {}
        event_id = _coalesce_int(event.get("id")) or default_event_id
        award_id = _coalesce_int(a.get("id"))
        title = str(a.get("title") or "")
        classification = str(a.get("classification") or "")
        qualifies = a.get("qualifications") or []
        if isinstance(qualifies, str):
            qualifies = [qualifies]
        winners = a.get("teamWinners") or a.get("team_winners") or []
        if not winners:
            # Some awards have no winners (e.g. not yet awarded)
            rows.append({
                "season_id": season_id,
                "season_name": season_name,
                "event_id": event_id,
                "award_id": award_id,
                "award_title": title,
                "classification": classification,
                "qualifies_for": [str(q) for q in qualifies],
                "team_number": "",
                "team_id": 0,
            })
            continue
        for w in winners:
            tnum = str(safe_get(w, "team", "number", default="") or w.get("number") or "")
            tid = _coalesce_int(safe_get(w, "team", "id", default=0) or w.get("id"))
            rows.append({
                "season_id": season_id,
                "season_name": season_name,
                "event_id": event_id,
                "award_id": award_id,
                "award_title": title,
                "classification": classification,
                "qualifies_for": [str(q) for q in qualifies],
                "team_number": tnum,
                "team_id": tid,
            })
    return pd.DataFrame(rows)


def clean_events_metadata(events_meta: dict, season_id: int, season_name: str,
                          worlds_event_id: int,
                          worlds_event: Optional[dict] = None) -> pd.DataFrame:
    """
    Build the events.parquet table. Includes every pre-Worlds event referenced by
    any team plus the Worlds event itself.
    """
    rows = []

    # Pre-Worlds events
    for eid_str, e in (events_meta or {}).items():
        eid = int(eid_str) if eid_str.isdigit() else _coalesce_int(e.get("id"))
        loc = e.get("location") or {}
        rows.append({
            "season_id": season_id,
            "season_name": season_name,
            "event_id": eid,
            "event_sku": str(e.get("sku") or ""),
            "event_name": str(e.get("name") or ""),
            "event_level": str(e.get("level") or ""),
            "event_start": str(e.get("start") or ""),
            "event_end": str(e.get("end") or ""),
            "event_city": str(loc.get("city") or ""),
            "event_region": str(loc.get("region") or ""),
            "event_country": str(loc.get("country") or ""),
            "is_worlds": False,
        })

    # Worlds event itself
    if worlds_event:
        loc = worlds_event.get("location") or {}
        rows.append({
            "season_id": season_id,
            "season_name": season_name,
            "event_id": worlds_event_id,
            "event_sku": str(worlds_event.get("sku") or ""),
            "event_name": str(worlds_event.get("name") or ""),
            "event_level": str(worlds_event.get("level") or ""),
            "event_start": str(worlds_event.get("start") or ""),
            "event_end": str(worlds_event.get("end") or ""),
            "event_city": str(loc.get("city") or ""),
            "event_region": str(loc.get("region") or ""),
            "event_country": str(loc.get("country") or ""),
            "is_worlds": True,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["event_id"], keep="last")
    return df


def clean_preworlds_rankings(rankings_by_event: dict, season_id: int,
                             season_name: str) -> pd.DataFrame:
    """Flatten pre-Worlds rankings cache (event_id -> list of rankings) into long format."""
    frames = []
    for eid_str, rlist in (rankings_by_event or {}).items():
        eid = int(eid_str) if eid_str.isdigit() else _coalesce_int(eid_str)
        df = clean_rankings(rlist, season_id, season_name, default_event_id=eid)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[f.name for f in SCHEMA_RANKINGS])
    return pd.concat(frames, ignore_index=True)


def clean_preworlds_skills(skills_by_event: dict, season_id: int,
                           season_name: str) -> pd.DataFrame:
    """Flatten pre-Worlds skills cache into long format."""
    frames = []
    for eid_str, slist in (skills_by_event or {}).items():
        eid = int(eid_str) if eid_str.isdigit() else _coalesce_int(eid_str)
        df = clean_skills(slist, season_id, season_name, default_event_id=eid)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[f.name for f in SCHEMA_SKILLS])
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Worlds-teams panel — the analytical unit of observation
# ---------------------------------------------------------------------------

def build_worlds_teams(teams_df: pd.DataFrame, rankings_df: pd.DataFrame,
                       skills_df: pd.DataFrame, season_id: int, season_name: str,
                       event_id: int) -> pd.DataFrame:
    """
    Build the panel of Worlds participants — one row per team at this Worlds.
    Joins team metadata, final rankings (including SP), and skills aggregates.
    This is the analytical unit of observation for the schedule-strength paper.
    """
    # Aggregate skills: max per (team, skill_type), then pivot
    if not skills_df.empty:
        skills_agg = (
            skills_df
            .groupby(["team_number", "skill_type"], as_index=False)["score"].max()
        )
        skills_pivot = skills_agg.pivot(
            index="team_number", columns="skill_type", values="score"
        ).reset_index()
    else:
        skills_pivot = pd.DataFrame(columns=["team_number"])

    # Worlds rankings only — drop pre-Worlds rows defensively
    wld_rankings = rankings_df[rankings_df["event_id"] == event_id].copy()

    # If the same team appears in rankings multiple times (it shouldn't at Worlds),
    # keep the one with the highest WP+AP (closest to "final" record).
    if not wld_rankings.empty:
        wld_rankings = (
            wld_rankings
            .sort_values(["wp", "ap", "sp"], ascending=False)
            .drop_duplicates(subset=["team_number"], keep="first")
        )

    df = teams_df.merge(
        wld_rankings[[
            "team_number", "division_id", "division_name",
            "rank", "wins", "losses", "ties", "wp", "ap", "sp",
        ]],
        on="team_number", how="left",
    )

    df = df.merge(skills_pivot, on="team_number", how="left")

    out = pd.DataFrame({
        "season_id": season_id,
        "season_name": season_name,
        "event_id": event_id,
        "team_id": df["team_id"],
        "team_number": df["team_number"],
        "team_name": df.get("team_name", ""),
        "region": df.get("region", ""),
        "country": df.get("country", ""),
        "city": df.get("city", ""),
        "postcode": df.get("postcode", ""),
        "latitude": df.get("latitude"),
        "longitude": df.get("longitude"),
        "division_id": df.get("division_id").fillna(0).astype("int32") if "division_id" in df else 0,
        "division_name": df.get("division_name", "").fillna("") if "division_name" in df else "",
        "final_rank": df.get("rank").fillna(0).astype("int32") if "rank" in df else 0,
        "final_wins": df.get("wins").fillna(0).astype("int32") if "wins" in df else 0,
        "final_losses": df.get("losses").fillna(0).astype("int32") if "losses" in df else 0,
        "final_ties": df.get("ties").fillna(0).astype("int32") if "ties" in df else 0,
        "final_wp": df.get("wp").fillna(0).astype("int32") if "wp" in df else 0,
        "final_ap": df.get("ap").fillna(0).astype("int32") if "ap" in df else 0,
        "final_sp": df.get("sp").fillna(0).astype("int32") if "sp" in df else 0,
        "driver_max": df.get("driver", 0).fillna(0).astype("int32") if "driver" in df else 0,
        "auto_max": df.get("programming", 0).fillna(0).astype("int32") if "programming" in df else 0,
        "skills_total_max": (
            (df.get("driver", 0).fillna(0) + df.get("programming", 0).fillna(0))
            .astype("int32")
            if ("driver" in df and "programming" in df) else 0
        ),
    })
    return out


# ---------------------------------------------------------------------------
# Parquet write with schema enforcement
# ---------------------------------------------------------------------------

def write_parquet(df: pd.DataFrame, path: Path, schema: pa.Schema,
                  logger: logging.Logger, label: str) -> None:
    """
    Write a DataFrame to parquet with strict schema enforcement.
    Reorders columns to match the schema, fills missing columns with defaults,
    and raises if any required column has the wrong type.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure every schema column exists and is in the right order
    expected_cols = [f.name for f in schema]
    out = pd.DataFrame()
    for f in schema:
        if f.name in df.columns:
            col = df[f.name]
        else:
            # Provide schema-appropriate default
            if pa.types.is_string(f.type):
                col = pd.Series([""] * len(df))
            elif pa.types.is_integer(f.type):
                col = pd.Series([0] * len(df), dtype="int64")
            elif pa.types.is_floating(f.type):
                col = pd.Series([None] * len(df), dtype="float64")
            elif pa.types.is_boolean(f.type):
                col = pd.Series([False] * len(df))
            elif pa.types.is_list(f.type):
                col = pd.Series([[] for _ in range(len(df))])
            else:
                col = pd.Series([None] * len(df))
        out[f.name] = col

    # Convert with explicit schema
    try:
        table = pa.Table.from_pandas(out, schema=schema, preserve_index=False)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as e:
        # Often a stray string in an integer column; coerce and retry
        logger.warning(f"  Schema coercion fallback for {label}: {e}")
        for f in schema:
            if pa.types.is_integer(f.type):
                out[f.name] = pd.to_numeric(out[f.name], errors="coerce").fillna(0).astype("int64")
            elif pa.types.is_floating(f.type):
                out[f.name] = pd.to_numeric(out[f.name], errors="coerce")
            elif pa.types.is_boolean(f.type):
                out[f.name] = out[f.name].astype(bool)
            elif pa.types.is_string(f.type):
                out[f.name] = out[f.name].astype(str).fillna("")
        table = pa.Table.from_pandas(out, schema=schema, preserve_index=False)

    pq.write_table(table, path)
    logger.info(f"  Wrote {label}: {len(out)} rows -> {path.name}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

TABLE_PLAN = [
    # (output filename, builder function name, applicable_schema)
    ("teams.parquet", SCHEMA_TEAMS),
    ("rankings.parquet", SCHEMA_RANKINGS),
    ("skills.parquet", SCHEMA_SKILLS),
    ("matches.parquet", SCHEMA_MATCHES),
    ("awards.parquet", SCHEMA_AWARDS),
    ("events.parquet", SCHEMA_EVENTS),
    ("worlds_teams.parquet", SCHEMA_WORLDS_TEAMS),
    ("preworlds_rankings.parquet", SCHEMA_RANKINGS),
    ("preworlds_skills.parquet", SCHEMA_SKILLS),
]


def clean_season(input_dir: Path, output_dir: Path, resume: bool,
                 logger: logging.Logger) -> dict:
    """Clean a single season's raw drop into typed parquet tables."""
    logger.info(f"=== Clean: {input_dir.name} ===")
    output_dir.mkdir(parents=True, exist_ok=True)

    info = load_json(input_dir / "season_info.json") or {}
    season_id = int(info.get("season_id") or 0)
    season_name = str(info.get("season_name") or "Unknown")
    worlds_event_id = int(info.get("worlds_event_id") or 0)
    if not (season_id and worlds_event_id):
        raise RuntimeError(
            f"season_info.json missing required fields in {input_dir}. "
            f"Run src.ingest first."
        )
    logger.info(f"Season: {season_name} (id={season_id}); "
                f"Worlds event id={worlds_event_id}")

    summary: dict[str, int] = {}

    def _maybe_write(filename: str, schema: pa.Schema, df: pd.DataFrame) -> None:
        path = output_dir / filename
        if resume and path.exists() and path.stat().st_size > 0:
            logger.info(f"  --resume: skipping {filename} (already present)")
            return
        write_parquet(df, path, schema, logger, label=filename)
        summary[filename] = len(df)

    # ---- Load raw ----
    raw_teams = load_json(input_dir / "teams.json") or []
    raw_rankings = load_json(input_dir / "rankings.json") or []
    raw_skills = load_json(input_dir / "skills.json") or []
    raw_matches = load_json(input_dir / "matches.json") or []
    raw_awards = load_json(input_dir / "awards.json") or []
    pw_team_events = load_json(input_dir / "preworlds_team_events.json") or {}
    pw_rankings = load_json(input_dir / "preworlds_rankings.json") or {}
    pw_skills = load_json(input_dir / "preworlds_skills.json") or {}
    pw_events_meta = load_json(input_dir / "preworlds_events_metadata.json") or {}

    # Worlds event metadata: derive from season_info if a full event object isn't on disk
    worlds_event_obj = {
        "id": worlds_event_id,
        "name": info.get("worlds_event_name"),
        "level": "World",
        "start": info.get("worlds_start"),
        "end": info.get("worlds_end"),
        "sku": "",
        "location": {},
    }

    # ---- Build dataframes ----
    teams_df = clean_teams(raw_teams, season_id, season_name)
    rankings_df = clean_rankings(raw_rankings, season_id, season_name, worlds_event_id)
    skills_df = clean_skills(raw_skills, season_id, season_name, worlds_event_id)
    matches_df = clean_matches(raw_matches, season_id, season_name, worlds_event_id)
    awards_df = clean_awards(raw_awards, season_id, season_name, worlds_event_id)
    events_df = clean_events_metadata(
        pw_events_meta, season_id, season_name, worlds_event_id, worlds_event_obj
    )
    pw_rankings_df = clean_preworlds_rankings(pw_rankings, season_id, season_name)
    pw_skills_df = clean_preworlds_skills(pw_skills, season_id, season_name)
    worlds_teams_df = build_worlds_teams(
        teams_df, rankings_df, skills_df, season_id, season_name, worlds_event_id
    )

    # ---- Write parquets ----
    _maybe_write("teams.parquet", SCHEMA_TEAMS, teams_df)
    _maybe_write("rankings.parquet", SCHEMA_RANKINGS, rankings_df)
    _maybe_write("skills.parquet", SCHEMA_SKILLS, skills_df)
    _maybe_write("matches.parquet", SCHEMA_MATCHES, matches_df)
    _maybe_write("awards.parquet", SCHEMA_AWARDS, awards_df)
    _maybe_write("events.parquet", SCHEMA_EVENTS, events_df)
    _maybe_write("worlds_teams.parquet", SCHEMA_WORLDS_TEAMS, worlds_teams_df)
    _maybe_write("preworlds_rankings.parquet", SCHEMA_RANKINGS, pw_rankings_df)
    _maybe_write("preworlds_skills.parquet", SCHEMA_SKILLS, pw_skills_df)

    # ---- Manifest ----
    manifest = {
        "season_id": season_id,
        "season_name": season_name,
        "worlds_event_id": worlds_event_id,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "row_counts": summary,
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written: {output_dir / 'manifest.json'}")
    logger.info(f"Row counts: {summary}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="src.clean",
        description="Transform raw RobotEvents JSON into typed parquet tables.",
    )
    p.add_argument("--input", type=Path, required=True,
                   help="Input directory (output of src.ingest).")
    p.add_argument("--output", type=Path, required=True,
                   help="Output directory for parquet tables.")
    p.add_argument("--resume", action="store_true",
                   help="Skip writing tables whose parquet files already exist.")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose (DEBUG) logging.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir: Path = args.output
    logger = setup_logging(output_dir, verbose=args.verbose)

    if not args.input.exists():
        logger.error(f"Input directory does not exist: {args.input}")
        return 2

    try:
        manifest = clean_season(args.input, output_dir, args.resume, logger)
    except Exception as e:
        logger.exception(f"Clean failed: {e}")
        return 1

    logger.info("=== Clean complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
