// VEX Visualizer timer (Cloudflare Worker with cron triggers).
//
// Starts the "Update VEX Visualizer Data" workflow on GitHub on a schedule.
// GitHub stopped running the workflow's own schedule for this repository in
// July 2026 (after 60 days without commits) and did not resume it, while
// manual starts (workflow_dispatch) keep working. This Worker sends those
// manual starts instead.
//
// Cron triggers (UTC), the same times as in .github/workflows/update-data.yml:
//   7,37 * * * *       live: signature events in progress (exits at once if none)
//   17 6 * * MON,THU   full: event list, newly finished events, skills standings
// Cloudflare numbers weekdays 1-7 from Sunday (GitHub uses 0-6), so the days
// are written as names. Any trigger other than the live one starts a full update.
//
// Secret GITHUB_TOKEN: a fine-grained GitHub token limited to the repository
// nullsetlabs/vex-visualizer with only "Actions: Read and write".
// Setup steps: README.md in this folder.

const REPO = 'nullsetlabs/vex-visualizer';
const WORKFLOW = 'update-data.yml';
const LIVE_CRON = '7,37 * * * *';

export default {
  async scheduled(event, env) {
    const mode = event.cron === LIVE_CRON ? 'live' : 'full';
    const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'vex-visualizer-timer',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main', inputs: { mode } }),
    });
    if (res.status !== 204) {
      // Appears under the Worker's Logs in the Cloudflare dashboard.
      throw new Error(`GitHub answered ${res.status} for the ${mode} update: ${(await res.text()).slice(0, 300)}`);
    }
    console.log(`Started the ${mode} update`);
  },

  // The timer has no web page; anyone opening its address gets "Not found".
  async fetch() {
    return new Response('Not found', { status: 404 });
  },
};
