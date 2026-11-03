#!/usr/bin/env python3
"""
Past-season award history for the VEX Visualizer (V5RC).

A one-time backfill of the awards at every event of earlier seasons, which no
longer change once a season is over. Each run continues where the last one
stopped, so a season of about 2,500 events fills in over a few runs; after
that the seasons need no more API requests.

Source: the VEX Events API (events.vex.com/api/v2), the same source and token
as season_pipeline.py. One request per event (its awards), plus the season's
event list and the team list of its World Championship events.

Files in data/history/
  seasons/{id}.json    one season: its events, their awards and the Worlds teams;
                       "pending" lists events not fetched yet, "complete" is set when none remain
  index.json           award counts per team and season (labels on event and team pages, History leaders)
  teams/{bucket}.json  every award of a team by season, with the event (team pages)

Usage
  python history.py                   # continue the backfill, then rebuild index.json and teams/
  python history.py --budget-min 25   # stop fetching after 25 minutes
  python history.py --rebuild         # no network; rebuild index.json and teams/ from seasons/
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import season_pipeline as sp

# Earlier seasons to keep, by the year they start (newest first). Team numbers
# carry over between seasons but team members change, so older seasons say
# less about the current one.
HISTORY_START_YEARS = (2025, 2024)

HIST_DIR = os.path.join(sp.DATA_DIR, "history")
SEASONS_DIR = os.path.join(HIST_DIR, "seasons")
TEAMS_DIR = os.path.join(HIST_DIR, "teams")
SAVE_EVERY = 100        # write the season file after this many events, so a stopped run keeps its work

# Award titles end in a program tag that changed over the years, for example
# "(VRC/VEXU/VAIRC)" in 2024-2025 and "(V5RC)" since; the tag is dropped.
PROGRAM_TAG = re.compile(r"\s*\((?:[A-Z0-9]+(?:\s*/\s*[A-Z0-9]+)*)\)\s*$")
KINDS = ("exc", "champ", "design", "skills")
INDEX_COLS = ["exc", "champ", "design", "skills", "awards", "wq", "worlds"]


def award_kind(title):
    t = title.lower()
    if t.startswith("excellence"):
        return "exc"
    if t.startswith("tournament champions") or t.startswith("division champion"):
        return "champ"
    if t.startswith("design"):
        return "design"
    if t.startswith("robot skills champion"):
        return "skills"
    return "other"


def find_seasons(client):
    """The V5RC seasons that start in HISTORY_START_YEARS: [{id, name, label, game, start}]."""
    found = []
    for s in client.pages("/seasons", {"program[]": sp.PROGRAM_ID}):
        start = s.get("years_start")
        if start in HISTORY_START_YEARS and (s.get("program") or {}).get("id", sp.PROGRAM_ID) == sp.PROGRAM_ID:
            name = s.get("name") or ""
            found.append({"id": s["id"], "name": name, "label": f"{start}-{start + 1}",
                          "game": name.split(":")[-1].strip() if ":" in name else "", "start": start})
    return sorted(found, key=lambda s: -s["start"])


def season_path(sid):
    return os.path.join(SEASONS_DIR, f"{sid}.json")


def start_season(client, meta):
    """Fetch a season's event list and its Worlds teams; returns the new season record."""
    sp.log(f"History: event list for {meta['label']} {meta['game']} (season {meta['id']})")
    events = client.pages("/events", {"season[]": meta["id"]})
    kept = [e for e in events if not sp.is_canceled(e)]
    rec = dict(meta)
    rec["events"] = {}
    for e in kept:
        loc = e.get("location") or {}
        rec["events"][str(e["id"])] = [e.get("sku"), e.get("name"), (e.get("start") or "")[:10], e.get("level") or "",
                                       loc.get("region") or "", loc.get("country") or "", sp.grade_from_name(e.get("name"))]
    rec["awards"] = {}
    rec["worlds"] = {}
    for e in kept:
        if (e.get("level") or "").lower() == "world":
            teams = client.pages(f"/events/{e['id']}/teams")
            rec["worlds"][str(e["id"])] = sorted({t.get("number", "") for t in teams if t.get("number")})
    # Newest events first, so a partly filled season already covers its end (and its Worlds).
    rec["pending"] = [eid for eid, _ in sorted(rec["events"].items(), key=lambda kv: kv[1][2], reverse=True)]
    rec["complete"] = False
    sp.log(f"  {len(kept)} events, {sum(len(v) for v in rec['worlds'].values())} Worlds teams")
    return rec


def compact_awards(awards):
    out = []
    for a in sorted(awards, key=lambda a: a.get("order") or 0):
        teams = [sp.team_num(w.get("team")) for w in a.get("teamWinners") or []]
        teams = [t for t in teams if t]
        if not teams:
            continue
        title = PROGRAM_TAG.sub("", a.get("title") or "").strip()
        out.append([title, teams, any("World Championship" in q for q in a.get("qualifications") or [])])
    return out


