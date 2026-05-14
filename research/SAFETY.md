# Research Data Pull — Safety Architecture

This document explains how the **`Research Data Pull`** GitHub Actions workflow
(`.github/workflows/research-data-pull.yml`) is engineered so that **no possible
failure mode can degrade the VEX Visualizer website**.

The visualizer at https://arjun-mohanan.github.io/vex-visualizer is built by the
existing `update-data.yml` workflow. It reads `index.html`, `teams_data.json`,
`ms_teams_data.json`, and `version.json` from the repo root. As long as the new
research workflow cannot modify those files, the website is bulletproof against
research-side failures.

## Five layers of defense

### Layer 1 — Physical isolation by directory

The research workflow writes ONLY to:

```
research/data/raw/season_{ID}_{GRADE}/
  teams.json
  rankings.json
  skills.json
  matches.json
  awards.json
  preworlds_team_events.json
  preworlds_rankings.json
  preworlds_skills.json
  preworlds_events_metadata.json
  season_info.json
  coverage.md
  ingest_log.txt
```

The visualizer pipeline (`update_data.py`, `fetch_ms_data.py`) never reads from
this directory. No path overlap means no possible conflict.

### Layer 2 — Pre-flight checksum capture

Before running the ingest script, the workflow records SHA-256 checksums of
every visualizer-critical file:

```
teams_data.json
ms_teams_data.json
index.html
version.json
update_data.py
fetch_ms_data.py
vex_visualizer_template.html
analytics_report.py
ANALYTICS_REPORT.md
```

These are stored in `/tmp/protected-before.txt` on the runner.

### Layer 3 — Post-flight checksum verification

After ingest completes (whether success or failure), the workflow re-checksums
the same protected files. If ANY checksum has changed, the workflow aborts with
`exit 1` BEFORE the commit step runs. Even a hypothetical bug that wrote to the
wrong path would be caught here.

### Layer 4 — Confined-staging check

Before committing, the workflow inspects all changed paths via `git status` and
verifies every modified file is under `research/`. If anything outside that
prefix is changed, the workflow aborts.

### Layer 5 — Manual trigger only

The workflow uses `workflow_dispatch` — it only runs when a maintainer clicks
"Run workflow" in the GitHub Actions UI. No schedule, no automatic invocation.
Each invocation is intentional, with explicit inputs.

## How failures cascade

Below is the behavior for every realistic failure mode. In ALL of them, the
visualizer continues serving the previous good state.

| Failure mode | Workflow result | Visualizer impact |
|---|---|---|
| RobotEvents API returns empty | Quality gate fails → no commit | None |
| API returns partial / 200 teams | Quality gate fails → no commit | None |
| Schema mismatch crashes the script | Job fails → no commit | None |
| Bug writes to wrong path | Layer 3 catches → workflow aborts | None |
| Workflow times out mid-run | No commit issued | None |
| Token revoked | Auth error → no commit | None |
| Network drops mid-pull | Retries exhausted → fail → no commit | None |
| Concurrent run attempted | Concurrency block prevents overlap | None |
| Everything works correctly | Commit to `research/data/raw/...` only | None (visualizer files untouched) |

## What the workflow does NOT do

- Does NOT modify `update_data.py`, `fetch_ms_data.py`, or any visualizer code
- Does NOT modify `teams_data.json` or `ms_teams_data.json`
- Does NOT modify `index.html` or `vex_visualizer_template.html`
- Does NOT modify `version.json`
- Does NOT modify `analytics_report.py` or `ANALYTICS_REPORT.md`
- Does NOT trigger `update-data.yml` (different workflow file, separate trigger)
- Does NOT push to GitHub Pages
- Does NOT run on a schedule

## Secret handling

The workflow reuses the existing `ROBOTEVENTS_TOKEN` repository secret.

- No new secrets need to be created.
- The secret value is never printed to logs (GitHub masks it automatically).
- The secret is only exposed as an environment variable inside the runner; it
  cannot be exfiltrated by the ingest script unless the script itself is
  compromised.
- The ingest script (`research/src/ingest.py`) only sends the token in the
  `Authorization: Bearer` header for HTTPS requests to `www.robotevents.com`.

## Reverting if something seems wrong

If at any point you see unexpected commits on `main` from this workflow:

1. **Diagnose first.** Open the commit on GitHub and inspect the diff. The
   diff should ONLY contain files under `research/data/`.
2. **If the diff is clean and confined to `research/`**: nothing is wrong; the
   visualizer is unaffected. Continue.
3. **If the diff contains ANY file outside `research/`**: revert the commit
   immediately via `git revert <commit_sha>` and report the issue. Then disable
   the workflow temporarily by renaming
   `.github/workflows/research-data-pull.yml` to add a `.disabled` suffix.

This should never happen given Layers 3 and 4, but the revert path is documented
for completeness.

## Audit trail

Every research pull leaves three artifacts:

1. The git commit message (`[Research] Pull season {N} {grade} — {timestamp}`)
2. The full workflow run log in GitHub Actions
3. The `coverage.md` file in the output directory

These together provide a complete audit of what was fetched, when, with what
parameters, and what quality grade the data passed.
