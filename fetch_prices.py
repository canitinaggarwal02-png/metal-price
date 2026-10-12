"""Fetch daily metal prices and save them to prices.json (read by the Price & Hedge Connector page).
Each price is tried from several free sources; the first one that gives a sensible number is used."""
import json, re, urllib.request, urllib.error, datetime, os

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
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


def fx():
    try:
        v = json.loads(get("https://open.er-api.com/v6/latest/USD"))["rates"]["INR"]
        return v if 50 <= v <= 200 else None
    except Exception:
        return None


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
    "cobalt_intl": first("Cobalt (LME Fastmarkets, international)",
                         lambda: near(get("https://www.lme.com/en/metals/ev/lme-cobalt-fastmarkets-mb"), ["Month 2 Closing Price", "Closing Price"], 20000, 150000)),
    "usd_inr": first("USD/INR", fx),
}
old = json.load(open("prices.json")) if os.path.exists("prices.json") else {}
now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
out = dict(old)
missing = []
for k, v in new.items():
    if v:
        out[k] = v
        out[k + "_as_of"] = now.strftime("%d %b %Y %H:%M IST")
    else:
        missing.append(k)
out["updated"] = now.strftime("%d %b %Y %H:%M IST")
out["missing"] = missing
out["sources"] = "dailymetalprice.com / tradingeconomics.com (LME cobalt), westmetall.com (LME nickel cash), lme.com (LME Cobalt Fastmarkets MB), open.er-api.com (USD/INR)"
json.dump(out, open("prices.json", "w"), indent=2)
print(json.dumps(out, indent=2))
