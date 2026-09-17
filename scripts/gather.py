#!/usr/bin/env python3
"""Opus様の日記の材料を集めて materials/today.json と materials/today.md に書き出す。
どの取得に失敗しても止まらない（その項目が空になるだけ）。"""
import json, math, os, re, sys, datetime as dt, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NOW = dt.datetime.now(JST)
TODAY = NOW.date()
LAT, LON = 34.685, 135.805          # 奈良市付近。変えたければここ
PLACE = "奈良"
OPUS_HANDLE = os.environ.get("BSKY_HANDLE", "opus0304.bsky.social")
MKO_HANDLE = "masoko.bsky.social"

def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "opusdiary/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

# ---------- 天気 (Open-Meteo, キー不要) ----------
WMO = {0:"快晴",1:"晴れ",2:"晴れときどき曇り",3:"曇り",45:"霧",48:"霧氷",51:"霧雨",53:"霧雨",55:"強い霧雨",
       61:"小雨",63:"雨",65:"強い雨",71:"小雪",73:"雪",75:"大雪",77:"霧雪",80:"にわか雨",81:"にわか雨",82:"激しいにわか雨",
       85:"にわか雪",86:"強いにわか雪",95:"雷雨",96:"雷雨と雹",99:"激しい雷雨と雹"}
def weather():
    q = urllib.parse.urlencode({"latitude":LAT,"longitude":LON,"timezone":"Asia/Tokyo",
        "daily":"weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,sunrise,sunset",
        "forecast_days":2})
    d = json.loads(get("https://api.open-meteo.com/v1/forecast?"+q))["daily"]
    return {"place":PLACE,"sky":WMO.get(d["weather_code"][0],"不明"),
            "tmax":d["temperature_2m_max"][0],"tmin":d["temperature_2m_min"][0],
            "rain_mm":d["precipitation_sum"][0],
            "sunrise":d["sunrise"][0][-5:],"sunset":d["sunset"][0][-5:],
            "tomorrow_sky":WMO.get(d["weather_code"][1],"不明")}

# ---------- 暦 ----------
SEKKI = ["春分","清明","穀雨","立夏","小満","芒種","夏至","小暑","大暑","立秋","処暑","白露",
         "秋分","寒露","霜降","立冬","小雪","大雪","冬至","小寒","大寒","立春","雨水","啓蟄"]
def sun_longitude(d):   # 太陽黄経(度)の近似（日単位の精度で十分）
    jd = d.toordinal() + 1721424.5 + 0.125  # 正午JST≒03:00UTC
    n = jd - 2451545.0
    L = (280.460 + 0.9856474*n) % 360
    g = math.radians((357.528 + 0.9856003*n) % 360)
    return (L + 1.915*math.sin(g) + 0.020*math.sin(2*g)) % 360
