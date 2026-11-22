"""End to end: make picks on 11/18 (pre game), then run 11/19 and grade 11/18 from real finals.
python -m pytest -q tests  (or python tests/test_pipeline.py)"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from make_fixtures import build  # noqa: E402
from model import espn, picks, run, splits  # noqa: E402
from model.data import load_games  # noqa: E402

PHASE = {"post_1118": False, "post_1119": False}


def _strip_live_odds(sb):
    """ESPN drops the odds block once a game has tipped."""
    for e in sb["events"]:
        if e["competitions"][0]["status"]["type"]["state"] != "pre":
            e["competitions"][0]["odds"] = []
    return sb


def fake_fetch(d):
    if d == "20261118":
        return _strip_live_odds(build("2026-11-18", completed=PHASE["post_1118"]))
    if d == "20261119":
        return _strip_live_odds(build("2026-11-19", completed=PHASE["post_1119"]))
    return {"events": []}


def fake_splits(page, edate="today"):
    """Real DK splits HTML, with one game renamed to tonight's Houston @ Kansas."""
    if page != 1:
        return ""
    html = (ROOT / "tests/fixtures/dk_splits_sample.html").read_text()
    return html.replace("HOU Rockets", "KU Jayhawks").replace("MIN Timberwolves", "HOU Cougars")


def run_for(day, out):
    run.OUT = out
    run.espn.fetch_scoreboard = fake_fetch
    run.load_games = lambda s, refresh=False: load_games(s, refresh=False)
    run.sp.fetch_page = fake_splits
    sys.argv = ["run", "--date", day]
    run.main()


def test_end_to_end():
    out = Path(tempfile.mkdtemp()) / "data"
    try:
        run_for("2026-11-18", out)
        p1 = json.loads((out / "picks.json").read_text())
        assert p1 and all(p["date"] == "2026-11-18" and "result" not in p for p in p1)
        PHASE["post_1118"] = True
        PHASE["post_1119"] = False
        run_for("2026-11-19", out)
        p2 = json.loads((out / "picks.json").read_text())
        graded = [p for p in p2 if p["date"] == "2026-11-18"]
        assert graded and all(p.get("result") in ("W", "L", "P") for p in graded)
        rec = json.loads((out / "record.json").read_text())
        o = rec["overall"]
        assert o["w"] + o["l"] + o["p"] == len(graded)
        board = json.loads((out / "board.json").read_text())
        assert board["date"] == "2026-11-19" and len(board["games"]) > 20
        g = board["games"][0]
        assert {"spread", "total", "moneyline"} <= set(g["eval"]["markets"])
        sp = json.loads((out / "splits.json").read_text())
        assert len(sp["games"]) == 10 and sp["error"] is None
        ss = json.loads((out / "sharp_picks.json").read_text())
        assert any(x["matchup"].endswith("Kansas") and x["market"] == "total" and x["side"] == "under" for x in ss), ss
        # games finish: ESPN drops odds, the board keeps the last pregame DK line, sharp sides get graded
        PHASE["post_1119"] = True
        run_for("2026-11-19", out)
        board = json.loads((out / "board.json").read_text())
        assert all(g["odds"] and g.get("odds_closed") for g in board["games"])
        rec = json.loads((out / "record.json").read_text())
        ss = json.loads((out / "sharp_picks.json").read_text())
        graded_spreads = [x for x in ss if x["market"] == "spread" and x.get("result")]
        assert any(x["market"] == "total" and x.get("result") for x in ss)  # still saved...
        o2 = rec["sharp_sides"]["overall"]
        assert o2["w"] + o2["l"] + o2["p"] == len(graded_spreads)  # ...but only spreads count
        assert set(rec["sharp_sides"]["by_market"]) <= {"spread"}
        print("picks 11/18:", len(graded), "record:", o, "| 11/19 new picks:", sum(p["date"] == "2026-11-19" for p in p2))
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)


