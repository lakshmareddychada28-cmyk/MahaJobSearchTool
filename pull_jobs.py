"""Pull open jobs from companies' own public ATS feeds (no job boards).
Adapters: Amazon, Workday (CXS), Greenhouse, Lever, Ashby, SmartRecruiters.
Companies: data/companies.csv (product companies hiring QA in Hyderabad, Bangalore and remote India; ATS + Slug filled where a public feed is known).
Targets: QA / SDET / test automation roles, about 2-5 years, located in Hyderabad, Bangalore or remote India.
Workday and SmartRecruiters list pages carry no description, so the description is read from each new posting's own page.
Companies with no reachable feed are listed under 'unreadable' - never guessed.
Output: data/new_jobs.json (only postings not in data/seen.json), data/coverage.json.
Robots: public JSON APIs only; HTML pages are skipped unless robots.txt allows."""
import csv, json, os, re, sys, time, urllib.request, urllib.robotparser, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
UA = "mahajobsearchtool/1.0 (personal job-feed reader)"
TITLE = re.compile(r"\bqa\b|\bqe\b|quality (assurance|engineer|engineering|analyst)|\bsdet\b|\bsdet[- ]?\d|software (development )?engineer,? (in|-) test|test(ing)? (engineer|analyst|automation|specialist)|automation (test|qa|engineer)|tester\b|software test", re.I)
SKIP = re.compile(r"senior|\bsr\b|staff|principal|lead|manager|director|head\b|architect|intern\b|vp\b|account executive|sales|technician|recruiter|marketing|hardware|silicon|asic|sram|std cell|analog|\bsoc\b|validation engineer|mechanical|manufacturing|supplier|clinical|food|construction", re.I)
def get(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}; h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=25) as r: return json.loads(r.read())
def slugs(name):
    b = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    return list(dict.fromkeys(["".join(b), "-".join(b), b[0] if b else ""]))
def strip(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", __import__("html").unescape(h or ""))).strip()
def job(c, title, loc, url, jid, posted=None, desc=""):
    return {"company": c, "title": title, "location": loc, "url": url, "id": f"{c}|{jid}", "posted": posted, "desc": strip(desc)[:6000]}
def amazon(c, u):
    d = get("https://www.amazon.jobs/en/search.json?base_query=quality+assurance+engineer&loc_query=India&result_limit=50&sort=recent")
    return [job(c, j["title"], j.get("normalized_location"), "https://www.amazon.jobs" + j["job_path"], j["id_icims"], j.get("posted_date"), " ".join([j.get("description",""), j.get("basic_qualifications",""), j.get("preferred_qualifications","")])) for j in d["jobs"]]
def workday(c, u):
    p = urlparse(u); host = p.netloc; tenant = host.split(".")[0]
    site = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)][0]
    out, ids = [], set()
    for q in WD_QUERIES:
        total = None
        for off in range(0, 60, 20):   # Workday serves 20 per page; 3 pages per search
            d = get(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}).encode(), {"Content-Type": "application/json"})
            posts = d.get("jobPostings", []); total = total or d.get("total", 0)
            for j in posts:
                if not j.get("externalPath") or j["externalPath"] in ids: continue
                ids.add(j["externalPath"])
                x = job(c, j["title"], j.get("locationsText"), f"https://{host}/{site}{j['externalPath']}", j["externalPath"], j.get("postedOn"))
                x["_detail"] = f"https://{host}/wday/cxs/{tenant}/{site}{j['externalPath']}"; out.append(x)
            if len(posts) < 20 or off + 20 >= (total or 0): break
    return out
WD_QUERIES = ("quality assurance", "QA engineer", "SDET", "software engineer in test", "test engineer", "test automation", "quality engineer")
def greenhouse(c, s):
    host = "boards-api.greenhouse.io"
    if s.startswith("eu:"): host, s = "boards-api.eu.greenhouse.io", s[3:]   # boards on Greenhouse's EU instance
    d = get(f"https://{host}/v1/boards/{s}/jobs?content=true")
    return [job(c, j["title"], j["location"]["name"], j["absolute_url"], j["id"], j.get("updated_at"), j.get("content","")) for j in d["jobs"]]
