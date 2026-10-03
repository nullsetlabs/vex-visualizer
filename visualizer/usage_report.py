#!/usr/bin/env python3
"""
VEX Visualizer usage report (Google Analytics 4).

Writes analytics/USAGE_REPORT.md and analytics/usage.json at the repo root with:
  - month-by-month visitors since the site launched (April 2026)
  - season-over-season comparison (2025-2026 Push Back vs 2026-2027 Override),
    including the growth multiple ("this season is N times last season")
  - which sections people open (view_* events) and what they click (click_*,
    search_team, compare_regions events sent by the page)
  - countries and devices

Only visits on the public hostnames are counted, so local testing is excluded.

Requires the GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable (a GA4
service account key with Viewer access to the property). Run by
.github/workflows/analytics-report-workflow.yml.
"""

import json
import os
import sys
from datetime import datetime, timezone

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, Metric, OrderBy, RunReportRequest,
)
from google.oauth2 import service_account

PROPERTY_ID = "533952361"   # GA4 property behind G-R1S9F2Z4HS
HOSTNAMES = ["vex.nullsetlabs.org", "arjun-mohanan.github.io"]
LAUNCH = "2026-04-01"

# Each season runs from the day after the previous Worlds to the end of its own
# Worlds. Add a line here each season (see the season-transition skill).
SEASONS = [
    ("2025-2026 Push Back", "2026-04-01", "2026-05-31"),
    ("2026-2027 Override", "2026-06-01", "2027-05-31"),
]
# Same-length windows worth comparing year to year (Worlds week, signature months).
WINDOWS = [
    ("Worlds 2026 week", "2026-04-21", "2026-04-27"),
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "analytics")


def client():
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
    if not raw:
        sys.exit("GOOGLE_APPLICATION_CREDENTIALS_JSON is not set")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    return BetaAnalyticsDataClient(credentials=creds)


def report(c, dims, mets, start, end, order=None, limit=1000):
    req = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name=m) for m in mets],
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimension_filter=FilterExpression(filter=Filter(
            field_name="hostName", in_list_filter=Filter.InListFilter(values=HOSTNAMES))),
        limit=limit,
    )
    if order:
        req.order_bys = [order]
    rows = c.run_report(req).rows
    return [([d.value for d in r.dimension_values], [float(m.value) for m in r.metric_values]) for r in rows]


def totals(c, start, end):
    rows = report(c, [], ["totalUsers", "newUsers", "sessions", "screenPageViews", "engagedSessions"], start, end)
    v = rows[0][1] if rows else [0, 0, 0, 0, 0]
    return dict(zip(["users", "newUsers", "sessions", "views", "engagedSessions"], [int(x) for x in v]))


def times(a, b):
    return f"{a / b:.1f}x" if b else "n/a"


