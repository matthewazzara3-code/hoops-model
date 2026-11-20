"""Compare model lines to DraftKings, choose picks, grade them, build the record."""
from __future__ import annotations

from collections import defaultdict

from .ratings import Ratings, p_cover, p_over

# Pick rules. CBB lines are sharp: most "edges" are model error, so the bar is high.
RULES = {
    "spread": {"min_edge": 3.0, "min_ev": 0.05},       # model margin vs DK line, points
    "total": {"min_edge": 4.5, "min_ev": 0.05},        # model total vs DK total, points
    "moneyline": {"min_ev": 0.08, "min_odds": -250, "max_odds": 250},
    "min_games": 0,  # require each team to have played this many games (0 = trust the prior)
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


def evaluate(game: dict, r: Ratings) -> dict:
    """Model numbers plus edge/EV for every DK market on one game."""
    h, a = game["home"], game["away"]
    p = r.predict(h["id"], a["id"], game["neutral"], game.get("conf", False))
    out = {"model": {"home_pts": p["home_pts"], "away_pts": p["away_pts"], "margin": p["margin"],
                     "total": p["total"], "pace": p["pace"], "home_spread": half(-p["margin"]),
                     "home_win": round(p["home_win"], 3), "home_ml": fair_odds(p["home_win"]),
                     "away_ml": fair_odds(1 - p["home_win"])},
           "known": r.known(h["id"]) and r.known(a["id"]),
           "games_played": [r.games.get(h["id"], 0), r.games.get(a["id"], 0)], "markets": {}}
    o = game.get("odds")
    if not o:
        return out
    sm, st = p["sigma_margin"], p["sigma_total"]
    if o["home_spread"] is not None:
        ph = p_cover(p["margin"], o["home_spread"], sm)
        out["markets"]["spread"] = {
            "edge": round(p["margin"] + o["home_spread"], 2),  # + favors home side
            "home": {"line": o["home_spread"], "odds": o["home_spread_odds"], "p": round(ph, 3),
                     "ev": round(ev(ph, o["home_spread_odds"]), 3)},
            "away": {"line": -o["home_spread"], "odds": o["away_spread_odds"], "p": round(1 - ph, 3),
                     "ev": round(ev(1 - ph, o["away_spread_odds"]), 3)}}
    if o["total"] is not None:
        po = p_over(p["total"], o["total"], st)
        out["markets"]["total"] = {
            "edge": round(p["total"] - o["total"], 2),  # + favors over
            "over": {"line": o["total"], "odds": o["over_odds"], "p": round(po, 3), "ev": round(ev(po, o["over_odds"]), 3)},
            "under": {"line": o["total"], "odds": o["under_odds"], "p": round(1 - po, 3),
                      "ev": round(ev(1 - po, o["under_odds"]), 3)}}
    if o["home_ml"] is not None and o["away_ml"] is not None and o["home_ml"] != 0:
        hw = p["home_win"]
        ih, ia = implied(o["home_ml"]), implied(o["away_ml"])
        out["markets"]["moneyline"] = {
            "edge": round(hw - ih / (ih + ia), 3),  # model win prob minus DK no-vig prob, home side
            "home": {"odds": o["home_ml"], "p": round(hw, 3), "ev": round(ev(hw, o["home_ml"]), 3)},
            "away": {"odds": o["away_ml"], "p": round(1 - hw, 3), "ev": round(ev(1 - hw, o["away_ml"]), 3)}}
    return out


def choose(game: dict, ev_: dict, rules: dict = RULES) -> list[dict]:
    """Picks for one game (at most one per market)."""
    if not ev_["known"] or min(ev_["games_played"]) < rules["min_games"]:
        return []
    picks = []
    m = ev_["markets"]
    names = {"home": game["home"]["short"] or game["home"]["name"], "away": game["away"]["short"] or game["away"]["name"]}
    if "spread" in m:
        side = "home" if m["spread"]["edge"] > 0 else "away"
        s = m["spread"][side]
        if abs(m["spread"]["edge"]) >= rules["spread"]["min_edge"] and s["ev"] >= rules["spread"]["min_ev"]:
            picks.append({"market": "spread", "side": side, "line": s["line"], "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["spread"]["edge"]), "label": f'{names[side]} {s["line"]:+g}'})
    if "total" in m:
        side = "over" if m["total"]["edge"] > 0 else "under"
        s = m["total"][side]
        if abs(m["total"]["edge"]) >= rules["total"]["min_edge"] and s["ev"] >= rules["total"]["min_ev"]:
            picks.append({"market": "total", "side": side, "line": s["line"], "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["total"]["edge"]), "label": f'{side.title()} {s["line"]:g}'})
    if "moneyline" in m:
        best = max(("home", "away"), key=lambda k: m["moneyline"][k]["ev"])
        s = m["moneyline"][best]
        rl = rules["moneyline"]
        if s["ev"] >= rl["min_ev"] and rl["min_odds"] <= s["odds"] <= rl["max_odds"]:
            picks.append({"market": "moneyline", "side": best, "line": None, "odds": s["odds"], "p": s["p"], "ev": s["ev"],
                          "edge": abs(m["moneyline"]["edge"]), "label": f'{names[best]} ML'})
    return picks


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


def record(picks: list[dict]) -> dict:
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
    return {"overall": agg(graded), "by_market": {k: agg(v) for k, v in by_m.items()}, "curve": curve,
            "pending": sum(1 for p in picks if not p.get("result"))}
