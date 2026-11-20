"""Compare model lines to DraftKings, choose picks, grade them, build the record."""
from __future__ import annotations

from collections import defaultdict

from scipy.stats import norm

from .ratings import Ratings, p_cover, p_over

# Pick rules. CBB lines are sharp, so the model's number is blended with DraftKings'
# before computing probabilities and EV: the DK line already carries injuries, lineups
# and sharp money. min_edge is the raw model vs DK gap; min_ev is on the blended number.
MARKET_WEIGHT = 0.70  # share of the DraftKings number in the blend (0 = model only)
RULES = {
    "spread": {"min_edge": 3.0, "min_ev": 0.025},
    "total": {"min_edge": 4.5, "min_ev": 0.02},
    "moneyline": {"min_ev": 0.03, "min_odds": -250, "max_odds": 250},
    "min_games": 0,  # require each team to have played this many games (0 = trust the prior)
    # skip a pick if DK's line has moved against it since it opened by at least this much
    "max_move_against": {"spread": 3.5, "total": 3.0, "moneyline": 0.06},
    # handle% - bets% on the pick's side at least this big: "Sharp ✓" badge
    "sharp_agree": 30,
    # handle% - bets% on the OTHER side at least this big: skip the pick
    "sharp_opposed": 15,
}


def payout(odds: float) -> float:
    return odds / 100 if odds > 0 else 100 / -odds


def implied(odds: float) -> float:
    return 1 / (1 + payout(odds))


def fair_odds(p: float) -> int:
    p = min(max(p, 0.001), 0.999)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def ev(p: float, odds: float) -> float:
    return p * payout(odds) - (1 - p)


def half(x: float) -> float:
    return round(x * 2) / 2


def evaluate(game: dict, r: Ratings, w: float = MARKET_WEIGHT) -> dict:
    """Model numbers, the model/DK blend, and edge/EV for every DK market on one game."""
    h, a = game["home"], game["away"]
    p = r.predict(h["id"], a["id"], game["neutral"], game.get("conf", False))
    out = {"model": {"home_pts": p["home_pts"], "away_pts": p["away_pts"], "margin": p["margin"],
                     "total": p["total"], "pace": p["pace"], "home_spread": half(-p["margin"]),
                     "home_win": round(p["home_win"], 3), "home_ml": fair_odds(p["home_win"]),
                     "away_ml": fair_odds(1 - p["home_win"])},
           "known": r.known(h["id"]) and r.known(a["id"]),
           "games_played": [r.games.get(h["id"], 0), r.games.get(a["id"], 0)], "markets": {}, "blend": {}}
    o = game.get("odds")
    if not o:
        return out
    sm, st = p["sigma_margin"], p["sigma_total"]
    margin, total = p["margin"], p["total"]
    if o["home_spread"] is not None:
        margin = w * -o["home_spread"] + (1 - w) * p["margin"]
    elif o["home_ml"] is not None and o["away_ml"] is not None and o["home_ml"] != 0:
        ih, ia = implied(o["home_ml"]), implied(o["away_ml"])
        margin = w * float(norm.ppf(min(max(ih / (ih + ia), 0.01), 0.99))) * sm + (1 - w) * p["margin"]
    if o["total"] is not None:
        total = w * o["total"] + (1 - w) * p["total"]
    win = float(norm.cdf(margin / sm))
    out["blend"] = {"margin": round(margin, 2), "total": round(total, 2), "home_spread": half(-margin),
                    "home_win": round(win, 3), "weight": w}
    if o["home_spread"] is not None:
        ph = p_cover(margin, o["home_spread"], sm)
        out["markets"]["spread"] = {
            "edge": round(p["margin"] + o["home_spread"], 2),  # raw model gap, + favors home side
            "home": {"line": o["home_spread"], "odds": o["home_spread_odds"], "p": round(ph, 3),
                     "ev": round(ev(ph, o["home_spread_odds"]), 3)},
            "away": {"line": -o["home_spread"], "odds": o["away_spread_odds"], "p": round(1 - ph, 3),
                     "ev": round(ev(1 - ph, o["away_spread_odds"]), 3)}}
    if o["total"] is not None:
        po = p_over(total, o["total"], st)
        out["markets"]["total"] = {
            "edge": round(p["total"] - o["total"], 2),  # raw model gap, + favors over
            "over": {"line": o["total"], "odds": o["over_odds"], "p": round(po, 3), "ev": round(ev(po, o["over_odds"]), 3)},
            "under": {"line": o["total"], "odds": o["under_odds"], "p": round(1 - po, 3),
                      "ev": round(ev(1 - po, o["under_odds"]), 3)}}
    if o["home_ml"] is not None and o["away_ml"] is not None and o["home_ml"] != 0:
        ih, ia = implied(o["home_ml"]), implied(o["away_ml"])
        out["markets"]["moneyline"] = {
            "edge": round(p["home_win"] - ih / (ih + ia), 3),  # raw model win prob minus DK no-vig, home side
            "home": {"odds": o["home_ml"], "p": round(win, 3), "ev": round(ev(win, o["home_ml"]), 3)},
            "away": {"odds": o["away_ml"], "p": round(1 - win, 3), "ev": round(ev(1 - win, o["away_ml"]), 3)}}
    return out


