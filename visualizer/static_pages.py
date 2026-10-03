"""
Standalone pages that search engines can index, written next to the season tracker.

The tracker itself is one page with #-routes, which search engines treat as a
single address. These plain HTML pages give each team, each signature event and
the main lists their own address, title and description, and link back into the
interactive tracker:

  /visualizer/team/<team number>/      every team with results this season
  /visualizer/event/<event code>/      every signature event of the season
  /visualizer/skills/                  official skills standings (HS and MS)
  /visualizer/signature/               signature events by month
  /visualizer/worlds-qualifiers/       teams qualified through awards
  /visualizer/trueskill/               TrueSkill rankings (HS and MS) and how they are calculated
  /visualizer/sitemap-pages.xml        sitemap of all of the above

Called by season_pipeline.py after the summaries are built. Files are only
rewritten when their content changes, and pages for teams or events that drop
out of the data are removed. Output contains no timestamps, so unchanged data
produces no commit.
"""

import html
import json
import os
import shutil

SITE = "https://vex.nullsetlabs.org"
BASE = "/visualizer"
GA_ID = "G-R1S9F2Z4HS"
CF_BEACON = '{"token": "ead9ddf5a1cf4549be8975e34994ea70"}'
SEASON_TITLE = "VEX V5RC Override 2026-2027"
GENERATED_DIRS = ("team", "event", "skills", "signature", "worlds-qualifiers", "trueskill")

e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def fmt_date(d):
    if not d:
        return ""
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    y, m, dd = d[:10].split("-")
    return f"{months[int(m) - 1]} {int(dd)}, {y}"


def fmt_range(a, b):
    if not a:
        return ""
    if not b or a == b:
        return fmt_date(a)
    return f"{fmt_date(a)} - {fmt_date(b)}"


def place(o):
    return ", ".join(x for x in (o.get("city"), o.get("region"),
                                 "" if o.get("country") == "United States" else o.get("country")) if x)


def grade_badge(g):
    cls = {"High School": "hs", "Middle School": "ms", "Blended": "mix"}.get(g, "")
    label = {"High School": "High school", "Middle School": "Middle school", "Blended": "High school + middle school"}.get(g, "")
    return f'<span class="badge {cls}">{label}</span>' if label else ""


def team_url(n):
    return f"{BASE}/team/{n}/"


GRADE_SHORT = {"High School": "high school", "Middle School": "middle school"}

# How TrueSkill is calculated, in plain words (the tracker's TrueSkill tab says the same).
TRUESKILL_HOW = """<ul class="how">
<li><b>What it is.</b> TrueSkill is a rating method from Microsoft Research, first used to match Xbox players. The VEX community uses it to compare teams that never played each other.</li>
<li><b>Start.</b> Every team starts with the same estimate: skill 25, with a large uncertainty (8.3).</li>
<li><b>After each match.</b> Teams on the winning alliance go up and teams on the losing alliance go down. A surprising result moves ratings more: beating a much stronger alliance counts for a lot, beating a weaker one for little. A tie moves the two alliances toward each other. Both partners get the same credit, and only the result counts, not the score margin.</li>
<li><b>Uncertainty shrinks.</b> Every match makes the estimate more certain, so ratings settle as teams play more.</li>
<li><b>The number shown</b> is skill minus three times the uncertainty (written &mu; &minus; 3&sigma;). This cautious estimate starts at 0 and rises as a team plays more matches, so a team with one event can rank below a team with a similar record over three events.</li>
<li><b>What counts.</b> Every qualification and elimination match with a result this season, in date order. Practice matches do not count.</li>
<li><b>Settings.</b> The standard TrueSkill settings: &beta; 4.17, &tau; 0.083, draw probability 10%. This is the same scale as the high school TrueSkill numbers in the Worlds 2026 dashboard. Other sites can show slightly different numbers if they use other settings or a different set of matches.</li>
<li><b>Not official.</b> TrueSkill is computed by this site from official VEX match results. VEX does not publish it, and it plays no part in official rankings or Worlds qualification.</li>
</ul>"""


