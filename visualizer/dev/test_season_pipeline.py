"""Tests for season_pipeline.py on synthetic data (no network, no token).

Run: python visualizer/dev/test_season_pipeline.py
"""
import random, statistics, sys, json, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import season_pipeline as sp

# 1. OPR recovers known contributions from noisy synthetic qualification matches.
random.seed(1)
teams = [f"{100+i}A" for i in range(40)]
true = {t: random.uniform(5, 60) for t in teams}
matches = []
for k in range(160):
    four = random.sample(teams, 4)
    red, blue = four[:2], four[2:]
    rs = round(sum(true[t] for t in red) + random.gauss(0, 4))
    bs = round(sum(true[t] for t in blue) + random.gauss(0, 4))
    matches.append({"div": 1, "round": 2, "played": True, "red": red, "blue": blue, "rs": rs, "bs": bs})
t0 = time.time(); opr = sp.compute_opr(matches); dt = time.time() - t0
err = max(abs(opr[t][0] - true[t]) for t in teams)
print(f"OPR: {len(opr)} teams, max abs error {err:.1f} pts, {dt*1000:.0f} ms")
assert len(opr) == 40 and err < 8

# 2. compact_event on records shaped like the real v2 responses.
ev = {"id": 1, "sku": "RE-V5RC-26-0001", "name": "Test Signature Event", "start": "2026-09-25T00:00:00-04:00",
      "end": "2026-09-27T00:00:00-04:00", "level": "Signature", "ongoing": False, "awards_finalized": True,
      "location": {"city": "Newark", "region": "New Jersey", "country": "United States"},
      "divisions": [{"id": 1, "name": "Default Division", "order": 1}], "event_type": None}
raw_teams = [{"number": t, "team_name": "Team " + t, "organization": "Org", "grade": "High School",
              "location": {"city": "X", "region": "Y", "country": "United States"}} for t in teams]
idinfo = lambda t: {"id": 0, "name": t, "code": None}
raw_rank = [{"division": {"id": 1}, "rank": i + 1, "team": idinfo(t), "wins": 5, "losses": 3, "ties": 0, "wp": 10,
             "ap": 2, "sp": 100, "high_score": 120, "average_points": 60.5} for i, t in enumerate(teams)]
raw_matches = [{"division": {"id": 1}, "round": m["round"], "instance": 1, "matchnum": i + 1, "name": f"Qualifier #{i+1}",
                "started": "2026-09-26T10:00:00-04:00", "scheduled": None, "scored": False,
                "alliances": [{"color": "blue", "score": m["bs"], "teams": [{"sitting": False, "team": idinfo(t)} for t in m["blue"]]},
                              {"color": "red", "score": m["rs"], "teams": [{"sitting": False, "team": idinfo(t)} for t in m["red"]]}]}
               for i, m in enumerate(matches)]
raw_matches.append({"division": {"id": 1}, "round": 5, "instance": 1, "matchnum": 1, "name": "Final #1-1",
                    "started": "2026-09-27T15:00:00-04:00", "alliances": [
                        {"color": "red", "score": 150, "teams": [{"sitting": False, "team": idinfo(teams[0])}, {"sitting": False, "team": idinfo(teams[1])}]},
                        {"color": "blue", "score": 90, "teams": [{"sitting": False, "team": idinfo(teams[2])}, {"sitting": False, "team": idinfo(teams[3])}]}]})
raw_skills = [{"team": {"id": 0, "name": teams[0], "code": "HS"}, "type": "driver", "score": 148, "attempts": 2},
              {"team": {"id": 0, "name": teams[0], "code": "HS"}, "type": "programming", "score": 108, "attempts": 3}]
raw_awards = [{"order": 49, "title": "Tournament Champions (V5)", "qualifications": ["World Championship"],
               "teamWinners": [{"team": idinfo(teams[0])}, {"team": idinfo(teams[1])}], "individualWinners": []},
              {"order": 45, "title": "Excellence Award (V5)", "qualifications": ["World Championship"],
               "teamWinners": [{"team": idinfo(teams[0])}], "individualWinners": []}]
c = sp.compact_event(ev, raw_teams, raw_skills, raw_awards, raw_rank, raw_matches)
assert c["grade"] == "High School" and c["complete"] is True
assert c["awards"][0]["title"] == "Excellence Award", c["awards"][0]
assert c["skills"][0] == {"team": teams[0], "driver": 148, "prog": 108, "rank": 1}
assert c["matches"][0]["red"] and c["rankings"][0]["team"] == teams[0]
idx, tindex, buckets, season = sp.build_summaries([ev], {1: c}, {"hs": [], "ms": []})
tm = {n: t for b in buckets.values() for n, t in b.items()}
assert len(tindex["rows"]) == 40 and tindex["cols"][0] == "team"
assert sp.team_bucket("6121E") == sum((i + 1) * ord(ch) for i, ch in enumerate("6121E")) % 64
assert season["monthly"][0]["month"] == "2026-09" and season["signature"][0]["champions"]
t = tm[teams[0]]
print("team summary:", {k: t[k] for k in ("w", "l", "ew", "el", "titles", "excellence", "worlds", "opr")}, t["events"][0]["result"])
assert t["worlds"] == ["Tournament Champions", 1] or t["worlds"] == ["Excellence Award", 1]
assert t["events"][0]["result"] == "Champion" and tm[teams[2]]["events"][0]["result"] == "Finalist"
print("event row:", {k: idx[0][k] for k in ("status", "results", "matches", "topScore", "champions", "excellence")})

# 3. TrueSkill matches the reference library (trueskill 0.4.5, default settings).
close = lambda r, mu, sigma: abs(r[0] - mu) < 1e-3 and abs(r[1] - sigma) < 1e-3
R = {}
sp.trueskill_update(R, ["1A", "1B"], ["2A", "2B"], 50, 40)          # red wins
assert close(R["1A"], 28.1083, 7.7744) and close(R["2B"], 21.8917, 7.7744), R
sp.trueskill_update(R, ["1A", "1B"], ["2A", "2B"], 30, 30)          # then a tie
assert close(R["1B"], 25.6964, 6.9801) and close(R["2A"], 24.3036, 6.9801), R
R = {}
sp.trueskill_update(R, ["3A"], ["4A"], 10, 20)                      # one-team alliances, blue wins
assert close(R["3A"], 20.6042, 7.1715) and close(R["4A"], 29.3958, 7.1715), R
assert not sp.trueskill_update(R, ["5A", "6A"], ["5A", "7A"], 1, 0)  # same team on both sides: skipped
ts = {n: tm[n]["trueskill"] for n in teams}
assert sorted(x["rank"] for x in ts.values()) == list(range(1, 41)) and all(x["of"] == 40 for x in ts.values())
top = min(teams, key=lambda n: ts[n]["rank"])
assert true[top] > statistics.median(true.values()), (top, true[top])
assert tindex["cols"][-3:] == ["ts", "tsRank", "tsMatches"] and season["leaders"]["trueSkillHS"][0]["team"] == top
print(f"TrueSkill: #1 {top} rating {ts[top]['rating']} over {ts[top]['matches']} matches")
print("season counts:", season["counts"])
print("ALL TESTS PASSED")