def move_against(game: dict, market: str, side: str) -> float | None:
    """How far DK's line has moved against `side` since it opened (points, or win prob for ML)."""
    o = game.get("odds") or {}
    op = game.get("open") or {}
    if market == "spread" and o.get("home_spread") is not None and op.get("home_spread") is not None:
        d = o["home_spread"] - op["home_spread"]  # home line got bigger = market moved toward away
        return d if side == "home" else -d
    if market == "total" and o.get("total") is not None and op.get("total") is not None:
        d = o["total"] - op["total"]
        return -d if side == "over" else d
    if market == "moneyline" and o.get("home_ml") and op.get("home_ml") and o.get("away_ml") and op.get("away_ml"):
        now = implied(o["home_ml"]) / (implied(o["home_ml"]) + implied(o["away_ml"]))
        then = implied(op["home_ml"]) / (implied(op["home_ml"]) + implied(op["away_ml"]))
        return (then - now) if side == "home" else (now - then)
    return None


def choose(game: dict, ev_: dict, rules: dict = RULES) -> tuple[list[dict], list[dict]]:
    """(picks, passes) for one game. Passes are model edges skipped by the sharp/line filters."""
    if not ev_["known"] or min(ev_["games_played"]) < rules["min_games"]:
        return [], []
    picks, passes = [], []
    m = ev_["markets"]
    sharp = game.get("sharp") or {}  # {market: {side: handle - bets}}
    names = {"home": game["home"]["short"] or game["home"]["name"], "away": game["away"]["short"] or game["away"]["name"]}
    bl = ev_.get("blend") or {}
    cands = []
    if "spread" in m:
        side = "home" if m["spread"]["edge"] > 0 else "away"
        s = m["spread"][side]
        if abs(m["spread"]["edge"]) >= rules["spread"]["min_edge"] and s["ev"] >= rules["spread"]["min_ev"]:
            cands.append({"market": "spread", "side": side, "line": s["line"], "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["spread"]["edge"]), "label": f'{names[side]} {s["line"]:+g}',
                          "blend_line": round(-bl["margin"] if side == "home" else bl["margin"], 1) if bl else None})
    if "total" in m:
        side = "over" if m["total"]["edge"] > 0 else "under"
        s = m["total"][side]
        if abs(m["total"]["edge"]) >= rules["total"]["min_edge"] and s["ev"] >= rules["total"]["min_ev"]:
            cands.append({"market": "total", "side": side, "line": s["line"], "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["total"]["edge"]), "label": f'{side.title()} {s["line"]:g}',
                          "blend_line": round(bl["total"], 1) if bl else None})
    if "moneyline" in m:
        best = max(("home", "away"), key=lambda k: m["moneyline"][k]["ev"])
        s = m["moneyline"][best]
        rl = rules["moneyline"]
        if s["ev"] >= rl["min_ev"] and rl["min_odds"] <= s["odds"] <= rl["max_odds"]:
            cands.append({"market": "moneyline", "side": best, "line": None, "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["moneyline"]["edge"]), "label": f'{names[best]} ML', "blend_line": s["p"]})
    other = {"home": "away", "away": "home", "over": "under", "under": "over"}
    for c in cands:
        mk, sd = c["market"], c["side"]
        mv = move_against(game, mk, sd)
        opp = sharp.get(mk, {}).get(other[sd])
        mine = sharp.get(mk, {}).get(sd)
        c["sharp"] = "agrees" if mine is not None and mine >= rules["sharp_agree"] else (
            "opposed" if opp is not None and opp >= rules["sharp_opposed"] else None)
        c["moved"] = round(mv, 3) if mv is not None else None
        if mv is not None and mv >= rules["max_move_against"][mk]:
            unit = "%" if mk == "moneyline" else " pts"
            amt = mv * 100 if mk == "moneyline" else mv
            passes.append({**c, "reason": f"DK line moved {amt:.1f}{unit} against it"})
        elif c["sharp"] == "opposed":
            passes.append({**c, "reason": f"Sharp money on the other side (handle beats bets by {opp})"})
        else:
            picks.append(c)
    return picks, passes


def line_now(game: dict, pick: dict) -> tuple[float | None, float | None]:
    """Current DK line/odds for the picked side (for closing line value)."""
    o = game.get("odds") or {}
    if pick["market"] == "spread" and o.get("home_spread") is not None:
        return ((o["home_spread"], o["home_spread_odds"]) if pick["side"] == "home"
                else (-o["home_spread"], o["away_spread_odds"]))
    if pick["market"] == "total" and o.get("total") is not None:
        return o["total"], o["over_odds"] if pick["side"] == "over" else o["under_odds"]
    if pick["market"] == "moneyline" and o.get("home_ml") is not None:
        return None, o["home_ml"] if pick["side"] == "home" else o["away_ml"]
    return None, None


def clv(pick: dict) -> float | None:
    """Closing line value: + means the line moved toward the pick after it was made."""
    if pick.get("close_odds") is None:
        return None
    if pick["market"] == "spread" and pick.get("close_line") is not None:
        return round(pick["line"] - pick["close_line"], 1)
    if pick["market"] == "total" and pick.get("close_line") is not None:
        d = pick["close_line"] - pick["line"]
        return round(d if pick["side"] == "over" else -d, 1)
    if pick["market"] == "moneyline":
        return round((implied(pick["close_odds"]) - implied(pick["odds"])) * 100, 1)  # prob points
    return None


def grade(pick: dict, game: dict) -> dict:
    hs, as_ = game["home"]["score"], game["away"]["score"]
    if hs is None or as_ is None:
        return pick
    if pick["market"] == "spread":
        margin = (hs - as_) if pick["side"] == "home" else (as_ - hs)
        x = margin + pick["line"]
    elif pick["market"] == "total":
        x = (hs + as_ - pick["line"]) * (1 if pick["side"] == "over" else -1)
    else:
        x = (hs - as_) if pick["side"] == "home" else (as_ - hs)
    res = "W" if x > 0 else ("P" if x == 0 else "L")
    pick.update(result=res, units=round(payout(pick["odds"]) if res == "W" else (0.0 if res == "P" else -1.0), 3),
                final=f'{game["away"]["abbr"]} {as_}, {game["home"]["abbr"]} {hs}', clv=clv(pick))
    return pick


def record(picks: list[dict], sharp_sides: list[dict] | None = None) -> dict:
    def agg(rows):
        w = sum(r["result"] == "W" for r in rows)
        l_ = sum(r["result"] == "L" for r in rows)
        p_ = sum(r["result"] == "P" for r in rows)
        u = sum(r["units"] for r in rows)
        risk = sum(1 for r in rows if r["result"] in "WL")
        c = [r["clv"] for r in rows if r.get("clv") is not None]
        return {"w": w, "l": l_, "p": p_, "units": round(u, 2), "roi": round(u / risk * 100, 1) if risk else 0.0,
                "win_pct": round(w / (w + l_) * 100, 1) if w + l_ else 0.0,
                "avg_clv": round(sum(c) / len(c), 2) if c else None, "clv_n": len(c)}
    graded = [p for p in picks if p.get("result")]
    by_m = defaultdict(list)
    for p in graded:
        by_m[p["market"]].append(p)
    daily = defaultdict(float)
    for p in sorted(graded, key=lambda x: x["date"]):
        daily[p["date"]] += p["units"]
    run, curve = 0.0, []
    for d in sorted(daily):
        run += daily[d]
        curve.append({"date": d, "units": round(run, 2), "day": round(daily[d], 2)})
    ss = [p for p in (sharp_sides or []) if p.get("result")]
    ss_m = defaultdict(list)
    for p in ss:
        ss_m[p["market"]].append(p)
    return {"overall": agg(graded), "by_market": {k: agg(v) for k, v in by_m.items()}, "curve": curve,
            "pending": sum(1 for p in picks if not p.get("result")),
            "model_sharp_agree": agg([p for p in graded if p.get("sharp") == "agrees"]),
            "sharp_sides": {"overall": agg(ss), "by_market": {k: agg(v) for k, v in ss_m.items()},
                            "pending": sum(1 for p in (sharp_sides or []) if not p.get("result"))}}