def lever(c, s):
    d = get(f"https://api.lever.co/v0/postings/{s}?mode=json")
    return [job(c, j["text"], j["categories"].get("location"), j["hostedUrl"], j["id"], dt.datetime.utcfromtimestamp(j["createdAt"] / 1000).isoformat(), j.get("descriptionPlain","")) for j in d]
def ashby(c, s):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{s}")
    return [job(c, j["title"], j.get("location"), j["jobUrl"], j["id"], j.get("publishedAt"), j.get("descriptionPlain","")) for j in d["jobs"]]
def smart(c, s):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100")
    out = []
    for j in d["content"]:
        loc = ", ".join(filter(None, [j["location"].get("city"), j["location"].get("region"), (j["location"].get("country") or "").upper()]))
        x = job(c, j["name"], loc, f"https://jobs.smartrecruiters.com/{s}/{j['id']}", j["id"], j.get("releasedDate"))
        x["_detail"] = f"https://api.smartrecruiters.com/v1/companies/{s}/postings/{j['id']}"; out.append(x)
    return out
def detail(j):
    """Fill in the description (and US location, posted date) from a posting's own page, for feeds whose list has none."""
    u = j.pop("_detail", None)
    if not u or j["desc"]: return
    d = get(u)
    if "jobPostingInfo" in d:   # Workday
        i = d["jobPostingInfo"]; j["desc"] = strip(i.get("jobDescription", ""))[:6000]
        country = (i.get("country") or {}).get("descriptor")
        if i.get("location"): j["location"] = i["location"] + (f", {country}" if country and country not in i["location"] else "")
        j["posted"] = i.get("startDate") or j["posted"]
    elif "jobAd" in d:   # SmartRecruiters
        j["desc"] = strip(" ".join((v or {}).get("text", "") for v in (d["jobAd"].get("sections") or {}).values()))[:6000]
FEEDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smart, "workday": lambda c, slug: workday(c, f"https://{slug}")}

RP = {}
def allowed(url):
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if base not in RP:
        rp = urllib.robotparser.RobotFileParser(); rp.set_url(base + "/robots.txt")
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            rp.parse(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore").splitlines())
        except Exception: rp = None   # no readable robots.txt -> treated as allowed, same as any crawler
        RP[base] = rp
    return True if RP[base] is None else RP[base].can_fetch(UA, url)
def page(url):
    if not allowed(url): raise PermissionError("robots")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=25) as r: return r.read().decode("utf-8", "ignore")
