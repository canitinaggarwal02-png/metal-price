"""Fetch daily metal prices and save them to live.json (read by the Price & Hedge Connector page).
Each price is tried from several free sources; the first one that gives a sensible number is used."""
import json, re, urllib.request, urllib.error, urllib.parse, datetime, os

UA = {"Referer": "https://www.cmegroup.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9"}
NUM = r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]{4,6}(?:\.[0-9]+)?)"


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
            print(f"  {r.status} {len(body):>7} bytes  {url}")
            return body
    except urllib.error.HTTPError as e:
        print(f"  {e.code}  blocked/failed  {url}")
    except Exception as e:
        print(f"  ERR {e!r:.60}  {url}")
    return ""


def text_of(html):
    for tag in ("scr" + "ipt", "style"):
        t_open, t_close = "<" + tag, "</" + tag + ">"
        html = re.sub(t_open + r"[\s\S]*?" + t_close, " ", html)
    t = html
    t = re.sub(r"<[^>]+>", " ", t).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t)


def val(s):
    return float(s.replace(",", ""))


def first_dollar(html, lo, hi):
    for m in re.finditer(r"\$\s*" + NUM, html):
        if lo <= val(m.group(1)) <= hi:
            return val(m.group(1))
    return None


def near(html, keys, lo, hi, after=600, before=200):
    t = text_of(html)
    for k in keys:
        for m in re.finditer(re.escape(k), t, re.I):
            i = m.start()
            s = max(0, i - before)
            best, dist = None, 1e9
            for n in re.finditer(NUM, t[s:i + after]):
                v = val(n.group(1))
                d = abs(n.start() - (i - s))
                if lo <= v <= hi and d < dist:
                    best, dist = v, d
            if best:
                return best
    return None


def te(metal, lo, hi):
    # tradingeconomics.com summary sentence, e.g. "Cobalt traded flat at 39,245 USD/T on October 8, 2026."
    t = text_of(get(f"https://tradingeconomics.com/commodity/{metal}"))
    m = re.search(metal + r"[^.]{0,80}?\b" + NUM + r"\s*USD/T", t, re.I)
    return val(m.group(1)) if m and lo <= val(m.group(1)) <= hi else None


def westmetall_ni():
    t = text_of(get("https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Ni_cash"))
    m = re.search(r"\d{1,2}\. \w+ 20\d\d\s+" + NUM, t)
    return val(m.group(1)) if m and 8000 <= val(m.group(1)) <= 60000 else None


def relays(url):
    # the site itself first, then two free reader services in case the site refuses GitHub's servers
    yield get(url)
    yield get("https://r.jina.ai/" + url)
    yield get("https://api.allorigins.win/raw?url=" + urllib.parse.quote(url, safe=""))


def cme_settle(d):
    # CME Cobalt Metal (Fastmarkets) futures settlement on trade date d, 2nd listed month, USD/lb -> USD/t
    url = ("https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/8971/FUT"
           f"?tradeDate={d:%m/%d/%Y}&strategy=DEFAULT&pageSize=50")
    for body in relays(url):
        rows = re.findall(r'"month"\s*:\s*"([A-Z]{3} \d{2})"[^}]*?"settle"\s*:\s*"([0-9.,]+)"', body or "")
        if len(rows) >= 2:
            month, lb = rows[1][0], val(rows[1][1])
            t = round(lb * 2204.62, 2)
            print(f"  CME {d} {month} settle {lb} $/lb = {t} $/t")
            return t if 20000 <= t <= 150000 else None
        if body and '"settlements"' in body:
            return 0  # answered, but no trading that day
    return None


CME_DAY = {}


def cme_cobalt():
    day = datetime.datetime.now(datetime.timezone.utc).date()
    for back in range(1, 6):
        d = day - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        t = cme_settle(d)
        if t:
            CME_DAY["date"] = d.strftime("%Y-%m-%d")
            return t
    return None


def lme_cobalt():
    # LME Cobalt (Fastmarkets MB) "Month 2 Closing Price" from lme.com, USD/t
    url = "https://www.lme.com/en/metals/ev/lme-cobalt-fastmarkets-mb"
    for body in relays(url):
        if not body:
            continue
        v = near(body, ["Month 2 Closing Price", "Closing Price"], 20000, 150000)
        if not v:
            # price sometimes sits in page data rather than visible text
            m = re.search(r'(?i)closing\s*price[^0-9]{0,300}?([0-9]{5,6}(?:\.[0-9]+)?)(?![0-9])', body)
            v = val(m.group(1)) if m and 20000 <= val(m.group(1)) <= 150000 else None
        if v:
            print(f"  LME Month 2 closing {v} $/t")
            return v
    return None