def page(path_url, title, description, crumbs, body, jsonld=None):
    """Full HTML document. path_url is the page's path, e.g. /visualizer/team/96Z/."""
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n' if jsonld else ""
    crumb_html = " / ".join(f'<a href="{e(u)}">{e(t)}</a>' if u else e(t) for t, u in crumbs)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{SITE}{e(path_url)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE}{e(path_url)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:image" content="{SITE}{BASE}/og-preview.png">
<meta property="og:site_name" content="VEX Visualizer">
<meta name="theme-color" content="#0a0e1a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{BASE}/pages.css">
<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>
<script>window.dataLayer = window.dataLayer || []; function gtag(){{dataLayer.push(arguments);}} gtag('js', new Date()); gtag('config', '{GA_ID}');</script>
{ld}</head>
<body>
<div class="strip"><div class="strip-inner">
  <span class="crumbs"><a href="https://nullsetlabs.org/">Null Set Labs</a> / <a href="https://vex.nullsetlabs.org/">VEX</a> / {crumb_html}</span>
  <a class="home" href="https://nullsetlabs.org/" aria-label="Back to Null Set Labs home">Home</a>
</div></div>
<main>
<div class="brand"><a href="{BASE}/">VEX Visualizer</a> by Arjun &bull; {e(SEASON_TITLE)} season tracker</div>
{body}
</main>
<footer>
  VEX Visualizer by Arjun, VEX team 6121 Conestoga Pioneers &bull; published by <a href="https://nullsetlabs.org/">Null Set Labs</a>
  <div class="links"><a href="{BASE}/">Season tracker</a> &bull; <a href="{BASE}/skills/">Skills standings</a> &bull; <a href="{BASE}/signature/">Signature events</a> &bull; <a href="{BASE}/trueskill/">TrueSkill rankings</a> &bull; <a href="{BASE}/worlds-qualifiers/">Worlds qualifiers</a> &bull; <a href="{BASE}/worlds-2026/">Worlds 2026 archive</a></div>
  <div class="fine">Event, team, match, skills and award data come from VEX Events (events.vex.com). OPR and TrueSkill are computed by this site from match results and are estimates. This site is an independent community project and is not affiliated with, endorsed by, or sponsored by the REC Foundation, VEX Robotics, or Innovation First International.</div>
</footer>
<script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{CF_BEACON}'></script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Individual pages
# ---------------------------------------------------------------------------

def team_page(t, by_id, sig_pages, last_season):
    n = t["team"]
    url = team_url(n)
    rec = f'{t["w"]}-{t["l"]}-{t["t"]}'
    sk = t.get("skills")
    title = f'{n} {t["name"]} | {SEASON_TITLE} results'.strip()
    desc_bits = [f'{n} {t["name"]}'.strip()]
    if t.get("org") or place(t):
        desc_bits.append(f'({", ".join(x for x in (t.get("org"), place(t)) if x)})')
    desc = " ".join(desc_bits) + f": {SEASON_TITLE} results from official VEX data. {len(t['events'])} event{'s' if len(t['events']) != 1 else ''}, qualification record {rec}"
    if t["titles"]:
        desc += f", {t['titles']} tournament title{'s' if t['titles'] != 1 else ''}"
    if sk:
        desc += f", skills score {sk['score']} (world rank {sk['rank']})"
    ts = t.get("trueskill")
    if ts:
        desc += f", TrueSkill {ts['rating']:.1f} (rank {ts['rank']} of {ts['of']} {GRADE_SHORT.get(t.get('grade'), 'teams')})"
    desc += "."
    facts = [
        (len(t["events"]), "Events", ""),
        (rec, "Qualification record", f'{t["winPct"]:.1f}% wins' if t.get("winPct") is not None else ""),
        (f'{t["ew"]}-{t["el"]}', "Elimination record", ""),
        (t["titles"], "Tournament titles", ""),
        (len(t["awards"]), "Awards", ""),
        (sk["score"] if sk else "-", "Season skills score", f'World rank {sk["rank"]}: driver {sk["driver"]}, autonomous {sk["prog"]}' if sk else ""),
        (f'{t["opr"]:.1f}' if t.get("opr") is not None else "-", "Best OPR (computed)", ""),
        (f'{ts["rating"]:.1f}' if ts else "-", "TrueSkill (computed)",
         f'Rank {ts["rank"]} of {ts["of"]} {GRADE_SHORT.get(t.get("grade"), "teams")}, {ts["matches"]} matches' if ts else ""),
    ]
    body = f"""<h1><span class="num">{e(n)}</span> {e(t["name"])}</h1>
<p class="lead">{e(t.get("org"))}{" &bull; " + e(place(t)) if place(t) else ""} {grade_badge(t.get("grade"))}
{'<span class="badge gold">Qualified for the 2027 World Championship</span>' if t.get("worlds") else ""}</p>
<a class="cta" href="{BASE}/#team/{e(n)}">Open in the VEX Visualizer</a><a class="cta alt" href="https://events.vex.com/teams/V5RC/{e(n)}">Official team page</a>
<div class="facts">{"".join(f'<div class="fact"><div class="v">{e(v)}</div><div class="k">{e(k)}</div>{f"<div class=n>{e(x)}</div>" if x else ""}</div>' for v, k, x in facts)}</div>
<p class="note">TrueSkill is a match rating computed by this site from every match this season. <a href="{BASE}/trueskill/#how">How TrueSkill is calculated</a> &bull; <a href="{BASE}/trueskill/">TrueSkill rankings</a></p>
"""
    if t.get("worlds"):
        we = by_id.get(t["worlds"][1], {})
        body += f'<p class="note">Qualified for the 2027 VEX Robotics World Championship through the {e(t["worlds"][0])} award at {e(we.get("name", "an event"))}.</p>'
    if t["awards"]:
        body += "<h2>Awards this season</h2><div class=\"awards\">" + "".join(
            f'<div class="award"><b>{e(title)}</b><span class="dim">{e(by_id.get(eid, {}).get("name", ""))}</span></div>'
            for title, eid in t["awards"]) + "</div>"
    rows = []
    for x in reversed(t["events"]):
        ev = by_id.get(x["id"], {})
        link = f"{BASE}/event/{ev.get('sku')}/" if ev.get("sku") in sig_pages else f"{BASE}/#event/{x['id']}"
        sig_badge = ' <span class="badge gold">Signature</span>' if ev.get("level") == "Signature" else ""
        rows.append(f'<tr><td>{e(fmt_range(ev.get("start"), ev.get("end")))}</td><td class="w"><a href="{e(link)}">{e(ev.get("name", "Event " + str(x["id"])))}</a>'
                    f'{sig_badge}</td>'
                    f'<td class="n">{e(x["rank"]) + " of " + e(x["of"]) if x.get("rank") else "-"}</td><td>{x["w"]}-{x["l"]}-{x["t"]}</td>'
                    f'<td>{e(x["result"]) or "-"}</td><td class="n">{x["skills"] or "-"}</td><td class="w">{e(", ".join(x["awards"]))}</td></tr>')
    body += ("<h2>Events this season</h2><div class=\"wrap\"><table><thead><tr><th>Dates</th><th>Event</th><th class=\"n\">Rank</th>"
             "<th>Record</th><th>Elimination result</th><th class=\"n\">Skills</th><th>Awards</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>")
    if last_season:
        p = last_season
        body += (f"<h2>Last season: Push Back 2025-2026</h2><p class=\"lead\">Competed at the 2026 VEX Robotics World Championship"
                 f"{' in the ' + e(p['division']) + ' division' if p.get('division') else ''}. Season record {p['wins']}-{p['losses']}"
                 f"{', skills (best driver + best autonomous) ' + str((p.get('driverMax') or 0) + (p.get('autoMax') or 0)) if p.get('driverMax') or p.get('autoMax') else ''}."
                 f" <a href=\"{BASE}/worlds-2026/\">Worlds 2026 dashboard</a></p>")
    ld = {"@context": "https://schema.org", "@type": "SportsTeam", "name": f'{n} {t["name"]}'.strip(),
          "sport": "VEX V5 Robotics Competition", "url": f"{SITE}{url}",
          "location": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": t.get("city", ""),
                                                     "addressRegion": t.get("region", ""), "addressCountry": t.get("country", "")}}}
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("Teams", None), (n, None)], body, ld)


