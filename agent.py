"""
UHNW Prospect Agent v3
Pulls from SEC EDGAR, ProPublica IRS 990, Google News RSS,
PR Newswire, GlobeNewswire, and BusinessWire.
Scores with Claude. Emails a weekly digest via SendGrid.

Target profiles:
- Big law equity partners (nationwide)
- Hedge fund / PE professionals
- Corporate C-suite executives
- Financial professionals leaving major firms
- Texas: energy, mineral rights, tech exits, ag/ranch land

Run once on Railway — restartPolicyType = NEVER in railway.toml
"""

import os, json, urllib.request, urllib.parse, re, time, sys
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from collections import Counter

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_TO          = os.environ["EMAIL_TO"]
SENDGRID_API_KEY  = os.environ["SENDGRID_API_KEY"]

TODAY  = datetime.now(timezone.utc).replace(tzinfo=None)
CUTOFF = TODAY - timedelta(days=90)

HEADERS = {"User-Agent": "ProspectAgent/3.0 contact@prospecting.local"}

# Major firms — departures from these score higher
BIG_LAW = [
    "Kirkland", "Latham", "Skadden", "Sullivan Cromwell", "Weil Gotshal",
    "Gibson Dunn", "Sidley", "Winston Strawn", "Baker McKenzie",
    "Vinson Elkins", "Haynes Boone", "Jackson Walker", "Bracewell",
]
BIG_FINANCE = [
    "Goldman Sachs", "Blackstone", "KKR", "Carlyle", "Apollo",
    "Bain Capital", "TPG", "Warburg Pincus", "Vista Equity",
    "Lone Star Funds", "Hillwood", "Hunt Companies",
]
TX_KEYWORDS = [
    "Texas", "Austin", "Houston", "Dallas", "San Antonio",
    "Fort Worth", "Midland", "Odessa", "Permian", "Eagle Ford",
]

print_log = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg, tag=""):
    line = f"[{TODAY.strftime('%H:%M:%S')}] {tag} {msg}".strip()
    print(line, flush=True)
    print_log.append(line)


def clean(text, max_len=150):
    """Strip HTML, entities, non-ASCII, and unsafe chars. Safe for JSON."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)           # HTML tags
    text = re.sub(r"&[a-zA-Z0-9#]+;", " ", text)   # HTML entities
    text = re.sub(r"[^\x20-\x7E]", " ", text)       # non-ASCII
    text = re.sub(r"\s+", " ", text).strip()         # whitespace
    text = text.replace(chr(34), chr(39))            # " → ' (no JSON-breaking quotes)
    text = text.replace("\\", "")                    # no backslashes
    return text[:max_len]


def fetch(url, timeout=15, silent=False):
    """Fetch a URL. Returns bytes or None. Silences 403/404 by default."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        err = str(e)
        is_common = any(code in err for code in ["403", "404", "429"])
        if not silent and not is_common:
            log(f"Fetch error — {url[:70]}: {e}", "⚠")
        return None


def is_texas(text):
    return any(kw in text for kw in TX_KEYWORDS)


def parse_rss(raw, source_label, prospect_type, texas=False):
    """Parse RSS XML into prospect dicts."""
    results = []
    try:
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title   = item.findtext("title", "") or ""
            link    = item.findtext("link", "") or ""
            pubdate = item.findtext("pubDate", "") or ""
            desc    = item.findtext("description", "") or ""
            entity  = clean(title, 150)
            detail  = clean(desc, 150) or entity
            tx      = texas or is_texas(entity + detail)
            if entity:
                results.append({
                    "source": source_label,
                    "type":   prospect_type,
                    "entity": entity,
                    "date":   pubdate[:20],
                    "detail": detail,
                    "url":    link,
                    "texas":  tx,
                })
    except Exception as e:
        log(f"RSS parse error ({source_label}): {e}", "⚠")
    return results


# ── 1. SEC EDGAR ──────────────────────────────────────────────────────────────

