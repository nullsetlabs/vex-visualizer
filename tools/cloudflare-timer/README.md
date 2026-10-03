# VEX Visualizer timer (Cloudflare)

A small Cloudflare Worker that starts the GitHub workflow "Update VEX Visualizer
Data" every 30 minutes (live mode) and on Monday and Thursday at 06:17 UTC
(full mode). It replaces GitHub's own schedule, which stopped running for this
repository in July 2026 after 60 days without commits and did not come back.

The Worker only asks GitHub to start the workflow. It never sees the VEX API
token, and it has no web page.

## Setup (about 15 minutes, once)

### 1. GitHub: a key that can only start this workflow

1. Signed in to GitHub as the owner of the nullsetlabs organization, open
   **Settings > Developer settings > Personal access tokens > Fine-grained tokens >
   Generate new token** (github.com/settings/personal-access-tokens/new).
2. Token name: `VEX Visualizer timer`.
3. Resource owner: **nullsetlabs**.
4. Expiration: the longest offered (up to one year). Write the date down; the
   timer stops when the token expires.
5. Repository access: **Only select repositories**, then **nullsetlabs/vex-visualizer**.
6. Permissions, under Repositories: **Actions: Read and write**. Leave everything else
   as it is ("Metadata: Read-only" is added automatically).
7. **Generate token** and copy it. GitHub shows it only once. Do not save it in a
   file or send it in chat; it goes straight into Cloudflare in step 2.4.

If the organization requires approval for fine-grained tokens, approve it under
**github.com/organizations/nullsetlabs/settings/personal-access-token-requests**.

### 2. Cloudflare: the Worker

1. In the Cloudflare dashboard (the account that has nullsetlabs.org), open
   **Workers & Pages > Create > Create Worker** (start from "Hello World").
   Name it `vex-visualizer-timer` and select **Deploy**.
2. Select **Edit code**, delete the sample, paste all of `worker.js` from this
   folder, and select **Deploy**.
3. Go back to the Worker and open **Settings**.
4. **Variables and Secrets > Add**: Type **Secret**, Variable name `GITHUB_TOKEN`,
   Value: paste the GitHub token. Select **Deploy** (or Save).
5. **Trigger Events > Add > Cron Triggers**. Add two schedules (times are UTC):
   - `7,37 * * * *`
   - `17 6 * * 1,4`
6. Optional: under **Domains & Routes**, turn off the workers.dev address. The
   Worker does not need one.

### 3. Check that it works

At the next 7 or 37 minutes past the hour, the Actions tab on GitHub shows a
new "Update VEX Visualizer Data" run, started by the token's owner. From a
terminal with the GitHub CLI:

    gh run list -R nullsetlabs/vex-visualizer --workflow update-data.yml -L 3

If no run appears, open the Worker's **Logs** in Cloudflare. "GitHub answered
401" or "403" means the token is wrong, expired or missing the Actions
permission; "404" means the token cannot see the repository.

## Later

- **Token renewal:** before the expiration date, generate a new token the same way
  and replace the `GITHUB_TOKEN` secret in the Worker's settings.
- **If GitHub's schedule starts working again**, both timers will start runs.
  That is harmless (runs wait their turn, and a live run with no signature event
  exits in seconds), but one of them can then be removed.
- **Changing the times:** change them both here (`wrangler.toml`, the Worker's
  `FULL_CRON`) and in the Worker's Cron Triggers, and keep
  `.github/workflows/update-data.yml` in step.