def event_page(row, ev, team_names):
    sku = row["sku"]
    url = f"{BASE}/event/{sku}/"
    title = f'{row["name"]} results | {SEASON_TITLE} signature event'
    desc = (f'{row["name"]} ({fmt_range(row["start"], row["end"])}, {place(row) or row.get("country", "")}): '
            f'{SEASON_TITLE} signature event.')
    if row.get("champions"):
        desc += f' Tournament champions {" and ".join(row["champions"])}.'
    desc += " Rankings, awards and skills from official VEX data."
    tl = lambda n: f'<a class="tm" href="{team_url(n)}">{e(n)}</a>' if n in team_names else f'<span class="tm">{e(n)}</span>'
    body = f"""<h1>{e(row["name"])}</h1>
<p class="lead">{e(fmt_range(row["start"], row["end"]))} &bull; {e(place(row) or row.get("country"))} {grade_badge(row.get("grade"))} <span class="badge gold">Signature event</span></p>
<a class="cta" href="{BASE}/#event/{row["id"]}">Open in the VEX Visualizer</a><a class="cta alt" href="https://events.vex.com/robot-competitions/vex-robotics-competition/{e(sku)}.html">Official event page</a>
"""
    if not ev or not (ev.get("matches") or ev.get("awards") or ev.get("skills")):
        body += f'<p class="note">{"This event has not started yet." if row.get("status") == "upcoming" else "No results have been published yet."} Results appear here after the event.</p>'
    else:
        names = {t["n"]: t["name"] for t in ev["teams"]}
        played = [m for m in ev["matches"] if m["played"]]
        scores = [s for m in played for s in (m["rs"], m["bs"])]
        champs = [n for a in ev["awards"] if a["title"].startswith("Tournament Champions") for n in a["teams"]]
        body += f"""<div class="facts">
<div class="fact"><div class="v">{len(ev["teams"])}</div><div class="k">Teams</div></div>
<div class="fact"><div class="v">{len(played)}</div><div class="k">Matches played</div></div>
<div class="fact"><div class="v">{max(scores) if scores else "-"}</div><div class="k">Highest alliance score</div></div>
<div class="fact"><div class="v">{(ev["skills"][0]["driver"] + ev["skills"][0]["prog"]) if ev["skills"] else "-"}</div><div class="k">Top skills score</div></div>
</div>"""
        if champs:
            body += "<h2>Tournament champions</h2><p class=\"lead\">" + " and ".join(f'{tl(n)} {e(names.get(n, ""))}' for n in champs) + "</p>"
        if ev["awards"]:
            body += "<h2>Awards</h2><div class=\"awards\">" + "".join(
                f'<div class="award"><b>{e(a["title"])}{" (qualifies for Worlds)" if "World Championship" in a["quals"] else ""}</b>'
                f'{" ".join(tl(n) for n in a["teams"]) or e(", ".join(a["people"]))}</div>' for a in ev["awards"]) + "</div>"
        if ev["rankings"]:
            body += ("<h2>Qualification rankings</h2><div class=\"wrap\"><table><thead><tr><th class=\"n\">Rank</th><th>Team</th><th>Name</th>"
                     "<th>Record</th><th class=\"n\">Win points</th><th class=\"n\">Average score</th></tr></thead><tbody>"
                     + "".join(f'<tr><td class="n">{r["rank"]}</td><td>{tl(r["team"])}</td><td class="dim">{e(names.get(r["team"], ""))}</td>'
                               f'<td>{r["w"]}-{r["l"]}-{r["t"]}</td><td class="n">{r["wp"]}</td><td class="n">{r["avg"]}</td></tr>'
                               for r in ev["rankings"]) + "</tbody></table></div>")
        if ev["skills"]:
            body += ("<h2>Skills at this event</h2><div class=\"wrap\"><table><thead><tr><th class=\"n\">Rank</th><th>Team</th><th>Name</th>"
                     "<th class=\"n\">Combined</th><th class=\"n\">Driver</th><th class=\"n\">Autonomous</th></tr></thead><tbody>"
                     + "".join(f'<tr><td class="n">{s["rank"]}</td><td>{tl(s["team"])}</td><td class="dim">{e(names.get(s["team"], ""))}</td>'
                               f'<td class="n">{s["driver"] + s["prog"]}</td><td class="n">{s["driver"]}</td><td class="n">{s["prog"]}</td></tr>'
                               for s in ev["skills"][:20]) + "</tbody></table></div>")
    ld = {"@context": "https://schema.org", "@type": "SportsEvent", "name": row["name"], "sport": "VEX V5 Robotics Competition",
          "startDate": row["start"], "endDate": row["end"], "url": f"{SITE}{url}",
          "location": {"@type": "Place", "name": place(row) or row.get("country", ""),
                       "address": {"@type": "PostalAddress", "addressLocality": row.get("city", ""),
                                   "addressRegion": row.get("region", ""), "addressCountry": row.get("country", "")}}}
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("Signature events", f"{BASE}/signature/"), (sku, None)], body, ld)