def fetch_sec_8k():
    log("Fetching SEC 8-K (exec departures)...", "📋")
    url = (
        "https://efts.sec.gov/LATEST/search-index?q=%22Texas%22"
        f"&dateRange=custom&startdt={CUTOFF.strftime('%Y-%m-%d')}"
        f"&enddt={TODAY.strftime('%Y-%m-%d')}&forms=8-K"
    )
    raw = fetch(url)
    if not raw:
        return []
    try:
        hits = json.loads(raw).get("hits", {}).get("hits", [])
        results = []
        for h in hits[:25]:
            s = h.get("_source", {})
            name = clean(s.get("entity_name", "Unknown"))
            date = s.get("file_date", "")
            results.append({
                "source": "SEC 8-K",
                "type":   "exec_transition",
                "entity": name,
                "date":   date,
                "detail": f"8-K filing by {name} on {date}. Material event — exec departure or major change.",
                "url":    f"https://efts.sec.gov/LATEST/search-index?q=Texas&forms=8-K",
                "texas":  True,
            })
        log(f"SEC 8-K: {len(results)} results", "✓")
        return results
    except Exception as e:
        log(f"SEC 8-K parse error: {e}", "⚠")
        return []


def fetch_sec_form4():
    log("Fetching SEC Form 4 (insider sales)...", "📋")
    url = (
        "https://efts.sec.gov/LATEST/search-index?q=%22Texas%22"
        f"&dateRange=custom&startdt={CUTOFF.strftime('%Y-%m-%d')}"
        f"&enddt={TODAY.strftime('%Y-%m-%d')}&forms=4"
    )
    raw = fetch(url)
    if not raw:
        return []
    try:
        hits = json.loads(raw).get("hits", {}).get("hits", [])
        results = []
        for h in hits[:20]:
            s = h.get("_source", {})
            name = clean(s.get("entity_name", "Unknown"))
            date = s.get("file_date", "")
            results.append({
                "source": "SEC Form 4",
                "type":   "insider_sale",
                "entity": name,
                "date":   date,
                "detail": f"Insider transaction at {name} on {date}. Potential large stock sale.",
                "url":    "https://efts.sec.gov/LATEST/search-index?q=Texas&forms=4",
                "texas":  True,
            })
        log(f"SEC Form 4: {len(results)} results", "✓")
        return results
    except Exception as e:
        log(f"SEC Form 4 parse error: {e}", "⚠")
        return []


def fetch_sec_formd():
    """Form D = new fund registrations. One query per key state, deduped."""
    log("Fetching SEC Form D (new fund launches)...", "📋")
    results = []
    seen    = set()
    # Query once per state with state param — not repeated identical URLs
    for state_code in ["TX", "NY", "IL", "CA", "FL"]:
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q=%22private+equity%22+OR+%22hedge+fund%22"
            f"&dateRange=custom&startdt={CUTOFF.strftime('%Y-%m-%d')}"
            f"&enddt={TODAY.strftime('%Y-%m-%d')}"
            f"&forms=D&entity={urllib.parse.quote(state_code)}"
        )
        raw = fetch(url)
        if not raw:
            time.sleep(0.3)
            continue
        try:
            hits = json.loads(raw).get("hits", {}).get("hits", [])
            for h in hits[:8]:
                s    = h.get("_source", {})
                name = clean(s.get("entity_name", "Unknown"))
                date = s.get("file_date", "")
                key  = re.sub(r"\W+", "", name.lower())[:40]
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "source": "SEC Form D",
                    "type":   "new_fund_launch",
                    "entity": name,
                    "date":   date,
                    "detail": f"New {state_code} fund registration. GP launching new PE or hedge fund vehicle.",
                    "url":    "https://efts.sec.gov/LATEST/search-index?forms=D",
                    "texas":  state_code == "TX",
                })
        except Exception as e:
            log(f"SEC Form D parse error ({state_code}): {e}", "⚠")
        time.sleep(0.4)
    log(f"SEC Form D: {len(results)} fund launches", "✓")
    return results


# ── 2. GOOGLE NEWS RSS ────────────────────────────────────────────────────────

def gnews(query, label, ptype, texas=False):
    """Single Google News RSS query with rate-limit sleep."""
    time.sleep(0.5)  # respectful rate limiting
    encoded = urllib.parse.quote(query)
    url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    raw     = fetch(url)
    if not raw:
        return []
    items = parse_rss(raw, f"Google News — {label}", ptype, texas)[:8]
    log(f"Google News [{label}]: {len(items)} items", "✓")
    return items