def post(url, body):
    if not allowed(url): raise PermissionError("robots")
    return get(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
def eightfold(c, host, domain):
    d = get(f"https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=50&query=quality+assurance&location=India&sort_by=timestamp")
    return [job(c, j["name"], j.get("location"), j.get("canonicalPositionUrl") or f"https://{host}/careers/job/{j['id']}", j["id"], dt.datetime.utcfromtimestamp(j["t_create"]).isoformat() if j.get("t_create") else None) for j in d.get("positions", [])]
def oracle(c, host, site):
    u = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber={site},limit=50,keyword=%22quality%20assurance%22,sortBy=POSTING_DATES_DESC"
    if not allowed(u): raise PermissionError("robots")
    d = get(u); items = d["items"][0].get("requisitionList", []) if d.get("items") else []
    return [job(c, j["Title"], j.get("PrimaryLocation"), f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}", j["Id"], j.get("PostedDate")) for j in items]
def phenom(c, host):
    body = {"lang": "en_us", "deviceType": "desktop", "country": "in", "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "Most recent", "subsearch": "", "from": 0, "jobs": True, "counts": True, "all_fields": ["category", "country", "state", "city", "type"], "size": 50, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page11", "siteType": "external", "keywords": "quality assurance", "global": True, "selected_fields": {}, "locationData": {}}
    d = post(f"https://{host}/widgets", body)["refineSearch"]["data"]["jobs"]
    return [job(c, j["title"], j.get("location") or j.get("cityStateCountry"), j.get("applyUrl") or f"https://{host}/job/{j['jobId']}", j["jobId"], j.get("postedDate"), j.get("descriptionTeaser", "")) for j in d]
def talentbrew(c, host):
    h = page(f"https://{host}/search-jobs/results?ActiveFacetID=0&CurrentPage=1&RecordsPerPage=50&Distance=50&RadiusUnitType=0&Keywords=quality+assurance&Location=&ShowRadius=False&IsPagination=False&FacetType=0&SearchType=5&SortCriteria=0&SortDirection=0&ResultsType=0")
    h = json.loads(h).get("results", h) if h.lstrip().startswith("{") else h
    out = []
    for m in re.finditer(r'<a href="(/job/[^"]+)"[^>]*>(.*?)</a>', h, re.S):
        t = re.search(r"<h[23][^>]*>(.*?)</h[23]>", m.group(2), re.S); loc = re.search(r'job-location[^>]*>(.*?)<', m.group(2), re.S)
        if t: out.append(job(c, strip(t.group(1)), strip(loc.group(1)) if loc else None, f"https://{host}{m.group(1)}", m.group(1)))
    return out
def discover(c, url):
    """Look at the company's own careers page (robots permitting) and use the job system it points to."""
    if not url.startswith("http"): return None, None, "no address"
    p = urlparse(url); host = p.netloc
    try: h = page(url)
    except PermissionError: return None, None, "robots disallow"
    except Exception as e: return None, None, f"page not readable ({type(e).__name__})"
    m = re.search(r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", h)
    if m: return "workday", workday(c, f"https://{m.group(1)}/{m.group(2)}"), None
    m = re.search(r"https?://([a-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z-]+/sites/([A-Za-z0-9_]+)", h)
    if m: return "oracle", oracle(c, m.group(1), m.group(2)), None
    m = re.search(r"https?://([a-z0-9-]+\.eightfold\.ai)", h)
    if m or "eightfold" in host:
        eh = m.group(1) if m else host; dom = re.search(r'domain=([a-z0-9.-]+)', h)
        return "eightfold", eightfold(c, eh, dom.group(1) if dom else re.sub(r"^(explore\.jobs\.|careers\.|jobs\.)", "", host) if "eightfold" not in host else host.split(".")[0] + ".com"), None
    if "phenom" in h.lower() or "/widgets" in h or 'ph-page' in h: return "phenom", phenom(c, host), None
    if "/search-jobs" in h or "talentbrew" in h.lower() or "tbcdn" in h: return "talentbrew", talentbrew(c, host), None
    m = re.search(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([a-z0-9_-]+)", h)
    if m: return "greenhouse", greenhouse(c, m.group(1)), None
    m = re.search(r"jobs\.lever\.co/([a-z0-9_-]+)", h)
    if m: return "lever", lever(c, m.group(1)), None
    return None, None, "page read but no open job system recognised (probably script-only)"

NOGUESS = {"Uber", "slice", "Acko"}  # guessed feeds were wrong/unverified
def fetch(name, url):
    if "amazon.jobs" in url: return "amazon", amazon(name, url)
    if "myworkdayjobs.com" in url: return "workday", workday(name, url)
    mo = re.search(r"https?://([a-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z-]+/sites/([A-Za-z0-9_]+)", url)
    if mo:
        try: return "oracle", oracle(name, mo.group(1), mo.group(2))
        except Exception as e: WHY[name] = f"oracle feed failed ({type(e).__name__})"
    if "eightfold.ai" in url:
        host = urlparse(url).netloc
        try: return "eightfold", eightfold(name, host, host.split(".")[0] + ".com")
        except Exception as e: WHY[name] = f"eightfold feed failed ({type(e).__name__})"
    try:
        k, r, why = discover(name, url)
        if r: return k, r
        WHY[name] = why
    except Exception as e: WHY[name] = f"discovery failed ({type(e).__name__})"
    for kind, fn in (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby), ("smartrecruiters", smart)):
        for sl in slugs(name):
            try:
                r = fn(name, sl)
                if r and len(r) > 5 and name not in NOGUESS: return kind, r
            except Exception: pass
    return None, None
def browser_fetch(name, url):
    try:
        import browser_pull
        k, r = browser_pull.fetch(name, url, job, allowed, strip)
        if r: return k, r
        WHY[name] = (WHY.get(name) or "") + "; browser found no job list"
    except PermissionError: WHY[name] = "robots disallow"
    except Exception as e: WHY[name] = (WHY.get(name) or "") + f"; browser failed ({type(e).__name__})"
    return None, None
BROWSER = False
WHY = {}
FEED_VERSION = 2   # bump when a change makes the pull see many older postings for the first time; that run files them under data/backlog.json
def key(n): return re.sub(r"[^a-z0-9]", "", n.lower())
def companies():
    out = []
    for r in csv.DictReader(open("data/companies.csv")):
        ats = (r.get("ATS") or "").strip().lower(); slug = (r.get("Slug") or "").strip()
        ok = ats in FEEDS and slug
        out.append({"name": r["Company"], "status": r["Portal status"], "url": r["Careers address"], "ats": ats if ok else None, "slug": slug if ok else None})
    return out
def fetch_api(co):
    if co["ats"]:
        try:
            r = FEEDS[co["ats"]](co["name"], co["slug"])
            if r: return co["ats"], r
            WHY[co["name"]] = f"{co['ats']} feed empty"
        except Exception as e:
            WHY[co["name"]] = f"{co['ats']} failed ({type(e).__name__})"
        if not co["url"]: return None, None
    first = WHY.pop(co["name"], None)   # why the listed feed failed, kept alongside the careers-page result
    try:   # no known feed, or the listed feed failed: use the careers page
        r = fetch(co["name"], co["url"])
    except Exception as e:
        WHY[co["name"]] = f"careers page failed ({type(e).__name__})"; r = (None, None)
    if first: WHY[co["name"]] = first + ("; " + WHY[co["name"]] if co["name"] in WHY else "")
    return r
def recent(posted, days=2):
    p = str(posted or "")
    if re.search(r"today|yesterday", p, re.I): return True
    m = re.search(r"(\d+)\+? days? ago", p, re.I)
    if m: return int(m.group(1)) <= days
    try: return (dt.datetime.utcnow() - dt.datetime.fromisoformat(p[:19].replace("Z", ""))).days <= days
    except ValueError: return False
def match(j): return TITLE.search(j["title"]) and not SKIP.search(j["title"])
TARGET = re.compile(r"\b(hyderabad|secunderabad|telangana|bangalore|bengaluru|karnataka)\b", re.I)
INDIA = re.compile(r"\b(india|ind)\b", re.I)
REMOTE = re.compile(r"remote|work from home|wfh|anywhere", re.I)
OTHER_IN = re.compile(r"\b(pune|chennai|gurgaon|gurugram|noida|mumbai|delhi|new delhi|kolkata|ahmedabad|jaipur|kochi|coimbatore|chandigarh|mohali|trivandrum|thiruvananthapuram|indore|bhubaneswar|vadodara|mysore|mysuru)\b", re.I)
def india_target(j):
    """Keep Hyderabad / Bangalore roles, remote roles open in India, and India-wide roles that name no other city.
    A blank location is kept (unclear); everything else is dropped."""
    l = (j.get("location") or "").strip()
    if not l: return True
    if TARGET.search(l): return True
    if REMOTE.search(l) and INDIA.search(l): return True
    if INDIA.search(l) and not OTHER_IN.search(l) and re.sub(r"[^a-z]", "", l.lower()) in ("india", "in", "remoteindia", "indiaremote", "ind"): return True
    return False
def safe_detail(j):
    try: detail(j)
    except Exception: j.pop("_detail", None)
def main():
    global BROWSER
    if "--browser" in sys.argv:
        import browser_pull; browser_pull.start(); BROWSER = True
    seen = set(json.load(open("data/seen.json"))) if os.path.exists("data/seen.json") else set()
    old = json.load(open("data/coverage.json")) if os.path.exists("data/coverage.json") else {}
    rewiden = old.get("feed_version", 1) < FEED_VERSION
    known = {i.split("|", 1)[0] for i in seen}
    first = not seen; new, backlog, openm = [], [], []
    cov = {"read": {}, "unreadable": [], "skipped_robots": [], "feed_version": FEED_VERSION}
    todo = []
    for co in companies():
        if co["status"] == "D": cov["skipped_robots"].append(co["name"])
        else: todo.append(co)
    with ThreadPoolExecutor(8) as ex: got = list(ex.map(fetch_api, todo))   # public JSON feeds, read side by side
    for co, (kind, jobs) in zip(todo, got):
        if not jobs and BROWSER and not co["ats"]: kind, jobs = browser_fetch(co["name"], co["url"])   # script-only pages, one at a time
        name = co["name"]
        if not jobs: cov["unreadable"].append(name); cov.setdefault("why", {})[name] = WHY.get(name, "no feed found"); continue
        cov["read"][name] = {"feed": kind, "open": len(jobs), "source": "ats slug" if co["ats"] else "careers page"}
        newco = name not in known
        for j in jobs:
            if match(j): openm.append(j)
            if j["id"] in seen: continue
            seen.add(j["id"])
            if not match(j): continue
            # A company read for the first time, or a run that widens the searches, sees older postings for the first time:
            # those go to the backlog, and only ones posted in the last 2 days count as new.
            if not first and (newco or rewiden) and not recent(j["posted"]): backlog.append(j)
            else: new.append(j)
    if first: new = []
    with ThreadPoolExecutor(8) as ex: list(ex.map(safe_detail, new))   # descriptions for new Workday / SmartRecruiters postings
    for j in openm + backlog: j.pop("_detail", None)
    before = len(new); new = [j for j in new if india_target(j)]; openm = [j for j in openm if india_target(j)]; backlog = [j for j in backlog if india_target(j)]
    cov["outside_target_new_dropped"] = before - len(new)
    cov["run_utc"] = dt.datetime.utcnow().isoformat(); cov["first_run_baseline"] = first
    cov["totals"] = {"companies_read": len(cov["read"]), "open_jobs": sum(v["open"] for v in cov["read"].values()), "open_matching": len(openm),
                     "new": len(new), "new_with_description": sum(1 for j in new if j["desc"]), "backlog_this_run": len(backlog)}
    json.dump(openm, open("data/open_matching.json", "w"), indent=1)  # everything open now that matches titles (for a one-time backlog review)
    json.dump(sorted(seen), open("data/seen.json", "w"))
    json.dump(new, open("data/new_jobs.json", "w"), indent=1)  # first run = baseline only
    if backlog: json.dump(backlog, open("data/backlog.json", "w"), indent=1)  # older postings seen for the first time (new companies / wider searches)
    json.dump(cov, open("data/coverage.json", "w"), indent=1)
    if BROWSER: browser_pull.stop()
    print(f"read {len(cov['read'])}, unreadable {len(cov['unreadable'])}, new {len(new)} ({cov['totals']['new_with_description']} with description), backlog {len(backlog)}, baseline={first}")
main()
