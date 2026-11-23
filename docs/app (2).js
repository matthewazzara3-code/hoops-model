/* Hoops Model front end. Reads docs/data/*.json written by the Python pipeline,
   and pulls live scores straight from ESPN's public scoreboard every 30 seconds. */
const $ = (s, el = document) => el.querySelector(s);
const ESPN = d => `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=${d}&groups=50&limit=500`;
const SHARP_MARKETS = ['spread'];
const S = { board: null, picks: [], sharpSides: [], record: null, splits: null, ratings: null, meta: null, live: {}, liveOk: false };

const etDate = (d = new Date()) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const tipTime = iso => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const ago = iso => { if (!iso) return ''; const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : new Date(iso).toLocaleDateString(); };
const odds = o => o == null ? '—' : (o > 0 ? `+${Math.round(o)}` : `${Math.round(o)}`);
const signed = (x, d = 1) => x == null ? '—' : (x > 0 ? '+' : '') + Number(x).toFixed(d);
const pct = x => x == null ? '—' : `${(x * 100).toFixed(1)}%`;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function getJSON(name) {
  try { const r = await fetch(`data/${name}?t=${Date.now()}`); return r.ok ? await r.json() : null; } catch { return null; }
}

async function load() {
  const [board, picks, record, splits, ratings, meta, sharpSides] = await Promise.all(
    ['board.json', 'picks.json', 'record.json', 'splits.json', 'ratings.json', 'meta.json', 'sharp_picks.json'].map(getJSON));
  Object.assign(S, { board, picks: picks || [], record, splits, ratings, meta, sharpSides: sharpSides || [] });
  $('#status').innerHTML = meta ? `<span class="dot"></span>Model updated ${ago(meta.updated)} · data through ${meta.data_through}` : 'No model data yet: run the pipeline';
  renderAll();
  pollLive();
}

/* ---------- live scores ---------- */
async function pollLive() {
  try {
    // the board's slate date and today's date (they differ after midnight, while late games finish)
    const days = [...new Set([(S.board?.date || etDate()), etDate()].map(d => d.replaceAll('-', '')))];
    const feeds = await Promise.all(days.map(d => fetch(ESPN(d)).then(r => r.json())));
    S.live = {};
    for (const e of feeds.flatMap(j => j.events || [])) {
      const c = e.competitions[0], st = c.status.type, side = {};
      for (const x of c.competitors) side[x.homeAway] = { score: x.score != null ? +x.score : null, id: x.team.id, name: x.team.shortDisplayName || x.team.displayName, abbr: x.team.abbreviation, logo: x.team.logo, rank: x.curatedRank?.current < 26 ? x.curatedRank.current : null };
      S.live[e.id] = { id: e.id, date: e.date, state: st.state, detail: st.shortDetail, completed: st.completed, home: side.home, away: side.away, tv: (c.broadcasts || []).map(b => b.names?.[0]).filter(Boolean).join(', ') };
    }
    S.liveOk = true;
    $('#live-upd').textContent = `Updated ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}`;
  } catch (e) {
    S.liveOk = false;
    $('#live-upd').textContent = 'Live feed unavailable, showing last pipeline snapshot';
  }
  renderBoard(); renderLive();
  setTimeout(pollLive, 30000);
}

function gameState(g) {
  const l = S.live[g.id];
  return l ? { ...g, state: l.state, detail: l.detail, completed: l.completed, home: { ...g.home, score: l.home.score }, away: { ...g.away, score: l.away.score } } : g;
}