def fetch_google_news():
    log("Fetching Google News RSS...", "📰")
    results = []

    # Big Law
    results += gnews('"makes partner" OR "promoted to partner" law firm',
                     "Big Law promotions", "biglaw")
    results += gnews('"lateral partner" OR "joins as partner" law firm',
                     "Lateral moves", "biglaw")
    results += gnews('"partner" "law firm" Texas OR Austin OR Houston OR Dallas',
                     "Texas law firm", "biglaw", texas=True)

    # PE / Hedge Fund
    results += gnews('"private equity" partner joins OR departs OR launches',
                     "PE partner moves", "pe_hedge")
    results += gnews('"hedge fund" launches OR raises million',
                     "Hedge fund launches", "pe_hedge")
    results += gnews(
        " OR ".join([f'"leaves {f}"' for f in BIG_FINANCE[:5]]),
        "Finance firm departures", "pe_hedge")

    # Corporate executives
    results += gnews('"chief executive" OR "CFO" OR "president" retires OR "steps down" OR departs',
                     "C-suite departures", "exec")
    results += gnews('"executive" Texas retires OR "steps down" OR departs million',
                     "Texas exec departures", "exec", texas=True)

    # Financial professionals
    results += gnews('"managing director" departs OR joins OR launches',
                     "MD transitions", "finance_pro")
    results += gnews('"portfolio manager" OR "fund manager" launches OR joins OR departs',
                     "Fund manager moves", "finance_pro")

    # Texas wealth events
    results += gnews('Texas acquired OR sold million energy OR "oil and gas" OR ranch OR land',
                     "Texas wealth events", "energy", texas=True)
    results += gnews('Austin OR Houston OR Dallas founder exit OR acquisition OR "sold company"',
                     "Texas founder exits", "tech_exit", texas=True)
    results += gnews('Texas "mineral rights" OR "working interest" sold OR acquired',
                     "Texas mineral rights", "energy", texas=True)

    # Medical
    results += gnews('"physician group" OR "medical group" acquired OR sold Texas OR Houston OR Dallas',
                     "Medical group acquisitions", "medical")

    # Key national markets
    for city in ["New York", "Chicago", "Miami"]:
        results += gnews(
            f'"{city}" "law firm" partner OR "private equity" OR "hedge fund" joins OR launches',
            f"{city} moves", "biglaw")

    log(f"Google News total: {len(results)} items", "✓")
    return results


# ── 3. PR NEWSWIRE ────────────────────────────────────────────────────────────

def fetch_prnewswire():
    log("Fetching PR Newswire RSS...", "📰")
    queries = [
        ("law firm partner Texas",     "biglaw",  True),
        ("private equity fund Texas",  "pe_hedge", True),
        ("executive retires Texas",    "exec",     True),
        ("law firm lateral partner",   "biglaw",   False),
        ("hedge fund launches",        "pe_hedge", False),
        ("acquisition Texas million",  "energy",   True),
    ]
    results = []
    for query, ptype, texas in queries:
        url = f"https://www.prnewswire.com/rss/news-releases-list.rss?q={urllib.parse.quote(query)}"
        raw = fetch(url, silent=True)
        if raw:
            results += parse_rss(raw, "PR Newswire", ptype, texas)[:5]
        time.sleep(0.3)
    log(f"PR Newswire: {len(results)} items", "✓")
    return results


# ── 4. OPEN NEWS (GlobeNewswire + BusinessWire) ───────────────────────────────

def fetch_open_news():
    log("Fetching GlobeNewswire + BusinessWire RSS...", "📰")
    feeds = [
        ("https://www.globenewswire.com/RssFeed/subjectcode/15-Legal+Affairs",
         "GlobeNewswire Legal", "biglaw"),
        ("https://www.globenewswire.com/RssFeed/subjectcode/2-Mergers+%26+Acquisitions",
         "GlobeNewswire M&A", "exec"),
        ("https://www.globenewswire.com/RssFeed/subjectcode/9-Management+Changes",
         "GlobeNewswire Exec Changes", "exec"),
        ("https://feed.businesswire.com/rss/home/?rss=G22&rssid=22",
         "BusinessWire Finance", "pe_hedge"),
        ("https://feed.businesswire.com/rss/home/?rss=G7&rssid=7",
         "BusinessWire Legal", "biglaw"),
    ]
    keywords = [
        "partner", "executive", "acquired", "sold", "million", "retires",
        "departs", "joins", "launches", "fund", "equity", "attorney",
        "private equity", "hedge fund", "merger", "law firm", "counsel",
    ]
    results = []
    for url, label, ptype in feeds:
        raw = fetch(url, silent=True)
        if not raw:
            continue
        items    = parse_rss(raw, label, ptype)
        filtered = [i for i in items
                    if any(k.lower() in (i["entity"] + i["detail"]).lower()
                           for k in keywords)]
        results += filtered[:8]
        time.sleep(0.3)
    log(f"Open news: {len(results)} items", "✓")
    return results