def backfill(client, rec):
    """Fetch awards for the season's pending events until done or out of time."""
    done = failed_in_a_row = 0
    try:
        while rec["pending"]:
            if client.remaining() < 90:
                sp.log("  time budget nearly used; the rest continue next run")
                break
            eid = rec["pending"][0]
            try:
                rec["awards"][eid] = compact_awards(client.pages(f"/events/{eid}/awards"))
            except RuntimeError as err:
                sp.log(f"  awards skipped for event {eid} for now: {err}")
                rec["pending"].append(rec["pending"].pop(0))   # retry at the end
                failed_in_a_row += 1
                if failed_in_a_row >= 3:
                    sp.log("  three events in a row failed; stopping here, the rest continue next run")
                    break
                continue
            failed_in_a_row = 0
            rec["pending"].pop(0)
            done += 1
            if done % SAVE_EVERY == 0:
                save(rec)
                sp.log(f"  {done} events done, {len(rec['pending'])} to go")
    finally:
        # Also when the time budget runs out mid-request: keep what was fetched.
        rec["complete"] = not rec["pending"]
        save(rec)
        sp.log(f"  {rec['label']}: {done} events fetched this run, {len(rec['pending'])} left")


def save(rec):
    rec["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sp.write_json(season_path(rec["id"]), rec)


def load_seasons():
    if not os.path.isdir(SEASONS_DIR):
        return []
    recs = [sp.read_json(os.path.join(SEASONS_DIR, f)) for f in os.listdir(SEASONS_DIR) if f.endswith(".json")]
    return sorted((r for r in recs if r), key=lambda r: -r["start"])


def build_summaries(recs):
    """Write index.json (counts per team and season) and teams/{bucket}.json (award lists)."""
    seasons = [{"id": r["id"], "label": r["label"], "game": r["game"], "complete": r["complete"],
                "events": len(r["events"]), "eventsDone": len(r["events"]) - len(r["pending"]),
                "worldsEvents": sorted(int(k) for k in r["worlds"])} for r in recs]
    index, detail = {}, {}
    for si, r in enumerate(recs):
        worlds_teams = {n for teams in r["worlds"].values() for n in teams}
        counts = {}
        for eid, awards in r["awards"].items():
            ev = r["events"].get(eid)
            if not ev:
                continue
            for title, teams, wq in awards:
                kind = award_kind(title)
                for n in teams:
                    c = counts.setdefault(n, dict.fromkeys(INDEX_COLS, 0))
                    c["awards"] += 1
                    if kind in KINDS:
                        c[kind] += 1
                    if wq:
                        c["wq"] = 1
                    detail.setdefault(n, {}).setdefault(r["label"], []).append([title, int(eid), ev[1], ev[2], ev[3]])
        for n in worlds_teams:
            counts.setdefault(n, dict.fromkeys(INDEX_COLS, 0))["worlds"] = 1
        for n, c in counts.items():
            index.setdefault(n, [None] * len(recs))[si] = [c[k] for k in INDEX_COLS]
    for awards in (a for t in detail.values() for a in t.values()):
        awards.sort(key=lambda a: (a[3], a[0]))
    sp.write_json(os.path.join(HIST_DIR, "index.json"), {"seasons": seasons, "cols": INDEX_COLS, "teams": dict(sorted(index.items()))})
    buckets = {b: {} for b in range(sp.TEAM_BUCKETS)}
    for n in sorted(detail):
        buckets[sp.team_bucket(n)][n] = detail[n]
    for b, data in buckets.items():
        sp.write_json(os.path.join(TEAMS_DIR, f"{b}.json"), data)
    sp.log(f"History summaries: {len(index)} teams across {len(recs)} seasons")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rebuild", action="store_true", help="no network; rebuild the summaries from data/history/seasons/")
    ap.add_argument("--budget-min", type=float, default=25, help="stop fetching after this many minutes")
    args = ap.parse_args()
    if not args.rebuild:
        if not sp.TOKEN:
            sp.log("ERROR: VEX_API_TOKEN is not set (use --rebuild to work from the saved seasons only).")
            return 2
        client = sp.Client(sp.TOKEN, args.budget_min * 60)
        try:
            for meta in find_seasons(client):
                rec = sp.read_json(season_path(meta["id"]))
                if rec and rec.get("complete"):
                    continue
                if not rec:
                    rec = start_season(client, meta)
                    save(rec)
                backfill(client, rec)
                if client.remaining() < 90:
                    break
        except sp.AuthError as e:
            sp.log(f"ERROR: {e}")
            return 3
        except sp.BudgetExceeded:
            sp.log("Time budget used up; the rest continue next run.")
        sp.log(f"History: {client.calls} requests")
    build_summaries(load_seasons())
    return 0


if __name__ == "__main__":
    sys.exit(main())
