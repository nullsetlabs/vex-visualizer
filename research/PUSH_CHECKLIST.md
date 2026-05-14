# Push Checklist — Research Data Pull Setup

This is a one-time setup. After completing it, the `Research Data Pull` workflow
is available in the GitHub Actions UI and can be triggered manually for any
season + grade combination.

## Prerequisites (already done)

- [x] `research/src/ingest.py` is in this folder
- [x] `research/src/clean.py` is in this folder
- [x] `research/src/__init__.py` is in this folder
- [x] `research/SAFETY.md` is in this folder
- [x] `.github/workflows/research-data-pull.yml` is in `.github/workflows/`
- [x] `ROBOTEVENTS_TOKEN` is already configured as a repo secret (it's
      currently powering the existing `update-data.yml` workflow)

The five files above are already sitting in your local cloned repo at:

```
C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup\
```

They just need to be committed and pushed.

## Step 1 — Commit and push from PowerShell

> **IMPORTANT: DO NOT use `git add .` or `git add -A`.**
>
> OneDrive sync has converted many existing files in your clone from LF to
> CRLF line endings. `git status` will show ~10 files as "modified" — these
> are just line-ending changes, not real content changes. We want to commit
> ONLY the new research files, not the line-ending churn.
>
> The commands below use explicit paths so only the right files are staged.

Open PowerShell. Then:

```powershell
cd "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup"

# Verify the new files are present (will also show the OneDrive M-files)
git status

# You should see (in red, "Untracked files"):
#   .github/workflows/research-data-pull.yml
#   research/PUSH_CHECKLIST.md
#   research/SAFETY.md
#   research/src/__init__.py
#   research/src/clean.py
#   research/src/ingest.py
#
# You may ALSO see (in red, "Changes not staged for commit"):
#   modified:  update_data.py
#   modified:  fetch_ms_data.py
#   modified:  index.html
#   ... and other repo-root files
# These are OneDrive line-ending churn — DO NOT stage them.

# Stage ONLY the new files — explicit paths
git add .github/workflows/research-data-pull.yml `
        research/SAFETY.md `
        research/PUSH_CHECKLIST.md `
        research/src/__init__.py `
        research/src/ingest.py `
        research/src/clean.py

# CRITICAL: verify exactly what's staged before committing
git diff --staged --stat

# Expected output — only these six new files, nothing else:
#   .github/workflows/research-data-pull.yml | NN +++
#   research/PUSH_CHECKLIST.md               | NN +++
#   research/SAFETY.md                       | NN +++
#   research/src/__init__.py                 |  0
#   research/src/clean.py                    | NN +++
#   research/src/ingest.py                   | NN +++
#   6 files changed, NNNN insertions(+)

# If the staged diff looks correct, commit:
git commit -m "Add research data pull workflow (schedule-strength project)"

# Push to main
git push origin main
```

If `git push` asks for credentials, use your GitHub username and a Personal
Access Token (NOT your account password — GitHub deprecated password auth).

**Optional cleanup (recommended, but not required):** to prevent OneDrive
from re-triggering the line-endings churn after this push, configure git
to ignore line-ending differences in this clone:

```powershell
cd "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup"
git config core.autocrlf input
git restore .
git status   # should now show only the untracked snapshots/ and backup_log.txt
```

`git restore .` discards the OneDrive line-ending modifications by checking
out the committed LF versions. The autocrlf setting tells git to convert CRLF
to LF on commit (so OneDrive-touched files stop showing as modified).

## Step 2 — Verify the workflow is registered

1. Open https://github.com/arjun-mohanan/vex-visualizer/actions in your browser
2. In the left sidebar, you should see two workflows:
   - "Update VEX Visualizer Data" (the existing one)
   - "Research Data Pull" (the new one)
3. Click "Research Data Pull"
4. You should see a "Run workflow" button in the top-right

If the new workflow doesn't appear in the sidebar within ~30 seconds of the
push, refresh the page. Workflow files take a few seconds for GitHub to register.

## Step 3 — First test run (smoke test, ~5–10 min)

This is the lowest-risk way to confirm everything is wired correctly:

1. Click "Research Data Pull" → "Run workflow"
2. Inputs:
   - **Branch:** main
   - **Season:** 197 (Push Back 2025-26; the default)
   - **Grade:** high (the default)
   - **Skip pre-Worlds:** ✅ **TRUE** (this is the smoke-test setting — skips
     the per-team event aggregation, so the run finishes in 5–10 minutes
     instead of 40–50)
3. Click the green "Run workflow" button
4. The run will appear in the list with a yellow circle. Click it to watch
   progress.

**What success looks like:**
- All 9 steps green-check
- A new commit on `main`: `[Research] Pull season 197 high (smoke) — ...`
- A new directory in the repo: `research/data/raw/season_197_high/`
- That directory contains `teams.json`, `rankings.json`, `skills.json`,
  `matches.json`, `awards.json`, `season_info.json`, `coverage.md`, and
  `ingest_log.txt` (no preworlds files since --skip-preworlds was set)
- `coverage.md` shows the three-gate verdict

**What failure looks like:**
- One of the steps shows a red X
- NO new commit is created (the workflow refuses to commit on failure)
- The visualizer site is UNAFFECTED — verify by visiting
  https://arjun-mohanan.github.io/vex-visualizer — it should look identical to
  before the workflow ran

## Step 4 — Verify the visualizer is unaffected

Regardless of whether the workflow succeeded or failed:

1. Open https://arjun-mohanan.github.io/vex-visualizer in a new tab
2. Confirm the team count, rankings, and overall appearance match the last
   known good state
3. Open `version.json` on the deployed site (via DevTools or by visiting
   `https://arjun-mohanan.github.io/vex-visualizer/version.json`) and check
   that `buildTime` matches the LAST visualizer commit, NOT the research
   commit

