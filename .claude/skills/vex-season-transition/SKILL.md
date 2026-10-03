---
name: vex-season-transition
description: Move the VEX Visualizer (vex.nullsetlabs.org/visualizer/) from one VEX V5RC season to the next, for example from 2026-2027 Override to 2027-2028 after Worlds 2027. Use when a new game has been revealed, or when asked to archive the finished season, switch the tracker to a new season, or set up the new season's data pipeline.
---

# VEX Visualizer: moving to a new season

Written in October 2026, after moving from Push Back 2025-2026 (the Worlds 2026
dashboard) to Override 2026-2027 (the season tracker). Next planned use: after
Worlds 2027 (late April 2027), for the 2027-2028 game. Update this file at the
end of each transition with anything that changed.

## How the site is built (October 2026)

- Repo `nullsetlabs/vex-visualizer` (public), served by GitHub Pages at
  vex.nullsetlabs.org (DNS on Cloudflare). Commit as Arjun Mohanan with his
  GitHub no-reply email; end commit messages with the Co-Authored-By line.
- `/` is the VEX hub page (`index.html` at the repo root).
- `/visualizer/` is the season tracker: `visualizer/index.html` is one static
  page (no build step) that loads JSON from `visualizer/data/` on demand.
- `/visualizer/worlds-2026/` is the frozen Push Back archive (a self-contained
  page plus the files that built it). Nothing updates it.
- `visualizer/season_pipeline.py` pulls official data from the VEX Events API
  (`https://events.vex.com/api/v2`, token in the `ROBOTEVENTS_TOKEN` secret,
  read as `VEX_API_TOKEN` or `ROBOTEVENTS_TOKEN`) plus the public World Skills
  Standings feed, and writes `visualizer/data/`. Season constants are at the
  top of the file.
- `.github/workflows/update-data.yml`: live check every 30 minutes (signature
  and World events only, no API calls when none is running), full refresh
  Monday and Thursday 06:17 UTC.
- `visualizer/data/pushback_2026.json`: last season's Worlds teams, shown as
  "Last season" on team pages and in search.
- `visualizer/data/webcasts.json`: hand-kept direct webcast links by event code.
- `visualizer/usage_report.py` and `.github/workflows/analytics-report-workflow.yml`:
  monthly GA4 usage report in `analytics/` (season over season, sections, clicks).
- Tools: `visualizer/dev/test_season_pipeline.py` (tests, no network) and
  `visualizer/dev/make_preview_data.py` (synthetic data for layout checks).
- Other pages that mention the season: robotics.nullsetlabs.org
  (`nullsetlabs/robotics-nullsetlabs`) and nullsetlabs.org
  (`nullsetlabs/nullsetlabs-umbrella`), both in their status strips.

## When

- Worlds (late April): the next game is revealed and the season ends.
- Within about a week after Worlds: freeze the finished season, switch the
  tracker to the new season. New-season events start appearing from June.
- Signature events run roughly September to February; Worlds the next April.

## Steps

1. **Confirm the new season id from the source.** Never match by game name;
   names can repeat across programs and years.
   - Public check: the season drop-down on
     https://events.vex.com/robot-competitions/vex-robotics-competition (the
     option values are season ids; Override 2026-2027 was 204, Push Back 197).
   - API check (token needed): `GET /seasons/{id}` must return the name with
     the new "YYYY-YYYY" and the matching years_start / years_end. The
     auto-mode classifier blocks Claude from reading token files, so ask the
     user to run the check in their own PowerShell (`run_season_pull.ps1` in
     the OneDrive project folder does this and prints only OK or FAIL).

2. **Finish the old season's data.** Run the workflow by hand
   (`gh workflow run update-data.yml -R nullsetlabs/vex-visualizer -f mode=full`)
   after Worlds awards are final, so the archive is complete.

3. **Freeze the old season as an archive.** Copy `visualizer/index.html` and
   `visualizer/data/` into a new folder, for example
   `visualizer/override-2027/` (the page loads `data/` relatively, so copying
   both keeps it working). Add a README.md saying it is frozen. Add the URL to
   `sitemap.xml`, and an archive link in the tracker header and footer.
   Check the copy loads at its new path before changing anything else.