def main():
    c = client()
    today = datetime.now(timezone.utc).date().isoformat()
    clip = lambda d: min(d, today)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hostnames": HOSTNAMES}

    # Month by month
    rows = report(c, ["yearMonth"], ["totalUsers", "sessions", "screenPageViews", "engagedSessions"], LAUNCH, today,
                  order=OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="yearMonth")))
    out["monthly"] = [{"month": f"{d[0][:4]}-{d[0][4:]}", "users": int(m[0]), "sessions": int(m[1]),
                       "views": int(m[2]), "engagedSessions": int(m[3])} for d, m in rows]

    # Seasons and windows
    out["seasons"] = []
    for name, start, end in SEASONS:
        if start > today:
            continue
        t = totals(c, start, clip(end))
        t.update({"season": name, "start": start, "end": clip(end), "complete": end <= today})
        out["seasons"].append(t)
    out["windows"] = []
    for name, start, end in WINDOWS:
        if start <= today:
            t = totals(c, start, clip(end))
            t.update({"window": name, "start": start, "end": clip(end)})
            out["windows"].append(t)

    # What people open and click this season
    season_start = out["seasons"][-1]["start"] if out["seasons"] else LAUNCH
    rows = report(c, ["eventName"], ["eventCount", "totalUsers"], season_start, today,
                  order=OrderBy(metric=OrderBy.MetricOrderBy(metric_name="eventCount"), desc=True), limit=200)
    events = [{"event": d[0], "count": int(m[0]), "users": int(m[1])} for d, m in rows]
    out["sections"] = [e for e in events if e["event"].startswith("view_")]
    out["clicks"] = [e for e in events if e["event"].startswith(("click_", "search_", "compare_"))]
    out["autoEvents"] = [e for e in events if e["event"] in ("page_view", "scroll", "click", "session_start", "user_engagement")]

    rows = report(c, ["country"], ["totalUsers"], season_start, today,
                  order=OrderBy(metric=OrderBy.MetricOrderBy(metric_name="totalUsers"), desc=True), limit=15)
    out["countries"] = [{"country": d[0], "users": int(m[0])} for d, m in rows]
    rows = report(c, ["deviceCategory"], ["totalUsers"], season_start, today,
                  order=OrderBy(metric=OrderBy.MetricOrderBy(metric_name="totalUsers"), desc=True), limit=5)
    out["devices"] = [{"device": d[0], "users": int(m[0])} for d, m in rows]

    # Markdown
    L = ["# VEX Visualizer usage report", "",
         f"Generated {out['generated']} from Google Analytics 4 (property {PROPERTY_ID}). "
         f"Counts only visits on {', '.join(HOSTNAMES)}.", ""]
    if len(out["seasons"]) >= 2:
        a, b = out["seasons"][-1], out["seasons"][-2]
        L += ["## Season over season", "",
              f"**{a['season']}{'' if a['complete'] else ' (to date)'}: {a['users']:,} users, "
              f"{times(a['users'], b['users'])} the {b['season']} season ({b['users']:,} users).**", "",
              "| Season | Dates | Users | Sessions | Page views | Engaged sessions |",
              "|---|---|---|---|---|---|"]
        for s in out["seasons"]:
            L.append(f"| {s['season']} | {s['start']} to {s['end']} | {s['users']:,} | {s['sessions']:,} | {s['views']:,} | {s['engagedSessions']:,} |")
        L.append("")
    if out["windows"]:
        L += ["## Comparison windows", "", "| Window | Dates | Users | Sessions | Page views |", "|---|---|---|---|---|"]
        for w in out["windows"]:
            L.append(f"| {w['window']} | {w['start']} to {w['end']} | {w['users']:,} | {w['sessions']:,} | {w['views']:,} |")
        L.append("")
    L += ["## Month by month", "", "| Month | Users | Sessions | Page views | Engaged sessions |", "|---|---|---|---|---|"]
    for m in out["monthly"]:
        L.append(f"| {m['month']} | {m['users']:,} | {m['sessions']:,} | {m['views']:,} | {m['engagedSessions']:,} |")
    L += ["", f"## Sections opened (since {season_start})", "",
          "From the page's view_* events (one per section opened).", "", "| Section | Times opened | Users |", "|---|---|---|"]
    for e in out["sections"]:
        L.append(f"| {e['event'][5:]} | {e['count']:,} | {e['users']:,} |")
    L += ["", f"## Clicks (since {season_start})", "",
          "From click_*, search_team and compare_regions events. Outbound links are also counted by GA4 as 'click'.", "",
          "| What was clicked | Clicks | Users |", "|---|---|---|"]
    for e in out["clicks"]:
        L.append(f"| {e['event']} | {e['count']:,} | {e['users']:,} |")
    L += ["", "## Countries (this season)", "", "| Country | Users |", "|---|---|"]
    L += [f"| {x['country']} | {x['users']:,} |" for x in out["countries"]]
    L += ["", "## Devices (this season)", "", "| Device | Users |", "|---|---|"]
    L += [f"| {x['device']} | {x['users']:,} |" for x in out["devices"]]

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "USAGE_REPORT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(OUT_DIR, "usage.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
