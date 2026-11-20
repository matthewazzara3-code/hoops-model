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

PHASE = {"post_1118": False}


def fake_fetch(d):
    if d == "20261118":
        return build("2026-11-18", completed=PHASE["post_1118"])
    if d == "20261119":
        return build("2026-11-19", completed=False)
    return {"events": []}


def fake_splits(page, edate="today"):
    return (ROOT / "tests/fixtures/dk_splits_sample.html").read_text() if page == 1 else ""


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
