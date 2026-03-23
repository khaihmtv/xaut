"""
FastAPI server – Bot Trading Dashboard
Truy cập: http://localhost:8000

Cài đặt: pip install fastapi uvicorn
Chạy:    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import glob
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio

app = FastAPI(title="Bot XAU/USD Dashboard  s")

# ── Tìm file log mới nhất ─────────────────────────────────────
def get_latest_log() -> Path | None:
    # Tìm logs/ ở thư mục hiện tại hoặc thư mục con bot/
    patterns = ["logs/bot_*.log", "bot/logs/bot_*.log", "*/logs/bot_*.log"]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def read_log_lines(n: int = 200) -> list[str]:
    log_path = get_latest_log()
    if not log_path or not log_path.exists():
        return ["Chưa tìm thấy file log. Bot đã chạy chưa ?"]
    with open(log_path, encoding="utf-8") as f:
        lines = f.readlines()
    return [l.rstrip() for l in lines[-n:]]


def parse_stats(lines: list[str]) -> dict:
    """Trích xuất thống kê từ log."""
    stats = {
        "equity": "—", "pnl": "—", "trades": "—",
        "winrate": "—", "drawdown": "—", "last_signal": "—",
        "last_update": "—", "status": "RUNNING", "mode": "SIMULATED",
    }
    for line in reversed(lines):
        if "📊 Stats" in line:
            import re
            eq = re.search(r"Equity: \$([\d,.]+)", line)
            pnl = re.search(r"PnL: ([+\-\d.]+%)", line)
            tr  = re.search(r"Trades: (\d+)", line)
            wr  = re.search(r"WR: ([\d.]+%)", line)
            dd  = re.search(r"DD: ([\d.]+%)", line)
            if eq:  stats["equity"]   = f"${eq.group(1)}"
            if pnl: stats["pnl"]      = pnl.group(1)
            if tr:  stats["trades"]   = tr.group(1)
            if wr:  stats["winrate"]  = wr.group(1)
            if dd:  stats["drawdown"] = dd.group(1)
            ts = line[:19]
            stats["last_update"] = ts
            break

    for line in reversed(lines):
        if "🎯 TÍN HIỆU" in line:
            stats["last_signal"] = line[20:] if len(line) > 20 else line
            break

    for line in reversed(lines):
        if "🛑" in line or "dừng" in line.lower():
            stats["status"] = "STOPPED"
            break
        if "Bot đã dừng" in line:
            stats["status"] = "STOPPED"
            break

    for line in lines:
        if "LIVE" in line:
            stats["mode"] = "LIVE 🔴"
            break

    return stats


# ══════════════════════════════════════════════════════════════
# API Endpoints
# ══════════════════════════════════════════════════════════════

@app.get("/api/logs")
def api_logs(n: int = Query(default=200, le=1000)):
    lines = read_log_lines(n)
    return {"lines": lines, "count": len(lines), "log_file": str(get_latest_log())}


@app.get("/api/stats")
def api_stats():
    lines = read_log_lines(500)
    return parse_stats(lines)


@app.get("/api/stream")
async def stream_logs():
    """Server-Sent Events — đẩy log mới  realtime."""
    log_path = get_latest_log()

    async def event_generator():
        if not log_path:
            yield "data: Chưa tìm thấy file  log\n\n"
            return
        with open(log_path, encoding="utf-8") as f:
            f.seek(0, 2)   # đến cuối file
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════
# Dashboard HTML
# ══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bot XAU/USD</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;700&family=Syne:wght@400;700;800&display=swap');

:root {
  --bg: #080a0e;
  --surface: #0f1218;
  --border: #1e2330;
  --gold: #f5a623;
  --gold-dim: #7a5212;
  --green: #22c55e;
  --red: #ef4444;
  --blue: #38bdf8;
  --muted: #4b5563;
  --text: #e2e8f0;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  min-height: 100vh;
  overflow-x: hidden;
}

/* Background grain */
body::before {
  content: '';
  position: fixed; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
  pointer-events: none; z-index: 0;
}

.wrap { max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; position: relative; z-index: 1; }

/* Header */
header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 2rem; padding-bottom: 1.2rem;
  border-bottom: 1px solid var(--border);
}

.logo { display: flex; align-items: center; gap: 0.8rem; }
.logo-icon {
  width: 36px; height: 36px; background: var(--gold);
  border-radius: 8px; display: flex; align-items: center; justify-content: center;
  font-size: 1.1rem; color: #000;
}
.logo h1 { font-family: 'Syne', sans-serif; font-size: 1.2rem; font-weight: 800; color: var(--gold); }
.logo p  { font-size: 0.7rem; color: var(--muted); margin-top: 1px; }

.status-badge {
  display: flex; align-items: center; gap: 0.5rem;
  background: var(--surface); border: 1px solid var(--border);
  padding: 0.4rem 1rem; border-radius: 99px; font-size: 0.75rem;
}
.dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
  animation: pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* Stats grid */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 0.8rem; margin-bottom: 1.5rem;
}

.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.2rem;
  position: relative; overflow: hidden;
  transition: border-color 0.2s;
}
.stat:hover { border-color: var(--gold-dim); }
.stat::after {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--gold-dim), transparent);
}

.stat-label { font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.4rem; }
.stat-value { font-size: 1.3rem; font-weight: 700; color: var(--text); }
.stat-value.green { color: var(--green); }
.stat-value.red   { color: var(--red); }
.stat-value.gold  { color: var(--gold); }

/* Signal bar */
.signal-bar {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.8rem 1.2rem;
  margin-bottom: 1.5rem; font-size: 0.78rem;
  display: flex; align-items: center; gap: 0.8rem;
}
.signal-bar span { color: var(--muted); }
.signal-bar .sig { color: var(--gold); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Log panel */
.log-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 0.6rem;
}
.log-title { font-family: 'Syne', sans-serif; font-size: 0.85rem; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; }

.controls { display: flex; gap: 0.5rem; }
.btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text); padding: 0.3rem 0.8rem; border-radius: 6px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
  cursor: pointer; transition: all 0.15s;
}
.btn:hover { border-color: var(--gold); color: var(--gold); }
.btn.active { background: var(--gold); border-color: var(--gold); color: #000; font-weight: 700; }

.log-box {
  background: #050608; border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem;
  height: 480px; overflow-y: auto;
  font-size: 0.75rem; line-height: 1.8;
  scroll-behavior: smooth;
}
.log-box::-webkit-scrollbar { width: 4px; }
.log-box::-webkit-scrollbar-track { background: transparent; }
.log-box::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

.log-line { display: block; padding: 1px 0; }
.log-line.info    { color: #94a3b8; }
.log-line.error   { color: var(--red); }
.log-line.critical{ color: var(--red); font-weight: 700; }
.log-line.warning { color: #fb923c; }
.log-line.signal  { color: var(--gold); font-weight: 700; }
.log-line.success { color: var(--green); }
.log-line.stats   { color: var(--blue); }
.log-line.stop    { color: var(--red); font-weight: 700; background: rgba(239,68,68,0.08); border-radius: 4px; padding: 2px 6px; }

.ts { color: var(--muted); margin-right: 0.5rem; }

footer { margin-top: 1.5rem; text-align: center; font-size: 0.65rem; color: var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">
      <div class="logo-icon">🥇</div>
      <div>
        <h1>XAU/USD BOT</h1>
        <p>EMA 9/34/100 · ATR SL2x TP3x · OKX Demo</p>
      </div>
    </div>
    <div class="status-badge">
      <div class="dot" id="dot"></div>
      <span id="status-text">RUNNING</span>
    </div>
  </header>

  <div class="stats">
    <div class="stat"><div class="stat-label">Equity</div><div class="stat-value gold" id="s-equity">—</div></div>
    <div class="stat"><div class="stat-label">PnL</div><div class="stat-value" id="s-pnl">—</div></div>
    <div class="stat"><div class="stat-label">Trades</div><div class="stat-value" id="s-trades">—</div></div>
    <div class="stat"><div class="stat-label">Winrate</div><div class="stat-value" id="s-winrate">—</div></div>
    <div class="stat"><div class="stat-label">Drawdown</div><div class="stat-value" id="s-dd">—</div></div>
    <div class="stat"><div class="stat-label">Last update</div><div class="stat-value" style="font-size:0.85rem" id="s-update">—</div></div>
  </div>

  <div class="signal-bar">
    <span>🎯 Last signal:</span>
    <div class="sig" id="s-signal">Chưa có tín hiệu</div>
  </div>

  <div class="log-header">
    <div class="log-title">📋 Live Log</div>
    <div class="controls">
      <button class="btn active" id="btn-live" onclick="toggleLive()">● LIVE</button>
      <button class="btn" onclick="scrollBottom()">↓ Cuối</button>
      <button class="btn" onclick="clearLog()">✕ Xoá</button>
    </div>
  </div>
  <div class="log-box" id="log-box"></div>

  <footer>Bot XAU/USD Dashboard · Tự động refresh · <span id="log-file"></span></footer>
</div>

<script>
let liveMode = true;
let es = null;

function colorLine(line) {
  const el = document.createElement('span');
  el.className = 'log-line';
  const ts = line.slice(0, 19);
  const rest = line.slice(20);
  let cls = 'info';
  if (line.includes('[ERROR]') || line.includes('[CRITICAL]')) cls = 'error';
  if (line.includes('[CRITICAL]')) cls = 'critical';
  if (line.includes('[WARNING]')) cls = 'warning';
  if (line.includes('🎯 TÍN HIỆU')) cls = 'signal';
  if (line.includes('✅')) cls = 'success';
  if (line.includes('📊 Stats')) cls = 'stats';
  if (line.includes('🛑') || line.includes('Bot đã dừng')) cls = 'stop';
  el.classList.add(cls);
  el.innerHTML = `<span class="ts">${ts}</span>${rest}`;
  return el;
}

function appendLine(line) {
  if (!line.trim()) return;
  const box = document.getElementById('log-box');
  box.appendChild(colorLine(line));
  if (liveMode) box.scrollTop = box.scrollHeight;
  // Giới hạn 500 dòng để tránh lag
  while (box.children.length > 500) box.removeChild(box.firstChild);
}

function toggleLive() {
  liveMode = !liveMode;
  const btn = document.getElementById('btn-live');
  btn.classList.toggle('active', liveMode);
  btn.textContent = liveMode ? '● LIVE' : '○ PAUSED';
}

function scrollBottom() {
  const box = document.getElementById('log-box');
  box.scrollTop = box.scrollHeight;
}

function clearLog() {
  document.getElementById('log-box').innerHTML = '';
}

// Load log ban đầu
async function loadInitial() {
  const r = await fetch('/api/logs?n=200');
  const d = await r.json();
  const box = document.getElementById('log-box');
  box.innerHTML = '';
  d.lines.forEach(appendLine);
  document.getElementById('log-file').textContent = d.log_file || '';
}

// Load stats
async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('s-equity').textContent  = d.equity;
  document.getElementById('s-trades').textContent  = d.trades;
  document.getElementById('s-winrate').textContent = d.winrate;
  document.getElementById('s-update').textContent  = d.last_update;
  document.getElementById('s-signal').textContent  = d.last_signal || 'Chưa có tín hiệu';

  const pnlEl = document.getElementById('s-pnl');
  pnlEl.textContent = d.pnl;
  pnlEl.className = 'stat-value ' + (d.pnl.startsWith('+') ? 'green' : d.pnl.startsWith('-') ? 'red' : '');

  const ddEl = document.getElementById('s-dd');
  ddEl.textContent = d.drawdown;
  const ddNum = parseFloat(d.drawdown);
  ddEl.className = 'stat-value ' + (ddNum > 10 ? 'red' : ddNum > 5 ? 'warning' : 'green');

  const stopped = d.status === 'STOPPED';
  document.getElementById('dot').style.background = stopped ? 'var(--red)' : 'var(--green)';
  document.getElementById('dot').style.boxShadow  = stopped ? '0 0 6px var(--red)' : '0 0 6px var(--green)';
  document.getElementById('status-text').textContent = stopped ? 'STOPPED' : `RUNNING · ${d.mode}`;
}

// SSE realtime
function startSSE() {
  if (es) es.close();
  es = new EventSource('/api/stream');
  es.onmessage = e => appendLine(e.data);
  es.onerror   = () => setTimeout(startSSE, 3000);
}

// Khởi động
loadInitial().then(() => {
  scrollBottom();
  startSSE();
});
loadStats();
setInterval(loadStats, 15000);   // refresh stats mỗi 15 giây
</script>
</body>
</html>
"""