# ── 5. IRS 990 / PROPUBLICA ───────────────────────────────────────────────────

def fetch_irs_990():
    log("Fetching IRS 990 (Texas philanthropy)...", "📋")
    searches = [
        "Texas energy foundation", "Texas family foundation",
        "Austin foundation", "Houston foundation",
        "Dallas foundation", "Texas ranch foundation",
    ]
    results = []
    for term in searches:
        url = (
            "https://projects.propublica.org/nonprofits/api/v2/search.json"
            f"?q={urllib.parse.quote(term)}&state%5Bid%5D=TX"
        )
        raw = fetch(url)
        if not raw:
            time.sleep(0.3)
            continue
        try:
            orgs = json.loads(raw).get("organizations", [])
            for org in orgs[:4]:
                revenue = org.get("totrevenue", 0) or 0
                assets  = org.get("totassests", 0) or 0
                if revenue < 100_000 and assets < 500_000:
                    continue
                name = clean(org.get("name", "Unknown"))
                results.append({
                    "source": "IRS 990 / ProPublica",
                    "type":   "philanthropy",
                    "entity": name,
                    "date":   str(org.get("tax_prd_yr", "")),
                    "detail": (f"{name} Texas nonprofit. "
                               f"Revenue ${revenue:,}. Assets ${assets:,}. "
                               f"City: {org.get('city', '')}"),
                    "url":    f"https://projects.propublica.org/nonprofits/organizations/{org.get('ein','')}",
                    "texas":  True,
                })
        except Exception as e:
            log(f"990 parse error: {e}", "⚠")
        time.sleep(0.3)
    log(f"IRS 990: {len(results)} nonprofits", "✓")
    return results


# ── 6. DEDUPLICATE ────────────────────────────────────────────────────────────

def deduplicate(prospects):
    seen, out = set(), []
    for p in prospects:
        key = re.sub(r"\W+", "", p["entity"].lower())[:40]
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ── 7. SCORE WITH CLAUDE ──────────────────────────────────────────────────────

BATCH_SIZE = 8

