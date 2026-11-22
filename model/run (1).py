"""Daily pipeline. python -m model.run [--date YYYY-MM-DD] [--no-splits] [--offline]

1. Load results (hoopR box scores, topped up from ESPN if the dataset lags)
2. Fit ratings (prior = last season, regressed)
3. Pull today's ESPN slate with DraftKings lines, compare to model, lock new picks
4. Track closing lines, grade finished picks, rebuild the record
5. Pull DraftKings betting splits and flag handle% >> bets%
Writes JSON for the website into docs/data/.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import espn, picks as pk, splits as sp
from .data import load_games
from .ratings import DEFAULTS, fit

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent.parent / "docs" / "data"
MODEL_VERSION = "1.2"


def season_for(d: date) -> int:
    return d.year + 1 if d.month >= 8 else d.year


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _clean(x):
    """JSON safe: NaN/inf -> None (browsers reject NaN in JSON)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _save(name: str, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(_clean(obj), indent=1, default=str, allow_nan=False))


def topup_from_espn(games: pd.DataFrame, season: int, today: date, fetch, log) -> pd.DataFrame:
    """If the box score dataset lags, add recent finals from ESPN (points only, pace estimated)."""
    last = games.date.max().date() if len(games) else date(today.year if today.month >= 8 else today.year - 1, 11, 1)
    days = [(last + timedelta(days=i)) for i in range(1, (today - last).days)]
    if not days:
        return games
    rows = []
    for d in days[-10:]:
        try:
            for g in espn.parse_scoreboard(fetch(d.strftime("%Y%m%d"))):
                if g["completed"] and g["home"]["score"] and g["away"]["score"]:
                    rows.append(dict(game_id=int(g["id"]), date=pd.Timestamp(d), season=season, neutral=g["neutral"], conf=g["conf"],
                                     h_team_id=g["home"]["id"], h_name=g["home"]["name"], a_team_id=g["away"]["id"],
                                     a_name=g["away"]["name"], h_pts=g["home"]["score"], a_pts=g["away"]["score"], pace=None))
        except Exception as e:
            log(f"ESPN top up {d}: {e}")
    if not rows:
        return games
    extra = pd.DataFrame(rows)
    extra = extra[~extra.game_id.isin(games.game_id)]
    log(f"Added {len(extra)} ESPN finals not yet in the box score dataset")
    return pd.concat([games, extra], ignore_index=True)


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower().replace("st.", "state").replace("&", "")))


def _side_is_home(label: str, game: dict) -> bool:
    def sc(team):
        a = _tokens(label)
        return len(a & (_tokens(team["name"]) | {team["abbr"].lower()})) / max(len(a), 1)
    return sc(game["home"]) >= sc(game["away"])