def skills_page(standings, team_names):
    url = f"{BASE}/skills/"
    title = f"{SEASON_TITLE} skills leaderboard | World Skills Standings"
    top = (standings.get("hs") or [{}])[0]
    desc = (f"Official VEX World Skills Standings for {SEASON_TITLE}: every high school and middle school team's best "
            f"combined driver + autonomous skills score.")
    if top.get("team"):
        desc += f" High school leader: {top['team']} {top.get('name', '')} with {top.get('score')}."
    tl = lambda n: f'<a class="tm" href="{team_url(n)}">{e(n)}</a>' if n in team_names else f'<span class="tm">{e(n)}</span>'
    body = f"""<h1>Skills leaderboard: {e(SEASON_TITLE)}</h1>
<p class="lead">Official World Skills Standings: each team's best combined driver + autonomous score from a single event this season. Ranks match events.vex.com.</p>
<a class="cta" href="{BASE}/#skills">Open the interactive leaderboard</a>"""
    for key, label in (("hs", "High school"), ("ms", "Middle school")):
        rows = standings.get(key) or []
        body += (f"<h2>{label} ({len(rows)} teams)</h2><div class=\"wrap\"><table><thead><tr><th class=\"n\">Rank</th><th>Team</th>"
                 "<th class=\"n\">Score</th><th class=\"n\">Autonomous</th><th class=\"n\">Driver</th><th>Name</th><th>Location</th></tr></thead><tbody>"
                 + "".join(f'<tr><td class="n">{r["rank"]}</td><td>{tl(r["team"])}</td><td class="n"><b>{r["score"]}</b></td><td class="n">{r["prog"]}</td>'
                           f'<td class="n">{r["driver"]}</td><td class="dim">{e(r["name"])}</td><td class="dim">{e(", ".join(x for x in (r.get("region"), r.get("country")) if x))}</td></tr>'
                           for r in rows) + "</tbody></table></div>")
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("Skills standings", None)], body)


