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
            return None  # answered, but no trading that day
    return None


def cme_cobalt():
    day = datetime.datetime.now(datetime.timezone.utc).date()
    for back in range(1, 6):
        d = day - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        t = cme_settle(d)
        if t:
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
    "cobalt_intl": first("Cobalt (international, LME Fastmarkets)",
                         lme_cobalt,
                         cme_cobalt),
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
if new["cobalt_intl"]:
    out["cobalt_intl_source"] = ("LME Cobalt Fastmarkets MB, Month 2 closing (lme.com)"
                                 if USED.get("Cobalt (international, LME Fastmarkets)") == "lme_cobalt"
                                 else "CME Cobalt Metal (Fastmarkets) futures, 2nd month (backup)")
out["updated"] = now.strftime("%d %b %Y %H:%M IST")
out["missing"] = missing
out["sources"] = "dailymetalprice.com / tradingeconomics.com (LME cobalt), westmetall.com (LME nickel cash), lme.com LME Cobalt Fastmarkets MB Month 2 closing, backup cmegroup.com Cobalt Metal (Fastmarkets) 2nd month (international cobalt), open.er-api.com (USD/INR)"
json.dump(out, open("live.json", "w"), indent=2)


# ---- price history for the Price trend tab: history.json = {series: {"YYYY-MM-DD": USD/t}}
def update_history():
    H = json.load(open("history.json")) if os.path.exists("history.json") else {}
    for k in ("cobalt_lme", "cobalt_intl", "nickel_lme"):
        H.setdefault(k, {})
    today = now.strftime("%Y-%m-%d")
    # LME physical cobalt: the dated rows on dailymetalprice.com (about a week each run)
    t = text_of(get("https://www.dailymetalprice.com/metalprices.php?c=co&u=mt&d=0"))
    for m in re.finditer(r"\$\s*([0-9,]+\.\d+)\s*\|?\s*([A-Z][a-z]{2} \d{1,2}, 20\d\d)", t):
        try:
            d = datetime.datetime.strptime(m.group(2), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        if 15000 <= val(m.group(1)) <= 150000:
            H["cobalt_lme"][d] = val(m.group(1))
    # LME nickel cash: whole current-year table on westmetall.com, plus last year once
    years = [now.year] + ([now.year - 1] if len(H["nickel_lme"]) < 200 else [])
    for y in years:
        url = "https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Ni_cash" + ("" if y == now.year else f"&year={y}")
        for m in re.finditer(r"(\d{1,2}\. [A-Z][a-z]+ 20\d\d)\s+" + NUM, text_of(get(url))):
            try:
                d = datetime.datetime.strptime(m.group(1), "%d. %B %Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            if 8000 <= val(m.group(2)) <= 60000:
                H["nickel_lme"][d] = val(m.group(2))
    # today's values
    for k in ("cobalt_lme", "nickel_lme", "cobalt_intl"):
        if new.get(k) and (k == "cobalt_intl" or today not in H[k]):
            H[k][today] = new[k]
    # international cobalt before this job existed: weekly CME settlements for the past year, a few per run
    have = set(H["cobalt_intl"])
    todo = []
    for w in range(1, 53):
        d = (now - datetime.timedelta(weeks=w)).date()
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
        if not any(abs((datetime.date.fromisoformat(x) - d).days) <= 3 for x in have):
            todo.append(d)
    for d in todo[:8]:
        v = cme_settle(d)
        if v:
            H["cobalt_intl"][d.isoformat()] = v
    for k in H:
        H[k] = dict(sorted(H[k].items()))
    H["updated"] = now.strftime("%d %b %Y %H:%M IST")
    H["note"] = "USD/tonne by date. cobalt_intl = LME Cobalt Fastmarkets MB month 2 (earlier weeks from CME Cobalt Metal futures 2nd month); cobalt_lme = LME physical (dailymetalprice.com); nickel_lme = LME nickel cash (westmetall.com)."
    json.dump(H, open("history.json", "w"), indent=1)
    print("history points:", {k: len(v) for k, v in H.items() if isinstance(v, dict)})


try:
    update_history()
except Exception as e:
    print("history update failed:", e)
WARN = "ACCESS EXPIRED: this is an old copy of the price page. Ask the owner for the new file and your access code"
json.dump({"updated": WARN, "notice": WARN, "missing": ["cobalt_lme", "nickel_lme", "cobalt_intl", "usd_inr"]},
          open("prices.json", "w"), indent=2)
print(json.dumps(out, indent=2))
