"""
Weather — Cefalù, Sicily. Seasonal, time-seeded, deterministic-per-day.
"""
from __future__ import annotations
import hashlib
import math
import random
import time
from datetime import datetime, timezone, timedelta

TZ_SICILY = timezone(timedelta(hours=2))

MONTHLY_WEATHER = {
    1:  (14, 8,  10, 15, 9.5,  "cool, grey, occasional storms from the Tyrrhenian"),
    2:  (14, 8,  9,  14, 10.5, "slightly warmer, almond trees beginning to blossom"),
    3:  (16, 9,  8,  14, 12,   "spring arriving, wildflowers on the Rocca"),
    4:  (18, 11, 6,  16, 13.5, "warm, clear, the gardens explode with green"),
    5:  (22, 14, 4,  18, 14.5, "perfect — warm days, cool evenings, jasmine blooming"),
    6:  (26, 18, 2,  22, 15,   "summer heat building, the sea becomes swimmable"),
    7:  (29, 21, 1,  25, 14.5, "hot, blazing, the stones radiate heat past midnight"),
    8:  (30, 22, 2,  27, 14,   "the peak — scorching days, warm nights, the sea is bath-warm"),
    9:  (27, 19, 5,  25, 12.5, "the heat breaks slowly, light becomes golden"),
    10: (23, 16, 8,  22, 11,   "autumn — shorter days, first rains, the olives ripen"),
    11: (18, 12, 10, 19, 10,   "grey returning, storms, damp stone smell"),
    12: (15, 9,  11, 16, 9.5,  "cool, dark early, candlelight in every room"),
}

def get_weather_now() -> dict:
    now = datetime.now(TZ_SICILY)
    month, hour = now.month, now.hour
    day_seed = int(now.timestamp() // 86400)
    rng = random.Random(f"weather_{day_seed}")

    avg_high, avg_low, rain_days, sea_temp, daylight, desc = MONTHLY_WEATHER[month]
    temp_var = rng.uniform(-3, 3)
    temp = avg_low + (avg_high - avg_low) * (0.5 + 0.5 * math.sin((hour - 6) * math.pi / 12)) + temp_var

    is_raining = rng.random() < (rain_days / 30)
    rain_intensity = rng.choice(["drizzling", "raining steadily", "pouring"]) if is_raining else ""
    wind_speed = rng.uniform(5, 25) if month in (11, 12, 1, 2, 3) else rng.uniform(2, 15)
    wind_dir = rng.choice(["tramontana", "scirocco", "ponente", "levante", "maestrale"])

    if is_raining:
        sky = rng.choice(["overcast, heavy grey", "dark clouds from the sea"])
    elif rng.random() < 0.3:
        sky = rng.choice(["partly cloudy", "scattered clouds"])
    else:
        sky = rng.choice(["clear, deep blue", "cloudless", "crystalline"])

    if hour < 6 or hour > 21:
        light = "dark" + (" — stars visible" if not is_raining else "")
    elif hour < 8:
        light = "dawn light, pink and orange"
    elif hour > 18:
        light = "golden hour" if not is_raining else "grey dusk"
    else:
        light = "full sun" if not is_raining else "diffused grey"

    if wind_speed > 20: sea = "rough, whitecaps"
    elif wind_speed > 12: sea = "choppy"
    else: sea = rng.choice(["calm, turquoise", "gentle swell", "flat as glass"])

    season = {12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
              6:"summer",7:"summer",8:"summer",9:"autumn",10:"autumn",11:"autumn"}[month]

    return {"temperature": round(temp, 1), "sky": sky, "light": light,
            "wind": f"{wind_dir}, {wind_speed:.0f} km/h", "sea": sea,
            "sea_temp": round(sea_temp + rng.uniform(-1, 1), 1),
            "rain": rain_intensity, "is_raining": is_raining,
            "season": season, "season_note": desc, "hour": hour,
            "daylight_hours": daylight}

def format_weather_for_prompt(w: dict) -> str:
    parts = [f"{w['temperature']:.0f}°C, {w['sky']}"]
    if w["is_raining"]: parts.append(w["rain"])
    parts.append(f"wind: {w['wind']}")
    parts.append(f"sea: {w['sea']}")
    return " | ".join(parts)