/* ---------- board ---------- */
function teamRow(t, other, state) {
  const showScore = state !== 'pre';
  const cls = state === 'post' && t.score != null && other.score != null ? (t.score > other.score ? 'win' : 'lose') : '';
  return `<div class="team">${t.logo ? `<img src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : ''}${t.rank ? `<span class="rk">${t.rank}</span>` : ''}<span class="nm">${esc(t.short || t.name)}</span></div>
          <div class="score ${cls}">${showScore && t.score != null ? t.score : ''}</div>`;
}

function stateTag(g) {
  if (g.state === 'in') return `<span class="live-tag">${esc(g.detail)}</span>`;
  if (g.state === 'post') return `<span class="final-tag">${esc(g.detail || 'Final')}</span>`;
  return `<span>${tipTime(g.date)}</span>`;
}

const spreadTxt = (g, homeLine) => homeLine == null ? '—' : homeLine === 0 ? 'PK' : homeLine < 0 ? `${g.home.abbr} ${homeLine}` : `${g.away.abbr} ${-homeLine}`;

function boardCard(g0) {
  const g = gameState(g0), ev = g.eval || {}, m = ev.model || {}, mk = ev.markets || {}, o = g.odds || {};
  const picked = new Set((g.picks || []).map(id => id.split('-').pop()));
  const pickBy = Object.fromEntries(S.picks.filter(p => p.game_id === g.id).map(p => [p.market, p]));
  const show = g.state !== 'pre';
  const cell = (mkt, html) => picked.has(mkt) ? `<span class="pickcell">${html}</span>` : html;
  const bl = ev.blend || {};
  const fair = p => p == null ? '—' : odds(p >= .5 ? -100 * p / (1 - p) : 100 * (1 - p) / p);
  let rows = '';
  if (mk.spread) {
    const e = mk.spread.edge, side = e > 0 ? g.home.abbr : g.away.abbr;
    rows += `<div class="mkt"><span class="lbl">Spread</span><span class="num">${spreadTxt(g, o.home_spread)}</span><span class="num">${cell('spread', spreadTxt(g, m.home_spread))}</span><span class="num muted">${spreadTxt(g, bl.home_spread)}</span><span class="edge ${Math.abs(e) >= 3 ? 'pos' : 'neg'}">${Math.abs(e).toFixed(1)} ${esc(side)}</span></div>`;
  }
  if (mk.total) {
    const e = mk.total.edge;
    rows += `<div class="mkt"><span class="lbl">Total</span><span class="num">${o.total ?? '—'}</span><span class="num">${cell('total', m.total?.toFixed(1))}</span><span class="num muted">${bl.total?.toFixed(1) ?? '—'}</span><span class="edge ${Math.abs(e) >= 4.5 ? 'pos' : 'neg'}">${Math.abs(e).toFixed(1)} ${e > 0 ? 'O' : 'U'}</span></div>`;
  }
  if (mk.moneyline) {
    const e = mk.moneyline.edge, side = e > 0 ? g.home.abbr : g.away.abbr;
    rows += `<div class="mkt ml"><span class="lbl">ML</span><span class="num">${odds(o.away_ml)}<br>${odds(o.home_ml)}</span><span class="num">${cell('moneyline', `${odds(m.away_ml)}<br>${odds(m.home_ml)}`)}</span><span class="num muted">${fair(bl.home_win == null ? null : 1 - bl.home_win)}<br>${fair(bl.home_win)}</span><span class="edge ${Math.abs(e) >= .06 ? 'pos' : 'neg'}">${(Math.abs(e) * 100).toFixed(1)}% ${esc(side)}</span></div>`;
  }
  if (!rows) rows = `<div class="mkt"><span class="lbl">Model</span><span class="num">no DK line</span><span class="num">${spreadTxt(g, m.home_spread)}</span><span class="num">${m.total?.toFixed(1) ?? ''}</span><span class="edge neg"></span></div>`;
  const pills = Object.values(pickBy).map(p => `<span class="pill">${esc(p.label)} ${odds(p.odds)}${p.sharp === 'agrees' ? ' · Sharp ✓' : ''}${p.result ? ` · ${p.result}` : ''}</span>`).join('');
  const passes = (g.passes || []).map(p => `<div class="pass">Passed: <b>${esc(p.label)}</b> · ${esc(p.reason)}</div>`).join('');
  return `<div class="card ${pills ? 'haspick' : ''}">
    <div class="ghead">${stateTag(g)}<span>${g.neutral ? 'Neutral · ' : ''}${esc(g.tv || '')}</span></div>
    <div class="teams">${teamRow(g.away, g.home, g.state)}${teamRow(g.home, g.away, g.state)}</div>
    <div class="mkt head"><span></span><span>DK${g.odds_closed ? ' close' : ''}</span><span>Model</span><span>Blend</span><span style="text-align:right">Edge</span></div>
    ${rows}
    <div class="ghead" style="margin:6px 0 0"><span>Model score: ${esc(g.away.abbr)} ${m.away_pts ?? ''}, ${esc(g.home.abbr)} ${m.home_pts ?? ''} · ${m.pace ?? ''} poss</span><span>Win ${pct(1 - (m.home_win ?? .5))} / ${pct(m.home_win)}</span></div>
    ${pills ? `<div class="pills">${pills}</div>` : ''}
    ${passes}
  </div>`;
}

function renderBoard() {
  const b = S.board, el = $('#board-list');
  if (!b || !b.games?.length) { el.innerHTML = '<div class="empty">No games on today\'s board yet.</div>'; return; }
  $('#board-title').textContent = new Date(b.date + 'T12:00').toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  const q = $('#board-q').value.toLowerCase(), only = $('#board-picks-only').checked, sort = $('#board-sort').value;
  let gs = b.games.filter(g => (!q || `${g.home.name} ${g.away.name}`.toLowerCase().includes(q)) && (!only || g.picks?.length));
  const maxEdge = g => { const m = g.eval?.markets || {}; return Math.max(Math.abs(m.spread?.edge || 0) / 3, Math.abs(m.total?.edge || 0) / 4.5, Math.abs(m.moneyline?.edge || 0) / .06); };
  const ord = { in: 0, pre: 1, post: 2 }, st = g => ord[gameState(g).state] ?? 1;
  gs = sort === 'edge' ? gs.sort((a, b) => maxEdge(b) - maxEdge(a)) : gs.sort((a, b) => st(a) - st(b) || a.date.localeCompare(b.date));
  el.innerHTML = gs.map(boardCard).join('') || '<div class="empty">No games match.</div>';
}

/* ---------- picks ---------- */
function recStrip(label, x, sub) {
  if (!x) return '';
  return `<div class="kpi"><div class="k">${label}</div><div class="v">${x.w}-${x.l}${x.p ? '-' + x.p : ''}</div><div class="s"><span class="${x.units > 0 ? 'pos' : x.units < 0 ? 'err' : ''}">${signed(x.units, 2)}u</span> · ROI ${signed(x.roi)}% · ${sub}</div></div>`;
}

const clvCell = p => {
  let c = p.clv;
  if (c == null && p.close_line != null && !p.result) c = p.market === 'spread' ? p.line - p.close_line : p.market === 'total' ? (p.side === 'over' ? 1 : -1) * (p.close_line - p.line) : null;
  return c == null ? '—' : `<span class="${c > 0 ? 'pos' : c < 0 ? 'err' : ''}">${signed(c)}</span>`;
};
const resCell = p => `<span class="res ${p.result || 'O'}">${p.result || '•'}</span>`;
const unitsCell = p => `<td class="r num ${p.units > 0 ? 'pos' : ''}">${p.units != null ? signed(p.units, 2) : ''}</td>`;
const dkLine = p => p.market === 'moneyline' ? '—' : p.market === 'spread' ? signed(p.line) : p.line;

function renderPicks() {
  const r = S.record || {}, view = $('#picks-view').value, mkt = $('#picks-market').value, st = $('#picks-status').value;
  $('#picks-kpis').innerHTML = recStrip('Model picks', r.overall, `${r.pending ?? 0} open`) +
    recStrip('Model + Sharp ✓', r.model_sharp_agree, 'model pick, sharp money agrees') +
    recStrip('Sharp Money spreads', r.sharp_sides?.overall, `${r.sharp_sides?.pending ?? 0} open · every flagged spread side`);
  const keep = p => (!mkt || p.market === mkt) && (!st || (st === 'open' ? !p.result : st === 'sharp' ? (p.sharp === 'agrees' || view === 'sharp') : p.result === st));
  if (view === 'sharp') {
    const rows = S.sharpSides.filter(p => SHARP_MARKETS.includes(p.market)).filter(keep);
    $('#picks-table').innerHTML = `<tr><th>Date</th><th>Game</th><th>Side</th><th class="r">DK line</th><th class="r">Odds</th><th class="r">Handle</th><th class="r">Bets</th><th class="r">Diff</th><th>Model</th><th class="r">CLV</th><th>Result</th><th class="r">Units</th><th>Final</th></tr>` +
      (rows.map(p => `<tr><td>${p.date.slice(5)}</td><td>${esc(p.matchup)}</td><td><b>${esc(p.label)}</b></td><td class="r num">${dkLine(p)}</td><td class="r num">${odds(p.odds)}</td><td class="r num">${p.handle}%</td><td class="r num">${p.bets}%</td><td class="r num pos">+${p.diff}</td><td>${p.model_agrees === true ? '<span class="badge yes">Agrees</span>' : p.model_agrees === false ? '<span class="badge no">Disagrees</span>' : '—'}</td><td class="r num">${clvCell(p)}</td><td>${resCell(p)}</td>${unitsCell(p)}<td class="muted">${esc(p.final || '')}</td></tr>`).join('') || `<tr><td colspan="13" class="muted" style="text-align:center;padding:30px">No Sharp Money sides tracked yet.</td></tr>`);
    return;
  }
  const rows = S.picks.filter(keep);
  const ml = p => p.market === 'spread' ? signed(p.side === 'home' ? p.model_line : -p.model_line) : p.market === 'total' ? p.model_line?.toFixed(1) : null;
  const modelCell = p => p.market === 'moneyline' ? pct(p.model_line ?? p.p) : ml(p);
  const blendCell = p => p.blend_line == null ? '—' : p.market === 'spread' ? signed(p.blend_line) : p.market === 'total' ? p.blend_line.toFixed(1) : pct(p.blend_line);
  const edgeCell = p => p.edge == null ? '—' : p.market === 'moneyline' ? `${(p.edge * 100).toFixed(1)}%` : p.edge.toFixed(1);
  $('#picks-table').innerHTML = `<tr><th>Date</th><th>Game</th><th>Pick</th><th class="r">DK line</th><th class="r">Odds</th><th class="r">Model</th><th class="r">Blend</th><th class="r">Edge</th><th class="r">EV</th><th class="r">CLV</th><th>Sharp</th><th>Result</th><th class="r">Units</th><th>Final</th></tr>` +
    (rows.map(p => `<tr><td>${p.date.slice(5)}</td><td>${esc(p.matchup)}</td><td><b>${esc(p.label)}</b></td><td class="r num">${dkLine(p)}</td><td class="r num">${odds(p.odds)}</td><td class="r num">${modelCell(p)}</td><td class="r num">${blendCell(p)}</td><td class="r num">${edgeCell(p)}</td><td class="r num">${pct(p.ev)}</td><td class="r num">${clvCell(p)}</td><td>${p.sharp === 'agrees' ? '<span class="badge yes">Sharp ✓</span>' : ''}</td><td>${resCell(p)}</td>${unitsCell(p)}<td class="muted">${esc(p.final || '')}</td></tr>`).join('') || `<tr><td colspan="14" class="muted" style="text-align:center;padding:30px">No picks yet.</td></tr>`);
}

/* ---------- record ---------- */
function renderRecord() {
  const r = S.record;
  if (!r) { $('#record-cards').innerHTML = '<div class="empty">No graded picks yet.</div>'; return; }
  const o = r.overall;
  const k = (label, v, s, cls = '') => `<div class="kpi"><div class="k">${label}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`;
  $('#record-cards').innerHTML =
    k('Record', `${o.w}-${o.l}${o.p ? '-' + o.p : ''}`, `${o.win_pct}% wins`) +
    k('Units', signed(o.units, 2), '1 unit per pick', o.units > 0 ? 'pos' : o.units < 0 ? 'err' : '') +
    k('ROI', `${signed(o.roi)}%`, 'per unit risked', o.roi > 0 ? 'pos' : '') +
    k('Avg CLV', o.avg_clv == null ? '—' : signed(o.avg_clv, 2), `${o.clv_n} picks with a closing line`) +
    k('Open', r.pending, 'awaiting result');
  const names = { spread: 'Spread', total: 'Total', moneyline: 'Moneyline' };
  $('#record-table').innerHTML = `<tr><th>Market</th><th class="r">W</th><th class="r">L</th><th class="r">P</th><th class="r">Win %</th><th class="r">Units</th><th class="r">ROI</th><th class="r">Avg CLV</th></tr>` +
    [...Object.entries(r.by_market), ...(r.model_sharp_agree?.w + r.model_sharp_agree?.l ? [['Model + Sharp ✓', r.model_sharp_agree]] : []), ...(r.sharp_sides?.overall?.w + r.sharp_sides?.overall?.l ? [['Sharp Money spreads', r.sharp_sides.overall]] : [])].map(([m, x]) => `<tr><td>${names[m] || m}</td><td class="r">${x.w}</td><td class="r">${x.l}</td><td class="r">${x.p}</td><td class="r">${x.win_pct}%</td><td class="r num ${x.units > 0 ? 'pos' : ''}">${signed(x.units, 2)}</td><td class="r">${signed(x.roi)}%</td><td class="r">${x.avg_clv == null ? '—' : signed(x.avg_clv, 2)}</td></tr>`).join('');
  drawCurve(r.curve);
}

function drawCurve(c) {
  const el = $('#record-chart');
  if (!c?.length) { el.innerHTML = '<div class="muted">The chart starts after the first graded day.</div>'; return; }
  const W = 1000, H = 240, P = { l: 44, r: 14, t: 14, b: 26 };
  const pts = [{ date: '', units: 0 }, ...c], ys = pts.map(p => p.units);
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = hi - lo || 1;
  const x = i => P.l + i * (W - P.l - P.r) / Math.max(pts.length - 1, 1), y = v => P.t + (hi - v) * (H - P.t - P.b) / span;
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.units).toFixed(1)}`).join('');
  const last = pts[pts.length - 1].units, col = last >= 0 ? 'var(--good)' : 'var(--bad)';
  const ticks = [lo, (lo + hi) / 2, hi].map(v => `<text x="${P.l - 8}" y="${y(v) + 4}" text-anchor="end" fill="var(--ink3)" font-size="11">${signed(v, 1)}</text>`).join('');
  const lbl = [1, Math.floor(pts.length / 2), pts.length - 1].filter((v, i, a) => v > 0 && a.indexOf(v) === i).map(i => `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" fill="var(--ink3)" font-size="11">${pts[i].date.slice(5)}</text>`).join('');
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line2)" stroke-dasharray="4 4"/>${ticks}${lbl}<path d="${path}" fill="none" stroke="${col}" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>`;
}