def trueskill_page(teams):
    url = f"{BASE}/trueskill/"
    groups = []
    for grade, label in (("High School", "High school"), ("Middle School", "Middle school")):
        rows = sorted((t for t in teams.values() if t.get("trueskill") and t.get("grade") == grade),
                      key=lambda t: t["trueskill"]["rank"])
        groups.append((label, rows))
    title = f"{SEASON_TITLE} TrueSkill rankings | True Skill ratings for every team"
    desc = (f"TrueSkill (True Skill) ratings for every high school and middle school team in the {SEASON_TITLE} season, "
            f"computed from official VEX match results, with how TrueSkill is calculated.")
    hs = groups[0][1]
    if hs:
        desc += f" High school leader: {hs[0]['team']} {hs[0]['name']} with {hs[0]['trueskill']['rating']:.1f}."
    body = f"""<h1>TrueSkill rankings: {e(SEASON_TITLE)}</h1>
<p class="lead">TrueSkill rates every team from the results of its matches this season, taking into account how strong its partners and opponents were. Computed by this site from official VEX match results. Higher is better.</p>
<a class="cta" href="{BASE}/#trueskill">Open the interactive rankings</a><a class="cta alt" href="#how">How TrueSkill is calculated</a>
<p class="note">Jump to: <a href="#high-school">High school ({len(groups[0][1])} teams)</a> &bull; <a href="#middle-school">Middle school ({len(groups[1][1])} teams)</a> &bull; <a href="#how">How it is calculated</a></p>"""
    for label, rows in groups:
        body += (f'<h2 id="{label.lower().replace(" ", "-")}">{label} ({len(rows)} teams)</h2>'
                 '<div class="wrap"><table><thead><tr><th class="n">Rank</th><th>Team</th>'
                 '<th class="n">TrueSkill</th><th class="n">Matches</th><th>Qualification record</th><th>Name</th><th>Location</th></tr></thead><tbody>'
                 + "".join(f'<tr><td class="n">{t["trueskill"]["rank"]}</td><td><a class="tm" href="{team_url(t["team"])}">{e(t["team"])}</a></td>'
                           f'<td class="n"><b>{t["trueskill"]["rating"]:.1f}</b></td><td class="n">{t["trueskill"]["matches"]}</td>'
                           f'<td>{t["w"]}-{t["l"]}-{t["t"]}</td><td class="dim">{e(t["name"])}</td><td class="dim">{e(place(t))}</td></tr>'
                           for t in rows) + "</tbody></table></div>")
        if not rows:
            body += '<p class="note">No rated teams yet.</p>'
    body += f'<h2 id="how">How TrueSkill is calculated</h2>{TRUESKILL_HOW}'
    ld = {"@context": "https://schema.org", "@type": "Dataset", "name": f"{SEASON_TITLE} TrueSkill rankings",
          "description": desc, "url": f"{SITE}{url}", "creator": {"@type": "Person", "name": "Arjun"},
          "isBasedOn": "https://events.vex.com/"}
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("TrueSkill rankings", None)], body, ld)


