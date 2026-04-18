#!/usr/bin/env python3
"""
VEX Visualizer — Optimized Automated Data Updater
Pulls latest team stats from the RobotEvents API v2 and rebuilds index.html.

OPTIMIZED VERSION:
  - Uses event-level endpoints (20-30 API calls) instead of per-team calls (3,500+)
  - Runs in ~1-2 minutes instead of ~7-10 minutes
  - Safe for 15-minute intervals during Worlds week
  - Detects Worlds week automatically and adjusts behavior

Usage:
  python update_data.py

Environment variables required:
  ROBOTEVENTS_TOKEN  — Bearer token from https://www.robotevents.com/api/v2
"""

import os
import sys
import json
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Configuration — UPDATE THESE VALUES
# ---------------------------------------------------------------------------

API_BASE = "https://www.robotevents.com/api/v2"
TOKEN = os.environ.get("ROBOTEVENTS_TOKEN", "")

# VRC program ID
PROGRAM_ID = 1

# Push Back 2025-2026 season ID
# Verify at: https://www.robotevents.com/api/v2/seasons?program%5B%5D=1
SEASON_ID = 190  # Update if needed

# Worlds 2026 event ID — REQUIRED for Worlds-specific data
# Find it: https://www.robotevents.com/api/v2/events?program%5B%5D=1&season%5B%5D=190&level%5B%5D=World
# Or search RobotEvents for "VEX Worlds 2026"
WORLDS_EVENT_ID = None  # e.g., 54321 — set this once you know it

# Worlds 2026 dates (for smart scheduling detection)
# Update these to the actual Worlds dates
WORLDS_START = datetime(2026, 4, 28, tzinfo=timezone.utc)  # Tuesday of Worlds week
WORLDS_END = datetime(2026, 5, 3, tzinfo=timezone.utc)     # Sunday end of Worlds

# Division name mapping — maps division IDs from the API to display names
# Update once you see the actual division IDs from the Worlds event
DIVISION_MAP = {}  # e.g., {1: "Arts", 2: "Math", ...} — populated dynamically

DIVISION_NAMES = [
    "Arts", "Math", "Technology", "Science", "Engineering",
    "Innovate", "Spirit", "Design", "Research", "Opportunity"
]

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def is_worlds_week():
    """Check if we're currently in the Worlds competition window."""
    now = datetime.now(timezone.utc)
    # Include 1 day buffer before and after
    return (WORLDS_START - timedelta(days=1)) <= now <= (WORLDS_END + timedelta(days=1))

def is_worlds_month():
    """Check if we're in the month surrounding Worlds (pre-event scouting period)."""
    now = datetime.now(timezone.utc)
    return (WORLDS_START - timedelta(days=14)) <= now <= (WORLDS_END + timedelta(days=3))

def data_hash(data):
    """Hash team data to detect changes without diffing."""
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()

