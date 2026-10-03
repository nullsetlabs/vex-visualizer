#!/usr/bin/env python3
"""
VEX Visualizer season pipeline (V5RC 2026-2027: Override).

Pulls official season data and writes the JSON files that the season page
(visualizer/index.html) loads from visualizer/data/.

Sources
  1. VEX Events API, events.vex.com/api/v2 (run by VEX; it replaced the old
     RobotEvents API). Bearer token in VEX_API_TOKEN (ROBOTEVENTS_TOKEN, the
     old name, also works). Events, teams, rankings, matches, skills, awards.
  2. events.vex.com/api/seasons/{id}/skills (public, no token): the official
     World Skills Standings feed used by the events.vex.com standings page.

Caching
  Each event is written once to data/events/{id}.json. An event is refetched
  only while it is in progress or its awards are not final; after that the
  cached file is reused, so a normal run only touches new or live events.

Usage
  python season_pipeline.py                # full run: event list, new or live events, standings, summaries
  python season_pipeline.py --live         # only events in progress now; exits quickly if there are none
  python season_pipeline.py --rebuild      # no network; rebuild the summary files from the cache
  python season_pipeline.py --max-events 40 --budget-min 15

Computed values (OPR, DPR, CCWM) are least-squares estimates from qualification
match scores. They are labeled as computed on the page.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "https://events.vex.com/api/v2"
STANDINGS_URL = "https://events.vex.com/api/seasons/{season}/skills?post_season=0&grade_level={grade}"
TOKEN = (os.environ.get("VEX_API_TOKEN") or os.environ.get("ROBOTEVENTS_TOKEN") or "").strip()

PROGRAM_ID = 1          # V5RC
SEASON_ID = 204         # Override 2026-2027 (Push Back 2025-2026 was 197)
SEASON_NAME = "Override"
SEASON_LABEL = "2026-2027"

# Guards so no other season's data gets in. Everything is keyed to SEASON_ID,
# never to the game name; the season record must say 2026-2027, and events
# must belong to that season and start after the game reveal (Apr 24, 2026).
SEASON_YEARS = (2026, 2027)
SEASON_FIRST_DAY = date(2026, 4, 24)

# Worlds mode stays dormant until the 2027 World Championship event is listed.
# When a World-level event appears in the season list it is reported in
# season.json; no Worlds-specific views are built yet.
WORLDS_EVENT_ID = None

USER_AGENT = "VEX-Visualizer/2.0 (+https://vex.nullsetlabs.org/visualizer/)"
REQUEST_TIMEOUT = 30    # seconds per request
MAX_ATTEMPTS = 4        # per request, for 5xx / network errors
MAX_RATE_LIMITED = 8    # per request, waits after HTTP 429 (too many requests)
# The VEX Events API allows roughly 100 requests a minute (seen Oct 2026:
# HTTP 429 about once a minute at ~2.7 requests/second). 0.7 s between
# request starts keeps a run near 85 a minute.
MIN_INTERVAL = 0.7

# An event whose awards never get finalized stops being refetched this many
# days after it ends.
STALE_AFTER_DAYS = 21

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
EVENTS_DIR = os.path.join(DATA_DIR, "events")

# Match round codes in the v2 API (checked against Override events).
ROUND_NAMES = {1: "Practice", 2: "Qualification", 3: "Quarterfinal", 4: "Semifinal",
               5: "Final", 6: "Round of 16", 7: "Round of 32", 8: "Round of 64"}
# Elimination depth, higher is further.
ELIM_DEPTH = {8: 1, 7: 2, 6: 3, 3: 4, 4: 5, 5: 6}
DEPTH_LABEL = {1: "Round of 64", 2: "Round of 32", 3: "Round of 16",
               4: "Quarterfinalist", 5: "Semifinalist", 6: "Finalist"}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# HTTP client: bounded retries and an overall time budget, so a run can never
# hang until the job limit.
# ---------------------------------------------------------------------------

class AuthError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Client:
    def __init__(self, token, budget_seconds):
        self.token = token
        self.deadline = time.monotonic() + budget_seconds
        self.calls = 0
        self.next_at = 0.0
        self.limits_logged = False

    def remaining(self):
        return self.deadline - time.monotonic()

    def _throttle(self):
        """Space request starts MIN_INTERVAL apart to stay under the API's rate limit."""
        wait = self.next_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.next_at = time.monotonic() + MIN_INTERVAL

    def get_json(self, url, auth=True):
        """GET a JSON document. Returns None on 404."""
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        errors = limited = 0
        while True:
            if self.remaining() <= 0:
                raise BudgetExceeded()
            self._throttle()
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                    final_url = resp.geturl()
                    ctype = resp.headers.get("Content-Type", "")
                    limits = {k: v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")}
                    body = resp.read()
                if "/auth/login" in final_url or "json" not in ctype.lower():
                    raise AuthError(f"expected JSON from {url} but got {ctype or 'no content type'} "
                                    f"at {final_url}; the token is missing, expired or not valid for events.vex.com")
                if auth and limits and not self.limits_logged:
                    log(f"  API rate limit headers: {limits}")
                    self.limits_logged = True
                return json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise AuthError(f"HTTP {e.code} from {url}; check VEX_API_TOKEN") from e
                if e.code == 404:
                    return None
                if e.code == 429:
                    # Rate limited: wait for the window to reset rather than give up.
                    limited += 1
                    if limited > MAX_RATE_LIMITED:
                        raise RuntimeError(f"still rate limited on {short(url)} after {MAX_RATE_LIMITED} waits")
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    wait = max(int(retry_after) if retry_after and retry_after.isdigit() else 0, 10 * limited)
                    wait = min(wait, 90)
                    if wait >= self.remaining():
                        raise BudgetExceeded()
                    log(f"  rate limited on {short(url)}; waiting {wait}s ({limited}/{MAX_RATE_LIMITED})")
                    time.sleep(wait)
                    continue
                if e.code < 500:
                    raise
                errors += 1
                last_err = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                errors += 1
                last_err = str(e)
            if errors >= MAX_ATTEMPTS:
                raise RuntimeError(f"giving up on {short(url)} after {errors} errors ({last_err})")
            wait = min(5 * errors, max(0, self.remaining()))
            log(f"  {last_err} on {short(url)}; retry {errors}/{MAX_ATTEMPTS - 1} in {wait:.0f}s")
            time.sleep(wait)

    def pages(self, path, params=None, max_pages=60):
        """GET every page of a paginated v2 endpoint and return the combined data list."""
        query = list((params or {}).items()) + [("per_page", 250)]
        out = []
        page = 1
        while page <= max_pages:
            url = f"{API_BASE}{path}?{urllib.parse.urlencode(query + [('page', page)])}"
            body = self.get_json(url)
            if body is None:
                break
            out.extend(body.get("data", []))
            if page >= (body.get("meta") or {}).get("last_page", 1):
                break
            page += 1
        return out


def short(url):
    return url.replace(API_BASE, "").split("&page=")[0][:90]


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    """Write compact JSON atomically. Returns True if the content changed."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    old = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def day(s):
    """'2026-09-25T00:00:00-04:00' -> date(2026, 9, 25)."""
    return date.fromisoformat(s[:10]) if s else None


def today_utc():
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Season event list
# ---------------------------------------------------------------------------

class SeasonMismatch(Exception):
    pass


def verify_season(client):
    """Stop unless SEASON_ID is the V5RC 2026-2027 season."""
    s = client.get_json(f"{API_BASE}/seasons/{SEASON_ID}") or {}
    name = s.get("name") or ""
    years = (s.get("years_start"), s.get("years_end"))
    program = (s.get("program") or {}).get("id")
    ok = (f"{SEASON_YEARS[0]}-{SEASON_YEARS[1]}" in name and program in (None, PROGRAM_ID)
          and years in ((None, None), SEASON_YEARS))
    if not ok:
        raise SeasonMismatch(f"season {SEASON_ID} is '{name}' {years}, not V5RC {SEASON_LABEL}")
    log(f"Season check OK: {name} (id {SEASON_ID})")


def in_season(e):
    """True if an event belongs to this season (by id and by date)."""
    sid = e.get("season")
    sid = sid.get("id") if isinstance(sid, dict) else sid
    start = day(e.get("start"))
    return (sid in (None, SEASON_ID)) and (start is None or start >= SEASON_FIRST_DAY)


CANCELED = re.compile(r"^\s*CANCEL+ED\b", re.I)


def is_canceled(e):
    """VEX marks canceled events by starting the name with 'CANCELED:'."""
    return bool(CANCELED.match(e.get("name") or ""))


def fetch_event_list(client):
    log(f"Fetching the season {SEASON_ID} event list...")
    events = client.pages("/events", {"season[]": SEASON_ID})
    kept = [e for e in events if in_season(e) and not is_canceled(e)]
    if len(kept) != len(events):
        log(f"  left out {len(events) - len(kept)} canceled events or events from another season")
    log(f"  {len(kept)} events")
    return kept


def grade_from_name(name):
    n = (name or "").lower()
    if "blended" in n:
        return "Blended"
    ms = "middle school" in n or re.search(r"\bms\b", n)
    hs = "high school" in n or re.search(r"\bhs\b", n)
    if ms and not hs:
        return "Middle School"
    if hs and not ms:
        return "High School"
    return ""


def event_status(ev, today):
    start, end = day(ev.get("start")), day(ev.get("end"))
    if start and start > today:
        return "upcoming"
    if ev.get("ongoing") or (end and end >= today):
        return "live"
    return "done"


def is_complete(ev, today):
    """True once an event's results will not change any more."""
    end = day(ev.get("end"))
    if not end:
        return False
    if end < today - timedelta(days=STALE_AFTER_DAYS):
        return True
    return bool(ev.get("awards_finalized")) and not ev.get("ongoing") and end < today


# ---------------------------------------------------------------------------
# Per-event fetch and compaction
# ---------------------------------------------------------------------------

AWARD_SUFFIX = re.compile(r"\s*\((V5|V5RC|VRC|VEX V5)\)\s*$", re.I)


def team_num(obj):
    """Team number from a v2 IdInfo ({id, name, code}) or a Team object."""
    if not isinstance(obj, dict):
        return ""
    return (obj.get("number") or obj.get("name") or "").strip()


def fetch_event(client, ev):
    eid = ev["id"]
    # Refresh the event itself so ongoing / awards_finalized are current.
    ev = client.get_json(f"{API_BASE}/events/{eid}") or ev
    if not in_season(ev):
        raise SeasonMismatch(f"event {eid} {ev.get('sku')} is not in season {SEASON_ID}")
    teams = client.pages(f"/events/{eid}/teams")
    skills = client.pages(f"/events/{eid}/skills")
    awards = client.pages(f"/events/{eid}/awards")
    rankings, matches = [], []
    for d in ev.get("divisions") or [{"id": 1, "name": "Default Division"}]:
        rankings += client.pages(f"/events/{eid}/divisions/{d['id']}/rankings")
        matches += client.pages(f"/events/{eid}/divisions/{d['id']}/matches")
    return compact_event(ev, teams, skills, awards, rankings, matches)


def compact_event(ev, teams, skills, awards, rankings, matches):
    loc = ev.get("location") or {}
    out_teams = []
    for t in teams:
        tl = t.get("location") or {}
        out_teams.append({
            "n": t.get("number", ""),
            "name": (t.get("team_name") or "").strip(),
            "org": (t.get("organization") or "").strip(),
            "grade": t.get("grade") or "",
            "city": tl.get("city") or "",
            "region": tl.get("region") or "",
            "country": tl.get("country") or "",
        })

    grades = {t["grade"] for t in out_teams if t["grade"]}
    if len(grades) == 1:
        grade = grades.pop()
    elif len(grades) > 1:
        grade = "Blended"
    else:
        grade = grade_from_name(ev.get("name"))

    out_rank = []
    for r in rankings:
        out_rank.append({
            "div": (r.get("division") or {}).get("id"),
            "rank": r.get("rank"),
            "team": team_num(r.get("team")),
            "w": r.get("wins") or 0, "l": r.get("losses") or 0, "t": r.get("ties") or 0,
            "wp": r.get("wp") or 0, "ap": r.get("ap") or 0, "sp": r.get("sp") or 0,
            "high": r.get("high_score") or 0, "avg": r.get("average_points") or 0,
        })
    out_rank.sort(key=lambda x: (x["div"] or 0, x["rank"] or 9999))

    by_team = {}
    for s in skills:
        n = team_num(s.get("team"))
        if not n:
            continue
        e = by_team.setdefault(n, {"team": n, "driver": 0, "prog": 0})
        if s.get("type") == "driver":
            e["driver"] = max(e["driver"], s.get("score") or 0)
        elif s.get("type") == "programming":
            e["prog"] = max(e["prog"], s.get("score") or 0)
    out_skills = sorted(by_team.values(), key=lambda x: (-(x["driver"] + x["prog"]), -x["prog"]))
    for i, s in enumerate(out_skills, 1):
        s["rank"] = i

    out_matches = []
    for m in matches:
        al = {a.get("color"): a for a in m.get("alliances") or []}
        red, blue = al.get("red") or {}, al.get("blue") or {}
        rs, bs = red.get("score") or 0, blue.get("score") or 0
        played = bool(m.get("started")) or (rs + bs) > 0
        out_matches.append({
            "div": (m.get("division") or {}).get("id"),
            "round": m.get("round"), "inst": m.get("instance"), "num": m.get("matchnum"),
            "name": m.get("name") or "",
            "red": [team_num(x.get("team")) for x in red.get("teams") or [] if not x.get("sitting")],
            "blue": [team_num(x.get("team")) for x in blue.get("teams") or [] if not x.get("sitting")],
            "rs": rs, "bs": bs, "played": played,
            "time": m.get("started") or m.get("scheduled"),
        })
    out_matches.sort(key=lambda x: (x["div"] or 0, x["round"] != 2, x["round"] or 0, x["inst"] or 0, x["num"] or 0))

    out_awards = []
    for a in sorted(awards, key=lambda a: a.get("order") or 0):
        out_awards.append({
            "title": AWARD_SUFFIX.sub("", a.get("title") or "").strip(),
            "quals": a.get("qualifications") or [],
            "teams": [team_num(w.get("team")) for w in a.get("teamWinners") or []],
            "people": a.get("individualWinners") or [],
        })

    today = today_utc()
    return {
        "id": ev["id"], "sku": ev.get("sku"), "name": ev.get("name"),
        "season": (ev.get("season") or {}).get("id") or SEASON_ID,
        "start": (ev.get("start") or "")[:10], "end": (ev.get("end") or "")[:10],
        "level": ev.get("level"), "type": ev.get("event_type"),
        "city": loc.get("city") or "", "region": loc.get("region") or "", "country": loc.get("country") or "",
        "grade": grade,
        "ongoing": bool(ev.get("ongoing")), "awardsFinalized": bool(ev.get("awards_finalized")),
        "complete": is_complete(ev, today),
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "divisions": [{"id": d.get("id"), "name": d.get("name")} for d in ev.get("divisions") or []],
        "teams": out_teams, "rankings": out_rank, "skills": out_skills,
        "matches": out_matches, "awards": out_awards,
        "opr": compute_opr(out_matches),
    }


# ---------------------------------------------------------------------------
# OPR / DPR / CCWM (least squares over qualification matches, per division)
# ---------------------------------------------------------------------------

def solve(a, b):
    """Solve a x = b by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        if abs(piv) < 1e-12:
            continue
        for r in range(c + 1, n):
            f = m[r][c] / piv
            if f:
                row_r, row_c = m[r], m[c]
                for k in range(c, n + 1):
                    row_r[k] -= f * row_c[k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        piv = m[r][r]
        if abs(piv) < 1e-12:
            continue
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / piv
    return x


def compute_opr(matches, ridge=0.01, min_matches=3):
    """Return {team: [opr, dpr, ccwm]} for teams with at least min_matches played qualification matches."""
    out = {}
    by_div = {}
    for m in matches:
        if m["round"] == 2 and m["played"] and m["red"] and m["blue"]:
            by_div.setdefault(m["div"], []).append(m)
    for div_matches in by_div.values():
        teams = sorted({t for m in div_matches for t in m["red"] + m["blue"]})
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        if n < 4:
            continue
        ata = [[0.0] * n for _ in range(n)]
        atb_off = [0.0] * n
        atb_def = [0.0] * n
        played = [0] * n
        for m in div_matches:
            for own, opp, own_score, opp_score in ((m["red"], m["blue"], m["rs"], m["bs"]),
                                                   (m["blue"], m["red"], m["bs"], m["rs"])):
                ids = [idx[t] for t in own]
                for i in ids:
                    atb_off[i] += own_score
                    atb_def[i] += opp_score
                    played[i] += 1
                    for j in ids:
                        ata[i][j] += 1
        for i in range(n):
            ata[i][i] += ridge
        opr = solve(ata, atb_off)
        dpr = solve(ata, atb_def)
        for t, i in idx.items():
            if played[i] >= min_matches:
                out[t] = [round(opr[i], 1), round(dpr[i], 1), round(opr[i] - dpr[i], 1)]
    return out


# ---------------------------------------------------------------------------
# Official World Skills Standings (public feed)
# ---------------------------------------------------------------------------

def fetch_standings(client):
    out = {}
    for key, grade in (("hs", "High School"), ("ms", "Middle School")):
        url = STANDINGS_URL.format(season=SEASON_ID, grade=urllib.parse.quote(grade))
        rows = client.get_json(url, auth=False) or []
        rows = [r for r in rows if SEASON_LABEL in ((r.get("event") or {}).get("seasonName") or SEASON_LABEL)]
        out[key] = [{
            "rank": r.get("rank"),
            "team": (r.get("team") or {}).get("team", ""),
            "name": (r.get("team") or {}).get("teamName", ""),
            "org": (r.get("team") or {}).get("organization", ""),
            "region": (r.get("team") or {}).get("region", ""),
            "country": (r.get("team") or {}).get("country", ""),
            "score": (r.get("scores") or {}).get("score", 0),
            "driver": (r.get("scores") or {}).get("driver", 0),
            "prog": (r.get("scores") or {}).get("programming", 0),
            "maxDriver": (r.get("scores") or {}).get("maxDriver", 0),
            "maxProg": (r.get("scores") or {}).get("maxProgramming", 0),
            "event": (r.get("event") or {}).get("sku", ""),
            "date": (r.get("event") or {}).get("startDate", ""),
        } for r in rows]
        log(f"  Skills standings {grade}: {len(out[key])} teams")
    return out


# ---------------------------------------------------------------------------
# Summary files built from the cache
# ---------------------------------------------------------------------------

def load_cached_events():
    out = {}
    if os.path.isdir(EVENTS_DIR):
        for fn in os.listdir(EVENTS_DIR):
            if fn.endswith(".json"):
                ev = read_json(os.path.join(EVENTS_DIR, fn))
                if ev and "id" in ev and ev.get("season", SEASON_ID) == SEASON_ID and in_season(ev) and not is_canceled(ev):
                    out[ev["id"]] = ev
    return out


def elim_result(ev, team):
    for a in ev["awards"]:
        if team in a["teams"]:
            if a["title"].startswith("Tournament Champions"):
                return "Champion"
            if a["title"].startswith("Tournament Finalists"):
                return "Finalist"
    depth = 0
    for m in ev["matches"]:
        if m["round"] in ELIM_DEPTH and m["played"] and team in m["red"] + m["blue"]:
            depth = max(depth, ELIM_DEPTH[m["round"]])
    return DEPTH_LABEL.get(depth, "")


TEAM_BUCKETS = 64


def team_bucket(n):
    """Shard key for data/teams/{bucket}.json. The page computes the same value."""
    return sum((i + 1) * ord(c) for i, c in enumerate(n.upper())) % TEAM_BUCKETS


def month_of(d):
    return (d or "")[:7]


def event_row(base, ev, today):
    """One line of the event index: listing info plus a results summary when cached."""
    base = base or {}
    src = ev or base
    loc = base.get("location") or {}
    row = {
        "id": src["id"],
        "sku": src.get("sku"),
        "name": src.get("name"),
        "start": (ev or {}).get("start") or (base.get("start") or "")[:10],
        "end": (ev or {}).get("end") or (base.get("end") or "")[:10],
        "level": src.get("level"),
        "city": ev["city"] if ev else loc.get("city", ""),
        "region": ev["region"] if ev else loc.get("region", ""),
        "country": ev["country"] if ev else loc.get("country", ""),
        "grade": ev["grade"] if ev else grade_from_name(base.get("name")),
    }
    row["month"] = month_of(row["start"])
    row["status"] = event_status(base if base else {"start": ev["start"], "end": ev["end"], "ongoing": ev["ongoing"]}, today)
    row["results"] = bool(ev and (ev["matches"] or ev["awards"] or ev["skills"]))
    if ev:
        played = [m for m in ev["matches"] if m["played"]]
        scores = [s for m in played for s in (m["rs"], m["bs"])]
        row["teams"] = len(ev["teams"])
        row["matches"] = len(played)
        row["topScore"] = max(scores) if scores else 0
        for key, title in (("champions", "Tournament Champions"), ("excellence", "Excellence Award"),
                           ("skillsChampion", "Robot Skills Champion")):
            row[key] = [t for a in ev["awards"] if a["title"].startswith(title) for t in a["teams"]]
        row["topSkills"] = (ev["skills"][0]["driver"] + ev["skills"][0]["prog"]) if ev["skills"] else 0
    return row


TEAM_INDEX_COLS = ["team", "name", "org", "grade", "city", "region", "country", "events", "w", "l", "t",
                   "winPct", "titles", "excellence", "awards", "worlds", "worldsAward", "skills", "skillsRank", "opr", "last"]


def build_summaries(event_list, cached, standings):
    """Return (event index, team index, team buckets, season overview)."""
    today = today_utc()
    listed = {e["id"]: e for e in event_list}
    index = [event_row(listed.get(eid), cached.get(eid), today) for eid in set(listed) | set(cached)]
    index.sort(key=lambda r: (r["start"] or "9999", r["name"] or ""))
    by_id = {r["id"]: r for r in index}

    # Team records, built event by event in date order.
    teams = {}
    for ev in sorted(cached.values(), key=lambda e: e["start"]):
        info = {t["n"]: t for t in ev["teams"]}
        competed = {r["team"] for r in ev["rankings"]} | {s["team"] for s in ev["skills"]}
        competed |= {t for m in ev["matches"] if m["played"] for t in m["red"] + m["blue"]}
        for n in competed:
            if not n:
                continue
            ti = info.get(n, {})
            t = teams.setdefault(n, {"team": n, "name": "", "org": "", "grade": "", "city": "", "region": "", "country": "",
                                     "events": [], "w": 0, "l": 0, "t": 0, "ew": 0, "el": 0,
                                     "awards": [], "worlds": None})
            for k in ("name", "org", "grade", "city", "region", "country"):
                if ti.get(k):
                    t[k] = ti[k]
            rk = next((r for r in ev["rankings"] if r["team"] == n), None)
            if rk:
                t["w"] += rk["w"]; t["l"] += rk["l"]; t["t"] += rk["t"]
            for m in ev["matches"]:
                if m["played"] and m["round"] in ELIM_DEPTH and n in m["red"] + m["blue"]:
                    mine, theirs = (m["rs"], m["bs"]) if n in m["red"] else (m["bs"], m["rs"])
                    if mine > theirs:
                        t["ew"] += 1
                    elif mine < theirs:
                        t["el"] += 1
            won = [a for a in ev["awards"] if n in a["teams"]]
            for a in won:
                t["awards"].append([a["title"], ev["id"]])
                if "World Championship" in a["quals"] and not t["worlds"]:
                    t["worlds"] = [a["title"], ev["id"]]
            sk = next((s for s in ev["skills"] if s["team"] == n), None)
            opr = ev["opr"].get(n)
            t["events"].append({
                "id": ev["id"],
                "rank": rk["rank"] if rk else None,
                "of": sum(1 for r in ev["rankings"] if r["div"] == rk["div"]) if rk else None,
                "w": rk["w"] if rk else 0, "l": rk["l"] if rk else 0, "t": rk["t"] if rk else 0,
                "result": elim_result(ev, n),
                "skills": (sk["driver"] + sk["prog"]) if sk else 0,
                "opr": opr[0] if opr else None, "ccwm": opr[2] if opr else None,
                "awards": [a["title"] for a in won],
            })

    std = {}
    for key in ("hs", "ms"):
        for r in standings.get(key, []):
            std[r["team"]] = r
    for n, t in teams.items():
        played = [e for e in t["events"] if e["opr"] is not None]
        t["opr"] = max((e["opr"] for e in played), default=None)
        t["ccwm"] = max((e["ccwm"] for e in played), default=None)
        t["lastOpr"] = played[-1]["opr"] if played else None
        t["titles"] = sum(1 for e in t["events"] if e["result"] == "Champion")
        t["excellence"] = sum(1 for a in t["awards"] if a[0].startswith("Excellence"))
        s = std.get(n)
        t["skills"] = {"rank": s["rank"], "score": s["score"], "driver": s["driver"], "prog": s["prog"]} if s else None
        total = t["w"] + t["l"] + t["t"]
        t["winPct"] = round(100 * t["w"] / total, 1) if total else None

    # Week-by-week and month-by-month progression.
    week_of = lambda d: (d - timedelta(days=d.weekday())).isoformat()
    weeks, months = {}, {}
    best_match = None
    for ev in cached.values():
        mo = months.setdefault(month_of(ev["start"]), {"events": 0, "signature": 0, "scores": [], "skills": 0, "worlds": 0})
        played = [m for m in ev["matches"] if m["played"]]
        if played or ev["skills"] or ev["awards"]:
            mo["events"] += 1
            mo["signature"] += ev["level"] == "Signature"
        for m in played:
            when = day(m["time"]) or day(ev["start"])
            w = weeks.setdefault(week_of(when), {"scores": [], "events": set(), "skills": 0})
            w["scores"] += [m["rs"], m["bs"]]
            w["events"].add(ev["id"])
            mo["scores"] += [m["rs"], m["bs"]]
            for side, score in (("red", m["rs"]), ("blue", m["bs"])):
                if not best_match or score > best_match["score"]:
                    best_match = {"score": score, "teams": m[side], "event": ev["id"], "match": m["name"],
                                  "round": ROUND_NAMES.get(m["round"], "")}
        if ev["skills"]:
            top = max(s["driver"] + s["prog"] for s in ev["skills"])
            w = weeks.setdefault(week_of(day(ev["start"])), {"scores": [], "events": set(), "skills": 0})
            w["events"].add(ev["id"])
            w["skills"] = max(w["skills"], top)
            mo["skills"] = max(mo["skills"], top)
    for t in teams.values():
        if t["worlds"] and t["worlds"][1] in cached:
            months[month_of(cached[t["worlds"][1]]["start"])]["worlds"] += 1

    def spread(sc):
        sc = sorted(sc)
        return {"max": sc[-1] if sc else None, "median": statistics.median(sc) if sc else None}

    weekly = [{"week": wk, "events": len(w["events"]), "matches": len(w["scores"]) // 2,
               **spread(w["scores"]), "skillsMax": w["skills"] or None} for wk, w in sorted(weeks.items())]
    monthly = [{"month": mk, "events": m["events"], "signature": m["signature"], "matches": len(m["scores"]) // 2,
                **spread(m["scores"]), "skillsMax": m["skills"] or None, "worlds": m["worlds"]}
               for mk, m in sorted(months.items()) if mk]

    held = [r for r in index if r["status"] != "upcoming"]
    soon = today + timedelta(days=14)
    title_leaders = sorted((t for t in teams.values() if t["titles"]),
                           key=lambda t: (-t["titles"], -t["excellence"], -(t["winPct"] or 0), t["team"]))[:8]
    season = {
        "season": {"id": SEASON_ID, "name": SEASON_NAME, "label": SEASON_LABEL},
        "counts": {
            "eventsListed": len(index),
            "eventsHeld": len(held),
            "eventsWithResults": sum(1 for r in index if r["results"]),
            "liveNow": sum(1 for r in index if r["status"] == "live"),
            "signature": sum(1 for r in index if r["level"] == "Signature"),
            "signatureDone": sum(1 for r in index if r["level"] == "Signature" and r["status"] == "done"),
            "teams": len(teams),
            "teamsHS": sum(1 for t in teams.values() if t["grade"] == "High School"),
            "teamsMS": sum(1 for t in teams.values() if t["grade"] == "Middle School"),
            "matches": sum(r.get("matches", 0) for r in index),
            "worldsQualified": sum(1 for t in teams.values() if t["worlds"]),
        },
        "records": {"match": best_match},
        "weekly": weekly,
        "monthly": monthly,
        "leaders": {
            "titles": [{k: t[k] for k in ("team", "name", "grade", "region", "country", "titles", "excellence", "winPct")}
                       for t in title_leaders],
            "skillsHS": standings.get("hs", [])[:5],
            "skillsMS": standings.get("ms", [])[:5],
        },
        "signature": [r for r in index if r["level"] == "Signature"],
        "recent": sorted((r for r in index if r["status"] == "done" and r["results"]),
                         key=lambda r: (r["end"], r["id"]), reverse=True)[:10],
        "upcoming": [r for r in index if r["status"] == "live"
                     or (r["status"] == "upcoming" and day(r["start"]) <= soon)][:40],
        "worldsEvents": [r for r in index if (r["level"] or "").lower() == "world"],
    }

    team_index = {"cols": TEAM_INDEX_COLS, "rows": [[
        t["team"], t["name"], t["org"], t["grade"], t["city"], t["region"], t["country"], len(t["events"]),
        t["w"], t["l"], t["t"], t["winPct"], t["titles"], t["excellence"], len(t["awards"]),
        t["worlds"][1] if t["worlds"] else 0, t["worlds"][0] if t["worlds"] else "",
        t["skills"]["score"] if t["skills"] else None, t["skills"]["rank"] if t["skills"] else None,
        t["opr"], by_id.get(t["events"][-1]["id"], {}).get("start", "") if t["events"] else "",
    ] for t in sorted(teams.values(), key=lambda t: t["team"])]}
    buckets = {b: {} for b in range(TEAM_BUCKETS)}
    for n, t in teams.items():
        buckets[team_bucket(n)][n] = t
    return index, team_index, buckets, season


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--live", action="store_true", help="only fetch events in progress now")
    ap.add_argument("--rebuild", action="store_true", help="no network; rebuild summaries from the cache")
    ap.add_argument("--max-events", type=int, default=150, help="most events to fetch in one run")
    ap.add_argument("--budget-min", type=float, default=20, help="stop fetching after this many minutes")
    args = ap.parse_args()

    log(f"VEX Visualizer season pipeline: season {SEASON_ID} ({SEASON_NAME} {SEASON_LABEL})")
    today = today_utc()
    cached = load_cached_events()
    log(f"  {len(cached)} events in the cache")
    list_path = os.path.join(DATA_DIR, "event_list_raw.json")
    standings_path = os.path.join(DATA_DIR, "skills.json")
    event_list = read_json(list_path, [])
    standings = read_json(standings_path, {"hs": [], "ms": []})

    meta_path = os.path.join(DATA_DIR, "meta.json")
    meta = read_json(meta_path, {})
    standings_changed = False
    listed_now = False

    if not args.rebuild:
        if not TOKEN:
            log("ERROR: VEX_API_TOKEN is not set (use --rebuild to work from the cache only).")
            return 2
        client = Client(TOKEN, args.budget_min * 60)
        try:
            verify_season(client)
            if not args.live or not event_list or meta.get("listFetched") != today.isoformat():
                event_list = fetch_event_list(client)
                write_json(list_path, [{k: e.get(k) for k in ("id", "sku", "name", "start", "end", "level", "location", "season",
                                                              "divisions", "ongoing", "awards_finalized", "event_type")}
                                       for e in event_list])
                listed_now = True

            if args.live:
                todo = [e for e in event_list
                        if day(e.get("start")) and day(e.get("end"))
                        and day(e["start"]) - timedelta(days=1) <= today <= day(e["end"]) + timedelta(days=1)]
                if not todo:
                    # Nothing running: leave the data untouched so the workflow has nothing to commit.
                    log("No events in progress; nothing to fetch.")
                    if listed_now:
                        meta["listFetched"] = today.isoformat()
                        write_json(meta_path, meta)
                    return 0
            else:
                todo = []
                for e in event_list:
                    start = day(e.get("start"))
                    if not start or start > today:
                        continue
                    c = cached.get(e["id"])
                    if c and c.get("complete"):
                        continue
                    todo.append(e)
            live = [e for e in todo if event_status(e, today) == "live"]
            rest = sorted([e for e in todo if event_status(e, today) != "live"], key=lambda e: e.get("end") or "", reverse=True)
            todo = (live + rest)[:args.max_events]
            log(f"Fetching {len(todo)} events ({len(live)} in progress)")

            fetched = failed = in_a_row = 0
            for i, e in enumerate(todo, 1):
                if client.remaining() < 90:
                    log("Time budget nearly used; stopping here, the rest continue next run.")
                    break
                log(f"  [{i}/{len(todo)}] {e.get('sku')} {(e.get('name') or '')[:60]}")
                try:
                    detail = fetch_event(client, e)
                except RuntimeError as err:
                    failed += 1
                    in_a_row += 1
                    log(f"    skipped this event for now: {err}")
                    if in_a_row >= 3:
                        log("Three events in a row failed; stopping here, the rest continue next run.")
                        break
                    continue
                in_a_row = 0
                write_json(os.path.join(EVENTS_DIR, f"{e['id']}.json"), detail)
                cached[e["id"]] = detail
                fetched += 1

            log("Fetching the official skills standings...")
            try:
                standings = fetch_standings(client)
                standings_changed = write_json(standings_path, standings)
            except RuntimeError as err:
                log(f"  kept the previous skills standings: {err}")
            if failed:
                log(f"{failed} events failed and will be retried next run")
            log(f"Fetched {fetched} events with {client.calls} requests")
        except AuthError as e:
            log(f"ERROR: {e}")
            return 3
        except SeasonMismatch as e:
            log(f"ERROR: {e}. Nothing was written.")
            return 4
        except BudgetExceeded:
            log("Time budget used up; writing summaries from what was fetched.")

    index, team_index, buckets, season = build_summaries(event_list, cached, standings)
    outputs = [("events.json", index), ("teams_index.json", team_index), ("season.json", season)]
    outputs += [(f"teams/{b}.json", data) for b, data in buckets.items()]
    changed = [name for name, data in outputs if write_json(os.path.join(DATA_DIR, name), data)]
    if standings_changed:
        changed.append("skills.json")
    if changed or "builtAt" not in meta:
        meta["builtAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if listed_now:
        meta["listFetched"] = today.isoformat()
    meta.update({"season": SEASON_ID, "events": len(index), "eventsCached": len(cached), "teams": len(team_index["rows"])})
    write_json(meta_path, meta)
    log(f"Summaries: {len(index)} events, {len(team_index['rows'])} teams; {len(changed)} files changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