def signature_list_page(index, sig_pages):
    url = f"{BASE}/signature/"
    sig = [r for r in index if r.get("level") == "Signature"]
    done = sum(1 for r in sig if r["status"] == "done")
    title = f"{SEASON_TITLE} signature events: schedule and results"
    desc = f"All {len(sig)} signature events of the {SEASON_TITLE} season with dates, locations, tournament champions and Excellence winners. {done} held so far."
    body = f"""<h1>Signature events: {e(SEASON_TITLE)}</h1>
<p class="lead">The top tier of the season. Awards at signature events can qualify teams for the VEX Robotics World Championship. {done} of {len(sig)} held.</p>
<a class="cta" href="{BASE}/#signature">Open in the VEX Visualizer</a>
<div class="wrap" style="margin-top:18px"><table><thead><tr><th>Dates</th><th>Event</th><th>Location</th><th>Grade</th><th>Tournament champions</th><th>Excellence</th></tr></thead><tbody>"""
    for r in sig:
        link = f"{BASE}/event/{r['sku']}/" if r["sku"] in sig_pages else f"{BASE}/#event/{r['id']}"
        body += (f'<tr><td>{e(fmt_range(r["start"], r["end"]))}</td><td class="w"><a href="{e(link)}">{e(r["name"])}</a></td>'
                 f'<td class="dim">{e(place(r) or r.get("country"))}</td><td>{grade_badge(r.get("grade"))}</td>'
                 f'<td>{" ".join(f"<a class=tm href={team_url(n)}>{e(n)}</a>" for n in r.get("champions") or []) or "<span class=dim>-</span>"}</td>'
                 f'<td>{" ".join(f"<a class=tm href={team_url(n)}>{e(n)}</a>" for n in r.get("excellence") or []) or "<span class=dim>-</span>"}</td></tr>')
    body += "</tbody></table></div>"
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("Signature events", None)], body)