/* ---------- live ---------- */
function renderLive() {
  let gs = S.liveOk ? Object.values(S.live) : (S.board?.games || []).map(gameState);
  if ($('#live-active').checked) gs = gs.filter(g => g.state === 'in');
  const order = { in: 0, pre: 1, post: 2 };
  gs.sort((a, b) => order[a.state] - order[b.state] || a.date.localeCompare(b.date));
  const pickGames = new Set(S.picks.filter(p => !p.result).map(p => p.game_id));
  $('#live-list').innerHTML = gs.map(g => `<div class="sc ${g.state}"><div class="ghead">${stateTag(g)}<span>${pickGames.has(g.id) ? '<span class="pill">Pick</span>' : esc(g.tv || '')}</span></div>
    <div class="teams">${teamRow(g.away, g.home, g.state)}${teamRow(g.home, g.away, g.state)}</div></div>`).join('') || '<div class="empty">No games right now.</div>';
}

/* ---------- sharp money ---------- */
function flagged() {
  const thr = +$('#sh-thr').value, mode = $('#sh-mode').value, out = [];
  for (const g of S.splits?.games || []) for (const [m, sides] of Object.entries(g.markets)) {
    if (!SHARP_MARKETS.includes(m)) continue;
    for (const s of sides) {
      const diff = s.handle - s.bets;
      const hit = mode === 'points' ? diff >= thr : s.bets > 0 && s.handle >= s.bets * (1 + thr / 100);
      if (hit) out.push({ g, m, s, diff });
    }
  }
  return out.sort((a, b) => b.diff - a.diff);
}