def fx():
    try:
        v = json.loads(get("https://open.er-api.com/v6/latest/USD"))["rates"]["INR"]
        return v if 50 <= v <= 200 else None
    except Exception:
        return None


USED = {}


def first(name, *tries):
    print(name)
    for f in tries:
        try:
            v = f()
        except Exception as e:
            print("  parse error", e)
            v = None
        if v:
            print(f"  -> {v}")
            USED[name] = getattr(f, "__name__", "")
            return v
    print("  -> not found")
    return None


new = {
    "cobalt_lme": first("Cobalt (LME physical)",
                        lambda: first_dollar(get("https://www.dailymetalprice.com/metalprices.php?c=co&u=mt&d=0"), 15000, 150000),
                        lambda: te("cobalt", 15000, 150000)),
    "nickel_lme": first("Nickel (LME)",
                        westmetall_ni,
                        lambda: first_dollar(get("https://www.dailymetalprice.com/metalprices.php?c=ni&u=mt&d=0"), 8000, 60000),
                        lambda: te("nickel", 8000, 60000)),
    "cobalt_lme_fm": first("Cobalt LME Fastmarkets MB month 2", lme_cobalt),
    "cobalt_cme": first("Cobalt CME futures 2nd month", cme_cobalt),
    "usd_inr": first("USD/INR", fx),
}
# live prices go to live.json (read only by the new access-code page); prices.json is kept as a warning for old copies
src = "live.json" if os.path.exists("live.json") else "prices.json"
old = json.load(open(src)) if os.path.exists(src) else {}
for k in ("notice",):
    old.pop(k, None)
now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
out = dict(old)
missing = []
for k, v in new.items():
    if v:
        out[k] = v
        out[k + "_as_of"] = now.strftime("%d %b %Y %H:%M IST")
    else:
        missing.append(k)
# international cobalt shown on the price page: LME first, CME as backup
if new["cobalt_lme_fm"]:
    out["cobalt_intl"], out["cobalt_intl_as_of"] = new["cobalt_lme_fm"], out["cobalt_lme_fm_as_of"]
    out["cobalt_intl_source"] = "LME Cobalt Fastmarkets MB, Month 2 closing (lme.com)"
elif new["cobalt_cme"]:
    out["cobalt_intl"], out["cobalt_intl_as_of"] = new["cobalt_cme"], out["cobalt_cme_as_of"]
    out["cobalt_intl_source"] = "CME Cobalt Metal (Fastmarkets) futures, 2nd month (backup)"
else:
    missing.append("cobalt_intl")
out["updated"] = now.strftime("%d %b %Y %H:%M IST")
out["missing"] = missing
out["sources"] = "dailymetalprice.com / tradingeconomics.com (LME cobalt), westmetall.com (LME nickel cash), lme.com LME Cobalt Fastmarkets MB Month 2 closing, backup cmegroup.com Cobalt Metal (Fastmarkets) 2nd month (international cobalt), open.er-api.com (USD/INR)"
json.dump(out, open("live.json", "w"), indent=2)


