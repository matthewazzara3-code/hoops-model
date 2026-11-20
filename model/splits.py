"""DraftKings betting splits (DK Network public page): % of handle vs % of bets.

Flags sides where the money share beats the ticket share by a threshold: few
bettors putting down a lot of money ("sharp" or big money side).
Default rule: handle% minus bets% >= 30 percentage points.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request

URL = "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/"
NCAAB_EVENT_GROUP = 92483  # DraftKings event group id for college basketball (NCAAB)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MARKETS = {"moneyline": "moneyline", "spread": "spread", "total": "total"}
MAX_PAGES = 25


class SplitsError(Exception):
    pass


def _txt(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s))
    return re.sub(r"\s+", " ", s).strip()


def _odds(s: str) -> int | None:
    s = _txt(s).replace("−", "-").replace("+", "")
    try:
        return int(s)
    except ValueError:
        return None


def parse_page(page: str) -> list[dict]:
    games = []
    for block in page.split('<div class="tb-se ')[1:]:
        t = re.search(r'event/(\d+)[^"]*"[^>]*>(.*?)</a>', block, re.S)
        if not t:
            continue
        title = _txt(t.group(2))
        sep = " @ " if " @ " in title else (" vs " if " vs " in title else None)
        if not sep:
            continue
        away, home = (x.strip() for x in title.split(sep, 1))
        when = re.search(r"</h5>\s*<span[^>]*>(.*?)</span>", block, re.S)
        # column order from the header (Handle then Bets today; read it, don't assume)
        head = re.search(r"tb-se-head(.*?)tb-market-wrap", block, re.S)
        handle_first = True
        if head:
            h = head.group(1).lower()
            if "handle" in h and "bets" in h:
                handle_first = h.index("handle") < h.index("bets")
        g = {"event_id": t.group(1), "away": away, "home": home,
             "when": _txt(when.group(1)) if when else "", "markets": {}}
        heads = list(re.finditer(r"<div[^>]*>\s*(Moneyline|Spread|Total)\s*</div>", block, re.I))
        for i, mh in enumerate(heads):
            key = MARKETS[mh.group(1).lower()]
            chunk = block[mh.end(): heads[i + 1].start() if i + 1 < len(heads) else len(block)]
            sides = []
            for row in chunk.split("tb-sodd")[1:]:
                lab = re.search(r'tb-slipline[^>]*>(.*?)</div>', row, re.S)
                odd = re.search(r'tb-odd-s[^>]*>(.*?)</a>', row, re.S)
                if not lab or not odd:
                    continue
                pcts = re.findall(r">\s*(\d{1,3})%", row[odd.end():])
                if len(pcts) < 2:
                    continue
                a, b = int(pcts[0]), int(pcts[1])
                handle, bets = (a, b) if handle_first else (b, a)
                sides.append({"label": _txt(lab.group(1)), "odds": _odds(odd.group(1)),
                              "handle": handle, "bets": bets})
            if sides:
                g["markets"][key] = sides
        games.append(g)
    return games


def fetch_page(page: int, edate: str = "today", group: int = NCAAB_EVENT_GROUP, timeout: int = 30) -> str:
    q = f"?tb_eg={group}&tb_edate={edate}&tb_emt=0&tb_page={page}"
    req = urllib.request.Request(URL + q, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise SplitsError(f"HTTP {e.code}") from e
    except Exception as e:
        raise SplitsError(f"{type(e).__name__}: {e}") from e
    if "tb_eg" not in body and "tb-se" not in body:
        raise SplitsError("not the splits page (blocked or layout changed)")
    return body


def fetch_all(edate: str = "today", fetch=None) -> list[dict]:
    fetch = fetch or fetch_page
    seen, out = set(), []
    for page in range(1, MAX_PAGES + 1):
        got = parse_page(fetch(page, edate))
        if got and not any(g["markets"] for g in got):
            raise SplitsError("games listed but no percentages read (layout changed?)")
        new = [g for g in got if g["event_id"] not in seen]
        if not new:
            break
        for g in new:
            seen.add(g["event_id"])
        out += new
        if len(got) < 10:
            break
    return out


def flag(games: list[dict], min_diff: float = 30.0, mode: str = "points") -> list[dict]:
    """Sides where handle beats bets by min_diff.
    mode='points': handle - bets >= min_diff (e.g. 65% handle vs 35% bets)
    mode='relative': handle >= bets * (1 + min_diff/100)"""
    out = []
    for g in games:
        for mkt, sides in g["markets"].items():
            for s in sides:
                diff = s["handle"] - s["bets"]
                hit = diff >= min_diff if mode == "points" else (
                    s["bets"] > 0 and s["handle"] >= s["bets"] * (1 + min_diff / 100))
                if hit:
                    out.append({"event_id": g["event_id"], "game": f'{g["away"]} @ {g["home"]}', "when": g["when"],
                                "market": mkt, **s, "diff": diff})
    return sorted(out, key=lambda x: -x["diff"])
