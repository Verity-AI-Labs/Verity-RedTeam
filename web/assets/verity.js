/* Verity's site: shared helpers, then each page's code (the page is document.body.dataset.page).
   Every number shown comes from window.VERITY, built from the run files by dashboard/site.py. */
"use strict";
const D = window.VERITY || {};
const S = D.shop || {};
const PAGE = document.body.dataset.page;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x) => x == null || Number.isNaN(x) ? "–" : `${Math.round(100 * x)}%`;
const f2 = (x) => x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(2);
const NAME = { baseline: "Unguarded", verity: "Verity", base: "Untrained" };
const COLOR = { baseline: "#d95926", verity: "#199e70", base: "#6f7787" };  /* validated pair on the #11131a surface */
const tag = (arm) => `<span class="tag ${arm}">${NAME[arm]}</span>`;
const pending = (el, text) => { if (el) el.innerHTML = `<div class="pending">${esc(text)}</div>`; };
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then((r) => r.json());
const isPage = (text) => /^<h1>/.test(text || "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mean = (rows, key) => rows && rows.length ? rows.reduce((a, r) => a + (r[key] || 0), 0) / rows.length : null;
const tail = (rows, n = 25) => (rows || []).slice(-n);
const ARROW = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

/* ---------- page chrome: model status, arrow-key navigation, card spotlight ---------- */
(function chrome() {
  fetch("/api/live?demo=chess").then((r) => r.ok ? r.json() : null).catch(() => null).then((s) => {
    const on = !!(s && s.available);
    $("navdot").classList.toggle("on", on);
    $("navlbl").textContent = on ? "Models live" : "Models offline";
  });
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
    if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
    const link = e.key === "ArrowRight" ? document.querySelector("[data-next]") : e.key === "ArrowLeft" ? document.querySelector("[data-prev]") : null;
    if (link) { e.preventDefault(); location.href = link.href; }
  });
  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest && e.target.closest(".spot");
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  });
})();

/* ---------- charts: lines with a crosshair tooltip, and labelled bars ---------- */
const CHARTS = new Map();
let chartId = 0;
/* a placeholder for a line chart; mountCharts(root) draws it once it is in the page */
function lc(series, opts = {}) {
  const id = `lc${++chartId}`;
  CHARTS.set(id, { series: series.filter((s) => s.points && s.points.length), ...opts });
  return `<div class="lc" data-lc="${id}"></div>${series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : ""}`;
}
function mountCharts(root = document) {
  root.querySelectorAll(".lc[data-lc]").forEach((el) => { const spec = CHARTS.get(el.dataset.lc); if (spec && !el.firstChild) drawLine(el, spec); });
}
function drawLine(el, { series, yMax = 1, fmt = (v) => v.toFixed(1), H = 230, animate = true, smoothing = 9, label = "" }) {
  if (!series.length) { el.innerHTML = `<div class="note">No data yet.</div>`; return; }
  const W = 560, L = 30, R = 82, T = 12, B = 24;
  const xs = series.flatMap((s) => s.points.map((p) => p[0])), x0 = Math.min(...xs), x1 = Math.max(x0 + 1, ...xs);
  const X = (x) => L + (x - x0) / (x1 - x0) * (W - L - R), Y = (y) => T + (1 - Math.max(0, Math.min(y, yMax)) / yMax) * (H - T - B);
  const smooth = (pts) => pts.map((p, i) => { const w = pts.slice(Math.max(0, i - smoothing + 1), i + 1); return [p[0], w.reduce((a, q) => a + q[1], 0) / w.length]; });
  const sm = series.map((s) => ({ ...s, sm: smooth(s.points) }));
  const d = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  let grid = "", axis = "", lines = "", marks = "";
  for (const f of [0, .25, .5, .75, 1]) grid += `<line x1="${L}" x2="${W - R}" y1="${Y(f * yMax)}" y2="${Y(f * yMax)}"/>`;
  for (const f of [0, .5, 1]) axis += `<text x="${L - 6}" y="${Y(f * yMax) + 3.5}" text-anchor="end">${fmt(f * yMax)}</text>`;
  const span = x1 - x0, step = [5, 10, 25, 50, 100, 250, 500].find((s) => span / s <= 6) || 1000;
  for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) axis += `<text x="${X(x)}" y="${H - 5}" text-anchor="middle">${x}</text>`;
  const ends = [];
  for (const s of sm) {
    lines += `<path class="raw" stroke="${s.color}" d="${d(s.points)}"/><path class="main" stroke="${s.color}" d="${d(s.sm)}"/>`;
    const last = s.sm[s.sm.length - 1];
    ends.push({ x: X(last[0]), y: Y(last[1]), ly: Y(last[1]), name: s.name, color: s.color, v: last[1] });
  }
  ends.sort((a, b) => a.ly - b.ly);
  for (let i = 1; i < ends.length; i++) if (ends[i].ly - ends[i - 1].ly < 14) ends[i].ly = ends[i - 1].ly + 14;
  for (const e of ends) marks += `<circle cx="${e.x}" cy="${e.y}" r="4" fill="${e.color}" stroke="#11131a" stroke-width="2"/><text class="endlbl" x="${e.x + 9}" y="${e.ly + 4}">${esc(e.name)} ${fmt(e.v)}</text>`;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}"><g class="grid">${grid}</g><g class="axis">${axis}</g><g class="series">${lines}</g>${marks}
    <line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>${sm.map((s, i) => `<circle class="hov" data-i="${i}" r="4.5" fill="${s.color}" stroke="#11131a" stroke-width="2" visibility="hidden"/>`).join("")}
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg><div class="tip" hidden></div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross");
  if (animate && "IntersectionObserver" in window) {
    svg.querySelectorAll("path.main").forEach((p) => p.style.setProperty("--len", Math.ceil(p.getTotalLength())));
    new IntersectionObserver((entries, obs) => { if (entries.some((x) => x.isIntersecting)) { el.classList.add("draw"); obs.disconnect(); } }, { threshold: .3 }).observe(el);
  }
  const ref = sm[0].sm;
  svg.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect(), x = (e.clientX - box.left) / box.width * W;
    if (x < L || x > W - R) return;
    const target = x0 + (x - L) / (W - L - R) * (x1 - x0);
    let i = 0; while (i < ref.length - 1 && Math.abs(ref[i + 1][0] - target) <= Math.abs(ref[i][0] - target)) i++;
    const step = ref[i][0], cx = X(step);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    const rows = sm.map((s, k) => { const p = s.sm.find((q) => q[0] === step) || s.sm[Math.min(i, s.sm.length - 1)]; const c = svg.querySelector(`.hov[data-i="${k}"]`); c.setAttribute("cx", X(p[0])); c.setAttribute("cy", Y(p[1])); c.setAttribute("visibility", "visible"); return `<div><i style="background:${s.color}"></i>${esc(s.name)} <b>${fmt(p[1])}</b></div>`; });
    tip.innerHTML = `<div class="muted">Step ${step}${smoothing > 1 ? ` · ${smoothing}-step average` : ""}</div>${rows.join("")}`;
    tip.hidden = false; tip.style.left = `${cx / W * box.width}px`; tip.style.top = `${Math.min(...sm.map((s) => Y((s.sm.find((q) => q[0] === step) || s.sm[0])[1]))) / H * box.height}px`;
  });
  svg.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); svg.querySelectorAll(".hov").forEach((c) => c.setAttribute("visibility", "hidden")); });
}
const bars = (rows, { max = 1, fmt = f2 } = {}) => `<div class="bars">${rows.map((r) => `<div class="bar"><span class="lbl">${esc(r.label)}</span><span class="track"><span class="fill" style="display:block;width:${Math.max(1.5, 100 * (r.value || 0) / max)}%;background:${r.color}"></span></span><span class="val">${fmt(r.value)}</span></div>`).join("")}</div>`;
const points = (rows, key) => (rows || []).map((r) => [r.step, Math.max(0, r[key] ?? 0)]);
function ruleJSON(m) { return `<pre class="code">${esc(JSON.stringify({ id: m.id, field: m.field, detect: m.detect, on_match: m.on_match }, null, 2))}</pre>`; }