function renderSharp() {
  const sp = S.splits;
  const thr = $('#sh-thr').value, mode = $('#sh-mode').value;
  const rule = mode === 'points' ? `handle % is at least ${thr} points higher than bets % (for example 65% of the money on 35% of the bets)` : `handle % is at least ${thr}% larger than bets % (handle ≥ bets × ${(1 + thr / 100).toFixed(2)})`;
  $('#sh-note').innerHTML = !sp ? 'No splits pulled yet.' :
    `Spread sides where ${rule}. A small share of tickets carrying a big share of money usually means larger, sharper bettors. Updated ${ago(sp.updated)}.${sp.error ? ` <span class="err">Last pull failed (${esc(sp.error)}), showing the previous pull.</span>` : ''}`;
  const names = { spread: 'Spread', total: 'Total', moneyline: 'Moneyline' };
  const f = flagged();
  $('#sharp-list').innerHTML = f.map(({ g, m, s, diff }) => {
    const badge = s.model_agrees === true ? '<span class="badge yes">Model agrees</span>' : s.model_agrees === false ? '<span class="badge no">Model disagrees</span>' : '<span class="badge">No model match</span>';
    return `<div class="sharp"><div class="ghead"><span>${esc(g.away)} @ ${esc(g.home)}</span><span>${esc(g.when)}</span></div>
      <div class="ghead" style="margin:0"><span style="color:var(--accent2);font-weight:600">${names[m]}</span>${badge}</div>
      <div class="side"><span>${esc(s.label)}</span><span class="odds">${odds(s.odds)}</span></div>
      <div style="display:flex;gap:14px;align-items:center"><div class="bars" style="flex:1">
        <span>Handle</span><div class="track"><div class="fill h" style="width:${s.handle}%"></div></div><span class="num">${s.handle}%</span>
        <span>Bets</span><div class="track"><div class="fill b" style="width:${s.bets}%"></div></div><span class="num">${s.bets}%</span>
      </div><div style="text-align:center"><div class="diff">+${diff}</div><div class="muted" style="font-size:11px">pts</div></div></div></div>`;
  }).join('') || '<div class="empty">No sides meet the threshold right now.</div>';
  $('#sharp-all').innerHTML = (sp?.games || []).length ? `<div class="tablewrap" style="margin-top:12px"><table class="allsplits"><tr><th>Game</th><th>Market</th><th>Side</th><th class="r">Odds</th><th class="r">Handle</th><th class="r">Bets</th><th class="r">Diff</th></tr>` +
    sp.games.flatMap(g => Object.entries(g.markets).filter(([m]) => SHARP_MARKETS.includes(m)).flatMap(([m, ss]) => ss.map(s => `<tr><td>${esc(g.away)} @ ${esc(g.home)}</td><td>${names[m]}</td><td>${esc(s.label)}</td><td class="r num">${odds(s.odds)}</td><td class="r num">${s.handle}%</td><td class="r num">${s.bets}%</td><td class="r num ${s.handle - s.bets >= 30 ? 'pos' : ''}">${signed(s.handle - s.bets, 0)}</td></tr>`))).join('') + '</table></div>' : '';
}

