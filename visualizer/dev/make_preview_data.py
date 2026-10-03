"""Build a clearly labeled SYNTHETIC dataset for layout checks (no network).

Writes visualizer/_preview/ (a copy of index.html plus synthetic data). Keep
_preview/ out of git: add "visualizer/_preview/" to .git/info/exclude.
Run: python visualizer/dev/make_preview_data.py, then open /visualizer/_preview/.
"""
import os, random, shutil, sys
from datetime import date, timedelta

vis = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, vis)
import season_pipeline as sp

random.seed(7)
out = os.path.join(vis, "_preview", "data")
shutil.rmtree(os.path.join(vis, "_preview"), ignore_errors=True)
os.makedirs(out)
shutil.copy(os.path.join(vis, "index.html"), os.path.join(vis, "_preview", "index.html"))
sp.DATA_DIR = out
sp.EVENTS_DIR = os.path.join(out, "events")

pool = [(f"{1000 + i}{'ABCX'[i % 4]}", "High School" if i % 3 else "Middle School") for i in range(300)]
skill = {t: random.uniform(0.5, 1.5) for t, _ in pool}
idinfo = lambda t: {"id": 0, "name": t, "code": None}
today = sp.today_utc()
events, cached = [], {}
for k in range(46):
    st = date(2026, 6, 20) + timedelta(days=5 * k)
    lvl = "Signature" if k % 5 == 3 else "Other"
    grade = random.choice(["High School", "Middle School"])
    ev = {"id": 900000 + k, "sku": f"TEST-{k:03d}", "name": f"Synthetic test event {k + 1}" + (" (signature)" if lvl == "Signature" else ""),
          "start": st.isoformat() + "T00:00:00-04:00", "end": (st + timedelta(days=1)).isoformat() + "T00:00:00-04:00",
          "level": lvl, "ongoing": False, "awards_finalized": True, "season": {"id": 204},
          "location": {"city": random.choice(["Newark", "Austin", "Toronto", "Shanghai", "Lincoln"]), "region": "Test region",
                       "country": random.choice(["United States", "Canada", "China"])},
          "divisions": [{"id": 1, "name": "Default Division"}], "event_type": None}
    events.append(ev)
    if st > today:
        continue
    tms = random.sample([t for t, g in pool if g == grade], random.randint(16, 32))
    level = 40 + 4 * k
    raw_teams = [{"number": t, "team_name": f"Test Team {t}", "organization": "Test School", "grade": grade,
                  "location": {"city": ev["location"]["city"], "region": "Test region", "country": ev["location"]["country"]}} for t in tms]
    ms, rec = [], {t: [0, 0, 0] for t in tms}
    for i in range(len(tms) * 3):
        four = random.sample(tms, 4)
        rs = max(0, round(sum(level / 2 * skill[t] for t in four[:2]) + random.gauss(0, 8)))
        bs = max(0, round(sum(level / 2 * skill[t] for t in four[2:]) + random.gauss(0, 8)))
        for t in four[:2]: rec[t][0 if rs > bs else 1 if rs < bs else 2] += 1
        for t in four[2:]: rec[t][0 if bs > rs else 1 if bs < rs else 2] += 1
        ms.append({"division": {"id": 1}, "round": 2, "instance": 1, "matchnum": i + 1, "name": f"Qualifier #{i + 1}", "started": ev["start"],
                   "alliances": [{"color": "red", "score": rs, "teams": [{"team": idinfo(t)} for t in four[:2]]},
                                 {"color": "blue", "score": bs, "teams": [{"team": idinfo(t)} for t in four[2:]]}]})
    ranked = sorted(tms, key=lambda t: (-rec[t][0], -skill[t]))
    rank = [{"division": {"id": 1}, "rank": i + 1, "team": idinfo(t), "wins": rec[t][0], "losses": rec[t][1], "ties": rec[t][2],
             "wp": 2 * rec[t][0], "ap": random.randint(0, 8), "sp": random.randint(100, 400), "high_score": level + 40,
             "average_points": level} for i, t in enumerate(ranked)]
    a, b, c, d_ = [ranked[0], ranked[3]], [ranked[1], ranked[2]], [ranked[4], ranked[7]], [ranked[5], ranked[6]]

    def elim(rnd, inst, r, bl):
        hi, lo = level + random.randint(20, 50), level - random.randint(0, 30)
        return {"division": {"id": 1}, "round": rnd, "instance": inst, "matchnum": 1, "name": f"R{rnd} #{inst}", "started": ev["start"],
                "alliances": [{"color": "red", "score": hi, "teams": [{"team": idinfo(t)} for t in r]},
                              {"color": "blue", "score": lo, "teams": [{"team": idinfo(t)} for t in bl]}]}
    ms += [elim(4, 1, a, c), elim(4, 2, b, d_), elim(5, 1, a, b)]
    sk = []
    for t in tms:
        sk.append({"team": {"name": t}, "type": "driver", "score": round(level * 0.9 * skill[t])})
        sk.append({"team": {"name": t}, "type": "programming", "score": round(level * 0.6 * skill[t])})
    q = ["World Championship"] if lvl == "Signature" or k % 2 else []
    aw = [{"order": 1, "title": "Excellence Award (V5)", "qualifications": q, "teamWinners": [{"team": idinfo(ranked[1])}]},
          {"order": 2, "title": "Tournament Champions (V5)", "qualifications": q, "teamWinners": [{"team": idinfo(t)} for t in a]},
          {"order": 3, "title": "Tournament Finalists (V5)", "qualifications": [], "teamWinners": [{"team": idinfo(t)} for t in b]},
          {"order": 4, "title": "Design Award (V5)", "qualifications": q, "teamWinners": [{"team": idinfo(ranked[9])}]},
          {"order": 5, "title": "Robot Skills Champion (V5)", "qualifications": [], "teamWinners": [{"team": idinfo(max(tms, key=lambda t: skill[t]))}]}]
    cev = sp.compact_event(ev, raw_teams, sk, aw, rank, ms)
    sp.write_json(os.path.join(sp.EVENTS_DIR, f"{ev['id']}.json"), cev)
    cached[ev["id"]] = cev

