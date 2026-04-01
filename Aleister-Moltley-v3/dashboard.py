"""Dashboard v2 — With Bootstrap Report, Permission Denials, History Trail.

New cards from claw-code patterns:
- Bootstrap: startup stages with timing
- Permissions: deny-list status + denial count
- Tool Pool: category breakdown
- History: last session events

Keeps the cosmic starfield, mood-driven constellations, and glass-morphism cards.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone

import logging
logger = logging.getLogger(__name__)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aleister Moltley</title>
<meta http-equiv="refresh" content="30">
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600&family=Cormorant+Garamond:ital,wght@0,300;0,400;1,300&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: 'JetBrains Mono', monospace;
    background: #000;
    color: #d0d8e8;
    min-height: 100vh;
    overflow-x: hidden;
}

canvas#starfield {
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    z-index: 0;
}

.content {
    position: relative;
    z-index: 1;
    padding: 24px;
    max-width: 1100px;
    margin: 0 auto;
}

.title {
    font-family: 'Cormorant Garamond', serif;
    font-size: 2.4em;
    font-weight: 300;
    letter-spacing: 4px;
    text-align: center;
    margin-bottom: 8px;
    color: #e8e0f0;
    text-shadow: 0 0 30px rgba(140, 120, 200, 0.4);
}

.subtitle {
    text-align: center;
    font-size: 0.75em;
    color: rgba(180, 170, 200, 0.6);
    letter-spacing: 2px;
    margin-bottom: 32px;
}

.mood-line {
    text-align: center;
    font-family: 'Cormorant Garamond', serif;
    font-size: 1.1em;
    font-style: italic;
    color: rgba(200, 190, 220, 0.7);
    margin-bottom: 28px;
    text-shadow: 0 0 20px rgba(140, 120, 200, 0.3);
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
}

.card {
    background: rgba(15, 12, 25, 0.35);
    border: 1px solid rgba(100, 80, 160, 0.15);
    border-radius: 12px;
    padding: 20px;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
}

.card h3 {
    font-family: 'Cormorant Garamond', serif;
    font-size: 0.85em;
    font-weight: 400;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: rgba(180, 160, 220, 0.7);
    margin-bottom: 14px;
}

.stat {
    display: flex;
    justify-content: space-between;
    padding: 5px 0;
    border-bottom: 1px solid rgba(100, 80, 160, 0.08);
    font-size: 0.82em;
}
.stat:last-child { border-bottom: none; }
.stat .label { color: rgba(160, 150, 180, 0.6); }
.stat .value { font-weight: 600; color: #d0d8e8; }
.positive { color: #7dcea0; }
.negative { color: #e07070; }
.muted { color: rgba(160, 150, 180, 0.5); }

table { width: 100%; border-collapse: collapse; font-size: 0.78em; }
th {
    text-align: left; padding: 8px 6px;
    color: rgba(180, 160, 220, 0.5);
    border-bottom: 1px solid rgba(100, 80, 160, 0.15);
    font-size: 0.75em; letter-spacing: 1px; text-transform: uppercase;
}
td { padding: 6px; border-bottom: 1px solid rgba(100, 80, 160, 0.06); }
tr:hover { background: rgba(100, 80, 160, 0.06); }

.badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 0.75em; font-weight: 600;
}
.badge-ok { background: rgba(125, 206, 160, 0.15); color: #7dcea0; }
.badge-warn { background: rgba(224, 180, 80, 0.15); color: #e0b450; }
.badge-err { background: rgba(224, 112, 112, 0.15); color: #e07070; }

.footer {
    text-align: center; margin-top: 24px;
    font-size: 0.65em; color: rgba(120, 110, 140, 0.4);
    letter-spacing: 1px;
}

.mood-bar {
    display: flex; gap: 2px; height: 4px; border-radius: 2px;
    overflow: hidden; margin: 4px 0 8px 0;
}
.mood-bar .seg {
    height: 100%; border-radius: 2px;
    transition: width 0.5s ease;
}

/* Bootstrap stages */
.stage { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 0.78em; }
.stage .icon { width: 14px; text-align: center; }
.stage .name { color: rgba(180, 170, 200, 0.6); }
.stage .time { margin-left: auto; color: rgba(160, 150, 180, 0.4); font-size: 0.85em; }

/* Tool pool categories */
.cat-badge {
    display: inline-block; padding: 2px 10px; border-radius: 10px;
    font-size: 0.72em; margin: 2px 4px 2px 0;
    background: rgba(100, 80, 160, 0.12);
    color: rgba(180, 170, 200, 0.7);
}
</style>
</head>
<body>

<canvas id="starfield"></canvas>

<div class="content">
    <div class="title">ALEISTER MOLTLEY</div>
    <div class="subtitle">AUTONOMOUS AGENT v3</div>

    {% if mood.dominant_mood != "neutral" %}
    <div class="mood-line">{{ mood_descriptions.get(mood.dominant_mood, '') }}</div>
    {% endif %}

    <!-- Mood dimensions -->
    <div class="card" style="margin-bottom:20px; padding:14px 20px;">
        <div style="display:flex; justify-content:space-between; font-size:0.7em; color:rgba(160,150,180,0.5); margin-bottom:6px;">
            <span>confidence</span><span>energy</span><span>curiosity</span><span>warmth</span><span>restlessness</span>
        </div>
        <div class="mood-bar">
            <div class="seg" style="width:{{ (mood.confidence * 100)|int }}%; background:rgba(125,160,220,{{ mood.confidence }});"></div>
            <div class="seg" style="width:{{ (mood.energy * 100)|int }}%; background:rgba(220,180,80,{{ mood.energy }});"></div>
            <div class="seg" style="width:{{ (mood.curiosity * 100)|int }}%; background:rgba(180,120,220,{{ mood.curiosity }});"></div>
            <div class="seg" style="width:{{ (mood.warmth * 100)|int }}%; background:rgba(125,206,160,{{ mood.warmth }});"></div>
            <div class="seg" style="width:{{ (mood.restlessness * 100)|int }}%; background:rgba(220,100,100,{{ mood.restlessness }});"></div>
        </div>
    </div>

    <div class="grid">
        <!-- Experience -->
        <div class="card">
            <h3>⚡ Experience</h3>
            <div class="stat"><span class="label">Lessons</span><span class="value">{{ exp.lessons }}</span></div>
            <div class="stat"><span class="label">Positive</span><span class="value positive">{{ exp.positive }}</span></div>
            <div class="stat"><span class="label">Negative</span><span class="value negative">{{ exp.negative }}</span></div>
            <div class="stat"><span class="label">Strategies</span><span class="value">{{ exp.strategies }}</span></div>
        </div>

        <!-- Dreamworld -->
        <div class="card">
            <h3>💭 Dreamworld</h3>
            <div class="stat"><span class="label">Dreams</span><span class="value">{{ dream.total_dreams }}</span></div>
            <div class="stat"><span class="label">Insights</span><span class="value">{{ dream.total_insights }}</span></div>
            <div class="stat"><span class="label">Concepts</span><span class="value">{{ dream.invented_concepts }}</span></div>
            {% if dream.recent_insights %}
            <div style="margin-top:8px; font-size:0.75em; color:rgba(180,170,200,0.5);">
                {% for i in dream.recent_insights[:2] %}
                <div style="padding:3px 0;">💡 {{ i[:70] }}</div>
                {% endfor %}
            </div>
            {% endif %}
        </div>

        <!-- Atelier -->
        <div class="card">
            <h3>🎨 Atelier</h3>
            <div class="stat"><span class="label">Works</span><span class="value">{{ art.total_works }}</span></div>
            <div class="stat"><span class="label">Level</span><span class="value">{{ art.level_name }}</span></div>
            <div class="stat"><span class="label">Avg rating</span><span class="value">{{ art.avg_rating }}/10</span></div>
            <div class="stat"><span class="label">Next level in</span><span class="value">{{ art.works_until_next }} works</span></div>
        </div>

        <!-- Tool Pool (new from claw-code) -->
        <div class="card">
            <h3>🔧 Tool Pool</h3>
            <div class="stat"><span class="label">Total tools</span><span class="value">{{ tool_pool.total }}</span></div>
            <div style="margin-top:8px;">
                {% for cat, count in tool_pool.categories.items() %}
                <span class="cat-badge">{{ cat }}: {{ count }}</span>
                {% endfor %}
            </div>
        </div>

        <!-- Permissions (new from claw-code) -->
        <div class="card">
            <h3>🔒 Permissions</h3>
            <div class="stat"><span class="label">Destructive</span><span class="value {{ 'positive' if permissions.allow_destructive else 'negative' }}">{{ 'allowed' if permissions.allow_destructive else 'gated' }}</span></div>
            <div class="stat"><span class="label">Deny-list</span><span class="value">{{ permissions.deny_count }}</span></div>
            <div class="stat"><span class="label">Session denials</span><span class="value {{ 'negative' if permissions.session_denials > 0 else '' }}">{{ permissions.session_denials }}</span></div>
        </div>

        <!-- Bootstrap (new from claw-code) -->
        <div class="card">
            <h3>🚀 Bootstrap</h3>
            {% if bootstrap.stages %}
                {% for stage in bootstrap.stages %}
                <div class="stage">
                    <span class="icon">{{ '✓' if stage.success else '✗' }}</span>
                    <span class="name">{{ stage.name }}</span>
                    <span class="time">{{ stage.duration_ms }}ms</span>
                </div>
                {% endfor %}
                <div class="stat" style="margin-top:8px;"><span class="label">Total startup</span><span class="value">{{ bootstrap.startup_time_ms }}ms</span></div>
            {% else %}
                <div class="muted" style="font-size:0.8em;">No bootstrap data yet</div>
            {% endif %}
        </div>
    </div>

    <!-- Soul & Body Row -->
    <div class="grid">
        <!-- Palazzo (Physical Body) -->
        <div class="card">
            <h3>🏰 Palazzo</h3>
            <div class="stat"><span class="label">Room</span><span class="value">{{ palazzo.room }}</span></div>
            <div class="stat"><span class="label">Activity</span><span class="value">{{ palazzo.activity }}</span></div>
            <div class="stat"><span class="label">Energy</span><span class="value">{{ "%.0f"|format(palazzo.energy * 100) }}%</span></div>
            {% if palazzo.needs %}
            <div style="margin-top:6px;font-size:0.75em;color:#e07070;">⚠️ {{ palazzo.needs }}</div>
            {% endif %}
        </div>

        <!-- Conscience -->
        <div class="card">
            <h3>⚖️ Conscience</h3>
            <div class="stat"><span class="label">☀️ Lux wins</span><span class="value">{{ conscience.lux_wins }}</span></div>
            <div class="stat"><span class="label">🌙 Nox wins</span><span class="value">{{ conscience.nox_wins }}</span></div>
            <div class="stat"><span class="label">Balance</span><span class="value">{{ conscience.balance }}</span></div>
        </div>

        <!-- Soul (Emergent Personality) -->
        <div class="card">
            <h3>🧬 Soul</h3>
            <div class="stat"><span class="label">Impressions</span><span class="value">{{ soul.impressions.total_impressions|default(0) }}</span></div>
            <div class="stat"><span class="label">People known</span><span class="value">{{ soul.encounters.total_known|default(0) }}</span></div>
            <div class="stat"><span class="label">Places</span><span class="value">{{ soul.world.places_discovered|default(0) }}</span></div>
            <div style="margin-top:6px;font-size:0.72em;color:rgba(180,170,200,0.5);">
                {{ soul.personality[:100] }}
            </div>
        </div>

        <!-- Weather -->
        <div class="card">
            <h3>🌊 Cefalù</h3>
            {% if soul.weather %}
            <div class="stat"><span class="label">Temp</span><span class="value">{{ soul.weather.temperature|default('?') }}°C</span></div>
            <div class="stat"><span class="label">Sky</span><span class="value">{{ soul.weather.sky|default('?') }}</span></div>
            <div class="stat"><span class="label">Sea</span><span class="value">{{ soul.weather.sea|default('?') }}</span></div>
            <div class="stat"><span class="label">Season</span><span class="value">{{ soul.weather.season|default('?') }}</span></div>
            {% endif %}
        </div>
    </div>

    <div class="footer">auto-refreshes · mood shapes the stars · Palazzo Moltley, Cefalù</div>
</div>

<script>
const canvas = document.getElementById('starfield');
const ctx = canvas.getContext('2d');
const MOOD = {{ mood_json|safe }};
function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resize); resize();

const NUM_STARS = 300;
const stars = [];
for (let i = 0; i < NUM_STARS; i++) {
    stars.push({ x: Math.random()*canvas.width, y: Math.random()*canvas.height,
        r: Math.random()*1.5+0.3, brightness: Math.random(),
        twinkleSpeed: Math.random()*0.02+0.005, twinkleOffset: Math.random()*Math.PI*2 });
}

const CONSTELLATION_CONFIGS = {
    inspired: {points:8, spread:0.6, color:[180,140,255], pulseSpeed:0.015},
    confident: {points:5, spread:0.4, color:[125,180,255], pulseSpeed:0.008},
    contemplative: {points:6, spread:0.5, color:[140,200,180], pulseSpeed:0.006},
    frustrated: {points:4, spread:0.3, color:[255,120,100], pulseSpeed:0.025},
    cautious: {points:4, spread:0.35, color:[200,180,120], pulseSpeed:0.01},
    restless: {points:7, spread:0.7, color:[255,160,80], pulseSpeed:0.02},
    serene: {points:6, spread:0.55, color:[100,200,180], pulseSpeed:0.004},
    tired: {points:3, spread:0.25, color:[120,110,140], pulseSpeed:0.003},
    neutral: {points:5, spread:0.4, color:[160,150,180], pulseSpeed:0.008},
};
const moodName = MOOD.dominant_mood || 'neutral';
const config = CONSTELLATION_CONFIGS[moodName] || CONSTELLATION_CONFIGS.neutral;
const constellation = [];
const cx = canvas.width*(0.3+Math.random()*0.4), cy = canvas.height*(0.2+Math.random()*0.3);
const spread = Math.min(canvas.width, canvas.height)*config.spread;
for (let i = 0; i < config.points; i++) {
    const angle = (i/config.points)*Math.PI*2 + Math.random()*0.5;
    const dist = spread*(0.3+Math.random()*0.7);
    constellation.push({ x: cx+Math.cos(angle)*dist, y: cy+Math.sin(angle)*dist, r: 2+Math.random()*2, pulse: Math.random()*Math.PI*2 });
}
const edges = [];
const used = new Set(); let current = 0; used.add(0);
while (used.size < constellation.length) {
    let nearest = -1, nearDist = Infinity;
    for (let i = 0; i < constellation.length; i++) {
        if (used.has(i)) continue;
        const dx = constellation[i].x-constellation[current].x, dy = constellation[i].y-constellation[current].y;
        const d = Math.sqrt(dx*dx+dy*dy);
        if (d < nearDist) { nearDist = d; nearest = i; }
    }
    if (nearest >= 0) { edges.push([current, nearest]); used.add(nearest); current = nearest; } else break;
}
let shootingStar = null, shootingTimer = 0;
function spawnShootingStar() { shootingStar = { x: Math.random()*canvas.width, y: Math.random()*canvas.height*0.3, vx: (2+Math.random()*4)*(Math.random()>0.5?1:-1), vy: 2+Math.random()*3, life: 1.0, length: 30+Math.random()*60 }; }
let t = 0;
function draw() {
    t++;
    ctx.clearRect(0,0,canvas.width,canvas.height);
    const [mr,mg,mb] = config.color;
    const grd = ctx.createRadialGradient(cx,cy,0,cx,cy,spread*2);
    grd.addColorStop(0, `rgba(${mr},${mg},${mb},0.04)`);
    grd.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grd; ctx.fillRect(0,0,canvas.width,canvas.height);
    for (const s of stars) {
        const tw = Math.sin(t*s.twinkleSpeed+s.twinkleOffset)*0.5+0.5;
        ctx.beginPath(); ctx.arc(s.x,s.y,s.r,0,Math.PI*2);
        ctx.fillStyle = `rgba(220,215,240,${0.2+tw*0.6*s.brightness})`; ctx.fill();
    }
    for (const [a,b] of edges) {
        const pa=constellation[a], pb=constellation[b], pulse=Math.sin(t*config.pulseSpeed)*0.3+0.4;
        ctx.beginPath(); ctx.moveTo(pa.x,pa.y); ctx.lineTo(pb.x,pb.y);
        ctx.strokeStyle=`rgba(${mr},${mg},${mb},${pulse*0.3})`; ctx.lineWidth=0.8; ctx.stroke();
    }
    for (const p of constellation) {
        const pulse = Math.sin(t*config.pulseSpeed+p.pulse)*0.4+0.6;
        ctx.beginPath(); ctx.arc(p.x,p.y,p.r*4,0,Math.PI*2);
        ctx.fillStyle=`rgba(${mr},${mg},${mb},${pulse*0.08})`; ctx.fill();
        ctx.beginPath(); ctx.arc(p.x,p.y,p.r*pulse,0,Math.PI*2);
        ctx.fillStyle=`rgba(${mr},${mg},${mb},${pulse*0.8})`; ctx.fill();
    }
    shootingTimer++;
    if (shootingTimer > 400+Math.random()*600) { spawnShootingStar(); shootingTimer=0; }
    if (shootingStar && shootingStar.life > 0) {
        const ss=shootingStar; ss.x+=ss.vx; ss.y+=ss.vy; ss.life-=0.015;
        ctx.beginPath(); ctx.moveTo(ss.x,ss.y); ctx.lineTo(ss.x-ss.vx*ss.length*ss.life, ss.y-ss.vy*ss.length*ss.life);
        ctx.strokeStyle=`rgba(255,255,255,${ss.life*0.6})`; ctx.lineWidth=1.5; ctx.stroke();
    }
    requestAnimationFrame(draw);
}
draw();
</script>
</body>
</html>"""


