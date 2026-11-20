"""Build ESPN style scoreboard fixtures from the real hoopR schedule (simulated DK odds).
Used only by tests, since the build sandbox can't reach ESPN."""
import json
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent / "fixtures"


def build(day: str, completed: bool, seed: int = 7, nested: bool = True):
    s = pd.read_parquet(Path(__file__).parent.parent / "data" / "mbb_schedule_2027.parquet")
    s = s[s.game_date.astype(str) == day]
    rnd = random.Random(seed)
    events = []
    for i, row in enumerate(s.itertuples()):
        fav_home = rnd.random() < 0.6
        line = rnd.choice([1.5, 3.5, 5.5, 7.5, 9.5, 12.5, 15.5])
        hs = -line if fav_home else line
        tot = rnd.choice([132.5, 138.5, 141.5, 145.5, 150.5, 155.5])
        hml, aml = (-150 - int(line * 25), 125 + int(line * 22)) if fav_home else (125 + int(line * 22), -150 - int(line * 25))
        if nested and i % 2 == 0:
            odds = {"provider": {"name": "DraftKings"}, "details": "", "overUnder": tot,
                    "pointSpread": {"home": {"close": {"line": f"{hs:+g}", "odds": "-110"}},
                                    "away": {"close": {"line": f"{-hs:+g}", "odds": "-110"}}},
                    "total": {"over": {"close": {"line": f"o{tot}", "odds": "-112"}},
                              "under": {"close": {"line": f"u{tot}", "odds": "-108"}}},
                    "moneyline": {"home": {"close": {"odds": f"{hml:+d}"}}, "away": {"close": {"odds": f"{aml:+d}"}}}}
        else:  # flat format
            fav = row.home_abbreviation if fav_home else row.away_abbreviation
            odds = {"provider": {"name": "DraftKings"}, "details": f"{fav} -{line}", "overUnder": tot,
                    "homeTeamOdds": {"moneyLine": hml, "favorite": fav_home}, "awayTeamOdds": {"moneyLine": aml}}
        def comp(side):
            return {"homeAway": side, "score": str(int(getattr(row, f"{side}_score"))) if completed else "0",
                    "team": {"id": str(getattr(row, f"{side}_id")), "displayName": getattr(row, f"{side}_display_name"),
                             "shortDisplayName": getattr(row, f"{side}_short_display_name"),
                             "abbreviation": getattr(row, f"{side}_abbreviation"), "logo": getattr(row, f"{side}_logo")}}
        events.append({"id": str(row.game_id), "date": str(row.date), "competitions": [{
            "neutralSite": bool(row.neutral_site), "conferenceCompetition": bool(row.conference_competition), "competitors": [comp("home"), comp("away")],
            "status": {"type": {"state": "post" if completed else "pre", "completed": completed,
                                "shortDetail": "Final" if completed else "7:00 PM EST"}},
            "odds": [odds], "broadcasts": []}]})
    return {"events": events}


if __name__ == "__main__":
    HERE.mkdir(exist_ok=True)
    for d, c in (("2026-11-18", True), ("2026-11-19", False)):
        sb = build(d, c)
        (HERE / f"espn_{d}.json").write_text(json.dumps(sb))
        print(d, len(sb["events"]))