best = {}
for cev in cached.values():
    for s in cev["skills"]:
        if s["driver"] + s["prog"] > best.get(s["team"], {"score": -1})["score"]:
            best[s["team"]] = {"team": s["team"], "name": f"Test Team {s['team']}", "org": "Test School", "region": "Test region",
                               "country": cev["country"], "score": s["driver"] + s["prog"], "driver": s["driver"], "prog": s["prog"],
                               "event": cev["sku"], "date": cev["start"], "g": cev["grade"]}
std = {}
for key, g in (("hs", "High School"), ("ms", "Middle School")):
    rows = sorted([r for r in best.values() if r["g"] == g], key=lambda r: -r["score"])
    std[key] = [{**{k: v for k, v in r.items() if k != "g"}, "rank": i} for i, r in enumerate(rows, 1)]
sp.write_json(os.path.join(out, "skills.json"), std)
idx, tindex, buckets, season = sp.build_summaries(events, cached, std)
sp.write_json(os.path.join(out, "events.json"), idx)
sp.write_json(os.path.join(out, "teams_index.json"), tindex)
sp.write_json(os.path.join(out, "season.json"), season)
for bkt, data in buckets.items():
    sp.write_json(os.path.join(out, "teams", f"{bkt}.json"), data)
sp.write_json(os.path.join(out, "meta.json"), {"builtAt": "2026-10-02T21:00:00+00:00"})
print("SYNTHETIC preview:", len(idx), "events,", len(cached), "with results,", len(tindex["rows"]), "teams,", len(season["monthly"]), "months")