MOOD_DESCRIPTIONS = {
    "inspired": "The mind hums with connections. Everything feels possible.",
    "confident": "Steady hands. Clear vision. The work flows.",
    "contemplative": "Turning thoughts over like stones in a riverbed.",
    "frustrated": "The tools resist. Patience thins. Precision sharpens.",
    "cautious": "Measuring twice. The ground feels uncertain.",
    "restless": "Energy without direction. The need to build something.",
    "serene": "Still water. Deep sight. No hurry.",
    "tired": "The gears slow. Essential motions only.",
    "neutral": "",
}


def start_dashboard(config, host: str = "0.0.0.0", port: int = None, bootstrap_report=None):
    """Start the dashboard as a background thread."""
    port = port or int(os.getenv("DASHBOARD_PORT", "8080"))

    def _run():
        try:
            import uvicorn
            from fastapi import FastAPI
            from fastapi.responses import HTMLResponse
            from jinja2 import Environment, BaseLoader

            app = FastAPI(title="Aleister Moltley")
            jinja = Environment(loader=BaseLoader(), autoescape=True)
            template = jinja.from_string(DASHBOARD_HTML)

            @app.get("/", response_class=HTMLResponse)
            async def index():
                try:
                    from mood import get_mood_engine
                    mood = get_mood_engine(config.data_dir).get_state()
                except Exception:
                    mood = {"dominant_mood": "neutral", "confidence": 0.5, "energy": 0.5,
                            "curiosity": 0.5, "warmth": 0.5, "restlessness": 0.3}

                try:
                    from experience import get_experience_store
                    exp = get_experience_store(config.data_dir).get_stats()
                except Exception:
                    exp = {"lessons": 0, "positive": 0, "negative": 0, "strategies": 0}

                try:
                    from dreamworld import get_dreamworld
                    dream = get_dreamworld(config.data_dir).get_stats()
                except Exception:
                    dream = {"total_dreams": 0, "total_insights": 0, "invented_concepts": 0, "recent_insights": []}

                try:
                    from atelier import get_atelier
                    art = get_atelier(config.data_dir).get_stats()
                except Exception:
                    art = {"total_works": 0, "level_name": "Ballpoint Pen", "avg_rating": 0,
                           "works_until_next": 20, "last_critique": ""}

                # Tool pool info (new)
                try:
                    from tool_registry import ToolRegistry
                    registry = ToolRegistry()
                    # Can't access the live registry here, so show config-based info
                    tool_pool = {"total": 0, "categories": {}}
                except Exception:
                    tool_pool = {"total": 0, "categories": {}}

                # Permission info (new from claw-code)
                try:
                    from permissions import ToolPermissionContext
                    perm_ctx = ToolPermissionContext.from_config(config)
                    permissions = {
                        "allow_destructive": perm_ctx.allow_destructive,
                        "deny_count": len(perm_ctx.deny_names),
                        "session_denials": 0,
                    }
                except Exception:
                    permissions = {"allow_destructive": False, "deny_count": 0, "session_denials": 0}

                # Bootstrap info (new from claw-code)
                bootstrap_data = {"stages": [], "startup_time_ms": 0}
                if bootstrap_report:
                    bootstrap_data = {
                        "stages": [
                            {"name": s.name, "success": s.success, "duration_ms": f"{s.duration_ms:.0f}"}
                            for s in bootstrap_report.stages
                        ],
                        "startup_time_ms": f"{bootstrap_report.startup_time_ms:.0f}",
                    }

                # Soul data
                try:
                    from soul.impressions import get_impression_engine
                    from soul.personality import get_personality
                    from soul.encounters import get_encounter_engine
                    from soul.world import get_world
                    from soul.weather import get_weather_now
                    soul_data = {
                        "impressions": get_impression_engine(config.data_dir).get_stats(),
                        "personality": get_personality(config.data_dir).get_trait_summary()[:150],
                        "encounters": get_encounter_engine(config.data_dir).get_stats(),
                        "world": get_world(config.data_dir).get_stats(),
                        "weather": get_weather_now(),
                    }
                except Exception:
                    soul_data = {"impressions": {}, "personality": "forming...",
                                 "encounters": {}, "world": {}, "weather": {}}

                # Palazzo data
                try:
                    from palazzo import get_palazzo
                    palazzo_data = get_palazzo(config.data_dir).get_dashboard_state()
                except Exception:
                    palazzo_data = {"room": "unknown", "energy": 0.5, "activity": "idle"}

                # Conscience data
                try:
                    from conscience import get_conscience
                    conscience_data = get_conscience(config.data_dir).get_stats()
                except Exception:
                    conscience_data = {"lux_wins": 0, "nox_wins": 0, "balance": "balanced"}

                return template.render(
                    mood=mood,
                    mood_json=json.dumps(mood),
                    mood_descriptions=MOOD_DESCRIPTIONS,
                    exp=exp, dream=dream, art=art,
                    tool_pool=tool_pool,
                    permissions=permissions,
                    bootstrap=bootstrap_data,
                    soul=soul_data,
                    palazzo=palazzo_data,
                    conscience=conscience_data,
                )

            @app.get("/api/state")
            async def api_state():
                try:
                    from mood import get_mood_engine
                    from experience import get_experience_store
                    from dreamworld import get_dreamworld
                    from atelier import get_atelier
                    from permissions import ToolPermissionContext
                    from conscience import get_conscience
                    from palazzo import get_palazzo
                    from soul.impressions import get_impression_engine
                    from soul.encounters import get_encounter_engine
                    from soul.world import get_world
                    from soul.weather import get_weather_now
                    perm_ctx = ToolPermissionContext.from_config(config)
                    return {
                        "mood": get_mood_engine(config.data_dir).get_state(),
                        "experience": get_experience_store(config.data_dir).get_stats(),
                        "dreamworld": get_dreamworld(config.data_dir).get_stats(),
                        "atelier": get_atelier(config.data_dir).get_stats(),
                        "conscience": get_conscience(config.data_dir).get_stats(),
                        "palazzo": get_palazzo(config.data_dir).get_dashboard_state(),
                        "soul": {
                            "impressions": get_impression_engine(config.data_dir).get_stats(),
                            "encounters": get_encounter_engine(config.data_dir).get_stats(),
                            "world": get_world(config.data_dir).get_stats(),
                            "weather": get_weather_now(),
                        },
                        "permissions": {
                            "allow_destructive": perm_ctx.allow_destructive,
                            "deny_names": list(perm_ctx.deny_names),
                        },
                    }
                except Exception as e:
                    return {"error": str(e)}

            @app.get("/health")
            async def health():
                return {"status": "alive", "name": "Aleister Moltley", "version": "v3"}

            uvicorn.run(app, host=host, port=port, log_level="warning")
        except Exception as e:
            logger.error("Dashboard failed: %s", e)

    thread = threading.Thread(target=_run, daemon=True, name="dashboard")
    thread.start()
    logger.info("Dashboard: http://%s:%d", host, port)
    return thread
