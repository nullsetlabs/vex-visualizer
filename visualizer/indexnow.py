"""
Tell search engines about new and changed pages right away (IndexNow).

IndexNow (indexnow.org) is a shared protocol: one request to api.indexnow.org
is passed on to Bing, Yandex, Seznam, Naver and others. Bing's index also feeds
ChatGPT search and Microsoft Copilot. Google does not take part; it reads the
sitemaps instead.

The key is public by design. It is published at https://vex.nullsetlabs.org/<KEY>.txt
so the search engines can check that requests come from the site's owner.

Called by season_pipeline.py after the standalone pages are built. Sends only
when running on GitHub Actions, never from local test runs. The first run sends
every page (recorded in data/indexnow.json); later runs send only the pages
whose content changed. A failure is logged and never stops the data update.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "vex.nullsetlabs.org"
KEY = "73378e154fcd79e8d723f03ed3a58d93"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10000                      # the protocol's limit per request
ALWAYS = [f"https://{HOST}/", f"https://{HOST}/visualizer/", f"https://{HOST}/visualizer/worlds-2026/"]
OK = (200, 202)                    # 202: accepted, key check still pending


def post(urls, log):
    """Send one batch. Returns the HTTP status (0 if the request failed)."""
    body = json.dumps({"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": urls}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "VEX-Visualizer/2.0 (+https://vex.nullsetlabs.org/visualizer/)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log(f"IndexNow: request failed ({e})")
        return 0


def run(changed, all_urls, state_path, read_json, write_json, log, environ=None, sender=post):
    """Submit changed URLs (all URLs the first time). Returns the number accepted."""
    environ = os.environ if environ is None else environ
    if environ.get("GITHUB_ACTIONS") != "true":
        log("IndexNow: not running on GitHub Actions; nothing sent")
        return 0
    state = read_json(state_path, {}) or {}
    first = not state.get("allSubmitted")
    urls = sorted({u for u in (list(all_urls) + ALWAYS if first else changed) if u.startswith(f"https://{HOST}/")})
    if not urls:
        return 0
    accepted = 0
    for i in range(0, len(urls), BATCH):
        batch = urls[i:i + BATCH]
        status = sender(batch, log)
        log(f"IndexNow: sent {len(batch)} {'(all pages, first run) ' if first else ''}URLs, HTTP {status}")
        if status in OK:
            accepted += len(batch)
    if first and accepted == len(urls):
        write_json(state_path, {"allSubmitted": datetime.now(timezone.utc).date().isoformat(), "count": accepted})
    return accepted