def test_odds_formats_and_grading():
    o = espn.parse_odds({"details": "DUKE -7.5", "overUnder": 145.5, "homeTeamOdds": {"moneyLine": -300},
                         "awayTeamOdds": {"moneyLine": 240}}, "UNC", "DUKE")
    assert o["home_spread"] == 7.5 and o["total"] == 145.5 and o["away_ml"] == 240
    o = espn.parse_odds({"pointSpread": {"home": {"close": {"line": "-3", "odds": "-115"}}},
                         "total": {"over": {"close": {"line": "o140.5", "odds": "-110"}}}}, "A", "B")
    assert o["home_spread"] == -3 and o["home_spread_odds"] == -115 and o["total"] == 140.5
    g = {"home": {"score": 70, "abbr": "H"}, "away": {"score": 67, "abbr": "A"}}
    assert picks.grade({"market": "spread", "side": "home", "line": -3, "odds": -110}, g)["result"] == "P"
    assert picks.grade({"market": "spread", "side": "away", "line": 3.5, "odds": -110}, g)["result"] == "W"
    assert picks.grade({"market": "total", "side": "under", "line": 136.5, "odds": -110}, g)["result"] == "L"
    w = picks.grade({"market": "moneyline", "side": "home", "line": None, "odds": 150}, g)
    assert w["result"] == "W" and w["units"] == 1.5
    c = {"market": "spread", "side": "home", "line": -3, "odds": -110, "close_line": -5, "close_odds": -110}
    assert picks.clv(c) == 2.0


def test_splits_flag_rules():
    games = splits.parse_page((ROOT / "tests/fixtures/dk_splits_sample.html").read_text())
    pts = splits.flag(games, 30, "points")
    assert pts and all(f["handle"] - f["bets"] >= 30 for f in pts)
    rel = splits.flag(games, 30, "relative")
    assert all(f["handle"] >= f["bets"] * 1.3 for f in rel)


if __name__ == "__main__":
    test_odds_formats_and_grading()
    test_splits_flag_rules()
    test_end_to_end()
    print("all passed")


class StubRatings:
    """Model says home by 6.5, total 145.8 (like Santa Clara / Houston-Kansas on 11/19)."""
    games = {"H": 5, "A": 5}

    def known(self, t):
        return True

    def predict(self, h, a, neutral=False, conf=False):
        return dict(home_pts=76.15, away_pts=69.65, margin=6.5, total=145.8, pace=70, sigma_margin=12.0,
                    sigma_total=17.5, home_win=0.70)


def _game(**odds):
    base = dict(home_spread=-1.5, home_spread_odds=-110, away_spread_odds=-110, total=151.5, over_odds=-105,
                under_odds=-115, home_ml=-130, away_ml=110, provider="DraftKings")
    base.update(odds)
    return {"id": "1", "neutral": False, "conf": False, "odds": base,
            "home": {"id": "H", "short": "Santa Clara", "name": "Santa Clara Broncos", "abbr": "SCU"},
            "away": {"id": "A", "short": "VCU", "name": "VCU Rams", "abbr": "VCU"}}


def test_blend_shrinks_ev():
    g = _game()
    raw = picks.evaluate(g, StubRatings(), w=0.0)
    blended = picks.evaluate(g, StubRatings())
    assert blended["markets"]["spread"]["home"]["ev"] < raw["markets"]["spread"]["home"]["ev"]
    assert abs(blended["blend"]["margin"] - (0.7 * 1.5 + 0.3 * 6.5)) < 1e-6
    assert blended["markets"]["spread"]["edge"] == 5.0  # raw gap still reported


def test_line_move_and_sharp_filters():
    g = _game(home_spread=-3.5)  # model home by 6.5 vs DK 3.5: 3 point edge
    g["open"] = {"home_spread": -3.5, "total": 151.5, "home_ml": -130, "away_ml": 110}
    p, ps = picks.choose(g, picks.evaluate(g, StubRatings(), w=0.0))
    assert any(x["market"] == "spread" for x in p)
    g["open"]["home_spread"] = -7.5  # line moved 4 pts toward VCU since open
    p, ps = picks.choose(g, picks.evaluate(g, StubRatings(), w=0.0))
    assert not any(x["market"] == "spread" for x in p) and any(x["market"] == "spread" for x in ps)
    g["open"]["home_spread"] = -3.5
    g["sharp"] = {"spread": {"away": 5, "home": -5}}  # slight lean the other way: still a pick
    p, ps = picks.choose(g, picks.evaluate(g, StubRatings(), w=0.0))
    assert any(x["market"] == "spread" for x in p)
    g["sharp"] = {"spread": {"away": 45, "home": -45}}
    p, ps = picks.choose(g, picks.evaluate(g, StubRatings(), w=0.0))
    assert any(x["market"] == "spread" and "Sharp" in x["reason"] for x in ps)
    g["sharp"] = {"spread": {"home": 35}}
    p, ps = picks.choose(g, picks.evaluate(g, StubRatings(), w=0.0))
    assert any(x["market"] == "spread" and x["sharp"] == "agrees" for x in p)