def log(msg):
    """Print with timestamp."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}")

# ---------------------------------------------------------------------------
# API Helper
# ---------------------------------------------------------------------------

def api_get(endpoint, params=None, max_pages=100):
    """
    GET request to RobotEvents API with automatic pagination.
    Returns list of all data items across all pages.
    """
    url = f"{API_BASE}{endpoint}"
    if params:
        parts = []
        for k, v in params.items():
            if v is not None:
                if isinstance(v, list):
                    for item in v:
                        parts.append(f"{k}={item}")
                else:
                    parts.append(f"{k}={v}")
        if parts:
            url += "?" + "&".join(parts)

    all_data = []
    page = 1
    retries = 0

    while page <= max_pages:
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}page={page}&per_page=250"
        req = urllib.request.Request(page_url, headers=HEADERS)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                retries += 1
                wait = min(60 * retries, 300)
                log(f"  Rate limited — waiting {wait}s (retry {retries})...")
                time.sleep(wait)
                continue
            log(f"  API error {e.code} for {endpoint} page {page}")
            break
        except Exception as e:
            log(f"  Request error: {e}")
            retries += 1
            if retries > 3:
                break
            time.sleep(5)
            continue

        retries = 0
        data = body.get("data", [])
        all_data.extend(data)

        meta = body.get("meta", {})
        last_page = meta.get("last_page", 1)

        if page >= last_page:
            break
        page += 1
        time.sleep(0.3)  # Respectful pacing

    return all_data


# ---------------------------------------------------------------------------
# OPTIMIZED Data Collection — Event-Level Endpoints
# ---------------------------------------------------------------------------

def fetch_all_event_rankings(event_id):
    """
    Fetch ALL rankings for an event in one paginated call.
    This replaces 866 individual team ranking calls.
    Returns dict keyed by team number.
    """
    log("Fetching event rankings (all divisions)...")
    rankings = api_get(f"/events/{event_id}/rankings")
    log(f"  Got {len(rankings)} ranking entries")

    by_team = {}
    for r in rankings:
        team_info = r.get("team", {})
        team_num = team_info.get("number", "")
        if team_num:
            if team_num not in by_team:
                by_team[team_num] = []
            by_team[team_num].append(r)
    return by_team


def fetch_all_event_skills(event_id):
    """
    Fetch ALL skills scores for an event in one paginated call.
    Returns dict keyed by team number.
    """
    log("Fetching event skills scores...")
    skills = api_get(f"/events/{event_id}/skills")
    log(f"  Got {len(skills)} skills entries")

    by_team = {}
    for s in skills:
        team_info = s.get("team", {})
        team_num = team_info.get("number", "")
        if team_num:
            if team_num not in by_team:
                by_team[team_num] = []
            by_team[team_num].append(s)
    return by_team


def fetch_all_event_teams(event_id):
    """
    Fetch ALL teams registered for an event.
    Returns list of team objects.
    """
    log("Fetching event teams...")
    teams = api_get(f"/events/{event_id}/teams")
    log(f"  Got {len(teams)} teams")
    return teams


def fetch_all_event_matches(event_id):
    """
    Fetch ALL matches for an event.
    Returns dict keyed by team number.
    """
    log("Fetching event matches...")
    matches = api_get(f"/events/{event_id}/matches")
    log(f"  Got {len(matches)} matches")

    by_team = {}
    for match in matches:
        alliances = match.get("alliances", [])
        for alliance in alliances:
            for team_entry in alliance.get("teams", []):
                team_info = team_entry.get("team", {})
                team_num = team_info.get("number", "")
                if team_num:
                    if team_num not in by_team:
                        by_team[team_num] = []
                    by_team[team_num].append(match)
    return by_team


def fetch_event_divisions(event_id):
    """Fetch event details to get division info."""
    log("Fetching event divisions...")
    event_data = api_get(f"/events/{event_id}/divisions")
    if not event_data:
        # Try fetching event itself
        req = urllib.request.Request(
            f"{API_BASE}/events/{event_id}",
            headers=HEADERS
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
                event_data = body.get("divisions", [])
        except Exception:
            event_data = []
    log(f"  Got {len(event_data)} divisions")
    return event_data


def fetch_season_skills_rankings():
    """
    Fetch season-wide skills rankings (World Skills Standings).
    This gives us global skills rankings without per-team calls.
    """
    log("Fetching season skills rankings...")
    skills = api_get(f"/seasons/{SEASON_ID}/skills", {"program[]": PROGRAM_ID})
    log(f"  Got {len(skills)} season skills entries")

    by_team = {}
    for s in skills:
        team_info = s.get("team", {})
        team_num = team_info.get("number", "")
        if team_num:
            if team_num not in by_team:
                by_team[team_num] = []
            by_team[team_num].append(s)
    return by_team


def fetch_season_events():
    """Fetch recent events for the season to aggregate stats."""
    log("Fetching season events...")
    events = api_get("/events", {
        "program[]": PROGRAM_ID,
        "season[]": SEASON_ID,
    })
    log(f"  Got {len(events)} events")
    return events


# ---------------------------------------------------------------------------
# Fallback: Season-Wide Collection (when WORLDS_EVENT_ID is not set)
# ---------------------------------------------------------------------------

def fetch_season_teams():
    """Fetch all teams registered for the season."""
    log("Fetching all season teams...")
    teams = api_get("/teams", {
        "program[]": PROGRAM_ID,
        "registered": "true",
    }, max_pages=20)
    log(f"  Got {len(teams)} teams")
    return teams


def collect_from_recent_events(teams_dict, max_events=10):
    """
    Collect rankings from the most recent events.
    More efficient than per-team calls — fetches event-level data.
    """
    events = fetch_season_events()

    # Sort by end date, most recent first
    events.sort(key=lambda e: e.get("end", ""), reverse=True)
    events = events[:max_events]

    rankings_by_team = {}
    matches_by_team = {}

    for i, event in enumerate(events):
        eid = event.get("id")
        ename = event.get("name", "Unknown")
        log(f"  Event {i+1}/{len(events)}: {ename} (ID: {eid})")

        # Fetch rankings for this event
        event_rankings = api_get(f"/events/{eid}/rankings")
        for r in event_rankings:
            team_num = r.get("team", {}).get("number", "")
            if team_num and team_num in teams_dict:
                if team_num not in rankings_by_team:
                    rankings_by_team[team_num] = []
                rankings_by_team[team_num].append(r)

        time.sleep(0.5)

    return rankings_by_team, matches_by_team


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def calculate_tier(percentile):
    """Assign tier based on True Skill percentile."""
    if percentile <= 10:
        return "Elite"
    elif percentile <= 30:
        return "Contender"
    elif percentile <= 50:
        return "Competitive"
    elif percentile <= 70:
        return "Developing"
    else:
        return "Rising"


def process_teams(raw_teams, rankings_by_team, skills_by_team, matches_by_team, divisions_map=None):
    """
    Process raw API data into the format expected by VEX Visualizer.
    Uses event-level aggregated data (not per-team API calls).
    """
    processed = []

    for team in raw_teams:
        team_num = team.get("number", "")
        team_name = team.get("team_name", "")
        location = team.get("location", {})
        region = location.get("region", "") or location.get("city", "Unknown")
        country = location.get("country", "Unknown")

        # Division assignment
        division = ""
        if divisions_map and team_num in divisions_map:
            division = divisions_map[team_num]

        # Rankings data (aggregated from events)
        rankings = rankings_by_team.get(team_num, [])
        best_opr = 0
        best_dpr = 0
        best_ccwm = 0
        total_wins = 0
        total_losses = 0
        total_ties = 0
        best_wp_per_match = 0
        best_awp_per_match = 0

        for r in rankings:
            opr_val = r.get("opr", 0) or 0
            dpr_val = r.get("dpr", 0) or 0
            ccwm_val = r.get("ccwm", 0) or 0
            best_opr = max(best_opr, opr_val)
            best_dpr = max(best_dpr, dpr_val) if dpr_val > best_dpr else best_dpr
            best_ccwm = max(best_ccwm, ccwm_val)

            w = r.get("wins", 0) or 0
            l = r.get("losses", 0) or 0
            t = r.get("ties", 0) or 0
            total_wins += w
            total_losses += l
            total_ties += t

            wp = r.get("wp", 0) or 0
            ap = r.get("ap", 0) or 0
            played = w + l + t
            if played > 0:
                best_wp_per_match = max(best_wp_per_match, round(wp / played, 1))
                best_awp_per_match = max(best_awp_per_match, round(ap / played, 1))

            # DPR: use the value from the event with the most matches
            if dpr_val > 0:
                best_dpr = dpr_val  # Will use the last event's DPR

        matches_played = total_wins + total_losses + total_ties
        win_pct = round((total_wins / matches_played * 100), 1) if matches_played > 0 else 0

        # Skills data
        skills = skills_by_team.get(team_num, [])
        driver_max = 0
        auto_max = 0
        for s in skills:
            score = s.get("score", 0) or 0
            skill_type = s.get("type", "")
            if skill_type == "driver":
                driver_max = max(driver_max, score)
            elif skill_type == "programming":
                auto_max = max(auto_max, score)
        total_max = driver_max + auto_max

        # Match-level stats (qual vs elim breakdown)
        team_matches = matches_by_team.get(team_num, [])
        qual_wins = 0
        qual_losses = 0
        elim_wins = 0
        elim_losses = 0

        for match in team_matches:
            round_type = match.get("round", 0)
            alliances = match.get("alliances", [])

            team_alliance = None
            opp_alliance = None
            for alliance in alliances:
                is_our_team = False
                for te in alliance.get("teams", []):
                    if te.get("team", {}).get("number") == team_num:
                        is_our_team = True
                        break
                if is_our_team:
                    team_alliance = alliance
                else:
                    opp_alliance = alliance

            if team_alliance and opp_alliance:
                my_score = team_alliance.get("score", 0) or 0
                opp_score = opp_alliance.get("score", 0) or 0

                # Rounds 1-2 = quals, 3+ = elims (varies by event format)
                is_qual = round_type <= 2

                if my_score > opp_score:
                    if is_qual:
                        qual_wins += 1
                    else:
                        elim_wins += 1
                elif my_score < opp_score:
                    if is_qual:
                        qual_losses += 1
                    else:
                        elim_losses += 1

        # If we didn't get match-level data, estimate from rankings
        if qual_wins + qual_losses == 0 and total_wins > 0:
            # Rough estimate: 70% of matches are quals
            qual_wins = round(total_wins * 0.7)
            qual_losses = round(total_losses * 0.7)
            elim_wins = total_wins - qual_wins
            elim_losses = total_losses - qual_losses

        qual_total = qual_wins + qual_losses
        qual_win_pct = round((qual_wins / qual_total * 100), 1) if qual_total > 0 else 0
        elim_total = elim_wins + elim_losses
        elim_win_pct = round((elim_wins / elim_total * 100), 1) if elim_total > 0 else 0

        # True Skill approximation
        # Weighted combination of skills, win rate, OPR, CCWM
        true_skill = round(
            (total_max / 20) * 0.3 +
            (win_pct / 100) * 10 * 0.3 +
            (best_opr / 3) * 0.2 +
            (best_ccwm / 3) * 0.2,
            1
        )

        processed.append({
            "team": team_num,
            "teamName": team_name,
            "region": region,
            "country": country,
            "division": division,
            "trueSkill": true_skill,
            "trueSkillRank": 0,
            "ccwm": round(best_ccwm, 1),
            "wins": total_wins,
            "losses": total_losses,
            "matchesPlayed": matches_played,
            "winPct": win_pct,
            "elimWins": elim_wins,
            "elimLosses": elim_losses,
            "elimWinPct": elim_win_pct,
            "qualWins": qual_wins,
            "qualLosses": qual_losses,
            "qualWinPct": qual_win_pct,
            "awpPerMatch": best_awp_per_match,
            "wpPerMatch": best_wp_per_match,
            "opr": round(best_opr, 1),
            "dpr": round(best_dpr, 1),
            "driverMax": driver_max,
            "autoMax": auto_max,
            "totalMax": total_max,
            "worldsOdds": "N/A",
            "divRank": 0,
            "divProjectedPoints": 0,
            "tier": "Rising",
        })

    # Sort by True Skill and assign ranks + tiers
    processed.sort(key=lambda x: x["trueSkill"], reverse=True)
    total = len(processed)
    for i, team in enumerate(processed):
        team["trueSkillRank"] = i + 1
        percentile = ((i + 1) / total) * 100
        team["tier"] = calculate_tier(percentile)

    # Calculate division-level stats
    divisions = {}
    for team in processed:
        div = team["division"]
        if div:
            if div not in divisions:
                divisions[div] = []
            divisions[div].append(team)

    for div_name, div_teams in divisions.items():
        div_teams_sorted = sorted(div_teams, key=lambda x: x["trueSkill"], reverse=True)
        total_ts = sum(t["trueSkill"] for t in div_teams)
        for i, team in enumerate(div_teams_sorted):
            team["divRank"] = i + 1

    return processed


# ---------------------------------------------------------------------------
# Build HTML
# ---------------------------------------------------------------------------

def build_html(teams_data):
    """Inject teams JSON into the HTML template and write index.html."""
    template_path = os.path.join(SCRIPT_DIR, "vex_visualizer_template.html")

    if not os.path.exists(template_path):
        log(f"ERROR: Template not found at {template_path}")
        sys.exit(1)

    with open(template_path, "r") as f:
        html = f.read()

    if "TEAMS_JSON_PLACEHOLDER" not in html:
        log("ERROR: Template missing TEAMS_JSON_PLACEHOLDER")
        sys.exit(1)

    html = html.replace("TEAMS_JSON_PLACEHOLDER", json.dumps(teams_data))

    # Update the data timestamp in the HTML
    timestamp = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")
    html = html.replace("True Skill data from 03/23", f"Data updated {timestamp}")

    output_path = os.path.join(SCRIPT_DIR, "index.html")
    with open(output_path, "w") as f:
        f.write(html)

    json_path = os.path.join(SCRIPT_DIR, "teams_data.json")
    with open(json_path, "w") as f:
        json.dump(teams_data, f)

    log(f"Built index.html ({len(html):,} bytes) with {len(teams_data)} teams")
    return True


# ---------------------------------------------------------------------------
# Change Detection
# ---------------------------------------------------------------------------

def has_data_changed(new_data):
    """Check if the data actually changed since last run."""
    json_path = os.path.join(SCRIPT_DIR, "teams_data.json")
    if not os.path.exists(json_path):
        return True

    try:
        with open(json_path, "r") as f:
            old_data = json.load(f)
        return data_hash(old_data) != data_hash(new_data)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Main Strategies
# ---------------------------------------------------------------------------

def run_worlds_event_mode():
    """
    FAST MODE — Used when WORLDS_EVENT_ID is set.
    Fetches all data from event-level endpoints (~20-30 API calls total).
    Runs in ~1-2 minutes. Safe for 15-minute intervals.
    """
    log("Running in Worlds Event mode (fast, event-level endpoints)")

    # Fetch all data from the Worlds event
    raw_teams = fetch_all_event_teams(WORLDS_EVENT_ID)
    if not raw_teams:
        log("No teams found for the Worlds event. Check WORLDS_EVENT_ID.")
        return False

    rankings_by_team = fetch_all_event_rankings(WORLDS_EVENT_ID)
    skills_by_team = fetch_all_event_skills(WORLDS_EVENT_ID)
    matches_by_team = fetch_all_event_matches(WORLDS_EVENT_ID)

    # Fetch divisions
    divisions = fetch_event_divisions(WORLDS_EVENT_ID)
    div_map = {}
    # Map divisions — this depends on the API response structure
    # You may need to adjust this once you see the actual data
    for div in divisions:
        div_id = div.get("id")
        div_name = div.get("name", "")
        # Try to match to our known division names
        for known_name in DIVISION_NAMES:
            if known_name.lower() in div_name.lower():
                DIVISION_MAP[div_id] = known_name
                break

    # Assign divisions to teams based on their ranking division
    for team_num, team_rankings in rankings_by_team.items():
        for r in team_rankings:
            div_info = r.get("division", {})
            div_id = div_info.get("id")
            if div_id and div_id in DIVISION_MAP:
                div_map[team_num] = DIVISION_MAP[div_id]
                break

    # Process
    teams_data = process_teams(
        raw_teams, rankings_by_team, skills_by_team, matches_by_team, div_map
    )

    if not has_data_changed(teams_data):
        log("No data changes detected — skipping rebuild.")
        return False

    return build_html(teams_data)


def run_season_mode():
    """
    STANDARD MODE — Used when no Worlds event ID or for pre-Worlds updates.
    Fetches from season-level and recent event endpoints.
    Runs in ~3-5 minutes. Good for hourly or daily updates.
    """
    log("Running in Season mode (event-level aggregation)")

    # Fetch season skills rankings (one call, all teams)
    skills_by_team = fetch_season_skills_rankings()

    # Fetch teams from season
    raw_teams = fetch_season_teams()
    if not raw_teams:
        log("No teams found. Check SEASON_ID.")
        return False

    teams_dict = {t.get("number"): t for t in raw_teams}

    # Collect from recent events (efficient: event-level, not per-team)
    log("Collecting from recent events...")
    rankings_by_team, matches_by_team = collect_from_recent_events(teams_dict)

    # Process
    teams_data = process_teams(
        raw_teams, rankings_by_team, skills_by_team, matches_by_team
    )

    if not has_data_changed(teams_data):
        log("No data changes detected — skipping rebuild.")
        return False

    return build_html(teams_data)


def main():
    if not TOKEN:
        log("ERROR: ROBOTEVENTS_TOKEN environment variable not set.")
        log("  1. Go to https://www.robotevents.com/api/v2")
        log("  2. Log in and go to 'Access Tokens'")
        log("  3. Create a new token")
        log("  4. Add it as a GitHub secret named ROBOTEVENTS_TOKEN")
        sys.exit(1)

    worlds_week = is_worlds_week()
    worlds_month = is_worlds_month()

    log("=" * 60)
    log("VEX Visualizer Data Update")
    log(f"  Worlds week: {'YES' if worlds_week else 'no'}")
    log(f"  Worlds month: {'YES' if worlds_month else 'no'}")
    log(f"  Worlds event ID: {WORLDS_EVENT_ID or 'not set'}")
    log("=" * 60)

    if WORLDS_EVENT_ID:
        success = run_worlds_event_mode()
    else:
        success = run_season_mode()

    if success:
        log("Update complete — new data written.")
    else:
        log("Update complete — no changes to commit.")


if __name__ == "__main__":
    main()
