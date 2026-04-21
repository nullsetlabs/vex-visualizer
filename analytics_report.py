#!/usr/bin/env python3
"""
VEX Visualizer — Google Analytics Report Generator
Pulls GA4 metrics and generates a markdown summary report.

This script is completely separate from the site build pipeline.
It reads from Google Analytics and writes a report file — it does NOT
touch index.html, teams_data.json, or any other site files.

Requires:
  - GOOGLE_APPLICATION_CREDENTIALS_JSON secret (service account key)
  - GA4 Property ID: 533952361
"""

import os
import sys
import json
from datetime import datetime, timedelta

try:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy
    )
    from google.oauth2 import service_account
except ImportError:
    print("Installing google-analytics-data package...")
    os.system(f"{sys.executable} -m pip install google-analytics-data --quiet")
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy
    )
    from google.oauth2 import service_account

PROPERTY_ID = "533952361"
WORLDS_START = "2026-04-21"
WORLDS_END = "2026-04-27"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_client():
    """Create GA4 client from service account credentials."""
    creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
    if not creds_json:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS_JSON not set")
        sys.exit(1)

    creds_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def run_report(client, dimensions, metrics, date_range, order_by=None, limit=10):
    """Run a GA4 report and return rows."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(start_date=date_range[0], end_date=date_range[1])],
        limit=limit,
    )
    if order_by:
        request.order_bys = [order_by]
    response = client.run_report(request)
    return response.rows


def generate_report():
    """Generate the full analytics report."""
    client = get_client()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Use Worlds date range, or up to today if Worlds hasn't ended
    end_date = min(today, WORLDS_END)
    date_range = (WORLDS_START, end_date)

    report_lines = []
    report_lines.append("# VEX Visualizer — Analytics Report")
    report_lines.append(f"")
    report_lines.append(f"**Report generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    report_lines.append(f"**Date range:** {date_range[0]} to {date_range[1]}")
    report_lines.append(f"**Property:** VEX Visualizer (G-R1S9F2Z4HS)")
    report_lines.append("")

    # --- Overall Summary ---
    report_lines.append("## Overall Summary")
    report_lines.append("")
    rows = run_report(client, [], [
        "totalUsers", "sessions", "screenPageViews",
        "averageSessionDuration", "bounceRate"
    ], date_range, limit=1)

    if rows:
        r = rows[0]
        total_users = r.metric_values[0].value
        sessions = r.metric_values[1].value
        pageviews = r.metric_values[2].value
        avg_duration = float(r.metric_values[3].value)
        bounce_rate = float(r.metric_values[4].value)
        minutes = int(avg_duration // 60)
        seconds = int(avg_duration % 60)

        report_lines.append(f"| Metric | Value |")
        report_lines.append(f"|--------|-------|")
        report_lines.append(f"| Total Users | **{total_users}** |")
        report_lines.append(f"| Sessions | **{sessions}** |")
        report_lines.append(f"| Page Views | **{pageviews}** |")
        report_lines.append(f"| Avg Session Duration | **{minutes}m {seconds}s** |")
        report_lines.append(f"| Bounce Rate | **{bounce_rate:.1f}%** |")
    report_lines.append("")

    # --- Daily Traffic ---
    report_lines.append("## Daily Traffic")
    report_lines.append("")
    rows = run_report(client, ["date"], [
        "totalUsers", "sessions", "screenPageViews"
    ], date_range, order_by=OrderBy(
        dimension=OrderBy.DimensionOrderBy(dimension_name="date")
    ), limit=30)

    if rows:
        report_lines.append("| Date | Users | Sessions | Page Views |")
        report_lines.append("|------|-------|----------|------------|")
        for r in rows:
            d = r.dimension_values[0].value
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            users = r.metric_values[0].value
            sess = r.metric_values[1].value
            pv = r.metric_values[2].value
            report_lines.append(f"| {date_str} | {users} | {sess} | {pv} |")
    report_lines.append("")

    # --- Top Countries ---
    report_lines.append("## Top Countries")
    report_lines.append("")
    rows = run_report(client, ["country"], ["totalUsers", "sessions"],
                      date_range, order_by=OrderBy(
                          metric=OrderBy.MetricOrderBy(metric_name="totalUsers"),
                          desc=True
                      ), limit=15)

    if rows:
        report_lines.append("| Country | Users | Sessions |")
        report_lines.append("|---------|-------|----------|")
        for r in rows:
            country = r.dimension_values[0].value
            users = r.metric_values[0].value
            sess = r.metric_values[1].value
            report_lines.append(f"| {country} | {users} | {sess} |")
    report_lines.append("")

    # --- Device Breakdown ---
    report_lines.append("## Device Breakdown")
    report_lines.append("")
    rows = run_report(client, ["deviceCategory"], ["totalUsers", "sessions"],
                      date_range, order_by=OrderBy(
                          metric=OrderBy.MetricOrderBy(metric_name="totalUsers"),
                          desc=True
                      ), limit=5)

    if rows:
        report_lines.append("| Device | Users | Sessions |")
        report_lines.append("|--------|-------|----------|")
        for r in rows:
            device = r.dimension_values[0].value
            users = r.metric_values[0].value
            sess = r.metric_values[1].value
            report_lines.append(f"| {device} | {users} | {sess} |")
    report_lines.append("")

    # --- Peak Hours ---
    report_lines.append("## Peak Hours (UTC)")
    report_lines.append("")
    rows = run_report(client, ["hour"], ["totalUsers", "screenPageViews"],
                      date_range, order_by=OrderBy(
                          metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                          desc=True
                      ), limit=24)

    if rows:
        # Sort by hour for display
        hour_data = []
        for r in rows:
            hour_data.append((int(r.dimension_values[0].value),
                              r.metric_values[0].value,
                              r.metric_values[1].value))
        hour_data.sort(key=lambda x: x[0])

        report_lines.append("| Hour (UTC) | Users | Page Views |")
        report_lines.append("|------------|-------|------------|")
        for h, users, pv in hour_data:
            report_lines.append(f"| {h:02d}:00 | {users} | {pv} |")
    report_lines.append("")

    # --- Footer ---
    report_lines.append("---")
    report_lines.append(f"*Generated automatically by VEX Visualizer Analytics Bot*")

    # Write report
    report_path = os.path.join(SCRIPT_DIR, "ANALYTICS_REPORT.md")
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))

    print(f"Report written to {report_path}")
    print(f"  Date range: {date_range[0]} to {date_range[1]}")
    if rows:
        print(f"  Total users: {total_users}")


if __name__ == "__main__":
    generate_report()