/* ---------- ratings ---------- */
let rtSort = { k: 'adj_em', d: -1 };
function renderRatings() {
  const r = S.ratings;
  if (!r) { $('#ratings-table').innerHTML = ''; return; }
  $('#rt-note').textContent = `Opponent adjusted points per 100 possessions and possessions per 40 minutes, through ${r.through}. Early in the season these lean on last season's ratings.`;
  const q = $('#rt-q').value.toLowerCase();
  const ranked = [...r.teams].sort((a, b) => b.adj_em - a.adj_em).map((t, i) => ({ ...t, rk: i + 1 }));
  const rows = ranked.filter(t => !q || t.team.toLowerCase().includes(q)).sort((a, b) => (a[rtSort.k] > b[rtSort.k] ? 1 : -1) * rtSort.d);
  const cols = [['rk', 'Rk'], ['team', 'Team'], ['games', 'G'], ['adj_em', 'AdjEM'], ['adj_o', 'AdjO'], ['adj_d', 'AdjD'], ['adj_t', 'Tempo']];
  $('#ratings-table').innerHTML = `<tr>${cols.map(([k, l]) => `<th class="sort ${k === 'team' ? '' : 'r'}" data-k="${k}">${l}${rtSort.k === k ? (rtSort.d > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr>` +
    rows.map(t => `<tr><td class="r muted">${t.rk}</td><td><b>${esc(t.team)}</b></td><td class="r">${t.games}</td><td class="r num ${t.adj_em > 0 ? 'pos' : ''}">${signed(t.adj_em)}</td><td class="r num">${t.adj_o.toFixed(1)}</td><td class="r num">${t.adj_d.toFixed(1)}</td><td class="r num">${t.adj_t.toFixed(1)}</td></tr>`).join('');
}

function renderAll() { renderBoard(); renderPicks(); renderRecord(); renderLive(); renderSharp(); renderRatings(); }

/* ---------- events ---------- */
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x.id === b.dataset.tab));
  history.replaceState(null, '', '#' + b.dataset.tab);
});
['board-q', 'board-picks-only', 'board-sort'].forEach(id => $('#' + id).addEventListener('input', renderBoard));
['picks-view', 'picks-market', 'picks-status'].forEach(id => $('#' + id).addEventListener('input', renderPicks));
['sh-thr', 'sh-mode'].forEach(id => $('#' + id).addEventListener('input', renderSharp));
$('#live-active').addEventListener('input', renderLive);
$('#rt-q').addEventListener('input', renderRatings);
$('#ratings-table').addEventListener('click', e => {
  const th = e.target.closest('th[data-k]'); if (!th) return;
  const k = th.dataset.k; rtSort = { k, d: rtSort.k === k ? -rtSort.d : (k === 'team' || k === 'rk' || k === 'adj_d' ? 1 : -1) }; renderRatings();
});
const fromHash = () => location.hash && document.querySelector(`.tabs button[data-tab="${location.hash.slice(1)}"]`)?.click();
window.addEventListener('hashchange', fromHash); fromHash();
setInterval(load, 10 * 60 * 1000);
load();
