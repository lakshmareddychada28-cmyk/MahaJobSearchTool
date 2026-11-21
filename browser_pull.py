"""Read script-rendered careers pages the way a visitor's browser does (headless Chromium),
only where robots.txt allows. Looks at the job-list data the page itself loads; falls back to
job links on the rendered page. No logins, no form submits, no clicking Apply."""
import json, re, time
TITLEK = ("title", "jobtitle", "job_title", "name", "text", "positiontitle", "displayjobtitle", "postingtitle")
LOCK = ("location", "locations", "city", "primarylocation", "locationtext", "joblocation", "locationsText".lower(), "normalized_location")
IDK = ("id", "jobid", "job_id", "requisitionid", "reqid", "jobreqid", "externalpath", "slug", "url", "applyurl", "absolute_url", "joburl", "path")
URLK = ("url", "applyurl", "absolute_url", "joburl", "canonicalpositionurl", "hostedurl", "externalpath", "path", "link")
_pw = _browser = None
def start():
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start(); _browser = _pw.chromium.launch()
def stop():
    try: _browser.close(); _pw.stop()
    except Exception: pass
def _lists(o, depth=0):
    if depth > 6: return
    if isinstance(o, list):
        if len(o) >= 3 and all(isinstance(x, dict) for x in o[:5]): yield o
        for x in o[:50]: yield from _lists(x, depth + 1)
    elif isinstance(o, dict):
        for v in o.values(): yield from _lists(v, depth + 1)
def _pick(d, keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k)
        if isinstance(v, str) and v.strip(): return v.strip()
        if isinstance(v, (int, float)) and k.startswith(("id", "job", "req")): return str(v)
        if isinstance(v, list) and v and isinstance(v[0], str): return ", ".join(v[:3])
        if isinstance(v, dict) and isinstance(v.get("name"), str): return v["name"]
    return None
def _from_json(bodies):
    best = []
    for b in bodies:
        for lst in _lists(b):
            rows = []
            for d in lst:
                t = _pick(d, TITLEK)
                if t and 5 <= len(t) <= 160 and (_pick(d, LOCK) or _pick(d, IDK)): rows.append((t, _pick(d, LOCK), _pick(d, URLK), _pick(d, IDK), d))
            if len(rows) >= 3 and len(rows) > len(best): best = rows
    return best
def fetch(name, url, mk_job, allowed, strip):
    if not allowed(url): raise PermissionError("robots")
    ctx = _browser.new_context(user_agent="mahajobsearchtool/1.0 (personal job-feed reader)", locale="en-US")
    page = ctx.new_page(); bodies = []
    def on_resp(r):
        try:
            if "json" in (r.headers.get("content-type") or "") and r.status == 200: bodies.append(r.json())
        except Exception: pass
    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        for _ in range(3): page.mouse.wheel(0, 4000); time.sleep(1.2)
        rows = _from_json(bodies); host = re.match(r"https?://[^/]+", page.url).group(0)
        if rows:
            out = []
            for t, loc, u, i, d in rows:
                link = u if u and u.startswith("http") else (host + (u if u and u.startswith("/") else "/" + u)) if u else page.url
                desc = _pick(d, ("description", "jobdescription", "descriptionteaser", "summary", "content")) or ""
                out.append(mk_job(name, t, loc, link, i or link, _pick(d, ("posteddate", "postedon", "dateposted", "created", "createdat", "t_create", "date")), desc))
            return "browser-json", out
        anchors = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, (e.innerText||'').trim().split('\\n')[0]])")
        out, seen = [], set()
        for href, text in anchors:
            if re.search(r"/(job|jobs|position|positions|requisition|opening|careers?/[^/]*\d)", href, re.I) and 8 <= len(text) <= 120 and href not in seen and re.search(r"engineer|developer|scientist|analyst|architect|programmer|sdet|\bqa\b|tester|quality", text, re.I):
                seen.add(href); out.append(mk_job(name, text, None, href, href))
        return ("browser-links", out) if len(out) >= 3 else (None, [])
    finally:
        ctx.close()
