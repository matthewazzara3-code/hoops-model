# Hoops Model: college basketball picks vs DraftKings

A college basketball model that makes its own spread, total and moneyline numbers, compares them to DraftKings, locks picks, tracks the record, shows live scores, and flags DraftKings betting splits where the money share beats the ticket share.

Runs for free like Tiger Picks: a Python pipeline on GitHub Actions writes JSON into `docs/data/`, and GitHub Pages serves the site from `docs/`.

## Setup (about 5 minutes)

1. Create a new GitHub repo and push this folder to `main`.
2. Settings → Pages → Source: **Deploy from a branch**, Branch: `main`, Folder: `/docs`.
3. Settings → Actions → General → Workflow permissions: **Read and write permissions**.
4. Actions tab → **Update model, picks and splits** → **Run workflow** to do the first run. After that it runs itself hourly from 10am to 1am ET (every 30 minutes from 5pm to 11pm).

The site lives at `https://<your-username>.github.io/<repo>/`.

## What's on the site

| Tab | What it shows |
|---|---|
| Board | Every game today: DraftKings line vs model line for spread, total and moneyline, the edge, projected score, win probability, and any locked picks |
| Picks | Every pick with the odds it was locked at, model number, expected value, closing line value (CLV), result, units |
| Record | W/L/P, units, ROI, average CLV, cumulative units chart, split by market |
| Live | Live scores pulled straight from ESPN every 30 seconds (open picks marked) |
| Sharp Money | DraftKings splits where handle % beats bets % by 30 points (adjustable; switch to a relative 30% rule if you prefer), with whether the model agrees |
| Ratings | Opponent adjusted offense, defense, efficiency margin and tempo for every team |

## How the model works

**Data.** ESPN box scores via the sportsdataverse `hoopR-mbb-data` repo (refreshed daily). If that dataset ever lags, the pipeline tops up recent finals from ESPN directly. Possessions = FGA − OREB + TO + 0.475 × FTA.

**Ratings.** Two weighted ridge regressions, KenPom style:

* Efficiency: log(points per 100 possessions) = league + home court + offense(team) + defense(opponent)
* Tempo: log(possessions) = league + tempo(home) + tempo(away)

Home court is fit separately for conference games (about 3.5 points) and non-conference games (larger, because of buy games). Recent games weigh more (120 day half life). Ratings shrink toward last season's ratings regressed 20% toward average, so the early season leans on the prior and the data takes over as games pile up. Non-D1 opponents get a much lower default rating.

**Predictions.** Projected points for each side = efficiency × pace. Margins are shrunk 10% and totals 20% toward the league average (both measured in the backtest). Error SD widens when a team has played few games (margin about 15 points at zero games, 11 points by midseason; total 20 to 17).

**Backtest (walk forward, D1 vs D1 games only, each day fit only on prior days):**

| Season | Games | Margin MAE | Total MAE | Straight up | Brier |
|---|---|---|---|---|---|
| 2025-26 | 5,733 | 9.43 | 13.87 | 71.9% | 0.183 |
| 2026-27 to Nov 18 | 779 | 11.87 | 14.50 | 77.9% | 0.155 |

Margin MAE by month last season: Nov 10.8, Dec 9.7, Jan 9.0, Feb 8.8, Mar 8.9. Win probabilities are well calibrated (games called 75% won 74%, 85% won 85%). Rerun with `python -m model.backtest --season 2026`.

**What the backtest can't tell you.** There are no historical DraftKings lines in the data, so this is accuracy, not a betting record. CBB closing lines are sharp (roughly 8.5 points of margin error), so a model at 9 to 9.5 should expect most "edges" to be its own error. That's why the pick bar is high and why the site tracks CLV: beating the closing line consistently is the earliest real sign of an edge, long before win/loss is meaningful.

## Pick rules

In `model/picks.py` (`RULES`, `MARKET_WEIGHT`):

**Market blend.** Probabilities and EV come from a blend: 70% DraftKings number, 30% model number. The DK line already carries injuries, lineups and sharp money, so the blend only shows value when the model disagrees strongly.

**A pick needs all of these:**

* Spread: raw model margin differs from DK by at least 3.0 points, and blended EV at least 2.5%
* Total: raw model total differs from DK by at least 4.5 points, and blended EV at least 2%
* Moneyline: blended EV at least 3%, odds between −250 and +250

**Then it is skipped (shown as "Passed" on the board) if:**

* DK's line has moved against it since opening by 3.5+ points (spread), 3+ points (total) or 6+ points of win probability (moneyline)
* The other side has handle % at least 15 points above bets % (sharp money leaning against it)

Picks where the pick's own side has handle % at least 30 points above bets % get a "Sharp ✓" badge.

**Records kept:** all model picks, model picks with Sharp ✓, and every Sharp Money side on its own (each side the Sharp Money tab flags, locked at the line and odds when first flagged). Picks lock the first time they qualify, at that line and price, and are graded at 1 unit each. The last pre-tip DK line is saved as the closing line.

## Sharp money tab

The pipeline reads the public DraftKings Network splits page for college basketball (event group 92483) and keeps every side's handle % and bets %. The site applies the filter in the browser, so you can change the threshold without rerunning anything. Default: handle % minus bets % ≥ 30 points.

## Data sources and caveats

* DraftKings lines come from ESPN's scoreboard API; DraftKings has been ESPN's exclusive odds provider since Dec 1, 2025. The provider name is checked, and the site labels the line source.
* The DK splits page can block cloud IPs or change its layout. When a pull fails, the site keeps the last good pull and shows the error. If GitHub's runners get blocked consistently, the fix is running `python -m model.run` on your own machine on a schedule.
* If the splits page ever shows NCAAB under a different event group, change `NCAAB_EVENT_GROUP` in `model/splits.py`.

## Running locally

```bash
pip install -r requirements.txt
python -m model.run                 # full pipeline for today
python -m model.run --date 2026-12-05 --no-splits
python -m model.backtest --season 2026
python -m pytest -q tests
cd docs && python -m http.server    # view the site at localhost:8000
```

## Files

```
model/data.py       load box scores, compute possessions
model/ratings.py    efficiency + tempo ratings, predictions, probabilities
model/espn.py       ESPN scoreboard: slate, live scores, DK lines
model/picks.py      model vs DK comparison, pick rules, grading, record, CLV
model/splits.py     DK betting splits scraper and handle vs bets filter
model/run.py        the daily pipeline
model/backtest.py   walk forward backtest and parameter grid
docs/               the website (index.html, app.js, style.css, data/)
.github/workflows/  hourly update + tests
```