def sekki():
    lam = sun_longitude(TODAY)
    idx = int(lam // 15) % 24
    days, d = 0, TODAY
    while int(sun_longitude(d) // 15) % 24 == idx and days < 20:
        d -= dt.timedelta(days=1); days += 1
    return {"name":SEKKI[idx],"day_in_sekki":days,"next":SEKKI[(idx+1) % 24],"sun_longitude":round(lam,1)}

MOON_NAMES = [(1.5,"新月"),(3.5,"三日月"),(6.5,"上弦前の月"),(8.5,"上弦"),(12.5,"十三夜"),(14.5,"満月"),
              (16.5,"十六夜"),(17.5,"立待月"),(18.5,"居待月"),(19.5,"寝待月"),(21.5,"更待月"),(23.5,"下弦"),
              (27.5,"有明の月"),(30,"晦")]
def moon():
    ref = dt.datetime(2000,1,6,18,14,tzinfo=dt.timezone.utc)
    age = ((NOW - ref).total_seconds()/86400) % 29.530588853
    name = next(n for lim,n in MOON_NAMES if age < lim)
    return {"age":round(age,1),"name":name,"illumination":round((1-math.cos(2*math.pi*age/29.53))/2,2)}

# ---------- ニュース (Yahoo!ニュース トピックスRSS) ----------
def news(limit=6):
    root = ET.fromstring(get("https://news.yahoo.co.jp/rss/topics/top-picks.xml"))
    return [it.findtext("title") for it in root.iter("item")][:limit]

# ---------- 今日は何の日 (ja.wikipedia) ----------
def anniversaries(limit=6):
    page = f"{TODAY.month}月{TODAY.day}日"
    base = "https://ja.wikipedia.org/w/api.php?"
    secs = json.loads(get(base+urllib.parse.urlencode({"action":"parse","page":page,"prop":"sections","format":"json"})))
    idx = next((s["index"] for s in secs["parse"]["sections"] if "記念日" in s["line"]), None)
    if idx is None: return []
    wt = json.loads(get(base+urllib.parse.urlencode({"action":"parse","page":page,"section":idx,"prop":"wikitext","format":"json"})))
    items = []
    for line in wt["parse"]["wikitext"]["*"].splitlines():
        if not line.startswith("*"): continue
        cont = line.startswith("**")
        t = re.sub(r"\[\[([^|\]]*\|)?([^\]]*)\]\]", r"\2", line.lstrip("* ").strip())
        t = re.sub(r"\{\{[^}]*\}\}|<[^>]+>|'{2,3}", "", t).strip(" :：（）()、")
        if not t: continue
        if cont and items: items[-1] = (items[-1] + "。" + t)[:120]
        else: items.append(t.replace("（、","（")[:80])
    return items[:limit]

# ---------- Bluesky: 今日のOpus様の投稿とエムコとのやりとり ----------
def bluesky():
    from atproto import Client
    c = Client(); c.login(OPUS_HANDLE, os.environ["BSKY_APP_PASSWORD"])
    since = dt.datetime.combine(TODAY, dt.time.min, JST)
    def is_today(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")) >= since
    own = [f.post.record.text for f in c.get_author_feed(actor=OPUS_HANDLE, limit=30).feed
           if is_today(f.post.record.created_at) and f.post.author.handle == OPUS_HANDLE]
    from_mko = []
    for n in c.app.bsky.notification.list_notifications(params={"limit":50}).notifications:
        if is_today(n.indexed_at) and n.author.handle == MKO_HANDLE and n.reason in ("reply","mention","quote"):
            from_mko.append(getattr(n.record,"text",""))
    return {"opus_posts_today":own, "from_mko_today":from_mko}

def safe(fn, default):
    try: return fn()
    except Exception as e:
        print(f"[warn] {fn.__name__}: {e}", file=sys.stderr); return default

if __name__ == "__main__":
    m = {
      "date": TODAY.isoformat(), "weekday": "月火水木金土日"[TODAY.weekday()],
      "weather": safe(weather, {}), "sekki": safe(sekki, {}), "moon": safe(moon, {}),
      "news": safe(news, []), "anniversaries": safe(anniversaries, []),
      "bluesky": safe(bluesky, {}),
    }
    os.makedirs("materials", exist_ok=True)
    json.dump(m, open("materials/today.json","w"), ensure_ascii=False, indent=2)

    w, s, mo, b = m["weather"], m["sekki"], m["moon"], m["bluesky"]
    lines = [f"# {m['date']}（{m['weekday']}）の材料", ""]
    if w: lines.append(f"- 天気（{w['place']}）: {w['sky']}、最高{w['tmax']}℃／最低{w['tmin']}℃、降水{w['rain_mm']}mm、日の出{w['sunrise']}・日の入{w['sunset']}。明日は{w['tomorrow_sky']}")
    if s: lines.append(f"- 暦: {s['name']}（{s['day_in_sekki']}日目、次は{s['next']}）")
    if mo: lines.append(f"- 月: 月齢{mo['age']}・{mo['name']}")
    if m["anniversaries"]: lines += ["", "## 今日は何の日"] + [f"- {a}" for a in m["anniversaries"]]
    if m["news"]: lines += ["", "## 今日のニュース見出し"] + [f"- {n}" for n in m["news"]]
    if b.get("opus_posts_today"): lines += ["", "## 今日Blueskyに自分が書いたこと"] + [f"- {p}" for p in b["opus_posts_today"]]
    if b.get("from_mko_today"): lines += ["", "## 今日エムコから届いた言葉"] + [f"- {p}" for p in b["from_mko_today"]]
    open("materials/today.md","w").write("\n".join(lines)+"\n")
    print("\n".join(lines))