If anything seems off, the workflow's Layer 3 checksum check should have
prevented it — but the visual confirmation is the final reassurance.

## Step 5 — Full run for 2026 (~40–50 min)

After the smoke test succeeds and the visualizer is confirmed unaffected:

1. Click "Research Data Pull" → "Run workflow" again
2. Inputs:
   - Season: 197
   - Grade: high
   - Skip pre-Worlds: ❌ **FALSE** (this time we want the full pull)
3. Click "Run workflow"
4. Wait. The run will take 40–50 minutes. The page can be closed; the run
   continues in the background.

When it finishes, the directory `research/data/raw/season_197_high/` will
contain the full set of files including `preworlds_team_events.json`,
`preworlds_rankings.json`, etc.

## Step 6 — Pull the data locally

The next scheduled run of your backup script (`backup_vex_visualizer.ps1`)
at 6 AM will pull the new commits to your local clone. If you want it
immediately, run:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\Vex Worlds visualizer project\backup_vex_visualizer.ps1"
```

After this runs, the research JSON will be at:

```
C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup\research\data\raw\season_197_high\
```

But for Claude to read it, you'll also need to refresh the copy in the
workspace folder:

```powershell
Copy-Item -Path "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup" -Destination "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\Vex Worlds visualizer project\vex-visualizer-repo-backup" -Recurse -Force
```

(Same copy command from before — just re-run it.)

## Step 7 — Backfill prior seasons (multi-year panel)

For each of Daniel's target seasons, run the workflow with the appropriate
season ID. Season IDs need to be looked up — they are NOT 197 minus N for
prior years. To find them:

1. Run the workflow once with season=197 and grade=high (smoke or full)
2. Inspect `research/data/raw/season_197_high/season_info.json` — the API
   returned the season name "Push Back"
3. For prior years, look at https://www.robotevents.com/api/v2/seasons?program%5B%5D=1
   in your browser while logged in — it returns JSON listing all seasons

Target seasons (per Daniel's email):
- 2025-26: Push Back, season_id = 197
- 2024-25: High Stakes, season_id = TBD
- 2023-24: Over Under, season_id = TBD
- 2022-23: Spin Up, season_id = TBD

The workflow can be run for each season + grade combination. Each full run
takes ~40-50 min. They can be queued sequentially (the concurrency block
prevents overlapping runs).

## Rolling back if needed

If any commit from the research workflow needs to be reverted:

```powershell
cd "C:\Users\sunis\OneDrive\Summer Camp\VEX Robotics\vex-visualizer-repo-backup"
git revert <commit-sha>
git push origin main
```

To temporarily disable the workflow without removing it:

```powershell
git mv .github/workflows/research-data-pull.yml .github/workflows/research-data-pull.yml.disabled
git commit -m "Temporarily disable research workflow"
git push origin main
```

Renaming the file with a non-`.yml` extension makes GitHub stop recognizing
it as a workflow.

## Troubleshooting

**Q: I pushed but the workflow doesn't appear in the Actions tab.**
A: Refresh the page after ~30 seconds. If still missing, check that the file
path is exactly `.github/workflows/research-data-pull.yml`. The `.github` is
case-sensitive on Linux runners.

**Q: The workflow ran and failed at "Verify research scripts present".**
A: This means `research/src/ingest.py` is missing on main. Run
`git ls-tree -r main research/src/` to confirm. If missing, re-run Step 1.

**Q: The workflow ran and failed at the protected-file checksum check.**
A: This shouldn't happen given how the script is designed. If it does, the
visualizer is still safe — no commit was made. Open an issue with the run log
attached.

**Q: The workflow succeeded but the JSON files look small / incomplete.**
A: Check `coverage.md` in the output directory. It contains the three-gate
verdict and details on which quality gates passed or failed.