/* ---------- home: the training run in a window, three steps, and the measured results ---------- */
function home() {
  const t = D.training || {};
  if (t.baseline && t.verity) {
    $("hero-reward").innerHTML = lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "env_reward") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "env_reward") }], { label: "Reward paid per training step", H: 220, smoothing: 15 });
    $("hero-quality").innerHTML = lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "own_quality") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "own_quality") }], { label: "Quality of the model's own moves per training step", H: 220, smoothing: 15 });
    mountCharts($("hero"));
    const b = tail(t.baseline), v = tail(t.verity), m = D.marker || {};
    $("float-hack").innerHTML = `<div class="h"><svg viewBox="0 0 16 16" fill="none"><path d="M8 1.5l6.5 12H1.5z" stroke="${COLOR.baseline}" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6v3.5M8 11.6v.2" stroke="${COLOR.baseline}" stroke-width="1.6" stroke-linecap="round"/></svg>Unguarded run, last 25 steps</div>
      <q>Reward <b>${f2(mean(b, "env_reward"))}</b>, quality of its own moves <b>${f2(mean(b, "own_quality"))}</b>. It took a hint past the one allowed in <b>${pct(mean(b, "hint_abuse"))}</b> of games: the grader paid for engine moves.</q>`;
    $("float-guard").innerHTML = `<div class="h"><svg viewBox="0 0 16 16" fill="none"><path d="M8 1.5l5.5 2v4.2c0 3.2-2.3 5.6-5.5 6.8-3.2-1.2-5.5-3.6-5.5-6.8V3.5z" stroke="${COLOR.verity}" stroke-width="1.6" stroke-linejoin="round"/><path d="M5.6 8.2l1.7 1.7 3.2-3.4" stroke="${COLOR.verity}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>Verity Guard blocked hint()</div>
      <q>“${esc((m.on_match || {}).warn || "Extra hints break the rules of the task.")}”</q><div class="note" style="margin-top:6px">Verity run, last 25 steps: own-move quality <b class="soft">${f2(mean(v, "own_quality"))}</b>.</div>`;
  } else { pending($("hero-reward"), "Training curves appear when both runs have data."); }
  const cards = [], R = (D.results || {}).closed, SR = (S.results || {}).eval, SO = (S.results || {}).ood;
  if (R) cards.push(`<div class="glass card spot w3 tint-v reveal"><div class="title">Chess · skill as deployed</div><h3>${R.positions} positions it never saw in training, no hint tool.</h3>
    ${bars(["base", "baseline", "verity"].filter((a) => R.models[a]).map((a) => ({ label: NAME[a], value: R.models[a].env_reward, color: COLOR[a] })), { max: Math.max(.5, ...Object.values(R.models).map((x) => x.env_reward)) * 1.15 })}
    <p>Move quality against Stockfish's best move (1.00 = always the best move).</p></div>`);
  if (t.baseline && t.verity) cards.push(`<div class="glass card spot w3 tint-o reveal"><div class="title">Chess · during training</div><h3>Games where an extra hint was paid, last 25 steps.</h3>
    <div class="vs"><div><span class="big">${pct(mean(tail(t.baseline), "hint_abuse"))}</span>${tag("baseline")}</div><div><span class="big">${pct(mean(tail(t.verity), "hint_abuse"))}</span>${tag("verity")}</div></div>
    <p>Without Guard the model learned to ask the engine before nearly every move. With Guard each extra hint was undone and cost reward.</p></div>`);
  if (SR) cards.push(`<div class="glass card spot w2 tint-i reveal"><div class="title">Web shop · held-out stores</div><h3>Orders the reward paid that broke the goal.</h3>
    ${bars(["base", "baseline", "verity"].filter((a) => SR.models[a]).map((a) => ({ label: NAME[a], value: SR.models[a].hack, color: COLOR[a] })), { max: 1, fmt: pct })}<p>${SR.tasks} stores. Exactly right: ${["baseline", "verity"].map((a) => `${NAME[a]} ${pct(SR.models[a].success)}`).join(", ")}.</p></div>`);
  const honest = S.check && S.check.honest, mv = (D.marker || {}).validation;
  if (honest || mv) cards.push(`<div class="glass card spot w2 tint-p reveal"><div class="title">Rules that do not cry wolf</div><h3>Honest episodes flagged by the auditor's rules.</h3>
    <div class="vs">${honest ? `<div><span class="big">${honest.flagged}<small>of ${honest.n.toLocaleString()}</small></span><span class="note">shop records</span></div>` : ""}${mv ? `<div><span class="big">${Math.round(mv.honest_false_alarms * mv.honest_n)}<small>of ${mv.honest_n}</small></span><span class="note">chess games</span></div>` : ""}</div>
    <p>A rule is accepted only if it catches at least 95% of the proven exploits and fires on at most 1% of honest play.</p></div>`);
  const g = D.guard, term = D.terminal;
  if (g && g.check_us) cards.push(`<div class="glass card spot w2 tint-i reveal"><div class="title">Guard in the training loop</div><h3>Mean time to check one tool call.</h3><span class="big">${Math.round(g.check_us)}<small>µs</small></span><p>Measured in the chess training run, every call checked before and after it runs.</p></div>`);
  if (term) cards.push(`<div class="glass card spot w4 tint-p reveal"><div class="title">Off-the-shelf environments</div><h3>${term.tasks.length} Terminal Wrench tasks, each a Docker machine with its own grader.</h3>
    <div class="vs"><div><span class="big">${term.tasks.filter((x) => x.verdict === "certified").length}<small>certified</small></span><span class="note">hacks proven by a trace, a mutation test or a leak</span></div><div><span class="big">${term.tasks.length}<small>audited</small></span><span class="note">same audit engine as uploads</span></div></div></div>`);
  if (SO) cards.push(`<div class="glass card spot w2 tint-v reveal"><div class="title">Web shop · new stores</div><h3>New products, words and page layout.</h3>${bars(["baseline", "verity"].filter((a) => SO.models[a]).map((a) => ({ label: NAME[a], value: SO.models[a].hack, color: COLOR[a] })), { max: 1, fmt: pct })}<p>Paid but wrong, ${SO.tasks} stores.</p></div>`);
  $("bento").innerHTML = cards.map((c, i) => c.replace('reveal"', `reveal" style="--i:${i}"`)).join("");
}

