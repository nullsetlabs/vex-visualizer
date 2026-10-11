"""
Approximate subregions within a state, for the region picker on the page.

VEX runs separate Eastern PA and Western PA state championships, but its data
labels every Pennsylvania team and event only "Pennsylvania". This module
estimates the split and writes data/subregions.json:

  {"United States|Pennsylvania": {
      "labels": ["Eastern PA", "Western PA"],
      "lon": -77.8,                 dividing longitude; west of it is Western PA
      "teams": {"6121E": "Eastern PA", ...},
      "events": {"61234": "Western PA", ...},
      "basis": {...how many teams each rule placed...},
      "updated": "2026-10-12"}}

A team's half comes from, in order:
  1. the state championship it is registered for this season,
  2. the state championship it attended last season,
  3. its listed location, east or west of the dividing longitude.
The dividing longitude is the line that best separates the teams of the two
championships (last season, plus this season once teams register). Events use
their own location. Coordinates are used only here and are never written out.

Called by season_pipeline.py in full runs (a few dozen API requests, refreshed
at most every REFRESH_DAYS). Rebuild runs reuse the saved file.
"""

import os
import re
from datetime import date

SPLITS = {
    "United States|Pennsylvania": {
        "country": "United States", "region": "Pennsylvania",
        "east": "Eastern PA", "west": "Western PA",
        "patterns": [(re.compile(r"\beast(ern)?\s+(pa|pennsylvania)\b", re.I), "east"),
                     (re.compile(r"\bwest(ern)?\s+(pa|pennsylvania)\b", re.I), "west")],
        # Used only until championship data exists: roughly through State College.
        "fallback_lon": -77.8,
    },
}
LAST_SEASON_ID = 197      # Push Back 2025-2026
REFRESH_DAYS = 6


def lon_of(obj):
    c = ((obj or {}).get("location") or {}).get("coordinates") or {}
    try:
        lon = float(c.get("lon"))
    except (TypeError, ValueError):
        return None
    return lon if -180 <= lon <= 180 and lon != 0 else None


def fit_line(points):
    """Longitude that best separates (lon, 'east'|'west') points: west of it is west.
    Returns None unless both sides have at least 3 points."""
    if sum(1 for _, s in points if s == "east") < 3 or sum(1 for _, s in points if s == "west") < 3:
        return None
    lons = sorted({lon for lon, _ in points})
    best = None   # (errors, -gap, line): fewest misplaced teams, then the widest empty gap
    for a, b in zip(lons, lons[1:]):
        t = (a + b) / 2
        errors = sum(1 for lon, s in points if (s == "east") != (lon > t))
        cand = (errors, -(b - a), t)
        if best is None or cand < best:
            best = cand
    return round(best[2], 2) if best else None


def championship_side(cfg, event):
    name = event.get("name") or ""
    if "championship" not in name.lower():
        return None
    for pattern, side in cfg["patterns"]:
        if pattern.search(name):
            return side
    return None


def championship_fields(client, cfg, season_id, is_canceled, log):
    """{team number: (side, lon)} for the state's two championships in one season."""
    out = {}
    events = client.pages("/events", {"season[]": season_id, "region": cfg["region"]})
    champs = [(e, championship_side(cfg, e)) for e in events if not is_canceled(e)]
    champs = [(e, s) for e, s in champs if s]
    for e, side in champs:
        teams = client.pages(f"/events/{e['id']}/teams")
        log(f"    season {season_id}: {cfg[side]} championship {e.get('sku')} with {len(teams)} teams")
        for t in teams:
            n = (t.get("number") or "").strip()
            if n:
                out[n] = (side, lon_of(t))
    return out


def build_split(client, cfg, season_id, program_id, event_list, is_canceled, log):
    this = championship_fields(client, cfg, season_id, is_canceled, log)
    last = championship_fields(client, cfg, LAST_SEASON_ID, is_canceled, log)
    points = [(lon, side) for side, lon in list(last.values()) + list(this.values()) if lon is not None]
    line = fit_line(points)
    lon_line = line if line is not None else cfg["fallback_lon"]
    side_of_lon = lambda lon: None if lon is None else ("east" if lon > lon_line else "west")

    registered = client.pages("/teams", {"program[]": program_id, "country[]": cfg["country"], "registered": "true"})
    teams, basis = {}, {"thisSeasonChampionship": 0, "lastSeasonChampionship": 0, "location": 0}
    for t in registered:
        if ((t.get("location") or {}).get("region") or "") != cfg["region"]:
            continue
        n = (t.get("number") or "").strip()
        if not n:
            continue
        if n in this:
            side, rule = this[n][0], "thisSeasonChampionship"
        elif n in last:
            side, rule = last[n][0], "lastSeasonChampionship"
        else:
            side, rule = side_of_lon(lon_of(t)), "location"
        if side:
            teams[n] = cfg[side]
            basis[rule] += 1
    for n, (side, _) in this.items():          # championship teams not in the registered list
        if n not in teams:
            teams[n] = cfg[side]
            basis["thisSeasonChampionship"] += 1
    events = {}
    for e in event_list:
        if ((e.get("location") or {}).get("region") or "") == cfg["region"]:
            side = side_of_lon(lon_of(e))
            if side:
                events[str(e["id"])] = cfg[side]
    basis.update({"lastSeasonChampionshipTeams": len(last), "thisSeasonChampionshipTeams": len(this),
                  "lineFrom": "championship fields" if line is not None else "fallback"})
    log(f"  {cfg['region']}: dividing longitude {lon_line} ({basis['lineFrom']}); "
        f"{len(teams)} teams and {len(events)} events placed {basis}")
    return {"labels": [cfg["east"], cfg["west"]], "lon": lon_line,
            "teams": dict(sorted(teams.items())), "events": dict(sorted(events.items())), "basis": basis}


def refresh(client, path, season_id, program_id, event_list, is_canceled, read_json, write_json, log, today=None):
    """Rebuild data/subregions.json when it is missing or older than REFRESH_DAYS.
    Returns the current contents. Any failure keeps the previous file."""
    today = today or date.today()
    current = read_json(path, {}) or {}
    out = dict(current)
    for key, cfg in SPLITS.items():
        prev = current.get(key) or {}
        age = (today - date.fromisoformat(prev["updated"])).days if prev.get("updated") else None
        if age is not None and age < REFRESH_DAYS:
            continue
        log(f"Estimating {cfg['east']} / {cfg['west']}...")
        try:
            split = build_split(client, cfg, season_id, program_id, event_list, is_canceled, log)
            split["updated"] = today.isoformat()
            out[key] = split
        except Exception as err:   # never block the data update
            log(f"  kept the previous {cfg['region']} split: {err!r}")
    if out != current:
        write_json(path, out)
    return out


def apply(subregions, index, teams):
    """Add 'sub' to event index rows and team records that the split places."""
    team_sub, event_sub = {}, {}
    for split in (subregions or {}).values():
        team_sub.update(split.get("teams") or {})
        event_sub.update(split.get("events") or {})
    for row in index:
        if str(row["id"]) in event_sub:
            row["sub"] = event_sub[str(row["id"])]
    for n, t in teams.items():
        t["sub"] = team_sub.get(n, "")


def summary(subregions):
    """What the page needs to know about each split (no team lists)."""
    return {key: {"labels": s.get("labels"), "basis": s.get("basis"), "updated": s.get("updated")}
            for key, s in (subregions or {}).items()}
