"""Fetch daily metal prices and save them to prices.json (read by the Price & Hedge Connector page)."""
import json, re, urllib.request, datetime, os

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print("failed", url, e)
        return ""


def first_dollar(html, lo, hi):
    for m in re.finditer(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]{4,6}(?:\.[0-9]+)?)", html):
        n = float(m.group(1).replace(",", ""))
        if lo <= n <= hi:
            return n
    return None


def lme_intl(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text.replace("&nbsp;", " "))
    for key in ("Month 2 Closing Price", "Closing Price"):
        for m in re.finditer(re.escape(key), text):
            i = m.start()
            start = max(0, i - 400)
            best, dist = None, 1e9
            for n in re.finditer(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{5,6}(?:\.\d{1,2})?", text[start:i + 800]):
                v = float(n.group(0).replace(",", ""))
                d = abs(n.start() - (i - start))
                if 20000 <= v <= 150000 and d < dist:
                    best, dist = v, d
            if best:
                return best
    return None


def fx():
    try:
        v = json.loads(get("https://open.er-api.com/v6/latest/USD"))["rates"]["INR"]
        return v if 50 <= v <= 200 else None
    except Exception:
        return None


old = json.load(open("prices.json")) if os.path.exists("prices.json") else {}
new = {
    "cobalt_lme": first_dollar(get("https://www.dailymetalprice.com/metalprices.php?c=co&u=mt&d=0"), 15000, 150000),
    "nickel_lme": first_dollar(get("https://www.dailymetalprice.com/metalprices.php?c=ni&u=mt&d=0"), 8000, 60000),
    "cobalt_intl": lme_intl(get("https://www.lme.com/en/metals/ev/lme-cobalt-fastmarkets-mb")),
    "usd_inr": fx(),
}
now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
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
out["sources"] = "dailymetalprice.com (LME cobalt, nickel), lme.com (LME Cobalt Fastmarkets MB month 2), open.er-api.com (USD/INR)"
json.dump(out, open("prices.json", "w"), indent=2)
print(json.dumps(out, indent=2))
