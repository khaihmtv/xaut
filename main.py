"""
FastAPI Dashboard – Multi-Coin Bot (XAU + SOL)
Truy cập: http://localhost:8000
Chạy:     uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import re
import glob
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
import asyncio

app = FastAPI(title="Multi-Coin Bot Dashboard")

COINS = ["XAU-USDT-SWAP", "SOL-USDT-SWAP"]

def get_latest_log() -> Path | None:
    patterns = ["logs/bot_multi_*.log", "logs/bot_*.log",
                "bot/logs/bot_multi_*.log", "*/logs/bot_multi_*.log"]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))

def read_log_lines(n: int = 300) -> list[str]:
    log_path = get_latest_log()
    if not log_path or not log_path.exists():
        return ["Chưa tìm thấy file log. Bot đã chạy chưa?"]
    with open(log_path, encoding="utf-8") as f:
        lines = f.readlines()
    return [l.rstrip() for l in lines[-n:]]

def parse_stats(lines: list[str]) -> dict:
    stats = {
        "equity": "—", "pnl": "—", "trades": "—",
        "winrate": "—", "drawdown": "—", "last_update": "—",
        "status": "RUNNING", "mode": "LIVE 🔴",
        "coins": {c: {"last_signal": "—"} for c in COINS},
    }
    for line in reversed(lines):
        if "📊 Stats" in line:
            eq  = re.search(r"Equity: \$([\d,.]+)", line)
            pnl = re.search(r"PnL: ([+\-\d.]+%)", line)
            tr  = re.search(r"Trades: (\d+)", line)
            wr  = re.search(r"WR: ([\d.]+%)", line)
            dd  = re.search(r"DD: ([\d.]+%)", line)
            if eq:  stats["equity"]   = f"${eq.group(1)}"
            if pnl: stats["pnl"]      = pnl.group(1)
            if tr:  stats["trades"]   = tr.group(1)
            if wr:  stats["winrate"]  = wr.group(1)
            if dd:  stats["drawdown"] = dd.group(1)
            stats["last_update"] = line[:19]
            break
    for coin in COINS:
        for line in reversed(lines):
            if f"[{coin}]" in line and "🎯" in line:
                stats["coins"][coin]["last_signal"] = line[20:] if len(line) > 20 else line
                break
    for line in reversed(lines):
        if "🛑" in line or "Bot đã dừng" in line:
            stats["status"] = "STOPPED"; break
    for line in lines[:30]:
        if "SIMULATED" in line:
            stats["mode"] = "DEMO 🔵"; break
    return stats

@app.get("/api/logs")
def api_logs(n: int = Query(default=300, le=1000)):
    lines = read_log_lines(n)
    return {"lines": lines, "count": len(lines), "log_file": str(get_latest_log())}

@app.get("/api/stats")
def api_stats():
    return parse_stats(read_log_lines(500))

@app.get("/api/stream")
async def stream_logs():
    log_path = get_latest_log()
    async def gen():
        if not log_path:
            yield "data: Chưa tìm thấy file log\n\n"; return
        with open(log_path, encoding="utf-8") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line: yield f"data: {line.rstrip()}\n\n"
                else: await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=HTML)

HTML = open("/mnt/user-data/outputs/_dashboard.html").read() if False else r"""<!DOCTYPE html>
<html lang="vi"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Multi-Coin Bot</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;700&family=Syne:wght@700;800&display=swap');
:root{--bg:#080a0e;--sur:#0f1218;--bor:#1e2330;--gold:#f5a623;--gdim:#7a5212;--sol:#9945ff;--sdim:#4a2280;--grn:#22c55e;--red:#ef4444;--blu:#38bdf8;--mut:#4b5563;--txt:#e2e8f0}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'JetBrains Mono',monospace;min-height:100vh}
.wrap{max-width:1280px;margin:0 auto;padding:2rem 1.5rem}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:2rem;padding-bottom:1.2rem;border-bottom:1px solid var(--bor)}
.logo{display:flex;align-items:center;gap:.8rem}
.logo-icon{width:36px;height:36px;background:linear-gradient(135deg,var(--gold),var(--sol));border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1rem}
.logo h1{font-family:'Syne',sans-serif;font-size:1.2rem;font-weight:800;background:linear-gradient(90deg,var(--gold),var(--sol));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo p{font-size:.68rem;color:var(--mut);margin-top:2px}
.badge{display:flex;align-items:center;gap:.5rem;background:var(--sur);border:1px solid var(--bor);padding:.4rem 1rem;border-radius:99px;font-size:.75rem}
.dot{width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 6px var(--grn);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:.8rem;margin-bottom:1.5rem}
.stat{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:1rem 1.2rem;position:relative;overflow:hidden}
.stat::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--gdim),transparent)}
.slabel{font-size:.63rem;color:var(--mut);text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem}
.sval{font-size:1.2rem;font-weight:700}
.gold{color:var(--gold)}.grn{color:var(--grn)}.red{color:var(--red)}
.coins-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem}
@media(max-width:640px){.coins-grid{grid-template-columns:1fr}}
.ccard{background:var(--sur);border:1px solid var(--bor);border-radius:12px;padding:1.2rem}
.ccard.xau{border-top:2px solid var(--gdim)}.ccard.sol{border-top:2px solid var(--sdim)}
.cheader{display:flex;align-items:center;gap:.6rem;margin-bottom:.8rem}
.ctag{padding:.2rem .7rem;border-radius:6px;font-size:.72rem;font-weight:700}
.ctag.xau{background:rgba(245,166,35,.15);color:var(--gold)}
.ctag.sol{background:rgba(153,69,255,.15);color:var(--sol)}
.cparams{font-size:.63rem;color:var(--mut)}
.csiglabel{font-size:.6rem;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.3rem}
.csig{font-size:.72rem;background:rgba(255,255,255,.03);border-radius:6px;padding:.5rem .7rem;min-height:2.4rem;word-break:break-all}
.log-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem}
.log-title{font-family:'Syne',sans-serif;font-size:.85rem;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.1em}
.ctrls{display:flex;gap:.5rem}
.btn{background:var(--sur);border:1px solid var(--bor);color:var(--txt);padding:.3rem .8rem;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:.72rem;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--gold);color:var(--gold)}
.btn.active{background:var(--gold);border-color:var(--gold);color:#000;font-weight:700}
.fbtns{display:flex;gap:.4rem;margin-bottom:.5rem}
.fbtn{background:var(--sur);border:1px solid var(--bor);color:var(--mut);padding:.25rem .7rem;border-radius:6px;font-size:.68rem;cursor:pointer;transition:all .15s}
.on-all{border-color:var(--blu)!important;color:var(--blu)!important;background:rgba(56,189,248,.08)!important}
.on-xau{border-color:var(--gold)!important;color:var(--gold)!important;background:rgba(245,166,35,.08)!important}
.on-sol{border-color:var(--sol)!important;color:var(--sol)!important;background:rgba(153,69,255,.08)!important}
.log-box{background:#050608;border:1px solid var(--bor);border-radius:10px;padding:1rem;height:460px;overflow-y:auto;font-size:.74rem;line-height:1.8}
.log-box::-webkit-scrollbar{width:4px}
.log-box::-webkit-scrollbar-thumb{background:var(--bor);border-radius:2px}
.ll{display:block;padding:1px 0}
.ll.info{color:#94a3b8}.ll.warn{color:#fb923c}.ll.err{color:var(--red)}.ll.crit{color:var(--red);font-weight:700}
.ll.sig{color:var(--gold);font-weight:700}.ll.sig-sol{color:var(--sol);font-weight:700}
.ll.ok{color:var(--grn)}.ll.stats{color:var(--blu)}
.ll.stop{color:var(--red);font-weight:700;background:rgba(239,68,68,.08);border-radius:4px;padding:2px 6px}
.txau{color:var(--gold)}.tsol{color:var(--sol)}.ts{color:var(--mut);margin-right:.4rem}
footer{margin-top:1.5rem;text-align:center;font-size:.63rem;color:var(--mut)}
</style></head><body>
<div class="wrap">
  <header>
    <div class="logo">
      <div class="logo-icon">⚡</div>
      <div><h1>MULTI-COIN BOT</h1><p>XAU · SOL · EMA Crossover · OKX · x3 Leverage</p></div>
    </div>
    <div class="badge"><div class="dot" id="dot"></div><span id="stxt">RUNNING</span></div>
  </header>

  <div class="stats-row">
    <div class="stat"><div class="slabel">Equity</div><div class="sval gold" id="s-eq">—</div></div>
    <div class="stat"><div class="slabel">PnL</div><div class="sval" id="s-pnl">—</div></div>
    <div class="stat"><div class="slabel">Tổng lệnh</div><div class="sval" id="s-tr">—</div></div>
    <div class="stat"><div class="slabel">Winrate</div><div class="sval" id="s-wr">—</div></div>
    <div class="stat"><div class="slabel">Drawdown</div><div class="sval" id="s-dd">—</div></div>
    <div class="stat"><div class="slabel">Last update</div><div class="sval" style="font-size:.8rem" id="s-up">—</div></div>
  </div>

  <div class="coins-grid">
    <div class="ccard xau">
      <div class="cheader"><div class="ctag xau">XAU-USDT-SWAP</div><div class="cparams">EMA 15/26/80 · SL 2.5x · TP 4.0x</div></div>
      <div class="csiglabel">Last Signal</div>
      <div class="csig" id="sig-xau">Chưa có tín hiệu</div>
    </div>
    <div class="ccard sol">
      <div class="cheader"><div class="ctag sol">SOL-USDT-SWAP</div><div class="cparams">EMA 21/50/80 · SL 1.5x · TP 4.0x</div></div>
      <div class="csiglabel">Last Signal</div>
      <div class="csig" id="sig-sol">Chưa có tín hiệu</div>
    </div>
  </div>

  <div class="log-hdr">
    <div class="log-title">📋 Live Log</div>
    <div class="ctrls">
      <button class="btn active" id="btn-live" onclick="toggleLive()">● LIVE</button>
      <button class="btn" onclick="scrollBot()">↓ Cuối</button>
      <button class="btn" onclick="clearLog()">✕ Xoá</button>
    </div>
  </div>
  <div class="fbtns">
    <button class="fbtn on-all" id="f-all" onclick="setFilter('all')">Tất cả</button>
    <button class="fbtn" id="f-xau" onclick="setFilter('xau')">🥇 XAU</button>
    <button class="fbtn" id="f-sol" onclick="setFilter('sol')">◎ SOL</button>
  </div>
  <div class="log-box" id="log-box"></div>
  <footer>Multi-Coin Bot Dashboard · Auto refresh 15s · <span id="lf"></span></footer>
</div>
<script>
let live=true,es=null,filter='all';
function setFilter(c){
  filter=c;
  ['all','xau','sol'].forEach(k=>{
    document.getElementById('f-'+k).className='fbtn'+(k===c?' on-'+c:'');
  });
  document.querySelectorAll('.ll').forEach(el=>{
    el.style.display=showLine(el.dataset.coin)?'':'none';
  });
}
function showLine(coin){
  if(filter==='all')return true;
  if(!coin)return filter==='all';
  return coin===filter;
}
function mkLine(line){
  const el=document.createElement('span');
  el.className='ll';
  const ts=line.slice(0,19),rest=line.slice(20);
  let coin='';
  if(line.includes('[XAU-USDT-SWAP]'))coin='xau';
  else if(line.includes('[SOL-USDT-SWAP]'))coin='sol';
  el.dataset.coin=coin;
  let cls='info';
  if(line.includes('[ERROR]')||line.includes('[CRITICAL]'))cls='err';
  if(line.includes('[CRITICAL]'))cls='crit';
  if(line.includes('[WARNING]'))cls='warn';
  if(line.includes('🎯')&&coin==='xau')cls='sig';
  if(line.includes('🎯')&&coin==='sol')cls='sig-sol';
  if(line.includes('✅'))cls='ok';
  if(line.includes('📊 Stats'))cls='stats';
  if(line.includes('🛑')||line.includes('Bot đã dừng'))cls='stop';
  el.classList.add(cls);
  const colored=rest
    .replace('[XAU-USDT-SWAP]','<span class="txau">[XAU]</span>')
    .replace('[SOL-USDT-SWAP]','<span class="tsol">[SOL]</span>');
  el.innerHTML=`<span class="ts">${ts}</span>${colored}`;
  el.style.display=showLine(coin)?'':'none';
  return el;
}
function append(line){
  if(!line.trim())return;
  const b=document.getElementById('log-box');
  b.appendChild(mkLine(line));
  if(live)b.scrollTop=b.scrollHeight;
  while(b.children.length>600)b.removeChild(b.firstChild);
}
function toggleLive(){
  live=!live;
  const b=document.getElementById('btn-live');
  b.classList.toggle('active',live);
  b.textContent=live?'● LIVE':'○ PAUSED';
}
function scrollBot(){document.getElementById('log-box').scrollTop=999999;}
function clearLog(){document.getElementById('log-box').innerHTML='';}
async function loadInitial(){
  const r=await fetch('/api/logs?n=300');
  const d=await r.json();
  document.getElementById('log-box').innerHTML='';
  d.lines.forEach(append);
  document.getElementById('lf').textContent=d.log_file||'';
  scrollBot();
}
async function loadStats(){
  const r=await fetch('/api/stats');
  const d=await r.json();
  document.getElementById('s-eq').textContent=d.equity;
  document.getElementById('s-tr').textContent=d.trades;
  document.getElementById('s-wr').textContent=d.winrate;
  document.getElementById('s-up').textContent=d.last_update;
  const pe=document.getElementById('s-pnl');
  pe.textContent=d.pnl;
  pe.className='sval '+(d.pnl&&d.pnl.startsWith('+')?'grn':d.pnl&&d.pnl.startsWith('-')?'red':'');
  const de=document.getElementById('s-dd');
  de.textContent=d.drawdown;
  const dn=parseFloat(d.drawdown)||0;
  de.className='sval '+(dn>10?'red':dn>5?'':'grn');
  const stopped=d.status==='STOPPED';
  const dot=document.getElementById('dot');
  dot.style.background=stopped?'var(--red)':'var(--grn)';
  dot.style.boxShadow=stopped?'0 0 6px var(--red)':'0 0 6px var(--grn)';
  document.getElementById('stxt').textContent=stopped?'STOPPED':'RUNNING · '+d.mode;
  if(d.coins){
    const x=d.coins['XAU-USDT-SWAP'],s=d.coins['SOL-USDT-SWAP'];
    if(x)document.getElementById('sig-xau').textContent=x.last_signal!=='—'?x.last_signal:'Chưa có tín hiệu';
    if(s)document.getElementById('sig-sol').textContent=s.last_signal!=='—'?s.last_signal:'Chưa có tín hiệu';
  }
}
function startSSE(){
  if(es)es.close();
  es=new EventSource('/api/stream');
  es.onmessage=e=>append(e.data);
  es.onerror=()=>setTimeout(startSSE,3000);
}
loadInitial().then(startSSE);
loadStats();
setInterval(loadStats,15000);
</script></body></html>"""