# ---- price history for the Price trend tab: history.json = {series: {"YYYY-MM-DD": USD/t}}
def update_history():
    H = json.load(open("history.json")) if os.path.exists("history.json") else {}
    for k in ("cobalt_lme", "cobalt_lme_fm", "cobalt_cme", "nickel_lme"):
        H.setdefault(k, {})
    # earlier versions kept the LME Fastmarkets price under cobalt_intl (from 17 Oct 2026)
    for d, v in H.pop("cobalt_intl", {}).items():
        if d >= "2026-10-17":
            H["cobalt_lme_fm"].setdefault(d, v)
    today = now.strftime("%Y-%m-%d")
    # LME physical cobalt: the dated rows on dailymetalprice.com (about a week each run)
    log = {}
    t = text_of(get("https://www.dailymetalprice.com/metalprices.php?c=co&u=mt&d=0"))
    i = t.find("$")
    log["dmp_sample"] = t[max(0, i - 120):i + 300]
    pats = [(r"[A-Z][a-z]{2,8}\.? \d{1,2}, 20\d\d", ("%b %d, %Y", "%B %d, %Y", "%b. %d, %Y")),
            (r"\d{1,2}/\d{1,2}/20\d\d", ("%m/%d/%Y",)),
            (r"20\d\d-\d\d-\d\d", ("%Y-%m-%d",))]
    found = 0; dmp = []
    for pat, fmts in pats:
        for m in re.finditer(pat, t):
            d = None
            for f in fmts:
                try:
                    d = datetime.datetime.strptime(m.group(0), f).strftime("%Y-%m-%d"); break
                except ValueError:
                    pass
            if not d:
                continue
            before = [val(n.group(1)) for n in re.finditer(NUM, t[max(0, m.start() - 40):m.start()]) if 15000 <= val(n.group(1)) <= 150000]
            after = [val(n.group(1)) for n in re.finditer(NUM, t[m.end():m.end() + 40]) if 15000 <= val(n.group(1)) <= 150000]
            best = before[-1] if before else (after[0] if after else None)
            if best:
                H["cobalt_lme"][d] = best; found += 1; dmp.append(d)
    log["dmp_rows"] = found
    if dmp:  # drop any cobalt point dated after the site's latest price
        for d in [d for d in H["cobalt_lme"] if d > max(dmp)]:
            H["cobalt_lme"].pop(d)
    # LME nickel cash: whole current-year table on westmetall.com, plus last year once
    wm = []
    years = [now.year] + ([now.year - 1] if len(H["nickel_lme"]) < 200 else [])
    for y in years:
        url = "https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Ni_cash" + ("" if y == now.year else f"&year={y}")
        for m in re.finditer(r"(\d{1,2}\. [A-Z][a-z]+ 20\d\d)\s+" + NUM, text_of(get(url))):
            try:
                d = datetime.datetime.strptime(m.group(1), "%d. %B %Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            if 8000 <= val(m.group(2)) <= 60000:
                H["nickel_lme"][d] = val(m.group(2)); wm.append(d)
    if wm:  # drop any nickel point dated after westmetall's latest price
        for d in [d for d in H["nickel_lme"] if d > max(wm)]:
            H["nickel_lme"].pop(d)
    log["nickel_rows"] = len(wm)
    # LME Fastmarkets cobalt: LME does not publish past prices for free, so only prices recorded by this job
    if new.get("cobalt_lme_fm"):
        H["cobalt_lme_fm"][today] = new["cobalt_lme_fm"]
    # CME cobalt futures: today's settlement, plus past settlements filled in up to 25 days per run (back about 6 months)
    if new.get("cobalt_cme") and CME_DAY.get("date"):
        H["cobalt_cme"][CME_DAY["date"]] = new["cobalt_cme"]
    nodata = set(H.get("cme_no_trading", []))
    start = now.date() - datetime.timedelta(days=200)
    todo = [start + datetime.timedelta(days=i) for i in range((now.date() - start).days)]
    todo = [d for d in reversed(todo) if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in H["cobalt_cme"]
            and d.strftime("%Y-%m-%d") not in nodata and d < now.date() - datetime.timedelta(days=1)]
    got = fails = 0
    for d in todo[:25]:
        t = cme_settle(d)
        if t:
            H["cobalt_cme"][d.strftime("%Y-%m-%d")] = t; got += 1; fails = 0
        elif t == 0:
            nodata.add(d.strftime("%Y-%m-%d"))  # holiday, no settlement
        else:
            fails += 1
            if fails >= 1:
                break  # CME not answering this run; try again next run
    H["cme_no_trading"] = sorted(nodata)
    log["cme_backfill_added"] = got
    if new.get("cobalt_lme") and not log["dmp_rows"]:
        H["cobalt_lme"][today] = new["cobalt_lme"]
    H["log"] = log
    for k in ("cobalt_lme", "cobalt_lme_fm", "cobalt_cme", "nickel_lme"):
        H[k] = dict(sorted(H[k].items()))
    H["updated"] = now.strftime("%d %b %Y %H:%M IST")
    H["note"] = "USD/tonne by date, recorded by this job. cobalt_lme_fm = LME Cobalt Fastmarkets MB month 2 (from 17 Oct 2026); cobalt_cme = CME Cobalt Metal (Fastmarkets) futures 2nd month settlement; cobalt_lme = LME physical (dailymetalprice.com); nickel_lme = LME nickel cash (westmetall.com)."
    json.dump(H, open("history.json", "w"), indent=1)
    print("history points:", {k: len(v) for k, v in H.items() if isinstance(v, dict)})


try:
    update_history()
except Exception as e:
    print("history update failed:", e)

# ---- news headlines for the Price trend tab: news.json, each headline scored up (+1) / down (-1) for the price
UP = ["export ban", "ban on export", "export quota", "quota", "supply cut", "output cut", "production cut", "cut output",
      "cuts output", "halt", "suspend", "shortage", "deficit", "strike", "disruption", "stockpil", "rally", "surge",
      "soar", "jump", "rise", "rises", "rising", "higher", "climb", "gain", "record high", "tight", "strong demand",
      "demand boost", "rebound", "extends ban", "tariff", "upward", "upside", "strengthen", "bottomed out", "firm", "recover"]
DOWN = ["surplus", "oversupply", "glut", "slump", "plunge", "fall", "falls", "falling", "drop", "decline", "lower",
        "weak demand", "slowdown", "lifts ban", "lift ban", "ends ban", "eases", "resume", "restart", "ramp up",
        "expansion", "new capacity", "inventories rise", "stocks rise", "sell-off", "selloff", "slide", "tumble",
        "lowest", "pressure", "downward", "downside", "weaken", "drag", "slip", "dip", "sink", "bearish", "decrease", "retreat"]
QUERIES = {"cobalt": ["cobalt price", "cobalt Congo export", "cobalt market"],
           "nickel": ["nickel price", "LME nickel", "nickel Indonesia"]}


def score(title):
    t = " " + title.lower() + " "
    up = [w for w in UP if re.search(r"\b" + re.escape(w), t)]
    down = [w for w in DOWN if re.search(r"\b" + re.escape(w), t)]
    if "stocks rise" in down or "inventories rise" in down:
        up = [w for w in up if w not in ("rise", "rises", "rising")]
    sc = (len(up) > 0) - (len(down) > 0)
    return sc, ", ".join(up if sc > 0 else down if sc < 0 else up + down)


def rss_items(q):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    enc = urllib.parse.quote(q)
    for url in (f"https://news.google.com/rss/search?q={enc}+when:30d&hl=en-IN&gl=IN&ceid=IN:en",
                f"https://www.bing.com/news/search?q={enc}&format=rss"):
        body = get(url)
        try:
            root = ET.fromstring(body)
        except Exception:
            continue
        out = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            src = it.find("source")
            source = (src.text if src is not None and src.text else "").strip()
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3]
            try:
                d = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(datetime.timezone.utc)
            except Exception:
                continue
            out.append({"date": d.strftime("%Y-%m-%d"), "title": title, "source": source, "link": (it.findtext("link") or "").strip()})
        if out:
            return out
    return []