def worlds_page(teams, by_id):
    url = f"{BASE}/worlds-qualifiers/"
    q = sorted((t for t in teams.values() if t.get("worlds")),
               key=lambda t: (by_id.get(t["worlds"][1], {}).get("start", ""), t["team"]), reverse=True)
    title = f"2027 VEX Worlds qualifiers so far | {SEASON_TITLE}"
    desc = (f"{len(q)} teams have qualified for the 2027 VEX Robotics World Championship through awards so far in the {SEASON_TITLE} season. "
            "List with the qualifying award and event.")
    body = f"""<h1>2027 World Championship qualifiers so far</h1>
<p class="lead">Teams that have won an award VEX lists as qualifying for the 2027 VEX Robotics World Championship. Spots given through skills rankings or reallocation are not included. Newest first.</p>
<a class="cta" href="{BASE}/#worlds">Open in the VEX Visualizer</a>
<div class="wrap" style="margin-top:18px"><table><thead><tr><th>Team</th><th>Name</th><th>Qualified by</th><th>Event</th><th>Date</th><th>Location</th></tr></thead><tbody>"""
    for t in q:
        ev = by_id.get(t["worlds"][1], {})
        body += (f'<tr><td><a class="tm" href="{team_url(t["team"])}">{e(t["team"])}</a></td><td class="dim">{e(t["name"])}</td>'
                 f'<td>{e(t["worlds"][0])}</td><td class="w">{e(ev.get("name", ""))}</td><td class="dim">{e(fmt_date(ev.get("start")))}</td>'
                 f'<td class="dim">{e(place(t))}</td></tr>')
    body += "</tbody></table></div>"
    return url, page(url, title, desc, [("Visualizer", f"{BASE}/"), ("Worlds qualifiers", None)], body)


# ---------------------------------------------------------------------------
# Build everything
# ---------------------------------------------------------------------------

def _write(path, text):
    old = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def build(vis_dir, index, buckets, standings, cached, last_season=None):
    """Write all pages under vis_dir. Returns (pages written or changed, pages removed)."""
    teams = {n: t for b in buckets.values() for n, t in b.items()}
    by_id = {r["id"]: r for r in index}
    sig_rows = [r for r in index if r.get("level") == "Signature" and r.get("sku")]
    sig_pages = {r["sku"] for r in sig_rows}
    last_season = last_season or {}
    pages = []
    for n in sorted(teams):
        evs = teams[n]["events"]
        lastmod = by_id.get(evs[-1]["id"], {}).get("end", "") if evs else ""
        pages.append(team_page(teams[n], by_id, sig_pages, last_season.get(n)) + (lastmod,))
    for r in sig_rows:
        pages.append(event_page(r, cached.get(r["id"]), set(teams)) + (r.get("end") or "",))
    latest = max((r["end"] for r in index if r.get("results") and r.get("end")), default="")
    pages.append(skills_page(standings, set(teams)) + (latest,))
    pages.append(signature_list_page(index, sig_pages) + (latest,))
    pages.append(worlds_page(teams, by_id) + (latest,))
    pages.append(trueskill_page(teams) + (latest,))

    written = 0
    keep = set()
    for url, text, _ in pages:
        rel = url[len(BASE) + 1:]
        path = os.path.join(vis_dir, *rel.strip("/").split("/"), "index.html")
        keep.add(os.path.normpath(path))
        written += _write(path, text)

    removed = 0
    for top in ("team", "event"):
        root = os.path.join(vis_dir, top)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.normpath(os.path.join(root, name, "index.html"))
            if p not in keep:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                removed += 1

    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, _, lastmod in sorted(pages, key=lambda p: p[0]):
        sm.append(f"  <url><loc>{SITE}{html.escape(url)}</loc>{f'<lastmod>{lastmod[:10]}</lastmod>' if lastmod else ''}</url>")
    sm.append("</urlset>")
    written += _write(os.path.join(vis_dir, "sitemap-pages.xml"), "\n".join(sm) + "\n")
    return written, removed
