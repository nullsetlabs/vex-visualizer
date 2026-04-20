#!/usr/bin/env python3
"""
VEX Visualizer — Middle School Data Fetcher
Pulls middle school team stats from the RobotEvents API v2.

Usage:
  ROBOTEVENTS_TOKEN=your_token python fetch_ms_data.py

This will create ms_teams_data.json which the main build pipeline
will automatically inject into the site.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

API_BASE = "https://www.robotevents.com/api/v2"
TOKEN = os.environ.get("ROBOTEVENTS_TOKEN", "")
PROGRAM_ID = 1  # VRC
SEASON_ID = 197  # Push Back 2025-2026

# Middle School Worlds event
MS_WORLDS_EVENT_ID = None  # Will auto-detect

DIVISION_NAMES = [
    "Arts", "Math", "Technology", "Science", "Engineering",
    "Innovate", "Spirit", "Design", "Research", "Opportunity"
]

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "User-Agent": "VEX-Visualizer/1.0 (GitHub Actions; +https://github.com/arjun-mohanan/vex-visualizer)",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}")


def api_get(endpoint, params=None, max_pages=100):
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
            if e.code == 429:
                retries += 1
                wait = min(60 * retries, 300)
                log(f"  Rate limited — waiting {wait}s...")
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
        time.sleep(0.3)

    return all_data


def find_ms_worlds_event():
    """Find the Middle School Worlds event."""
    log("Searching for Middle School Worlds event...")
    events = api_get(f"/seasons/{SEASON_ID}/events")

    # Priority 1: World level + "championship" + "middle"
    for event in events:
        name = (event.get("name", "") or "").lower()
        level = (event.get("level", "") or "").lower()
        if level == "world" and "middle" in name and ("championship" in name or "vex worlds" in name):
            log(f"  Found: {event.get('name')} (ID: {event.get('id')})")
            return event

    # Priority 2: World level + "middle" (but not regional contests)
    for event in events:
        name = (event.get("name", "") or "").lower()
        level = (event.get("level", "") or "").lower()
        if level == "world" and "middle" in name and "robot contest" not in name:
            log(f"  Found: {event.get('name')} (ID: {event.get('id')})")
            return event

    # Priority 3: Known SKU
    for event in events:
        sku = (event.get("sku", "") or "")
        if sku == "RE-V5RC-26-4026":
            log(f"  Found via SKU: {event.get('name')} (ID: {event.get('id')})")
            return event

    # Priority 4: Fallback search via /events endpoint
    log("  Trying fallback /events endpoint...")
    events_all = api_get("/events", {
        "program[]": PROGRAM_ID,
        "season[]": SEASON_ID,
        "level[]": "World",
    })
    for event in events_all:
        name = (event.get("name", "") or "").lower()
        sku = (event.get("sku", "") or "")
        if ("middle" in name and "championship" in name) or sku == "RE-V5RC-26-4026":
            log(f"  Found via fallback: {event.get('name')} (ID: {event.get('id')})")
            return event

    return None


def calculate_tier(percentile):
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


def main():
    if not TOKEN:
        log("ERROR: ROBOTEVENTS_TOKEN not set.")
        log("  Usage: ROBOTEVENTS_TOKEN=your_token python fetch_ms_data.py")
        sys.exit(1)

    log("=" * 60)
    log("VEX Visualizer — Middle School Data Fetcher")
    log(f"  Token: {TOKEN[:6]}...")
    log("=" * 60)

    # Verify token
    log("Verifying token...")
    req = urllib.request.Request(f"{API_BASE}/seasons/{SEASON_ID}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            log(f"  Token OK — Season: {body.get('name', 'Unknown')}")
    except Exception as e:
        log(f"  Token verification FAILED: {e}")
        sys.exit(1)

    # Find Middle School Worlds event
    ms_event = find_ms_worlds_event()
    if not ms_event:
        log("Could not find Middle School Worlds event. Trying known event ID...")
        # Try fetching directly with a likely event ID range
        for test_id in range(55000, 56000):
            try:
                req = urllib.request.Request(f"{API_BASE}/events/{test_id}", headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read().decode())
                    name = body.get("name", "")
                    if "middle" in name.lower() and "world" in name.lower():
                        ms_event = body
                        log(f"  Found: {name} (ID: {test_id})")
                        break
            except Exception:
                continue
            time.sleep(0.2)

    if not ms_event:
        log("ERROR: Could not find Middle School Worlds event.")
        log("  You can set MS_WORLDS_EVENT_ID manually in this script.")
        sys.exit(1)

    event_id = ms_event.get("id")
    log(f"Using event ID: {event_id}")

    # Fetch teams
    log("Fetching MS teams...")
    raw_teams = api_get(f"/events/{event_id}/teams")
    log(f"  Got {len(raw_teams)} teams")

    if not raw_teams:
        log("No teams found. Check event ID.")
        sys.exit(1)

    # Fetch rankings
    log("Fetching MS rankings...")
    rankings = api_get(f"/events/{event_id}/rankings")
    log(f"  Got {len(rankings)} ranking entries")

    rankings_by_team = {}
    div_map = {}
    for r in rankings:
        team_num = r.get("team", {}).get("number", "")
        if team_num:
            if team_num not in rankings_by_team:
                rankings_by_team[team_num] = []
            rankings_by_team[team_num].append(r)

            # Extract division
            div_info = r.get("division", {})
            div_name = div_info.get("name", "")
            for known in DIVISION_NAMES:
                if known.lower() in div_name.lower():
                    div_map[team_num] = known
                    break

    # Fetch skills
    log("Fetching MS skills...")
    skills = api_get(f"/events/{event_id}/skills")
    log(f"  Got {len(skills)} skills entries")

    skills_by_team = {}
    for s in skills:
        team_num = s.get("team", {}).get("number", "")
        if team_num:
            if team_num not in skills_by_team:
                skills_by_team[team_num] = []
            skills_by_team[team_num].append(s)

    # If no rankings yet (event hasn't started), try getting season stats from recent events
    if not rankings:
        log("No Worlds rankings yet. Collecting from recent season events...")
        events = api_get(f"/seasons/{SEASON_ID}/events")
        events.sort(key=lambda e: e.get("end", ""), reverse=True)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        completed = [e for e in events if (e.get("end", "") or "") <= now_str]
        teams_dict = {t.get("number"): t for t in raw_teams}

        for i, event in enumerate(completed[:15]):
            eid = event.get("id")
            ename = event.get("name", "")
            # Skip the worlds event itself
            if eid == event_id:
                continue
            log(f"  Event {i+1}: {ename}")
            er = api_get(f"/events/{eid}/rankings")
            for r in er:
                tn = r.get("team", {}).get("number", "")
                if tn and tn in teams_dict:
                    if tn not in rankings_by_team:
                        rankings_by_team[tn] = []
                    rankings_by_team[tn].append(r)
            es = api_get(f"/events/{eid}/skills")
            for s in es:
                tn = s.get("team", {}).get("number", "")
                if tn and tn in teams_dict:
                    if tn not in skills_by_team:
                        skills_by_team[tn] = []
                    skills_by_team[tn].append(s)
            time.sleep(0.5)

    # Process teams
    log("Processing teams...")
    processed = []

    for team in raw_teams:
        team_num = team.get("number", "")
        team_name = team.get("team_name", "")
        location = team.get("location", {})
        region = location.get("region", "") or location.get("city", "Unknown")
        country = location.get("country", "Unknown")
        division = div_map.get(team_num, "")

        # Rankings data
        team_rankings = rankings_by_team.get(team_num, [])
        best_opr = 0
        best_dpr = 0
        best_ccwm = 0
        total_wins = 0
        total_losses = 0
        best_wp = 0
        best_awp = 0

        for r in team_rankings:
            best_opr = max(best_opr, r.get("opr", 0) or 0)
            best_dpr = max(best_dpr, r.get("dpr", 0) or 0)
            best_ccwm = max(best_ccwm, r.get("ccwm", 0) or 0)
            total_wins += r.get("wins", 0) or 0
            total_losses += r.get("losses", 0) or 0
            w = r.get("wins", 0) or 0
            l = r.get("losses", 0) or 0
            t = r.get("ties", 0) or 0
            played = w + l + t
            if played > 0:
                wp = r.get("wp", 0) or 0
                ap = r.get("ap", 0) or 0
                best_wp = max(best_wp, round(wp / played, 1))
                best_awp = max(best_awp, round(ap / played, 1))

        matches_played = total_wins + total_losses
        win_pct = round((total_wins / matches_played * 100), 1) if matches_played > 0 else 0

        # Skills data
        team_skills = skills_by_team.get(team_num, [])
        driver_max = 0
        auto_max = 0
        for s in team_skills:
            score = s.get("score", 0) or 0
            if s.get("type", "") == "driver":
                driver_max = max(driver_max, score)
            elif s.get("type", "") == "programming":
                auto_max = max(auto_max, score)
        total_max = driver_max + auto_max

        # TrueSkill approximation
        true_skill = round(
            (total_max / 20) * 0.3 +
            (win_pct / 100) * 10 * 0.3 +
            (best_opr / 3) * 0.2 +
            (best_ccwm / 3) * 0.2,
            1
        )

        # Estimate qual/elim split
        qual_wins = round(total_wins * 0.7)
        qual_losses = round(total_losses * 0.7)
        elim_wins = total_wins - qual_wins
        elim_losses = total_losses - qual_losses
        qual_total = qual_wins + qual_losses
        qual_win_pct = round((qual_wins / qual_total * 100), 1) if qual_total > 0 else 0
        elim_total = elim_wins + elim_losses
        elim_win_pct = round((elim_wins / elim_total * 100), 1) if elim_total > 0 else 0

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
            "awpPerMatch": best_awp,
            "wpPerMatch": best_wp,
            "opr": round(best_opr, 1),
            "dpr": round(best_dpr, 1),
            "driverMax": driver_max,
            "autoMax": auto_max,
            "totalMax": total_max,
            "worldsOdds": "N/A",
            "divRank": 0,
            "divProjectedPoints": 0,
            "tier": "Rising",
            "divTotal": 0,
            "divStrengthRank": 0,
        })

    # Sort by TrueSkill, assign ranks and tiers
    processed.sort(key=lambda x: x["trueSkill"], reverse=True)
    total = len(processed)
    for i, team in enumerate(processed):
        team["trueSkillRank"] = i + 1
        percentile = ((i + 1) / total) * 100
        team["tier"] = calculate_tier(percentile)

    # Division ranks
    divisions = {}
    for team in processed:
        div = team["division"]
        if div:
            if div not in divisions:
                divisions[div] = []
            divisions[div].append(team)

    div_strengths = {}
    for div_name, div_teams in divisions.items():
        avg_ts = sum(t["trueSkill"] for t in div_teams) / len(div_teams)
        div_strengths[div_name] = avg_ts

    sorted_divs = sorted(div_strengths.items(), key=lambda x: x[1], reverse=True)
    div_str_rank = {name: rank + 1 for rank, (name, _) in enumerate(sorted_divs)}

    for div_name, div_teams in divisions.items():
        div_sorted = sorted(div_teams, key=lambda x: x["trueSkill"], reverse=True)
        for i, team in enumerate(div_sorted):
            team["divRank"] = i + 1
            team["divTotal"] = len(div_teams)
            team["divStrengthRank"] = div_str_rank.get(div_name, 0)

    # Save
    output_path = os.path.join(SCRIPT_DIR, "ms_teams_data.json")
    with open(output_path, "w") as f:
        json.dump(processed, f)

    log(f"\nDone! Saved {len(processed)} middle school teams to ms_teams_data.json")
    log(f"Divisions found: {len(divisions)}")
    for div_name in sorted(divisions.keys()):
        log(f"  {div_name}: {len(divisions[div_name])} teams")

    # Summary
    with_stats = sum(1 for t in processed if t["matchesPlayed"] > 0)
    with_skills = sum(1 for t in processed if t["totalMax"] > 0)
    log(f"\nData coverage:")
    log(f"  Teams with match data: {with_stats}/{total}")
    log(f"  Teams with skills data: {with_skills}/{total}")


if __name__ == "__main__":
    main()