def update_news():
    N = json.load(open("news.json")) if os.path.exists("news.json") else {}
    cutoff = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    for metal, qs in QUERIES.items():
        seen = {x["title"].lower(): x for x in N.get(metal, [])}
        for q in qs:
            for x in rss_items(q):
                if metal not in x["title"].lower():
                    continue
                x["score"], x["why"] = score(x["title"])
                seen.setdefault(x["title"].lower(), x)
        items = [x for x in seen.values() if x["date"] >= cutoff]
        items.sort(key=lambda x: x["date"], reverse=True)
        N[metal] = items[:60]
    N["updated"] = now.strftime("%d %b %Y %H:%M IST")
    N["note"] = "Headlines from Google News / Bing News RSS. score +1 = news that usually pushes the price up, -1 = down, 0 = neutral, by keyword."
    json.dump(N, open("news.json", "w"), indent=1)
    print("news:", {m: len(N[m]) for m in QUERIES})


try:
    update_news()
except Exception as e:
    print("news update failed:", e)

WARN = "ACCESS EXPIRED: this is an old copy of the price page. Ask the owner for the new file and your access code"
json.dump({"updated": WARN, "notice": WARN, "missing": ["cobalt_lme", "nickel_lme", "cobalt_intl", "usd_inr"]},
          open("prices.json", "w"), indent=2)
print(json.dumps(out, indent=2))