def match_splits(dk_games: list[dict], board: list[dict]) -> dict:
    """Attach each DK splits game to an ESPN game (by team name tokens), record model agreement,
    and return {espn_id: {market: {side: handle - bets}}} for the pick filters."""
    def team_score(label, team):
        a = _tokens(label)
        b = _tokens(team["name"]) | {team["abbr"].lower()}
        return len(a & b) / max(len(a), 1)
    sharp: dict = {}
    for g in dk_games:
        best, best_s = None, 0.0
        for b in board:
            s = (team_score(g["home"], b["home"]) + team_score(g["away"], b["away"])) / 2
            if s > best_s:
                best, best_s = b, s
        g["espn_id"] = best["id"] if best and best_s >= 0.5 else None
        g["model"] = None
        if not g["espn_id"]:
            continue
        mk = best["eval"].get("markets", {})
        g["model"] = {"margin": best["eval"]["model"]["margin"], "total": best["eval"]["model"]["total"],
                      "home_spread": best["eval"]["model"]["home_spread"]}
        gs = sharp.setdefault(best["id"], {})
        for mkt, sides in g["markets"].items():
            for s in sides:
                lab = s["label"].lower()
                agree, side = None, None
                if mkt == "total":
                    side = "over" if lab.startswith("over") else "under"
                    if "total" in mk:
                        agree = (mk["total"]["edge"] > 0) == (side == "over")
                elif mkt in ("spread", "moneyline"):
                    is_home = team_score(s["label"], best["home"]) >= team_score(s["label"], best["away"])
                    side = "home" if is_home else "away"
                    if mkt in mk:
                        agree = (mk[mkt]["edge"] > 0) == is_home
                s["model_agrees"] = agree
                if side:
                    gs.setdefault(mkt, {})[side] = s["handle"] - s["bets"]
    return sharp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="slate date (ET), default today")
    ap.add_argument("--no-splits", action="store_true")
    ap.add_argument("--offline", action="store_true", help="use cached data only (no ESPN/DK calls)")
    a = ap.parse_args()
    logs: list[str] = []
    log = lambda m: (logs.append(m), print(m))  # noqa: E731
    now = datetime.now(ET)
    # before 6am ET the slate is still yesterday's (late games finish after midnight)
    today = date.fromisoformat(a.date) if a.date else (now - timedelta(hours=6)).date()
    season = season_for(today)
    fetch = espn.fetch_scoreboard

    # 1-2. data + ratings
    prev = load_games(season - 1)
    prior = fit(prev).to_prior(DEFAULTS["regress"])
    games = load_games(season, refresh=not a.offline)
    games = games[games.date.dt.date < today]
    if not a.offline:
        games = topup_from_espn(games, season, today, fetch, log)
    if games.pace.isna().any():
        r0 = fit(games.dropna(subset=["pace"]), prior=prior)
        miss = games.pace.isna()
        games.loc[miss, "pace"] = [r0.predict(h, w, n, c)["pace"] for h, w, n, c in
                                   zip(games.h_team_id[miss], games.a_team_id[miss], games.neutral[miss], games.conf[miss])]
    r = fit(games, asof=pd.Timestamp(today), prior=prior)
    tbl = r.table()
    tbl = tbl[tbl.team_id.isin(prior.keys()) | (tbl.games >= 3)]
    _save("ratings.json", {"updated": now.isoformat(), "through": str(games.date.max().date()),
                           "teams": tbl.head(400).to_dict("records")})
    log(f"Fit on {len(games)} games through {games.date.max().date()}")

    # 3. today's slate
    all_picks = _load(OUT / "picks.json", [])
    by_id = {p["id"]: p for p in all_picks}
    slates = {}
    if not a.offline:
        pending_dates = {p["date"] for p in all_picks + _load(OUT / "sharp_picks.json", []) if not p.get("result")}
        for d in sorted(pending_dates | {str(today), str(today - timedelta(days=1))}):
            try:
                slates[d] = espn.parse_scoreboard(fetch(d.replace("-", "")))
            except Exception as e:
                log(f"ESPN {d}: {e}")
    # first DK line we saw for each game (fallback when ESPN has no opening line)
    # and the last pregame DK line (ESPN drops odds once a game tips, so keep it for the board)
    first_seen = _load(OUT / "lines.json", {})
    board = []
    for g in slates.get(str(today), []):
        o = g.get("odds")
        rec = first_seen.get(g["id"])
        if o and g["state"] == "pre":
            if not rec:
                rec = first_seen[g["id"]] = {k: o.get(k) for k in ("home_spread", "total", "home_ml", "away_ml")}
                rec["date"] = str(today)
            rec["last"] = o
        if not g.get("open") and rec:
            g["open"] = {k: rec.get(k) for k in ("home_spread", "total", "home_ml", "away_ml")}
        if not o and rec and rec.get("last") and g["state"] != "pre":
            g["odds"], g["odds_closed"] = rec["last"], True
        g["eval"] = pk.evaluate(g, r)
        board.append(g)
    cutoff = str(today - timedelta(days=7))
    _save("lines.json", {k: v for k, v in first_seen.items() if v.get("date", "9") >= cutoff})

    # splits first, so sharp money can veto picks
    sharp, dk_games = {}, []
    if not a.no_splits and not a.offline:
        try:
            dk_games = sp.fetch_all("today")
            sharp = match_splits(dk_games, board)
            _save("splits.json", {"updated": now.isoformat(), "error": None, "games": dk_games})
            log(f"Splits: {len(dk_games)} games, {len(sp.flag(dk_games))} sides with handle >= bets + 30")
        except Exception as e:
            old = _load(OUT / "splits.json", {"games": []})
            old.update(error=f"{type(e).__name__}: {e}", error_at=now.isoformat())
            _save("splits.json", old)
            log(f"Splits failed: {e}")

    # track every Sharp Money side (handle >= bets + 30) as its own record
    sharp_sides = _load(OUT / "sharp_picks.json", [])
    ss_ids = {x["id"] for x in sharp_sides}
    board_by_id = {g["id"]: g for g in board}
    for dg in (dk_games if sharp else []):
        bg = board_by_id.get(dg.get("espn_id"))
        if not bg or bg["state"] != "pre":
            continue
        for mkt, sides in dg["markets"].items():
            for s in sides:
                diff = s["handle"] - s["bets"]
                if diff < pk.RULES["sharp_agree"] or s.get("odds") is None:
                    continue
                lab = s["label"].lower()
                if mkt == "total":
                    side = "over" if lab.startswith("over") else "under"
                else:
                    side = "home" if _side_is_home(s["label"], bg) else "away"
                sid = f'{bg["id"]}-{mkt}-{side}'
                if sid in ss_ids:
                    continue
                mline = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", s["label"])
                line = None if mkt == "moneyline" else (float(mline.group(1)) if mline else None)
                if mkt != "moneyline" and line is None:
                    continue
                sharp_sides.append({"id": sid, "game_id": bg["id"], "date": str(today), "tip": bg["date"],
                                    "matchup": f'{bg["away"]["short"]} @ {bg["home"]["short"]}', "market": mkt,
                                    "side": side, "line": line, "odds": s["odds"], "label": s["label"],
                                    "handle": s["handle"], "bets": s["bets"], "diff": diff, "made_at": now.isoformat(),
                                    "model_agrees": s.get("model_agrees")})
                ss_ids.add(sid)

    for g in board:
        e = g["eval"]
        g["sharp"] = sharp.get(g["id"], {})
        g["passes"] = []
        if g["state"] == "pre" and g.get("odds"):
            new_picks, passes = pk.choose(g, e)
            g["passes"] = [ps for ps in passes if f'{g["id"]}-{ps["market"]}' not in by_id]
            for p in new_picks:
                pid = f'{g["id"]}-{p["market"]}'
                if pid not in by_id:  # picks lock the first time they're made
                    p.update(id=pid, game_id=g["id"], date=str(today), made_at=now.isoformat(), tip=g["date"],
                             matchup=f'{g["away"]["short"]} @ {g["home"]["short"]}', model_version=MODEL_VERSION,
                             model_line=e["model"]["home_spread"] if p["market"] == "spread" else (
                                 e["model"]["total"] if p["market"] == "total" else (
                                     e["model"]["home_win"] if p["side"] == "home" else round(1 - e["model"]["home_win"], 3))))
                    by_id[pid] = p
                    log(f"New pick: {p['matchup']} {p['label']} ({p['odds']:+g}) EV {p['ev']:.1%}")
            for ps in g["passes"]:
                log(f"Pass: {g['away']['short']} @ {g['home']['short']} {ps['label']}: {ps['reason']}")
        g["picks"] = [pid for pid in by_id if pid.startswith(g["id"] + "-")]

    # 4. closing lines + grading
    games_by_id = {g["id"]: g for sl in slates.values() for g in sl}
    for p in by_id.values():
        g = games_by_id.get(p["game_id"])
        if not g:
            continue
        if g["state"] == "pre":
            ln, od = pk.line_now(g, p)
            if od is not None:
                p["close_line"], p["close_odds"] = ln, od
        if g["completed"] and not p.get("result"):
            pk.grade(p, g)
    for p in sharp_sides:
        g = games_by_id.get(p["game_id"])
        if not g:
            continue
        if g["state"] == "pre":
            ln, od = pk.line_now(g, p)
            if od is not None:
                p["close_line"], p["close_odds"] = ln, od
        if g["completed"] and not p.get("result"):
            pk.grade(p, g)
    sharp_sides.sort(key=lambda x: (x["date"], x.get("tip", "")), reverse=True)
    _save("sharp_picks.json", sharp_sides)
    all_picks = sorted(by_id.values(), key=lambda x: (x["date"], x.get("tip", "")), reverse=True)
    _save("picks.json", all_picks)
    _save("record.json", pk.record(all_picks, sharp_sides))
    board.sort(key=lambda g: g["date"])
    _save("board.json", {"date": str(today), "updated": now.isoformat(), "games": board})

    _save("meta.json", {"updated": now.isoformat(), "slate": str(today), "model_version": MODEL_VERSION,
                        "sharp_markets": list(pk.SHARP_MARKETS),
                        "games_fit": int(len(games)), "data_through": str(games.date.max().date()),
                        "hca_pts": {"conference": round(r.hca * 72 * 0.9, 1), "non_conference": round(r.hca_nc * 72 * 0.9, 1)}, "league_total": round(r.league_total, 1),
                        "rules": pk.RULES, "market_weight": pk.MARKET_WEIGHT, "log": logs[-30:]})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
