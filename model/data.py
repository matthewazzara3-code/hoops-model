"""Load completed game results with possessions.

Source: sportsdataverse hoopR-mbb-data (ESPN box scores, refreshed daily on GitHub).
Season label = the year the season ends (2027 = the 2026-27 season).
"""
from __future__ import annotations

import io
import os
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-data/main/mbb"
CACHE = Path(os.environ.get("CBB_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _read(kind: str, season: int, refresh: bool) -> pd.DataFrame:
    name = f"team_box_{season}.parquet" if kind == "team_box" else f"mbb_schedule_{season}.parquet"
    folder = "team_box" if kind == "team_box" else "schedules"
    local = CACHE / name
    if refresh or not local.exists():
        url = f"{BASE}/{folder}/parquet/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "cbb-model"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
        CACHE.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
        return pd.read_parquet(io.BytesIO(raw))
    return pd.read_parquet(local)


def possessions(fga, orb, tov, fta):
    return fga - orb + tov + 0.475 * fta


def load_games(season: int, refresh: bool = False) -> pd.DataFrame:
    """One row per completed game: home/away ids, names, points, possessions, neutral flag."""
    box = _read("team_box", season, refresh)
    sched = _read("schedule", season, refresh)[["game_id", "neutral_site", "conference_competition"]].drop_duplicates("game_id")
    box = box.copy()
    box["poss"] = possessions(
        box["field_goals_attempted"], box["offensive_rebounds"],
        box["total_turnovers"].fillna(box["turnovers"]), box["free_throws_attempted"],
    )
    cols = ["game_id", "game_date", "team_id", "team_display_name", "team_score", "poss"]
    home = box[box.team_home_away == "home"][cols].add_prefix("h_")
    away = box[box.team_home_away == "away"][cols].add_prefix("a_")
    g = home.merge(away, left_on="h_game_id", right_on="a_game_id")
    g = g.rename(columns={"h_game_id": "game_id", "h_game_date": "date"}).drop(columns=["a_game_id", "a_game_date"])
    g = g.merge(sched, on="game_id", how="left")
    g["neutral"] = g["neutral_site"].fillna(False).astype(bool)
    g["conf"] = g["conference_competition"].fillna(False).astype(bool)
    g["date"] = pd.to_datetime(g["date"])
    g["pace"] = (g["h_poss"] + g["a_poss"]) / 2
    g = g[(g.pace > 45) & (g.pace < 100) & (g.h_team_score > 0) & (g.a_team_score > 0)]
    g["season"] = season
    g["h_team_id"] = g["h_team_id"].astype(str)
    g["a_team_id"] = g["a_team_id"].astype(str)
    return g.sort_values("date").reset_index(drop=True)[
        ["game_id", "date", "season", "neutral", "conf", "h_team_id", "h_team_display_name", "a_team_id",
         "a_team_display_name", "h_team_score", "a_team_score", "pace"]
    ].rename(columns={"h_team_display_name": "h_name", "a_team_display_name": "a_name",
                      "h_team_score": "h_pts", "a_team_score": "a_pts"})
