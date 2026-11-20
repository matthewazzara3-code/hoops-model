"""Opponent adjusted efficiency and tempo ratings (KenPom style, multiplicative).

Efficiency: log(points per 100 poss) = c + hca*home + off[team] + def[opponent]
Tempo:      log(possessions)          = t0 + tempo[home] + tempo[away]

Both are weighted ridge regressions that shrink toward a prior (last season's
ratings, regressed toward average). Recent games weigh more (half life in days).
Predicted points = exp(efficiency) * exp(tempo) / 100 for each side.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import norm

DEFAULTS = dict(half_life=120.0, lam_eff=2.0, lam_tempo=4.0, regress=0.8, new_team_off=-0.20, new_team_def=0.12,
                hca_prior=0.045, lam_hca=300.0)
MIN_GAMES_D1 = 10
# backtested on 2025-26 walk forward: raw margins and totals are too spread out
MARGIN_SHRINK = 0.90
TOTAL_SHRINK = 0.80  # teams with fewer games in a full season are treated as non-D1


@dataclass
class Ratings:
    teams: dict[str, int]
    names: dict[str, str]
    c: float
    hca: float  # conference games
    off: np.ndarray
    dfn: np.ndarray
    t0: float
    tempo: np.ndarray
    games: dict[str, int] = field(default_factory=dict)
    total_bias: float = 0.0
    hca_nc: float = 0.045  # non-conference games
    league_total: float = 142.0

    # ---- prediction -------------------------------------------------
    def _ix(self, tid: str):
        return self.teams.get(str(tid))

    def sigmas(self, home: str, away: str) -> tuple[float, float]:
        """Backtested error SD of margin and total; wider when either team has few games."""
        mg = min(self.games.get(str(home), 0), self.games.get(str(away), 0))
        return 11.0 + 4.0 * np.exp(-mg / 6), 17.0 + 3.0 * np.exp(-mg / 3)

    def known(self, tid: str) -> bool:
        return self._ix(tid) is not None

    def predict(self, home: str, away: str, neutral: bool = False, conf: bool = False) -> dict:
        h, a = self._ix(home), self._ix(away)
        oh = self.off[h] if h is not None else DEFAULTS["new_team_off"]
        dh = self.dfn[h] if h is not None else DEFAULTS["new_team_def"]
        th = self.tempo[h] if h is not None else 0.0
        oa = self.off[a] if a is not None else DEFAULTS["new_team_off"]
        da = self.dfn[a] if a is not None else DEFAULTS["new_team_def"]
        ta = self.tempo[a] if a is not None else 0.0
        pace = float(np.exp(self.t0 + th + ta))
        hca = 0.0 if neutral else (self.hca if conf else self.hca_nc)
        eff_h = np.exp(self.c + hca + oh + da)
        eff_a = np.exp(self.c + oa + dh)
        ph, pa = eff_h * pace / 100 + self.total_bias / 2, eff_a * pace / 100 + self.total_bias / 2
        margin = (ph - pa) * MARGIN_SHRINK
        total = self.league_total + TOTAL_SHRINK * (ph + pa - self.league_total)
        ph, pa = (total + margin) / 2, (total - margin) / 2
        sm, st = self.sigmas(home, away)
        return dict(home_pts=round(ph, 1), away_pts=round(pa, 1), margin=round(margin, 2),
                    total=round(ph + pa, 2), pace=round(pace, 1), sigma_margin=round(sm, 2),
                    sigma_total=round(st, 2), home_win=float(norm.cdf(margin / sm)))


    # ---- tables -----------------------------------------------------
    def table(self) -> pd.DataFrame:
        inv = {i: t for t, i in self.teams.items()}
        rows = []
        avg_pace = float(np.exp(self.t0 + 0))  # per-side tempo baseline
        for i in range(len(self.off)):
            tid = inv[i]
            adj_o = float(np.exp(self.c + self.off[i]))
            adj_d = float(np.exp(self.c + self.dfn[i]))
            rows.append(dict(team_id=tid, team=self.names.get(tid, tid), games=self.games.get(tid, 0),
                             adj_o=round(adj_o, 1), adj_d=round(adj_d, 1), adj_em=round(adj_o - adj_d, 1),
                             adj_t=round(float(np.exp(self.t0 + 2 * self.tempo[i])), 1)))
        return pd.DataFrame(rows).sort_values("adj_em", ascending=False).reset_index(drop=True)

    def to_prior(self, regress: float) -> dict:
        """Regressed ratings for D1 teams only; everyone else falls back to the non-D1 default."""
        inv = {i: t for t, i in self.teams.items()}
        return {inv[i]: (regress * self.off[i], regress * self.dfn[i], regress * self.tempo[i])
                for i in range(len(self.off)) if self.games.get(inv[i], 0) >= MIN_GAMES_D1}


def _ridge(X, y, w, prior, lam_vec):
    """argmin sum w (y - Xb)^2 + sum lam (b - prior)^2"""
    W = sparse.diags(w)
    A = (X.T @ W @ X).toarray() + np.diag(lam_vec)
    r = y - X @ prior
    delta = np.linalg.solve(A, X.T @ (w * r))
    return prior + delta


def fit(games: pd.DataFrame, asof=None, prior: dict | None = None, params: dict | None = None) -> Ratings:
    """Fit on games strictly before `asof` (all games if None)."""
    p = {**DEFAULTS, **(params or {})}
    g = games if asof is None else games[games.date < pd.Timestamp(asof)]
    prior = prior or {}
    ids = sorted(set(g.h_team_id) | set(g.a_team_id) | set(prior))
    teams = {t: i for i, t in enumerate(ids)}
    names = {}
    for col_id, col_n in (("h_team_id", "h_name"), ("a_team_id", "a_name")):
        names.update(dict(zip(g[col_id], g[col_n])))
    n, m = len(ids), len(g)
    ref = (g.date.max() if asof is None else pd.Timestamp(asof)) if m else pd.Timestamp.now()
    age = (ref - g.date).dt.days.to_numpy(float)
    w = 0.5 ** (age / p["half_life"])
    hi = g.h_team_id.map(teams).to_numpy()
    ai = g.a_team_id.map(teams).to_numpy()
    home_flag = (~g.neutral.to_numpy()).astype(float)
    conf = g["conf"].to_numpy().astype(bool) if "conf" in g else np.zeros(m, bool)

    # efficiency design: params [c, hca_conf, off(n), def(n), hca_nonconf]
    k = 3 + 2 * n
    rows = np.arange(2 * m)
    ones = np.ones(2 * m)
    hca_col = np.where(conf, 1, k - 1)
    r_idx = np.concatenate([rows, rows[:m], rows, rows])
    c_idx = np.concatenate([np.zeros(2 * m, int), hca_col,
                            2 + np.concatenate([hi, ai]), 2 + n + np.concatenate([ai, hi])])
    vals = np.concatenate([ones, home_flag, ones, ones])
    X = sparse.csr_matrix((vals, (r_idx, c_idx)), shape=(2 * m, k))
    y = np.log(np.concatenate([g.h_pts.to_numpy(), g.a_pts.to_numpy()]) / np.concatenate([g.pace, g.pace]) * 100)
    pri = np.zeros(k)
    pri[0], pri[1], pri[k - 1] = np.log(104.0), p["hca_prior"], p["hca_prior"]
    pri[2:2 + n], pri[2 + n:k - 1] = p["new_team_off"], p["new_team_def"]
    for t, (o, d, _) in prior.items():
        pri[2 + teams[t]], pri[2 + n + teams[t]] = o, d
    lam = np.full(k, p["lam_eff"])
    lam[0], lam[1], lam[k - 1] = 1e-4, p["lam_hca"], p["lam_hca"]
    b = _ridge(X, y, np.concatenate([w, w]), pri, lam) if m else pri
    off, dfn = b[2:2 + n], b[2 + n:k - 1]
    cnt = pd.concat([g.h_team_id, g.a_team_id]).value_counts().to_dict()
    # core = D1: teams in the prior, or (no prior yet) teams with enough games this season
    core = np.array([(t in prior) if prior else cnt.get(t, 0) >= MIN_GAMES_D1 for t in ids])
    if not core.any():
        core[:] = True
    # identify: center D1 off and def on 0, push the mean into c
    mo, md = off[core].mean(), dfn[core].mean()
    c = b[0] + mo + md
    off, dfn = off - mo, dfn - md

    # tempo design: [t0, tempo(n)]
    kt = 1 + n
    r2 = np.arange(m)
    Xt = sparse.csr_matrix((np.ones(3 * m), (np.concatenate([r2, r2, r2]),
                                             np.concatenate([np.zeros(m, int), 1 + hi, 1 + ai]))), shape=(m, kt))
    yt = np.log(g.pace.to_numpy())
    prt = np.zeros(kt)
    prt[0] = np.log(68.0)
    for t, (_, _, tp) in prior.items():
        prt[1 + teams[t]] = tp
    lamt = np.full(kt, p["lam_tempo"])
    lamt[0] = 1e-4
    bt = _ridge(Xt, yt, w, prt, lamt) if m else prt
    tempo = bt[1:]
    mt = tempo[core].mean()
    t0, tempo = bt[0] + 2 * mt, tempo - mt

    r = Ratings(teams, names, float(c), float(b[1]), off, dfn, float(t0), tempo, cnt)
    r.hca_nc = float(b[k - 1])
    if m:  # in sample weighted bias of totals (log model underpredicts the mean slightly)
        pace_hat = np.exp(t0 + tempo[hi] + tempo[ai])
        tot_hat = (np.exp(r.c + np.where(conf, r.hca, r.hca_nc) * home_flag + off[hi] + dfn[ai]) + np.exp(r.c + off[ai] + dfn[hi])) * pace_hat / 100
        both = core[hi] & core[ai]
        if both.sum() > 20:
            r.total_bias = float(np.average(((g.h_pts + g.a_pts).to_numpy() - tot_hat)[both], weights=w[both]))
            r.league_total = float(np.average((g.h_pts + g.a_pts).to_numpy()[both], weights=w[both]))
    return r


def p_cover(margin: float, home_line: float, sigma: float) -> float:
    """P(home covers home_line), e.g. home_line -5.5 means home must win by 6+."""
    return float(norm.cdf((margin + home_line) / sigma))


def p_over(total: float, line: float, sigma: float) -> float:
    return float(norm.cdf((total - line) / sigma))