4. **Build the "last season" file for team pages.** From the archived
   `data/teams_index.json` (record, titles, Excellence, skills, Worlds
   qualification), write a compact file like `data/pushback_2026.json`, and
   point `needPushback` / `lastSeason` / `pushbackPanel` in the page at it (or
   generalize them to a list of past seasons).

5. **Reset the pipeline.** In `season_pipeline.py` set `SEASON_ID`,
   `SEASON_NAME`, `SEASON_LABEL`, `SEASON_YEARS` and `SEASON_FIRST_DAY` (the day
   after the game reveal). Empty `visualizer/data/` except the last-season file
   and `webcasts.json` (reset to `{}`). Run
   `python visualizer/dev/test_season_pipeline.py`.

6. **Update the page for the new game.** Search for the old season in the page:
   `grep -n "Override\|2026-2027\|2026-27" visualizer/index.html`. Replace the
   game guide in `<template id="override-content">`, rename the tab and its
   hash route (keep the old hash working), update the title, meta, Open Graph,
   JSON-LD, banner text, Help guide and footer. Keep "by Arjun Mohanan" in the
   title and header.

7. **First data pull.** Run the workflow with `mode=full`. In the log, check
   "Season check OK" with the new name, the event count, and that no 429 error
   stopped the run. A run fetches at most 150 events in 20 minutes; the rest
   follow on later runs.

8. **Check before telling anyone.** Serve the repo root locally
   (`python -m http.server`), open `/visualizer/`, go through every tab, a team
   page, an event page and search (type a full team number). Check phone width
   375 px for sideways scrolling. Check the archive link.

9. **Update the other pages.** The hub card at the repo root, the Robotics site
   status strip and VEX card, and the nullsetlabs.org status strip. Add the new
   season and its Worlds week to `SEASONS` and `WINDOWS` in `usage_report.py`.

10. **Worlds.** When the World Championship event is listed (level "World"),
    live tracking starts automatically (`LIVE_LEVELS` includes "World").
    Division views for Worlds are not built yet; the archived Worlds 2026
    dashboard is the reference design.

## Lessons from 2026

- The old RobotEvents API (`www.robotevents.com/api/v2`) returns 404. VEX runs
  the API at `events.vex.com/api/v2`.
- In rankings, matches and awards the team number is `team.name`, not
  `team.number`. Match rounds: 2 qualification, 3 quarterfinal, 4 semifinal,
  5 final, 6 round of 16 (7 and 8 assumed round of 32 and 64).
- The API has no OPR; the pipeline computes OPR, DPR and CCWM and the page
  labels them as computed.
- Rate limit: the API reports `x-ratelimit-limit: 100` but answered HTTP 429
  after about four minutes at 85 requests a minute. Requests start 1.0 s apart
  and 429s are waited out; one failed event is skipped, never the whole run.
- Canceled events have names starting "CANCELED:" and are left out.
- VEX event web pages block scripts (Cloudflare), so webcast links cannot be
  scraped; use `data/webcasts.json` and the YouTube search links.
- Python set order changes between runs: sort every output, or unchanged data
  produces noise commits.
- Many teams register but have no results for months; search shows "No
  results yet this season" and the last-season panel instead of nothing.
- Windows: clone with `-c core.autocrlf=false`; very long temp paths break git.
- Pushing changes to workflow files needs the gh login to have the `workflow`
  scope (`gh auth refresh -h github.com -s workflow`, run in the same Windows
  user session as Claude, not an Administrator window).
- GitHub disables scheduled workflows after 60 days without repository
  activity; re-enable with `gh workflow enable`.
- Keep unpublished research out of public repos and pages until it is
  published.

## Style the user expects

- Plain, factual headings; no pitch or marketing wording.
- Colorful, phone-first layout for high school students: cards, icons,
  meaning colors with labels, a plain-language Help guide.
- Official data first; anything computed is labeled as computed.
- Credit "by Arjun Mohanan" next to the title.
