"""Walk forward backtest: each day, fit only on games before that day, predict that day.

python -m model.backtest --season 2026
"""
from __future__ import annotations

import argparse
import itertools

import numpy as np
import pandas as pd

from .data import load_games
from .ratings import DEFAULTS, fit


def walk_forward(season: int, params: dict | None = None, every: int = 1) -> pd.DataFrame:
    prev = load_games(season - 1)
    prior_r = fit(prev, params=params)
    prior = prior_r.to_prior({**DEFAULTS, **(params or {})}["regress"])
    g = load_games(season)
    out = []
    dates = sorted(g.date.unique())
    for i, d in enumerate(dates):
        if i % every:
            continue
        r = fit(g, asof=d, prior=prior, params=params)
        day = g[g.date == d]
        for row in day.itertuples():
            p = r.predict(row.h_team_id, row.a_team_id, row.neutral, row.conf)
            out.append(dict(game_id=row.game_id, date=d, pred_margin=p["margin"], pred_total=p["total"],
                            home_win_p=p["home_win"], margin=row.h_pts - row.a_pts, total=row.h_pts + row.a_pts,
                            h_games=r.games.get(row.h_team_id, 0), a_games=r.games.get(row.a_team_id, 0),
                            d1=(row.h_team_id in prior) and (row.a_team_id in prior)))
    return pd.DataFrame(out)


def summarize(bt: pd.DataFrame) -> dict:
    bt = bt[bt.d1]
    e_m = bt.margin - bt.pred_margin
    e_t = bt.total - bt.pred_total
    won = (bt.margin > 0).astype(float)
    return dict(games=len(bt), mae_margin=round(e_m.abs().mean(), 2), mae_total=round(e_t.abs().mean(), 2),
                bias_total=round(e_t.mean(), 2), sd_margin=round(e_m.std(), 2), sd_total=round(e_t.std(), 2),
                su_acc=round(((bt.pred_margin > 0) == (bt.margin > 0)).mean(), 3),
                brier=round(((bt.home_win_p - won) ** 2).mean(), 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    if a.grid:
        for hl, lam, reg in itertools.product([60, 120, 240], [0.5, 1, 2, 4], [0.7, 0.85]):
            prm = dict(half_life=hl, lam_eff=lam, lam_tempo=lam, regress=reg)
            s = summarize(walk_forward(a.season, prm, every=3))
            print(prm, s, flush=True)
    else:
        bt = walk_forward(a.season)
        print(summarize(bt))
        bt["month"] = bt.date.dt.to_period("M")
        print(bt.groupby("month").apply(summarize, include_groups=False).apply(pd.Series))


if __name__ == "__main__":
    main()