def score_batch(batch, batch_num):
    """Score one batch. Returns list of scored prospects."""
    batch_text = "\n\n".join([
        f"#{j+1} | SRC:{clean(p['source'],40)} | TYPE:{p['type']} | TX:{p.get('texas',False)}\n"
        f"ENTITY:{clean(p['entity'],100)}\n"
        f"DATE:{p['date'][:20]}\n"
        f"DETAIL:{clean(p['detail'],120)}"
        for j, p in enumerate(batch)
    ])

    # Pass URLs separately so they don't pollute the text
    urls = [p.get("url", "") for p in batch]

    prompt = (
        f"You are a senior analyst for a Texas-based financial advisor targeting UHNW individuals ($2M+ wealth events).\n"
        f"Today: {TODAY.strftime('%B %d, %Y')}\n\n"
        "TARGET PROFILES (ranked by value):\n"
        "1. Big law equity partners — capital account payout, often unadvised\n"
        "2. PE/hedge fund professionals — carry, deferred comp, new fund launch\n"
        "3. Corporate C-suite executives — deferred comp, LTIP, RSUs on departure\n"
        "4. Financial professionals leaving Goldman, Blackstone, KKR, etc\n"
        "5. Texas energy/mineral rights sellers — Permian, Eagle Ford\n"
        "6. Texas tech founders post-acquisition\n"
        "7. Texas ranch/ag land sellers — first-time liquid\n"
        "8. Medical group sale participants\n"
        "9. Texas nonprofit founders/large donors\n\n"
        "SCORING:\n"
        "HIGH = clear individual $2M+ event, actionable now\n"
        "MEDIUM = strong signal, incomplete info\n"
        "LOW = weak signal, corporate not individual\n"
        "SKIP = irrelevant, foreign, no wealth signal\n\n"
        "Texas gets a small boost. Nationwide HIGH beats Texas LOW.\n\n"
        "OUTPUT RULES — CRITICAL:\n"
        "- Return ONLY a JSON array. Zero other text.\n"
        "- Start your response with [ and end with ]\n"
        "- All string values must be ASCII only, no special chars\n"
        "- why_it_matters: max 100 chars\n"
        "- action: max 80 chars\n\n"
        'Example: [{"rank":"HIGH","texas":true,"entity":"Name","source":"src",'
        '"date":"2026-01-01","profile_type":"biglaw","estimated_wealth_event":"$5m",'
        '"why_it_matters":"Why this matters","action":"Next step","url":"http://x.com"}]\n\n'
        "profile_type must be one of: biglaw, pe_hedge, exec, finance_pro, energy, tech_exit, ag_ranch, medical, philanthropy, other\n\n"
        f"SIGNALS:\n{batch_text}\n\n"
        f"URLS (match by position): {json.dumps(urls)}"
    )

    payload = json.dumps({
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw_text = json.loads(r.read())["content"][0]["text"].strip()

        # Extract JSON array — find outermost [ ... ]
        start = raw_text.find("[")
        end   = raw_text.rfind("]")
        if start == -1 or end == -1:
            log(f"Batch {batch_num}: no JSON array found in response", "⚠")
            return []
        raw_text = raw_text[start:end + 1]

        try:
            scored = json.loads(raw_text)
            log(f"Batch {batch_num}: {len(scored)} prospects scored", "✓")
            return scored
        except json.JSONDecodeError:
            # Attempt repair: close unclosed braces then array
            raw_text = raw_text.rstrip().rstrip(",")
            open_braces = raw_text.count("{") - raw_text.count("}")
            if open_braces > 0:
                raw_text += "}" * open_braces
            if not raw_text.endswith("]"):
                raw_text += "]"
            try:
                scored = json.loads(raw_text)
                log(f"Batch {batch_num}: repaired JSON, {len(scored)} prospects", "✓")
                return scored
            except Exception:
                log(f"Batch {batch_num}: JSON unrecoverable, skipping", "⚠")
                return []

    except Exception as e:
        log(f"Batch {batch_num}: API error — {e}", "⚠")
        return []


def score_with_claude(prospects):
    log(f"Scoring {len(prospects)} signals with Claude ({BATCH_SIZE}/batch)...", "🤖")
    if not prospects:
        return []

    scored_all = []
    for i in range(0, len(prospects), BATCH_SIZE):
        batch  = prospects[i:i + BATCH_SIZE]
        scored = score_batch(batch, i // BATCH_SIZE + 1)
        scored_all += scored
        time.sleep(1.5)  # rate limit between batches

    rank_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "SKIP": 3}
    filtered   = [p for p in scored_all if p.get("rank") not in ("SKIP", None)]
    sorted_out = sorted(
        filtered,
        key=lambda p: (
            rank_order.get(p.get("rank", "LOW"), 2),
            0 if p.get("texas") else 1,
        ),
    )
    log(f"Scoring complete: {len(sorted_out)} actionable prospects", "✓")
    return sorted_out


# ── 8. EMAIL DIGEST ───────────────────────────────────────────────────────────

PROFILE_LABELS = {
    "biglaw":       "⚖️ Big Law Partner",
    "pe_hedge":     "💼 PE / Hedge Fund",
    "exec":         "🏢 Corporate Executive",
    "finance_pro":  "🏦 Finance Professional",
    "energy":       "⛽ Energy / O&G",
    "tech_exit":    "💻 Tech Exit",
    "ag_ranch":     "🌾 Ag / Ranch",
    "medical":      "🏥 Medical",
    "philanthropy": "❤️ Philanthropy Signal",
    "other":        "📌 Other",
}
RANK_COLORS = {"HIGH": "#C0392B", "MEDIUM": "#E67E22", "LOW": "#95A5A6"}


def prospect_card(p):
    color   = RANK_COLORS.get(p.get("rank", "LOW"), "#ccc")
    profile = PROFILE_LABELS.get(p.get("profile_type", "other"), "📌 Other")
    texas   = "🤠 Texas" if p.get("texas") else ""
    wealth  = p.get("estimated_wealth_event", "")
    badges  = "".join([
        f'<span style="font-size:11px;background:#f0f0f0;padding:2px 8px;border-radius:4px;margin-right:4px;">{profile}</span>',
        f'<span style="font-size:11px;background:#FFF3CD;padding:2px 8px;border-radius:4px;margin-right:4px;">{texas}</span>' if texas else "",
        f'<span style="font-size:11px;background:#E8F5E9;padding:2px 8px;border-radius:4px;color:#2E7D32;">{wealth}</span>' if wealth and wealth != "unknown" else "",
    ])
    return (
        f'<div style="border-left:3px solid {color};padding:12px 16px;margin-bottom:14px;'
        f'background:#fafafa;border-radius:0 6px 6px 0;">'
        f'<div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:4px;">{p.get("entity","")[:120]}</div>'
        f'<div style="margin-bottom:6px;">{badges}</div>'
        f'<div style="font-size:12px;color:#888;margin-bottom:8px;">{p.get("source","")} · {p.get("date","")[:16]}</div>'
        f'<div style="font-size:14px;color:#333;margin-bottom:8px;line-height:1.5;">{p.get("why_it_matters","")}</div>'
        f'<div style="font-size:13px;color:#555;margin-bottom:8px;"><strong>Next step:</strong> {p.get("action","")}</div>'
        f'<a href="{p.get("url","#")}" style="font-size:12px;color:#185FA5;text-decoration:none;">View source →</a>'
        f'</div>'
    )


def section_html(title, prospects):
    if not prospects:
        return ""
    cards = "".join(prospect_card(p) for p in prospects)
    return (
        f'<h2 style="font-size:16px;font-weight:600;color:#1a1a1a;margin:28px 0 12px;'
        f'padding-bottom:8px;border-bottom:2px solid #eee;">{title} ({len(prospects)})</h2>'
        f'{cards}'
    )


def stat_box(n, label, color="#1a1a1a"):
    return (
        f'<div style="text-align:center;padding:12px;background:#f8f8f8;border-radius:8px;">'
        f'<div style="font-size:24px;font-weight:600;color:{color};">{n}</div>'
        f'<div style="font-size:11px;color:#888;margin-top:2px;">{label}</div></div>'
    )


def send_digest(scored):
    log("Building email digest...", "📧")

    high   = [p for p in scored if p.get("rank") == "HIGH"]
    medium = [p for p in scored if p.get("rank") == "MEDIUM"]
    low    = [p for p in scored if p.get("rank") == "LOW"]

    type_counts = Counter(
        p.get("profile_type", "other") for p in scored
        if p.get("rank") in ("HIGH", "MEDIUM")
    )
    top_types   = ", ".join(
        f"{PROFILE_LABELS.get(k, k)} ({v})"
        for k, v in type_counts.most_common(3)
    )
    texas_count = sum(1 for p in scored if p.get("texas") and p.get("rank") in ("HIGH", "MEDIUM"))

    no_results_msg = ""
    if not scored:
        no_results_msg = (
            '<div style="background:#FFF8E1;border-radius:6px;padding:16px;margin:20px 0;'
            'font-size:14px;color:#5D4037;">'
            'No high-signal prospects found this week. All sources ran successfully. '
            'Try again next Monday or adjust search criteria.</div>'
        )

    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
        'max-width:660px;margin:0 auto;padding:28px;color:#1a1a1a;">'
        '<h1 style="font-size:22px;font-weight:600;margin-bottom:4px;">UHNW Prospect Digest</h1>'
        f'<p style="font-size:13px;color:#888;margin-bottom:24px;">'
        f'{TODAY.strftime("%A, %B %d, %Y")} · Nationwide + Texas priority</p>'
        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:24px;">'
        f'{stat_box(len(scored), "Total signals")}'
        f'{stat_box(len(high), "High priority", "#C0392B")}'
        f'{stat_box(texas_count, "Texas signals", "#185FA5")}'
        f'{stat_box(len(medium), "Worth watching", "#E67E22")}'
        '</div>'
        + (f'<div style="background:#FFF8E1;border-radius:6px;padding:10px 14px;margin-bottom:20px;'
           f'font-size:13px;color:#5D4037;"><strong>Top profiles:</strong> {top_types}</div>'
           if top_types else "")
        + no_results_msg
        + section_html("🔴 High Priority — Act This Week", high)
        + section_html("🟡 Worth Watching", medium)
        + (section_html("⚪ Low Signal", low) if low else "")
        + '<div style="margin-top:32px;padding-top:16px;border-top:1px solid #eee;'
          'font-size:11px;color:#aaa;line-height:1.8;">'
          'Sources: SEC EDGAR (8-K, Form 4, Form D) · IRS 990 · Google News · '
          'PR Newswire · GlobeNewswire · BusinessWire<br>'
          'Runs every Monday 7:00am Austin time</div>'
          '</body></html>'
    )

    subject = (
        f"Prospects — {len(high)} high priority · {texas_count} Texas · {TODAY.strftime('%b %d')}"
        if scored else
        f"Prospect Digest — quiet week · {TODAY.strftime('%b %d')}"
    )

    payload = json.dumps({
        "personalizations": [{"to": [{"email": EMAIL_TO}]}],
        "from":    {"email": EMAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }).encode()

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type":  "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log(f"Digest sent → {EMAIL_TO} (HTTP {r.status})", "✉️")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        log(f"SendGrid error {e.code}: {body}", "⚠")
        raise


# ── MAIN ──────────────────────────────────────────────────────────────────────

CACHE_RAW    = "/tmp/agent_raw.json"
CACHE_SCORED = "/tmp/agent_scored.json"


def save(path, data):
    """Save data to disk. Logged so you can see it in Railway logs."""
    try:
        with open(path, "w") as f:
            json.dump(data, f)
        log(f"Saved {len(data)} items → {path}", "💾")
    except Exception as e:
        log(f"Save failed ({path}): {e}", "⚠")


def load(path):
    """Load cached data from disk. Returns None if not found."""
    try:
        with open(path) as f:
            data = json.load(f)
        log(f"Loaded {len(data)} cached items from {path}", "💾")
        return data
    except Exception:
        return None


def main():
    log("=" * 55, "")
    log(f"UHNW Prospect Agent v3 — {TODAY.strftime('%Y-%m-%d %H:%M')} UTC", "🚀")
    log("=" * 55, "")

    # ── Phase 1: Fetch ───────────────────────────────────────
    # Check for cached raw data first — skip fetch if already done this run
    raw = load(CACHE_RAW)
    if raw:
        log("Resuming from cached raw signals — skipping fetch phase", "⏩")
    else:
        log("Phase 1: Fetching signals...", "")
        raw  = []
        raw += fetch_sec_8k()
        raw += fetch_sec_form4()
        raw += fetch_sec_formd()
        raw += fetch_google_news()
        raw += fetch_prnewswire()
        raw += fetch_open_news()
        raw += fetch_irs_990()

        log(f"Raw signals: {len(raw)}", "📊")
        raw = deduplicate(raw)
        log(f"After deduplication: {len(raw)}", "📊")
        save(CACHE_RAW, raw)  # save before expensive scoring step

    # ── Phase 2: Score ───────────────────────────────────────
    # Check for cached scored data — skip scoring if already done
    scored = load(CACHE_SCORED)
    if scored:
        log("Resuming from cached scored prospects — skipping scoring phase", "⏩")
    else:
        log("Phase 2: Scoring with Claude...", "")
        scored = score_with_claude(raw)
        save(CACHE_SCORED, scored)  # save before email step

    # ── Phase 3: Send ────────────────────────────────────────
    log("Phase 3: Sending email digest...", "")
    send_digest(scored)  # always send — even quiet weeks

    # Clean up cache files so next scheduled run starts fresh
    for path in [CACHE_RAW, CACHE_SCORED]:
        try:
            import os as _os
            _os.remove(path)
        except Exception:
            pass

    log("Agent complete ✓", "🏁")
    sys.exit(0)  # clean exit — Railway will not restart (NEVER policy)


if __name__ == "__main__":
    main()