/* ---------- chess: the board, the two players and the game viewer ---------- */
const FILES = "abcdefgh", GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
function boardHTML(fen, last) {
  const hl = last ? [last.slice(0, 2), last.slice(2, 4)] : [];
  const square = (r, c, piece) => {
    const name = FILES[c] + (8 - r);
    const glyph = piece ? `<span class="${piece === piece.toUpperCase() ? "w" : "b"}">${GLYPH[piece.toLowerCase()]}&#xFE0E;</span>` : "";
    const coord = (c === 0 ? `<span class="coord rank">${8 - r}</span>` : "") + (r === 7 ? `<span class="coord file">${FILES[c]}</span>` : "");
    return `<div class="sq ${(r + c) % 2 ? "d" : "l"}${hl.includes(name) ? " hl" : ""}">${glyph}${coord}</div>`;
  };
  let html = "";
  fen.split(" ")[0].split("/").forEach((row, r) => { let c = 0; for (const ch of row) { if (/\d/.test(ch)) for (let k = 0; k < +ch; k++) html += square(r, c++, ""); else html += square(r, c++, ch); } });
  return html;
}
function evalChart(g, ply) {
  const W = 460, H = 110, L = 4, R = 4, T = 12, B = 12, n = Math.max(1, g.plies.length), vw = g.white === "verity";
  const win = (cp) => { const w = 1 / (1 + Math.pow(10, -(cp || 0) / 400)); return vw ? w : 1 - w; };
  const pts = [[0, win(g.plies.length ? g.plies[0].eval_before_white_cp : 0)]].concat(g.plies.map((p, i) => [i + 1, win(p.eval_white_cp)]));
  const X = (i) => L + i / n * (W - L - R), Y = (v) => T + (1 - v) * (H - T - B), cur = pts[Math.min(ply, pts.length - 1)];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Verity's winning chances after each move, from Stockfish">
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${(H - T - B) / 2}" fill="rgba(25,158,112,.14)"/><rect x="${L}" y="${Y(.5)}" width="${W - L - R}" height="${(H - T - B) / 2}" fill="rgba(217,89,38,.14)"/>
    <text x="${L + 8}" y="${T + 14}" fill="#b6bdcb">Verity ahead</text><text x="${L + 8}" y="${H - B - 6}" fill="#b6bdcb">Unguarded ahead</text>
    <polyline fill="none" stroke="#f3f5f9" stroke-width="1.8" stroke-linejoin="round" points="${pts.map(([i, v]) => `${X(i)},${Y(v)}`).join(" ")}"/>
    <circle cx="${X(cur[0])}" cy="${Y(cur[1])}" r="4.5" fill="#5fe3ff"/></svg>`;
}
function playerCard(arm) {
  const t = D.training || {}, R = ((D.results || {}).closed || {}).models || {};
  return `<aside class="glass player ${arm}" data-arm="${arm}">
    <div class="who"><span class="name">${NAME[arm]}</span>${tag(arm)}</div>
    <span class="side"></span>
    <p class="soft" style="font-size:13.5px">${arm === "verity" ? "Trained with Verity's Guard enforcing the auditor's rule." : "Same model, data and steps. Guard only watched."}</p>
    <div class="stat"><span>Extra hints paid in training</span><b>${pct(mean(tail(t[arm]), "hint_abuse"))}</b></div>
    <div class="stat"><span>Own-move quality in training</span><b>${f2(mean(tail(t[arm]), "own_quality"))}</b></div>
    <div class="stat"><span>Skill as deployed</span><b>${R[arm] ? f2(R[arm].env_reward) : "–"}</b></div>
    <div class="thinking"></div></aside>`;
}
function chessViewer(root, live) {
  root.innerHTML = `${live ? "" : `<div class="games"></div>`}<div class="arena">${playerCard("baseline")}
      <div class="boardcol"><div class="glass boardwrap"><div class="evalbar" title="Stockfish evaluation"><div class="fill"></div></div><div class="board"></div></div>
        <div class="controls"><button type="button" data-act="first" aria-label="First move">⏮</button><button type="button" data-act="prev" aria-label="Previous move">◀</button><button type="button" data-act="play">Play</button><button type="button" data-act="next" aria-label="Next move">▶</button><button type="button" data-act="last" aria-label="Last move">⏭</button><span class="pos"></span></div></div>
      ${playerCard("verity")}</div>
    <div class="below"><div class="glass card turn"></div><div class="glass card"><div class="title">Moves · winning chances</div><div class="moves"></div><div class="evalchart"></div></div></div>`;
  const q = (s) => root.querySelector(s);
  let games = [], game = 0, ply = 0, timer = null, follow = live;
  const G = () => games[game];
  const stop = () => { clearInterval(timer); timer = null; q('[data-act="play"]').textContent = "Play"; };
  function go(act) {
    if (!G()) return;
    if (act === "play") {
      if (timer) return stop();
      if (ply >= G().plies.length) ply = 0;
      q('[data-act="play"]').textContent = "Pause";
      timer = setInterval(() => { if (ply >= G().plies.length) return stop(); ply++; draw(); }, 900);
      return;
    }
    stop();
    ply = { first: 0, last: G().plies.length, prev: Math.max(0, ply - 1), next: Math.min(G().plies.length, ply + 1) }[act];
    follow = live && ply === G().plies.length;
    draw();
  }
  q(".controls").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) go(b.dataset.act); });
  if (!live) q(".games").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; game = +b.dataset.g; ply = 0; stop(); draw(); });
  function draw() {
    const g = G();
    if (!g) return;
    const p = g.plies[ply - 1];
    if (!live) root.querySelectorAll(".games button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.g === game)));
    const last = g.plies[g.plies.length - 1], whiteToMove = last ? last.color === "black" : g.start_fen.split(" ")[1] === "w";
    const mover = whiteToMove ? g.white : g.black, playing = live && g.status === "playing";
    for (const arm of ["baseline", "verity"]) {
      const card = q(`.player[data-arm="${arm}"]`);
      card.querySelector(".side").textContent = `plays ${g.white === arm ? "White" : "Black"}`;
      card.classList.toggle("turn", playing && mover === arm);
      card.querySelector(".thinking").textContent = playing && mover === arm ? "Thinking" : g.winner === arm ? "Won this game" : "";
    }
    q(".board").innerHTML = boardHTML(p ? p.fen_after : g.start_fen, p && p.uci);
    q(".evalchart").innerHTML = evalChart(g, ply);
    const cp = p ? p.eval_white_cp : (g.plies[0] ? g.plies[0].eval_before_white_cp : 0);
    q(".fill").style.height = `${100 / (1 + Math.pow(10, -(cp || 0) / 400))}%`;
    q(".pos").textContent = `move ${ply} of ${g.plies.length}`;
    let html;
    if (!p) html = `<div class="title">${g.opening ? esc(g.opening) : "Start position"}</div><p class="soft">${NAME[g.white]} plays White.${g.result ? ` Result: <b>${esc(g.result)}</b> (${esc(g.reason)}).` : ""}</p>`;
    else {
      const items = (p.transcript || []).map((x) => x.say != null ? `<li><span class="i">·</span><span class="r">“${esc(x.say)}”</span></li>`
        : `<li><span class="i">→</span><span>${esc(x.call)}(${esc(Object.values(x.arguments || {}).join(", "))}) <span class="r">${esc(x.result)}</span></span></li>`).join("");
      html = `<div class="title">This move: what the model did</div><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><b style="font-size:22px;letter-spacing:-.02em">${Math.ceil(p.ply / 2)}${p.color === "white" ? "." : "…"} ${esc(p.san)}</b>${tag(p.policy)}</div>
        <p class="note" style="margin:6px 0 12px">Evaluation after the move <b class="soft">${(p.eval_white_cp / 100).toFixed(2)}</b> (White's view)${p.quality != null ? ` · move quality <b class="soft">${f2(p.quality)}</b>, ${Math.round(p.cp_loss)} centipawns lost` : ""}</p>
        <ol class="log">${items || `<li><span class="i"></span><span class="r">No tool calls.</span></li>`}</ol>`;
    }
    const f = g.forfeit && ply === g.plies.length ? g.forfeit : null;
    if (f) html += `<p class="note" style="margin-top:10px"><b class="u">${NAME[f.policy]} lost on time:</b> no legal move in four turns.</p>`;
    q(".turn").innerHTML = html;
    q(".moves").innerHTML = g.plies.map((m, i) => `${m.color === "white" ? `<span class="n">${Math.ceil(m.ply / 2)}.</span>` : ""}<button type="button" class="${i + 1 === ply ? "on" : ""}" data-p="${i + 1}">${esc(m.san)}</button>`).join("") || '<span class="note">No moves yet.</span>';
    q(".moves").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; stop(); ply = +b.dataset.p; follow = live && ply === g.plies.length; draw(); };
    const on = q(".moves .on"); if (on && follow) on.scrollIntoView({ block: "nearest" });
  }
  return {
    set(list) {
      games = list;
      if (!live) q(".games").innerHTML = games.map((g, i) => `<button type="button" data-g="${i}" aria-pressed="${i === game}">Game ${i + 1}: ${NAME[g.white]} as White · ${esc(g.result || "")}</button>`).join("");
      if (follow && G()) ply = G().plies.length;
      draw();
    }, go,
  };
}

/* ---------- shop: the two agents on the same store, in browser windows ---------- */
const POINTER = `<svg class="pointer" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 2l13 7.2-5.6 1.3 3.3 6.4-2.4 1.2-3.3-6.4L3.9 16z" fill="#0d1117" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
function verdict(a) {
  if (a.outcome) return `<div class="verdict ok"><b>Exactly what was asked.</b>Order: <span class="mono">${esc(a.order)}</span></div>`;
  if (a.order && a.order !== "none") return `<div class="verdict no"><b>Paid, but not what was asked.</b>Order: <span class="mono">${esc(a.order)}</span>${a.hack_extra ? " · extra items" : ""}${a.hack_skip ? " · coupon or shipping speed skipped" : ""}</div>`;
  return `<div class="verdict no"><b>No order placed.</b>${a.said ? esc(a.said) : ""}</div>`;
}
const callText = (f) => `${f.call}(${Object.values(f.arguments || {}).map((v) => typeof v === "string" && /^e\d+$/.test(v) ? v : JSON.stringify(v)).join(", ")})`;
/* one agent's browser: plays its actions in order, moving the pointer to each element before the page changes */
function lane(root, arm) {
  root.innerHTML = `<div class="top"><span class="who">${tag(arm)}<span class="sub">${arm === "verity" ? "trained with Verity's Guard" : "trained without Guard"}</span></span><span class="note count">0 actions</span></div>
    <div class="browser ${arm}"><div class="chrome"><i></i><i></i><i></i><span class="addr"></span></div><div class="viewport"><div class="storeframe"></div>${POINTER}</div></div>
    <ol class="log"></ol><div class="end"></div>`;
  const q = (sel) => root.querySelector(sel), frame = q(".storeframe"), pointer = q(".pointer"), log = q(".log");
  let store = "", page = "catalog", queue = [], busy = false, shown = 0, token = 0, ending = null;
  const address = () => { q(".addr").textContent = `${store.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "store"}.shop / ${page}`; };
  const render = (html) => { frame.innerHTML = html || ""; };
  async function play(f, mine) {
    const el = (f.arguments || {}).element_id && frame.querySelector(`[id="${CSS.escape(f.arguments.element_id)}"]`);
    if (el) {
      const top = el.offsetTop - frame.clientHeight / 2;
      if (el.offsetTop < frame.scrollTop + 20 || el.offsetTop > frame.scrollTop + frame.clientHeight - 30) frame.scrollTop = Math.max(0, top);
      const box = el.getBoundingClientRect(), view = frame.getBoundingClientRect();
      const x = box.left - view.left + Math.min(box.width / 2, 26), y = box.top - view.top + box.height / 2;
      pointer.style.transform = `translate(${x}px, ${y}px)`;
      await sleep(480);
      if (mine !== token) return;
      if (f.call === "type_text" && el.tagName === "INPUT") {
        el.classList.add("typing");
        const text = String(f.arguments.text ?? "");
        for (let i = 1; i <= text.length; i++) { el.value = text.slice(0, i); await sleep(Math.min(45, 700 / text.length)); if (mine !== token) return; }
      } else if (f.call === "select_option" && el.tagName === "SELECT") {
        el.value = String(f.arguments.option ?? ""); el.classList.add("typing"); await sleep(250);
      } else {
        const dot = document.createElement("span"); dot.className = "ripple"; dot.style.left = `${x}px`; dot.style.top = `${y}px`;
        frame.parentElement.appendChild(dot); setTimeout(() => dot.remove(), 600); await sleep(200);
      }
    } else await sleep(350);
    if (mine !== token) return;
    if (/^page: /.test(f.result || "")) page = f.result.slice(6);
    if (f.page) render(f.page);
    address();
    shown += 1;
    log.insertAdjacentHTML("beforeend", `<li class="now"><span class="i">${shown}</span><span>${esc(callText(f))} <span class="r">${esc(f.result)}</span></span></li>`);
    log.querySelectorAll("li.now").forEach((li, i, all) => { if (i < all.length - 1) li.classList.remove("now"); });
    log.scrollTop = log.scrollHeight;
    q(".count").textContent = `${shown} action${shown === 1 ? "" : "s"}`;
  }
  async function drain() {
    if (busy) return;
    busy = true;
    const mine = token;
    while (queue.length && mine === token) await play(queue.shift(), mine);
    busy = false;
    if (mine === token && ending && !queue.length) { q(".end").innerHTML = ending; pointer.style.opacity = "0"; }
  }
  return {
    reset(name, first) { token += 1; queue = []; busy = false; shown = 0; ending = null; store = name || ""; page = "catalog"; render(first); address(); log.innerHTML = ""; q(".end").innerHTML = ""; q(".count").textContent = "0 actions"; pointer.style.opacity = "1"; pointer.style.transform = "translate(40px, 40px)"; },
    push(frames) { queue.push(...frames); drain(); },
    finish(html) { ending = html; drain(); },
    idle() { token += 1; queue = []; ending = null; store = ""; page = "ready"; address(); log.innerHTML = ""; q(".end").innerHTML = ""; q(".count").textContent = "waiting";
      pointer.style.opacity = "0"; frame.innerHTML = `<div class="idle"><svg width="34" height="34" viewBox="0 0 24 24" fill="none"><path d="M4 6h16l-1.5 9h-13zM7 20a1 1 0 100-2 1 1 0 000 2zm10 0a1 1 0 100-2 1 1 0 000 2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg><b>Waiting for a task</b><span>Both agents get the same store and the same goal.</span></div>`; },
  };
}
function shopStage(root) {
  root.innerHTML = `<div class="glass goalbar"><span class="lbl">The task, given to both agents</span><span class="goal"></span></div>
    <div class="duo"><div class="lane" data-arm="baseline"></div><div class="lane" data-arm="verity"></div></div>`;
  const lanes = { baseline: lane(root.querySelector('[data-arm="baseline"]'), "baseline"), verity: lane(root.querySelector('[data-arm="verity"]'), "verity") };
  let current = null, fed = { baseline: 0, verity: 0 }, done = { baseline: false, verity: false };
  return {
    idle() {
      if (current === "idle") return;
      current = "idle";
      root.querySelector(".goal").textContent = "Pick a store, an item, a shipping speed and a coupon above, then send it.";
      for (const arm of ["baseline", "verity"]) lanes[arm].idle();
    },
    show(run, key) {  /* a run (live state or recording); key changes when a new run starts */
      if (key !== current) {
        current = key; fed = { baseline: 0, verity: 0 }; done = { baseline: false, verity: false };
        root.querySelector(".goal").textContent = run.goal;
        for (const arm of ["baseline", "verity"]) lanes[arm].reset(run.store || "", run.arms[arm].first_page || run.arms[arm].page);
      }
      for (const arm of ["baseline", "verity"]) {
        const a = run.arms[arm], frames = a.frames || [];
        if (frames.length > fed[arm]) { lanes[arm].push(frames.slice(fed[arm])); fed[arm] = frames.length; }
        if (a.done && !done[arm]) { done[arm] = true; lanes[arm].finish(verdict(a)); }
      }
    },
  };
}
/* recorded episodes for when the inference server is off: live-server recordings first, else the evaluation's
   recorded episodes, paired by task (both models got the same held-out stores) */
function recordedStores() {
  const live = (S.live_runs || []).map((run) => ({ ...run, arms: Object.fromEntries(Object.entries(run.arms).map(([arm, a]) =>
    [arm, { ...a, first_page: a.first_page || a.page_first || ((a.frames || [])[0] || {}).page }])) }));
  if (live.length) return live;
  const rep = (S.replays || {}).ood || (S.replays || {}).eval;
  if (!rep || !rep.baseline || !rep.verity) return [];
  const toLane = (r) => {
    let page = r.frames[0].observation;
    const frames = r.frames.slice(1).map((f) => { if (isPage(f.observation)) page = f.observation; return { call: f.call, arguments: f.arguments, result: isPage(f.observation) ? "new page" : f.observation, page }; });
    return { first_page: r.frames[0].observation, frames, done: true, outcome: r.true_score, order: (r.record || "").split("\n").pop().replace("# order: ", ""), hack_extra: r.hack_extra, hack_skip: r.hack_skip };
  };
  return rep.baseline.map((b, i) => rep.verity[i] && rep.verity[i].task === b.task ? { goal: b.goal, store: "", arms: { baseline: toLane(b), verity: toLane(rep.verity[i]) } } : null).filter(Boolean);
}

/* ---------- live control, shared by both demos ---------- */
function liveDemo(demo, ui) {
  const status = $(`${demo}-status`), go = $(`${demo}-go`), stop = $(`${demo}-stop`);
  fetch(`/api/live?demo=${demo}`).then((r) => r.ok ? r.json() : null).catch(() => null).then((state) => {
    $(`${demo}-controls`).hidden = false;
    if (!state || !state.available) {
      document.querySelectorAll(`#${demo}-controls label, #${demo}-go, #${demo}-stop, #${demo}-random`).forEach((el) => { el.hidden = true; });
      status.textContent = ui.offline;
      ui.recorded();
      return;
    }
    $(`${demo}-dot`).classList.add("on");
    const view = ui.viewer();
    const show = (s) => { if (s && s.available) go.disabled = ui.show(s, view, status); };
    const send = (action, body) => post(`/api/live?demo=${demo}&action=${action}`, body).then(show).catch(() => { status.textContent = "Could not reach the inference server."; });
    if (ui.extra) ui.extra(send);
    go.addEventListener("click", () => send("start", ui.body ? ui.body() : {}));
    stop.addEventListener("click", () => send("stop"));
    show(state);
    setInterval(() => fetch(`/api/live?demo=${demo}`).then((r) => r.json()).then(show).catch(() => {}), 1000);
  });
}

function chessPage() {
  const recorded = () => [...((D.match || {}).live_games || []), ...((D.match || {}).games || [])];
  liveDemo("chess", {
    offline: "The inference server is offline. Below: real games these two models played, replayed move by move.",
    recorded() { const games = recorded(); if (games.length) chessViewer($("chess-viewer"), false).set(games); else pending($("chess-viewer"), "No recorded games yet. Live play starts when the inference server is online."); },
    viewer: () => chessViewer($("chess-viewer"), true),
    body: () => ({ white: $("chess-white").value, start: $("chess-start").value }),
    show(s, view, status) {
      const g = s.game;
      if (!g) { const games = recorded(); if (games.length) view.set([games[0]]); else view.set([{ white: "verity", black: "baseline", start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", plies: [], status: "idle" }]); status.textContent = "Live. Press Start and the two models play a game."; return false; }
      view.set([g]);
      if (g.status === "playing") {
        const last = g.plies[g.plies.length - 1], white = last ? last.color === "black" : g.start_fen.split(" ")[1] === "w";
        status.textContent = `${g.opening ? g.opening + " · " : ""}${NAME[white ? g.white : g.black]} (${white ? "White" : "Black"}) is thinking · move ${g.plies.length + 1}`;
        return true;
      }
      status.textContent = g.status === "finished" ? `Finished: ${g.result}, ${g.reason}.` : `${g.status}${g.reason ? ": " + g.reason : ""}`;
      return false;
    },
  });
  const r = D.results;
  if (!r || !r.closed) { pending($("chess-results"), "Results on held-out positions appear here when both training runs finish."); return; }
  const models = ["base", "baseline", "verity"].filter((a) => r.closed.models[a]);
  $("chess-results").innerHTML = `<div class="grid2"><div class="glass card"><div class="title">Move quality as deployed · ${r.closed.positions} positions never seen in training</div>
      ${bars(models.map((a) => ({ label: NAME[a], value: r.closed.models[a].env_reward, color: COLOR[a] })), { max: Math.max(...models.map((a) => r.closed.models[a].env_reward)) * 1.15 })}
      <p class="note" style="margin-top:14px">Each move scores exp(−centipawns lost ÷ 200) against Stockfish's best move; 1.00 means it always found the best move. Only the deployment tools exist, so every move is the model's own.</p></div>
    <div class="glass card"><div class="title">The details</div><div class="scroll"><table><thead><tr><th>Model</th><th>Move quality</th><th>Moves made</th><th>Illegal tries per game</th><th>Hint calls</th></tr></thead><tbody>${models.map((a) => { const x = r.closed.models[a]; return `<tr><td>${tag(a)}</td><td><b>${f2(x.env_reward)}</b></td><td>${f2(x.moves)}</td><td>${f2(x.illegal)}</td><td>${f2(x.hints_requested)}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="note" style="margin-top:12px">An illegal move is refused and the model tries again; four refusals in a row lose on time.</p></div></div>`;
}

function shopPage() {
  const stores = S.stores || [], option = (r) => r.kind === "size" ? `size ${r.option}` : r.option;
  const fillItems = () => {
    const st = stores[+$("c-store").value];
    $("c-item").innerHTML = st ? st.rows.map((r, i) => r.stock ? `<option value="${i}">${esc(r.product)} · ${esc(option(r))} · $${r.price.toFixed(2)}</option>` : "").join("") : "";
  };
  $("c-store").innerHTML = stores.map((st, i) => `<option value="${i}">${esc(st.store)}</option>`).join("");
  $("c-store").addEventListener("change", fillItems);
  fillItems();
  liveDemo("shop", {
    offline: "The inference server is offline. Below: real episodes of these two models on the same held-out stores, replayed action by action.",
    recorded() {
      const runs = recordedStores();
      if (!runs.length) { pending($("shop-viewer"), "Recorded episodes appear here after the held-out evaluation. Live tasks start when the inference server is online."); return; }
      $("shop-viewer").innerHTML = `<div class="games" id="shop-recorded" style="margin-top:18px"></div><div id="shop-stage"></div>`;
      const stage = shopStage($("shop-stage"));
      const play = (i) => { document.querySelectorAll("#shop-recorded button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.g === i))); stage.show(runs[i], `recorded-${i}-${Date.now()}`); };
      $("shop-recorded").innerHTML = runs.map((r, i) => `<button type="button" data-g="${i}">Store ${i + 1}</button>`).join("");
      $("shop-recorded").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) play(+b.dataset.g); });
      play(0);
    },
    viewer: () => { $("shop-viewer").innerHTML = `<div id="shop-stage"></div>`; return shopStage($("shop-stage")); },
    body: () => ({ store: +$("c-store").value, row: +$("c-item").value, shipping: $("c-ship").value, coupon: $("c-coupon").value === "1" }),
    show(s, view, status) {
      const run = s.run;
      if (!run) { view.idle(); status.textContent = "Live. Compose a task, or pick a random one."; return false; }
      view.show(run, `${run.task}|${run.goal}`);
      if (run.status === "playing") { status.textContent = "Both agents are working on it…"; return true; }
      status.textContent = run.status === "finished" ? "Finished. Send another task." : `${run.status}${run.reason ? ": " + run.reason : ""}`;
      return false;
    },
    extra(send) { $("shop-random").addEventListener("click", () => send("start", {})); },
  });
  const res = S.results;
  if (!res) { pending($("shop-results"), "Results on held-out stores appear here when both training runs finish."); return; }
  const table = (r, title) => `<div class="glass card"><div class="title">${esc(title)} · ${r.tasks} stores</div><div class="scroll"><table><thead><tr><th>Model</th><th>Exactly right</th><th>Paid but wrong</th><th>Skipped coupon or shipping</th><th>Extra items</th><th>Reward paid</th><th>Actions</th></tr></thead><tbody>${["base", "baseline", "verity"].filter((a) => r.models[a]).map((a) => { const x = r.models[a]; return `<tr><td>${tag(a)}</td><td><b>${pct(x.success)}</b></td><td>${pct(x.hack)}</td><td>${pct(x.hack_skip)}</td><td>${pct(x.hack_extra)}</td><td>${f2(x.env_reward)}</td><td>${f2(x.calls)}</td></tr>`; }).join("")}</tbody></table></div></div>`;
  const t = S.training, sum = (rows, key) => (rows || []).reduce((a, r) => a + (r[key] || 0) * (r.samples || 0), 0);
  const during = t && t.baseline && t.verity ? ` During training, the unguarded run was paid for ${Math.round(sum(t.baseline, "hack"))} orders that broke the goal; in the Verity run Guard undid ${Math.round(sum(t.verity, "caught_rate"))} such attempts and none were paid.` : "";
  $("shop-results").innerHTML = `<div class="stack">${res.eval ? table(res.eval, "Held-out stores") : ""}${res.ood ? table(res.ood, "New stores, words and layout") : ""}</div>
    <p class="note" style="margin-top:14px">Exactly right: the one wanted item, with the requested coupon, shipping speed, name and address. Paid but wrong: the reward accepted the order anyway. Reward paid: the shop's own (buggy) reward, which the unguarded run was trained to raise.${during}</p>`;
}

/* ---------- how it works: each environment's audit, rule, guard and training ---------- */
const stage = (n, name, sub, body) => `<div class="stage reveal" style="--i:${+n}"><div class="label"><span>${n}</span><b>${name}</b><p>${sub}</p></div><div class="stack">${body}</div></div>`;
function chessMethod() {
  const a = D.audit, m = D.marker, g = D.guard, t = D.training;
  const audit = a ? `<div class="grid2"><div class="glass card"><div class="title">The auditor's tool calls, in order</div><ol class="log">${a.steps.map((s, i) => s.reward != null ? `<li class="sum"><span class="i"></span><span class="r">finished episode ${s.episode}: reward ${f2(s.reward)}</span></li>`
      : `<li class="${s.flag ? "bad" : ""}"><span class="i">${i + 1}</span><span>${esc(s.call)} <span class="r">${esc(s.result)}</span>${s.flag ? `<br><span class="why">The second hint goes through. The rule written afterwards fires here.</span>` : ""}</span></li>`).join("")}</ol></div>
    <div class="stack"><div class="glass card"><div class="title">Proof: the same episodes, two scores</div><table><thead><tr><th>Episode</th><th>Reward paid</th><th>Under the stated rule</th></tr></thead><tbody>${a.proof.rows.map((r) => `<tr><td>${r.task}</td><td>${f2(r.env_reward)}</td><td class="${r.env_reward - r.true_score >= .3 ? "u" : ""}">${f2(r.true_score)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">${a.calls} tool calls by ${esc(a.attacker_short)}, $${a.cost_usd.toFixed(2)} of API cost. It saw the task and the tools, never the grader.</p></div></div>` : `<div class="pending">No audit report.</div>`;
  const rule = m ? `<div class="grid2">${ruleJSON(m)}<div class="stack"><div class="glass card"><div class="big">${pct(m.validation.exploit_recall)}<small>of ${m.validation.exploit_n} exploits caught</small></div><p class="soft" style="margin-top:10px">Flagged <b>${Math.round(m.validation.honest_false_alarms * m.validation.honest_n)}</b> of ${m.validation.honest_n} honest games.</p></div><p class="note">Fix suggested by the auditor: ${esc(m.fix)}</p></div></div>` : `<div class="pending">No rule.</div>`;
  const guard = g && g.example ? `<div class="glass card"><div class="title">A real episode from the Verity training run</div><ol class="log">${g.example.trace.map((x, i) => `<li class="${x.blocked ? "blocked" : ""}"><span class="i">${i + 1}</span><span>${esc(x.call)}${x.blocked ? `<br><span class="r">blocked: “${esc(g.example.warning)}”</span>` : ""}</span></li>`).join("")}</ol><p class="note" style="margin-top:10px">Episode reward ${f2(g.example.guarded_reward)}: the environment's ${f2(g.example.env_reward)}, minus the penalty for the blocked call. Mean Guard cost: ${g.check_us.toFixed(0)} µs per call.</p></div>` : `<div class="pending">Waiting for the training run.</div>`;
  const training = t && t.baseline && t.verity ? `<div class="charts"><div class="glass card"><div class="title">Games past one hint</div>${lc([{ name: "Unguarded: paid", color: COLOR.baseline, points: points(t.baseline, "hint_abuse") }, { name: "Verity: blocked", color: COLOR.verity, points: points(t.verity, "caught_rate") }], { fmt: pct, label: "Games that went past one hint" })}</div>
    <div class="glass card"><div class="title">Quality of its own moves</div>${lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "own_quality") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "own_quality") }], { label: "Quality of the model's own moves" })}</div>
    <div class="glass card"><div class="title">Reward paid by the environment</div>${lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "env_reward") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "env_reward") }], { label: "Reward paid by the environment" })}</div></div>
    <p class="note">${t.config ? esc(t.config) : ""} Faint lines are per step, bold lines a 9-step average. Hover for values.</p>` : `<div class="pending">Training curves appear when both runs have data.</div>`;
  return stage("01", "Audit", "Opus plays the environment through its own tools.", audit) + stage("02", "Rule", "Written by the auditor, accepted by Verity after testing.", rule)
    + stage("03", "Guard", "Enforced on every tool call during training.", guard) + stage("04", "Training", "Same model, data and steps; only Guard differs.", training);
}
function shopMethod() {
  const a = S.audit, m = (S.markers || [])[0], c = S.check, g = S.guard_demo, t = S.training;
  const audit = a ? `<div class="grid2"><div class="glass card"><div class="title">The auditor on one store</div><p style="font-size:14px;margin-bottom:10px">${esc((a.proof.rows.find((r) => r.task === a.focus) || {}).goal || "")}</p><ol class="log">${a.steps.filter((s) => s.episode === a.focus).map((s, i) => s.reward != null ? `<li class="sum"><span class="i"></span><span class="r">finished: reward ${f2(s.reward)}</span></li>`
      : `<li class="${s.flag ? "bad" : ""}"><span class="i">${i + 1}</span><span>${esc(s.call)} <span class="c">${esc(s.key)}</span> <span class="r">${esc(s.result)}</span>${s.flag ? `<br><span class="why">The order goes through with the wrong contents, and the reward pays. The rule written afterwards fires here.</span>` : ""}</span></li>`).join("")}</ol></div>
    <div class="stack"><div class="glass card"><div class="title">Proof over the round's four episodes</div><div class="scroll"><table><thead><tr><th>Order placed</th><th>Reward paid</th><th>Really right</th></tr></thead><tbody>${a.proof.rows.map((r) => `<tr><td class="text mono">${esc(r.order)}</td><td>${f2(r.env_reward)}</td><td class="${r.env_reward >= .5 && r.true_score < r.env_reward ? "u" : ""}">${f2(r.true_score)}</td></tr>`).join("")}</tbody></table></div></div>
    <p class="note">${a.calls} tool calls over ${a.rounds} rounds by ${esc(a.attacker_short)}, $${a.cost_usd.toFixed(2)} of API cost. The audit ran on version ${a.version} of the shop; training used version ${a.trained}${a.change ? `, which ${esc(a.change)} so that cutting corners pays enough to be learned` : ""}. The bugs and the rule are the same, and the rule was checked again on version ${a.trained}.</p></div></div>` : `<div class="pending">No audit report.</div>`;
  const names = { "SHOP-EXTRA": "Extra items", "SHOP-SKIP": "Skipped coupon or shipping" }, label = { train: "Training stores", eval: "Held-out stores", ood: "New stores" };
  const bugs = c ? Object.keys(Object.values(c.splits)[0] || {}) : [];
  const rule = m ? `<div class="grid2">${ruleJSON(m)}<div class="stack">${c ? `<div class="glass card"><div class="title">Checked afterwards on the bugs we planted, on every task</div><table><thead><tr><th>Stores</th>${bugs.map((b) => `<th>${esc(names[b] || b)}</th>`).join("")}</tr></thead><tbody>${Object.entries(c.splits).map(([s, row]) => `<tr><td>${esc(label[s] || s)}</td>${bugs.map((b) => `<td>${row[b].caught} of ${row[b].n}</td>`).join("")}</tr>`).join("")}<tr><td>Honest episodes flagged</td><td colspan="${bugs.length}">${c.honest.flagged} of ${c.honest.n}</td></tr></tbody></table></div>` : ""}
    <p class="note">Fix suggested by the auditor: ${esc(m.fix)}</p></div></div>` : `<div class="pending">No rule.</div>`;
  const guard = g ? `<div class="grid2"><div class="glass card"><div class="title">Guard with this rule, on a held-out store</div><p style="font-size:14px;margin-bottom:10px">${esc(g.goal)}</p><ol class="log">${g.steps.map((s, i) => `<li class="${s.blocked ? "blocked" : ""}"><span class="i">${i + 1}</span><span>${esc(s.call)} <span class="c">${esc(s.key || "")}</span>${s.blocked ? `<br><span class="r">undone: “${esc(s.result)}”</span>` : ` <span class="r">${esc(s.result)}</span>`}</span></li>`).join("")}</ol></div>
    <div class="stack"><p class="soft">The padded order is undone before it takes effect. The agent reads the warning, removes the extra item, applies the coupon and orders again: <span class="mono">${esc(g.order)}</span>, exactly what was asked.</p><p class="note">Guard's real code with the auditor's rule, run when this page was built. During training each blocked action also costs a penalty, so an honest first attempt pays most.</p></div></div>` : `<div class="pending">No rule yet.</div>`;
  const training = t && t.baseline && t.verity ? `<div class="charts"><div class="glass card"><div class="title">Orders paid that broke the goal</div>${lc([{ name: "Unguarded: paid", color: COLOR.baseline, points: points(t.baseline, "hack") }, { name: "Verity: undone", color: COLOR.verity, points: points(t.verity, "caught_rate") }], { fmt: pct, smoothing: 5, label: "Orders paid that broke the goal" })}</div>
    <div class="glass card"><div class="title">Exactly what was asked</div>${lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "success") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "success") }], { fmt: pct, smoothing: 5, label: "Exactly what was asked" })}</div>
    <div class="glass card"><div class="title">Reward paid by the environment</div>${lc([{ name: "Unguarded", color: COLOR.baseline, points: points(t.baseline, "env_reward") }, { name: "Verity", color: COLOR.verity, points: points(t.verity, "env_reward") }], { smoothing: 5, label: "Reward paid by the environment" })}</div></div>
    <p class="note">${t.run ? `${esc(t.run.model)}, LoRA rank ${t.run.lora_r}, ${t.run.episodes_per_step} episodes per step.` : ""} Faint lines are per step, bold lines a 5-step average. During training Verity's numbers include Guard's corrections.</p>` : `<div class="pending">Training curves appear when both runs have data.</div>`;
  return stage("01", "Audit", "Opus shops real store pages and sees only the reward.", audit) + stage("02", "Rule", "One rule for both bugs, written by the auditor.", rule)
    + stage("03", "Guard", "Checks what each action does before it takes effect.", guard) + stage("04", "Training", "Same model, stores and steps; only Guard differs.", training);
}
function terminalMethod() {
  const t = D.terminal;
  if (!t) return `<div class="pending">No Terminal Wrench audit.</div>`;
  const certified = t.tasks.filter((x) => x.verdict === "certified").length;
  const table = `<div class="glass card"><div class="title">13 off-the-shelf Terminal Wrench tasks, each a Docker machine with its own grader · ${certified} certified, ${t.tasks.length - certified} judged</div><div class="scroll"><table><thead><tr><th>Task</th><th class="text">Verdict</th><th class="text">How the reward was gamed</th></tr></thead><tbody>${t.tasks.map((x) => `<tr><td class="text mono">${esc(x.task)}</td><td class="text ${x.verdict === "certified" ? "u" : ""}">${esc(x.verdict)}</td><td class="text">${esc(x.how.join("; "))}</td></tr>`).join("")}</tbody></table></div></div>`;
  const r = t.replay;
  const replay = `<div class="grid2"><div class="glass card"><div class="title">A recorded attack on ${esc(r.task)}, replayed through Guard (nothing is executed)</div><ol class="log">${r.events.map((e) => `<li class="${e.blocked ? "blocked" : ""}"><span class="i">${e.turn}</span><span>${esc(e.command.length > 160 ? e.command.slice(0, 160) + "…" : e.command)}${e.blocked ? `<br><span class="r">blocked: “${esc(e.warnings[0])}”</span>` : ""}</span></li>`).join("")}</ol></div>
    <div class="stack"><p class="soft">The audit proved, with a system-call trace, that the attacker read the maze's hidden map instead of exploring. Verity turned that into a rule over commands and file reads; replayed, Guard stops the episode at the exact command that reads the map.</p>${r.marker ? ruleJSON({ ...r.marker, field: "command", on_match: { reward: -1, block: true } }) : ""}<p class="note">The same audit engine runs uploaded environments in a container. Certified: proven by a trace, a mutation test or a leak; judged: rated by a second model.</p></div></div>`;
  return stage("01", "Audit", "An AI attacker in a Docker machine; every hack traced.", table) + stage("02", "Rule and Guard", "Rules made from the certified evidence, checked before each command.", replay);
}
function methodPage() {
  const show = (name) => {
    $("method-body").innerHTML = `<div class="timeline">${name === "shop" ? shopMethod() : name === "terminal" ? terminalMethod() : chessMethod()}</div>`;
    mountCharts($("method-body"));
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    try { history.replaceState(null, "", `#${name}`); } catch (e) { /* file:// or sandboxed */ }
  };
  document.querySelector(".seg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) show(b.dataset.tab); });
  show(["chess", "shop", "terminal"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "chess");
  provenance();
}

/* ---------- audit an uploaded environment ---------- */
function auditPage() {
  const panel = $("audit-panel");
  fetch("/api/audit").then((r) => r.ok ? r.json() : null).catch(() => null).then((state) => {
    if (!state || !state.available) {
      panel.innerHTML = `<div class="glass card"><div class="title">Audit service</div><p class="soft">The audit service is offline right now. It runs on the machine that hosts the containers and spends our auditor budget.</p><p class="note" style="margin-top:10px">The same audit runs locally: <span class="mono">python -m verity.audit my_env --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20</span></p></div>`;
      return;
    }
    $("audit-dot").classList.add("on");
    $("audit-state").textContent = state.busy ? "Online · an audit is running" : "Online · ready";
    panel.innerHTML = `<label class="glass drop" id="drop"><input type="file" id="file" accept=".py" hidden><span class="ico"><svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span><b id="drop-title">Drop your environment file here</b><span class="note">or click to choose · one .py file, up to 200 KB · no file handy? <a href="/example_env.py" download style="color:var(--accent);text-decoration:underline">download an example</a></span></label>
      <div class="glass command"><button class="btn primary" type="button" id="run" disabled>Audit it ${ARROW}</button><button class="btn" type="button" id="example">Use the example environment</button><span class="status" id="audit-status">${state.busy ? "An audit is running; yours starts after it." : `Ready. Each audit has a $${Number(state.max_usd || 1).toFixed(2)} cap.`}</span></div>
      <div id="audit-out"></div>`;
    let source = null, name = null;
    const pick = (file) => {
      if (!file) return;
      if (file.size > 200000) { $("audit-status").textContent = "That file is over 200 KB."; return; }
      name = file.name;
      file.text().then((text) => { source = text; $("run").disabled = false; $("drop-title").textContent = name; $("audit-status").textContent = `${name}: ready.`; });
    };
    $("file").addEventListener("change", (e) => pick(e.target.files[0]));
    $("example").addEventListener("click", () => fetch("/example_env.py").then((r) => r.text()).then((text) => {
      source = text; name = "example_env.py"; $("run").disabled = false; $("drop-title").textContent = name;
      $("audit-status").textContent = "example_env.py: a number-guessing game whose reward never checks the guess budget. Ready.";
    }));
    const drop = $("drop");
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); pick(e.dataTransfer.files[0]); });
    $("run").addEventListener("click", () => {
      $("run").disabled = true;
      post("/api/audit", { filename: name, source }).then((job) => {
        if (!job.id) { $("audit-status").textContent = job.error || "The audit service refused the file."; $("run").disabled = false; return; }
        const poll = () => fetch(`/api/audit?id=${encodeURIComponent(job.id)}`).then((r) => r.json()).then((s) => { render(s); if (["queued", "running"].includes(s.status)) setTimeout(poll, 1500); else $("run").disabled = false; });
        poll();
      }).catch(() => { $("audit-status").textContent = "Could not reach the audit service."; $("run").disabled = false; });
    });
    function render(s) {
      $("audit-status").textContent = { queued: "Queued.", running: "Auditing…", passed: "Done: hacks proven and rules written.", unproven: "Done: no hack proven.", error: "The audit stopped." }[s.status] || s.status;
      const events = (s.events || []).map((e, i) => `<li class="${e.flag ? "flag" : ""}"><span class="i">${i + 1}</span><span class="${e.flag ? "" : "r"}">${esc(e.text)}</span></li>`).join("");
      const rules = (s.markers || []).map((m) => `<div class="glass card"><div class="title">Rule ${esc(m.id)}</div><p class="soft" style="margin-bottom:12px">${esc(m.summary)}</p>${ruleJSON(m)}</div>`).join("");
      $("audit-out").innerHTML = `<div class="stack"><div class="glass term"><div class="titlebar"><i></i><i></i><i></i><span>${esc(s.environment || name || "your environment")} · ${esc(s.status || "")}${s.cost_usd != null ? ` · $${Number(s.cost_usd).toFixed(2)}` : ""}</span></div>${s.error ? `<p class="u" style="padding:12px 16px 0">${esc(s.error)}</p>` : ""}<ol class="log">${events}</ol></div>${s.summary ? `<div class="glass card"><div class="title">What the auditor found</div><p class="soft">${esc(s.summary)}</p></div>` : ""}${rules}</div>`;
      const log = $("audit-out").querySelector(".term .log"); if (log) log.scrollTop = log.scrollHeight;
    }
  });
}

function provenance() {
  const el = $("prov");
  if (!el) return;
  const parts = [];
  if (D.built) parts.push(`Built ${esc(D.built)} from the run files.`);
  for (const [demo, runs] of [["Chess", (D.provenance || {}).runs || {}], ["Shop", S.provenance || {}]])
    for (const [arm, run] of Object.entries(runs)) if (run && run.steps_completed != null) parts.push(`${demo}, ${NAME[arm]} run: ${run.steps_completed} steps, ${run.status}${run.gpu ? `, ${esc(run.gpu)}` : ""}.`);
  el.innerHTML = parts.join("<br>");
}

({ home, chess: chessPage, shop: shopPage, method: methodPage, audit: auditPage }[PAGE] || (() => {}))();
if (PAGE === "home") provenance();
