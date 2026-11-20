"""ESPN scoreboard: schedule, live scores, results and DraftKings lines.

DraftKings has been ESPN's exclusive odds provider since Dec 1, 2025, so the odds
block here is DK's line. The provider name is kept so the site can show it.
"""
from __future__ import annotations

import json
import re
import urllib.request

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
              "?dates={d}&groups=50&limit=500")


def fetch_scoreboard(yyyymmdd: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(SCOREBOARD.format(d=yyyymmdd), headers={"User-Agent": "Mozilla/5.0 cbb-model"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _num(x):
    """'-110' / '+240' / 'o145.5' / 'EVEN' / 'OFF' / 145.5 -> float or None"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().lower().replace("−", "-")
    if s in ("even", "ev", "pk", "pick"):
        return 0.0 if s in ("pk", "pick") else 100.0
    s = re.sub(r"^[ou]", "", s)
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


def _dig(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_odds(o: dict | None, home_abbr: str, away_abbr: str) -> dict | None:
    """Normalize one ESPN odds entry. home_spread is the home team's line (negative = home favored)."""
    if not o:
        return None
    out = {"provider": _dig(o, "provider", "name") or "", "home_spread": None, "home_spread_odds": None,
           "away_spread_odds": None, "total": None, "over_odds": None, "under_odds": None,
           "home_ml": None, "away_ml": None}
    # newer nested format (current/close, then open)
    for stage in ("close", "current", "open"):
        if out["home_spread"] is None:
            out["home_spread"] = _num(_dig(o, "pointSpread", "home", stage, "line"))
            out["home_spread_odds"] = _num(_dig(o, "pointSpread", "home", stage, "odds"))
            out["away_spread_odds"] = _num(_dig(o, "pointSpread", "away", stage, "odds"))
        if out["total"] is None:
            out["total"] = _num(_dig(o, "total", "over", stage, "line"))
            out["over_odds"] = _num(_dig(o, "total", "over", stage, "odds"))
            out["under_odds"] = _num(_dig(o, "total", "under", stage, "odds"))
        if out["home_ml"] is None:
            out["home_ml"] = _num(_dig(o, "moneyline", "home", stage, "odds"))
            out["away_ml"] = _num(_dig(o, "moneyline", "away", stage, "odds"))
    # flat format fallbacks
    if out["total"] is None:
        out["total"] = _num(o.get("overUnder"))
    if out["over_odds"] is None:
        out["over_odds"] = _num(o.get("overOdds"))
        out["under_odds"] = _num(o.get("underOdds"))
    if out["home_ml"] is None:
        out["home_ml"] = _num(_dig(o, "homeTeamOdds", "moneyLine"))
        out["away_ml"] = _num(_dig(o, "awayTeamOdds", "moneyLine"))
    if out["home_spread_odds"] is None:
        out["home_spread_odds"] = _num(_dig(o, "homeTeamOdds", "spreadOdds"))
        out["away_spread_odds"] = _num(_dig(o, "awayTeamOdds", "spreadOdds"))
    if out["home_spread"] is None:
        det = (o.get("details") or "").strip()
        m = re.match(r"^(\S+)\s+([+-]?\d+(?:\.\d+)?)$", det)
        if det.upper() in ("EVEN", "PK", "PICK"):
            out["home_spread"] = 0.0
        elif m:
            fav, line = m.group(1).upper(), abs(float(m.group(2)))
            if fav == home_abbr.upper():
                out["home_spread"] = -line
            elif fav == away_abbr.upper():
                out["home_spread"] = line
        if out["home_spread"] is None and o.get("spread") is not None:
            sp = _num(o.get("spread"))
            fav_home = _dig(o, "homeTeamOdds", "favorite")
            if sp is not None and fav_home is not None:
                out["home_spread"] = -abs(sp) if fav_home else abs(sp)
    for k in ("home_spread_odds", "away_spread_odds", "over_odds", "under_odds"):
        if out[k] is None or out[k] == 0:
            out[k] = -110.0
    if all(out[k] is None for k in ("home_spread", "total", "home_ml")):
        return None
    return out


def parse_scoreboard(sb: dict) -> list[dict]:
    games = []
    for e in sb.get("events", []):
        c = (e.get("competitions") or [{}])[0]
        comp = {x.get("homeAway"): x for x in c.get("competitors", [])}
        if "home" not in comp or "away" not in comp:
            continue
        st = c.get("status") or e.get("status") or {}
        side = {}
        for k in ("home", "away"):
            t = comp[k].get("team", {})
            rank = _dig(comp[k], "curatedRank", "current")
            side[k] = {"id": str(t.get("id", "")), "name": t.get("displayName", ""),
                       "short": t.get("shortDisplayName") or t.get("location", ""),
                       "abbr": t.get("abbreviation", ""), "logo": t.get("logo", ""),
                       "score": int(comp[k]["score"]) if str(comp[k].get("score", "")).isdigit() else None,
                       "rank": rank if rank and rank < 26 else None}
        odds_list = c.get("odds") or []
        dk = next((o for o in odds_list if "draft" in (_dig(o, "provider", "name") or "").lower()), None)
        o = parse_odds(dk or (odds_list[0] if odds_list else None), side["home"]["abbr"], side["away"]["abbr"])
        games.append({
            "id": str(e.get("id")), "date": e.get("date"), "neutral": bool(c.get("neutralSite")),
            "conf": bool(c.get("conferenceCompetition")),
            "state": _dig(st, "type", "state") or "pre", "detail": _dig(st, "type", "shortDetail") or "",
            "completed": bool(_dig(st, "type", "completed")), "home": side["home"], "away": side["away"],
            "odds": o, "tv": ", ".join(b.get("names", [""])[0] for b in c.get("broadcasts", []) if b.get("names")),
        })
    return games
