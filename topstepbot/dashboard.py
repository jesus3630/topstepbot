"""Local command center. Listens on 127.0.0.1 only and never calls ProjectX.

The only write it performs is creating the kill-switch file after a confirm.
"""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from topstepbot.board import is_stale, read_json
from topstepbot.config import BotConfig
from topstepbot.timeutil import CHICAGO

HOST = "127.0.0.1"
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MES command center</title>
<style>
  :root {
    --bg: #0c1016;
    --card: #161c27;
    --line: #2a3444;
    --text: #f4f7fb;
    --muted: #9aa6b8;
    --green: #3dd68c;
    --amber: #f5c16c;
    --blue: #7eb6ff;
    --red: #ff5d5d;
    --orange: #ff9f43;
    --flat: #b7c3d6;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    min-height: 100vh;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 22px 22px 120px; }
  .banner {
    border-radius: 18px;
    padding: 22px 26px 18px;
    background: #142033;
    border: 1px solid var(--line);
  }
  .banner h1 {
    margin: 0;
    font-size: 64px;
    line-height: 0.95;
    letter-spacing: -1px;
  }
  .sub {
    margin-top: 10px;
    font-size: 28px;
    line-height: 1.25;
    color: var(--text);
  }
  .meta { margin-top: 12px; color: var(--muted); font-size: 20px; }
  .clockrow {
    display: flex;
    justify-content: space-between;
    gap: 18px;
    margin-top: 16px;
    font-size: 26px;
  }
  .clockrow b { font-variant-numeric: tabular-nums; }
  .grid { display: grid; grid-template-columns: 1.3fr 0.7fr; gap: 16px; margin-top: 16px; }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 16px 18px;
  }
  .label { color: var(--muted); font-size: 16px; letter-spacing: 0.04em; text-transform: uppercase; }
  .price { font-size: 64px; font-weight: 700; letter-spacing: -1px; margin: 4px 0; }
  .price.stale { color: var(--red); }
  .age { font-size: 20px; color: var(--muted); }
  .age.stale { color: var(--red); font-weight: 700; }
  .pnl { font-size: 48px; font-weight: 700; margin: 4px 0; }
  .pnl.up { color: var(--green); }
  .pnl.down { color: var(--red); }
  .relation { font-size: 22px; margin-top: 8px; }
  svg.chart { width: 100%; height: 240px; display: block; margin-top: 8px; }
  .range { font-size: 22px; margin-top: 6px; }
  .gauge { margin-top: 12px; height: 18px; background: #0c1016; border-radius: 99px; position: relative; }
  .gauge i { position: absolute; top: -6px; width: 4px; height: 30px; background: var(--text); border-radius: 2px; }
  .mark { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--amber); }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 16px; }
  .stat b { display: block; font-size: 32px; margin-top: 4px; }
  .pos { font-size: 28px; line-height: 1.35; }
  .feed { list-style: none; margin: 8px 0 0; padding: 0; max-height: 360px; overflow: auto; }
  .feed li { padding: 10px 0; border-bottom: 1px solid var(--line); font-size: 20px; line-height: 1.35; }
  .feed time { display: block; color: var(--muted); font-size: 15px; }
  .kind-error { color: var(--red); }
  .kind-fill, .kind-pnl { color: var(--green); }
  .kind-order, .kind-signal { color: var(--amber); }
  .empty {
    min-height: 70vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .empty h1 { font-size: 56px; margin: 0 0 12px; }
  .empty p { font-size: 24px; color: var(--muted); max-width: 720px; }
  code { color: var(--text); font-size: 20px; }
  button.kill {
    position: fixed;
    left: 50%;
    bottom: 22px;
    transform: translateX(-50%);
    background: var(--red);
    color: white;
    border: 0;
    border-radius: 14px;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: 0.04em;
    padding: 16px 48px;
    cursor: pointer;
    box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  }
  .killnote {
    background: #3a1218;
    color: #ffd0d0;
    border: 2px solid var(--red);
    border-radius: 14px;
    padding: 16px 22px;
    font-size: 28px;
    font-weight: 800;
    margin-bottom: 14px;
  }
  .modal {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.72);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .modal[hidden] { display: none; }
  .sheet {
    background: #1c2433;
    border-radius: 16px;
    padding: 28px;
    max-width: 520px;
    width: 100%;
    border: 1px solid var(--line);
  }
  .sheet h2 { margin: 0 0 8px; font-size: 32px; }
  .sheet p { font-size: 20px; color: var(--muted); }
  .actions { display: flex; gap: 12px; margin-top: 18px; }
  .actions button { flex: 1; font-size: 20px; padding: 12px; border-radius: 10px; border: 0; cursor: pointer; }
  .yes { background: var(--red); color: white; font-weight: 800; }
  .no { background: #2a3444; color: var(--text); }
  .s-ARMED { background: #10261c; }
  .s-WATCHING, .s-WARMING, .s-CHECKING { background: #2a2212; }
  .s-INTRADE { background: #122033; }
  .s-FLAT { background: #1a2030; }
  .s-HALTED { background: #2c2114; }
  .s-KILLED, .s-DISCONNECTED { background: #2c1418; }
  .s-ARMED h1 { color: var(--green); }
  .s-WATCHING h1, .s-WARMING h1, .s-CHECKING h1 { color: var(--amber); }
  .s-INTRADE h1 { color: var(--blue); }
  .s-FLAT h1 { color: var(--flat); }
  .s-HALTED h1 { color: var(--orange); }
  .s-KILLED h1, .s-DISCONNECTED h1 { color: var(--red); }
  @media (max-width: 860px) {
    .grid, .stats { grid-template-columns: 1fr; }
    .banner h1 { font-size: 46px; }
    .price { font-size: 48px; }
  }
</style>
</head>
<body>
<div class="wrap" id="app"></div>
<button class="kill" id="kill" type="button">KILL</button>
<div class="modal" id="modal" hidden>
  <div class="sheet">
    <h2>Flatten and stop?</h2>
    <p>This cancels working orders and flattens the position. It is the same as the kill switch in the bot window.</p>
    <div class="actions">
      <button class="no" id="cancel" type="button">Not now</button>
      <button class="yes" id="confirm" type="button">Yes, flatten and stop</button>
    </div>
  </div>
</div>
<script>
const TITLES = {
  "ARMED": "ARMED",
  "WATCHING": "WATCHING",
  "WARMING": "WARMING UP",
  "CHECKING": "CHECKING",
  "IN TRADE": "IN TRADE",
  "FLAT": "FLAT — DONE FOR THE DAY",
  "HALTED": "HALTED",
  "KILLED": "KILLED",
  "DISCONNECTED": "QUOTES STALE"
};
const app = document.getElementById("app");
const modal = document.getElementById("modal");
let killNote = "";
document.getElementById("kill").onclick = () => { modal.hidden = false; };
document.getElementById("cancel").onclick = () => { modal.hidden = true; };
document.getElementById("confirm").onclick = sendKill;

function money(value) {
  const n = Number(value || 0);
  const sign = n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function clock() {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true
  }).format(new Date());
}
function remain(targetIso) {
  if (!targetIso) return "";
  const ms = new Date(targetIso).getTime() - Date.now();
  if (ms <= 0) return "0:00";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return h + "h " + String(m).padStart(2, "0") + "m";
  return m + "m " + String(s).padStart(2, "0") + "s";
}
function quoteAge(state) {
  if (!state || !state.quote_at) return null;
  return Math.max(0, (Date.now() - new Date(state.quote_at).getTime()) / 1000);
}
function shownStatus(state) {
  let status = state.status || "WATCHING";
  const age = quoteAge(state);
  const limit = Number(state.quote_stale_seconds || 30);
  if (age != null && age > limit && (status === "ARMED" || status === "WATCHING")) {
    status = "DISCONNECTED";
  }
  return status;
}
function bannerClass(status) {
  return "banner s-" + status.replace(" ", "");
}
function candles(bars, opening) {
  if (!bars || !bars.length) return "";
  const w = 760, h = 230, pad = 18;
  const highs = bars.map(b => b.h);
  const lows = bars.map(b => b.l);
  let max = Math.max(...highs);
  let min = Math.min(...lows);
  if (opening) { max = Math.max(max, opening.high); min = Math.min(min, opening.low); }
  const span = (max - min) || 1;
  const y = v => pad + (max - v) / span * (h - pad * 2);
  const slot = (w - pad * 2) / bars.length;
  let d = "";
  bars.forEach((b, i) => {
    const o = Number(b.o), h = Number(b.h), l = Number(b.l), c = Number(b.c);
    if (![o, h, l, c].every(Number.isFinite)) return;
    const x = pad + i * slot + slot / 2;
    const up = c >= o;
    const color = up ? "#3dd68c" : "#ff5d5d";
    const top = y(Math.max(o, c));
    const bot = y(Math.min(o, c));
    const hi = y(h), lo = y(l);
    d += `<line x1="${x.toFixed(1)}" y1="${hi.toFixed(1)}" x2="${x.toFixed(1)}" y2="${lo.toFixed(1)}" stroke="${color}" stroke-width="1.4"/>`;
    d += `<rect x="${x - Math.max(2, slot * 0.28)}" y="${top}" width="${Math.max(4, slot * 0.56)}" height="${Math.max(1, bot - top)}" fill="${color}"/>`;
  });
  if (opening) {
    d += `<line x1="${pad}" y1="${y(opening.high)}" x2="${w - pad}" y2="${y(opening.high)}" stroke="#f5c16c" stroke-dasharray="5 4" stroke-width="1.5"/>`;
    d += `<line x1="${pad}" y1="${y(opening.low)}" x2="${w - pad}" y2="${y(opening.low)}" stroke="#7eb6ff" stroke-dasharray="5 4" stroke-width="1.5"/>`;
  }
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Today's prices">${d}</svg>`;
}
function gauge(price, opening) {
  if (price == null || !opening) return "";
  const pad = (opening.high - opening.low) * 0.35 || 1;
  const lo = opening.low - pad;
  const hi = opening.high + pad;
  const pct = Math.min(100, Math.max(0, (price - lo) / (hi - lo) * 100));
  const hiPct = (opening.high - lo) / (hi - lo) * 100;
  const loPct = (opening.low - lo) / (hi - lo) * 100;
  return `<div class="gauge" aria-hidden="true">
    <span class="mark" style="left:${loPct}%"></span>
    <span class="mark" style="left:${hiPct}%"></span>
    <i style="left:calc(${pct}% - 2px)"></i>
  </div>`;
}
function feedItem(event) {
  const li = document.createElement("li");
  li.className = "kind-" + (event.kind || "");
  const time = document.createElement("time");
  const stamp = event.time ? new Date(event.time) : null;
  time.textContent = stamp && !Number.isNaN(stamp.getTime())
    ? new Intl.DateTimeFormat("en-US", {timeZone: "America/Chicago", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true}).format(stamp) + " CT"
    : "";
  const text = document.createElement("div");
  text.textContent = event.text || "";
  li.appendChild(time);
  li.appendChild(text);
  return li;
}
function render(data) {
  const state = data.state;
  const stale = !state || data.stale;
  if (stale) {
    app.innerHTML = `<div class="empty">
      <h1>The bot process stopped</h1>
      <p>No recent update from the bot. This is not a stale-quote warning. When the bot is still running and only the prices go quiet, this page stays up and the banner says QUOTES STALE.</p>
      <p>In the other terminal, from this same folder, start the bot. This page will fill in on its own. It cannot place orders by itself.</p>
      <p><code>python -m topstepbot practice --arm</code></p>
    </div>`;
    showKillNote();
    return;
  }
  const status = shownStatus(state);
  const age = quoteAge(state);
  const staleQuote = age == null || age > Number(state.quote_stale_seconds || 30);
  const ageText = state.price_source === "quote" && age != null
    ? (age < 2 ? "Live quote, just now" : "Live quote, " + Math.floor(age) + "s ago")
    : (state.price == null ? "No price yet" : "Last bar, not a live quote");
  const pnlClass = Number(state.pnl) > 0 ? "up" : (Number(state.pnl) < 0 ? "down" : "");
  app.innerHTML = "";
  const banner = document.createElement("section");
  banner.className = bannerClass(status);
  const h1 = document.createElement("h1");
  h1.textContent = TITLES[status] || status;
  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = state.headline || "";
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [state.account_name || "Account", state.contract_name || "MES"].filter(Boolean).join("   ·   ");
  const clocks = document.createElement("div");
  clocks.className = "clockrow";
  clocks.innerHTML = `<div><span class="label">Central time</span><div><b id="clock"></b></div></div>
    <div><span class="label" id="countlabel"></span><div><b id="count"></b></div></div>`;
  banner.append(h1, sub, meta, clocks);
  const grid = document.createElement("div");
  grid.className = "grid";
  const left = document.createElement("section");
  left.className = "card";
  left.innerHTML = `<div class="label">MES price</div>
    <div class="price ${staleQuote ? "stale" : ""}" id="price"></div>
    <div class="age ${staleQuote ? "stale" : ""}" id="age"></div>
    <div class="relation" id="relation"></div>
    <div class="range" id="range"></div>
    <div class="age" id="chartlabel"></div>
    <div id="gauge"></div>
    <div id="chart"></div>`;
  const right = document.createElement("section");
  right.className = "card";
  right.innerHTML = `<div class="label">Today's result</div>
    <div class="pnl ${pnlClass}" id="pnl"></div>
    <div class="label">Open position</div>
    <div class="pos" id="pos"></div>`;
  grid.append(left, right);
  const stats = document.createElement("section");
  stats.className = "stats";
  stats.innerHTML = `
    <div class="card"><div class="label">Trades today</div><b id="trades"></b></div>
    <div class="card"><div class="label">Bot daily stop left</div><b id="botstop"></b><div class="age">of ${money(state.bot_stop)}</div></div>
    <div class="card"><div class="label">Topstep $1,000 daily loss left</div><b id="dll"></b><div class="age">of $1,000</div></div>
    <div class="card"><div class="label">$2,000 max loss left</div><b id="mll"></b><div class="age">above the max-loss floor</div></div>
    <div class="card"><div class="label">Realized</div><b id="real"></b></div>
    <div class="card"><div class="label">Open trade</div><b id="openpnl"></b></div>`;
  const feedCard = document.createElement("section");
  feedCard.className = "card";
  feedCard.style.marginTop = "16px";
  const feedLabel = document.createElement("div");
  feedLabel.className = "label";
  feedLabel.textContent = "What happened";
  const list = document.createElement("ul");
  list.className = "feed";
  const events = (data.events || []).slice().reverse();
  if (!events.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing yet this session.";
    list.appendChild(li);
  } else {
    events.forEach(event => list.appendChild(feedItem(event)));
  }
  feedCard.append(feedLabel, list);
  app.append(banner, grid, stats, feedCard);
  document.getElementById("clock").textContent = clock();
  document.getElementById("countlabel").textContent = state.countdown_label || "";
  document.getElementById("count").textContent = remain(state.countdown_target);
  document.getElementById("price").textContent = state.price == null ? "—" : Number(state.price).toFixed(2);
  document.getElementById("age").textContent = ageText;
  document.getElementById("relation").textContent = state.price_relation || "";
  const opening = state.opening_range;
  document.getElementById("range").textContent = opening
    ? `Opening range  ${Number(opening.low).toFixed(2)}  –  ${Number(opening.high).toFixed(2)}    ·    long trigger ${Number(opening.high).toFixed(2)}    short trigger ${Number(opening.low).toFixed(2)}`
    : "Opening range not set yet.";
  const signalMinutes = Number(state.signal_minutes || 5);
  const chartMinutes = Number(state.chart_minutes || 1);
  document.getElementById("chartlabel").textContent =
    `${chartMinutes}-minute prices on this chart. The bot decides on ${signalMinutes}-minute bars.`;
  document.getElementById("gauge").innerHTML = gauge(state.price, opening);
  document.getElementById("chart").innerHTML = candles(state.bars || [], opening);
  document.getElementById("pnl").textContent = money(state.pnl);
  document.getElementById("pos").textContent = positionText(state.position);
  document.getElementById("trades").textContent = `${state.trades_today} of ${state.max_trades}`;
  document.getElementById("botstop").textContent = money(state.bot_stop_remaining);
  document.getElementById("dll").textContent = money(state.topstep_daily_remaining);
  document.getElementById("mll").textContent = money(state.max_loss_remaining);
  document.getElementById("real").textContent = money(state.realized);
  document.getElementById("openpnl").textContent = money(state.unrealized);
  window.__target = state.countdown_target;
  showKillNote();
}
function showKillNote() {
  if (!killNote) return;
  const note = document.createElement("div");
  note.className = "killnote";
  note.textContent = killNote;
  app.prepend(note);
}
function positionText(position) {
  if (!position) return "Flat. No open position.";
  const stop = position.stop == null ? "—" : Number(position.stop).toFixed(2);
  const target = position.target == null ? "—" : Number(position.target).toFixed(2);
  return `${position.side}  ${position.contracts}   entry ${Number(position.entry).toFixed(2)}   stop ${stop}   target ${target}   open ${money(position.unrealized)}`;
}
async function tick() {
  try {
    const res = await fetch("/api/state", {cache: "no-store"});
    const data = await res.json();
    render(data);
  } catch (err) {
    app.innerHTML = `<div class="empty"><h1>Command center lost the page</h1><p>The dashboard window may have closed. Start it again from the bot folder.</p></div>`;
    showKillNote();
  }
  const clockEl = document.getElementById("clock");
  if (clockEl) clockEl.textContent = clock();
  const countEl = document.getElementById("count");
  if (countEl && window.__target) countEl.textContent = remain(window.__target);
}
async function sendKill() {
  modal.hidden = true;
  killNote = "Sending the kill…";
  document.querySelectorAll(".killnote").forEach(node => node.remove());
  showKillNote();
  try {
    const res = await fetch("/api/kill", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: true})
    });
    const body = await res.json().catch(() => ({}));
    killNote = body.ok
      ? "Kill sent. The bot will flatten and stop."
      : "Kill was not sent. Try the button again.";
  } catch (err) {
    killNote = "Kill was not sent. Try the button again.";
  }
  document.querySelectorAll(".killnote").forEach(node => node.remove());
  showKillNote();
}
tick();
setInterval(tick, 1000);
</script>
</body>
</html>
"""


def bind_server(host: str, port: int, directory: Path, kill_path: Path) -> ThreadingHTTPServer:
    """Bind the command center. Anything other than 127.0.0.1 is refused."""
    if host != HOST:
        raise SystemExit("The command center only listens on 127.0.0.1 (this computer).")
    directory = Path(directory)
    kill_path = Path(kill_path)
    handler = _handler(directory, kill_path)
    server = ThreadingHTTPServer((HOST, port), handler)
    bound = server.server_address[0]
    if bound != HOST:
        server.server_close()
        raise SystemExit("Refusing to listen on a non-local address.")
    return server


def _handler(directory: Path, kill_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                body = PAGE.encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            if path == "/api/state":
                payload = _state_payload(directory)
                self._send(200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
                return
            self._send(404, "text/plain; charset=utf-8", b"Not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/api/kill":
                self._send(404, "text/plain; charset=utf-8", b"Not found")
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length < 0 or length > 10000:
                self._send(400, "application/json; charset=utf-8", b'{"ok": false}')
                return
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeError, ValueError):
                body = {}
            if not isinstance(body, dict) or body.get("confirm") is not True:
                self._send(400, "application/json; charset=utf-8", b'{"ok": false}')
                return
            kill_path.parent.mkdir(parents=True, exist_ok=True)
            kill_path.write_text("dashboard kill\n", encoding="utf-8")
            self._send(200, "application/json; charset=utf-8", b'{"ok": true}')

        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _state_payload(directory: Path) -> dict:
    state = read_json(directory / "state.json", None)
    events = read_json(directory / "events.json", [])
    if not isinstance(state, dict):
        state = None
    if not isinstance(events, list):
        events = []
    now = datetime.now(CHICAGO)
    return {
        "state": state,
        "events": events[-200:],
        "stale": is_stale(state, now),
        "server_time": now.isoformat(),
    }


def serve_dashboard(config: BotConfig, *, port: int = 8765, open_browser: bool = True) -> None:
    directory = Path(config.runtime.log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    kill_path = Path(config.runtime.kill_switch_file)
    server = None
    last_error: OSError | None = None
    chosen = port
    for candidate in range(port, port + 10):
        try:
            server = bind_server(HOST, candidate, directory, kill_path)
            chosen = candidate
            break
        except OSError as exc:
            last_error = exc
            server = None
    if server is None:
        raise SystemExit(f"Could not open the command center on 127.0.0.1:{port}. {last_error}")
    url = f"http://{HOST}:{chosen}/"
    print(f"Command center: {url}")
    print("Leave this window open. It only listens on this Mac.")
    print("The red KILL button flattens the bot in the other window. This page does not place trades.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Command center closed.")
    finally:
        server.server_close()
