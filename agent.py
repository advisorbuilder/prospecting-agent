"""
UHNW Prospect Agent v2
Pulls from SEC EDGAR, ProPublica IRS 990, Google News RSS,
PR Newswire, and Business Journal RSS feeds.
Scores with Claude. Emails a weekly digest.

Target profiles:
- Big law equity partners (nationwide)
- Hedge fund / PE professionals
- Corporate executives (public + private)
- Financial professionals leaving major firms
- Texas-specific: energy, ag, ranch, tech exits

Priority: Texas always surfaced, but nationwide high-signal
prospects ranked above low-signal Texas ones.
"""

import os, json, smtplib, urllib.request, urllib.parse, re, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

# ── Config ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_TO          = os.environ["EMAIL_TO"]

TODAY   = datetime.utcnow()
CUTOFF  = TODAY - timedelta(days=90)
HEADERS = {"User-Agent": "ProspectAgent/2.0 contact@prospecting.local"}

# Texas cities + nationwide top markets
TEXAS_CITIES    = ["Austin", "Houston", "Dallas", "San Antonio", "Fort Worth",
                   "Midland", "Odessa", "Plano", "Frisco", "The Woodlands"]
NATIONAL_CITIES = ["New York", "Chicago", "San Francisco", "Miami", "Boston",
                   "Los Angeles", "Denver", "Charlotte", "Atlanta", "Seattle"]
ALL_CITIES      = TEXAS_CITIES + NATIONAL_CITIES

# Major firms — departures from these are high-signal
BIG_LAW   = ["Kirkland", "Latham", "Skadden", "Sullivan Cromwell", "Weil Gotshal",
             "Gibson Dunn", "Sidley", "Winston Strawn", "Baker McKenzie",
             "Vinson Elkins", "Haynes Boone", "Jackson Walker", "Bracewell"]
BIG_FINANCE = ["Goldman Sachs", "Blackstone", "KKR", "Carlyle", "Apollo",
               "Bain Capital", "TPG", "Warburg Pincus", "Advent International",
               "Vista Equity", "Lone Star Funds", "Hillwood", "Hunt Companies"]

print_log = []

def log(msg, tag=""):
    line = f"[{TODAY.strftime('%H:%M:%S')}] {tag} {msg}".strip()
    print(line)
    print_log.append(line)

def fetch(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        log(f"Fetch error {url[:60]}: {e}", "⚠")
        return None


# ── 1. SEC EDGAR ─────────────────────────────────────────────────────────────

def fetch_sec(form, label, query=""):
    log(f"Fetching SEC {form}...", "📋")
    q = urllib.parse.quote(query or "Texas")
    url = (
        f"https://efts.sec.gov/LATEST/search-index?q={q}"
        f"&dateRange=custom&startdt={CUTOFF.strftime('%Y-%m-%d')}"
        f"&enddt={TODAY.strftime('%Y-%m-%d')}&forms={form}"
    )
    raw = fetch(url)
    if not raw:
        return []
    try:
        data  = json.loads(raw)
        hits  = data.get("hits", {}).get("hits", [])
        results = []
        for h in hits[:25]:
            s = h.get("_source", {})
            results.append({
                "source":  f"SEC {form}",
                "type":    "sec_filing",
                "entity":  s.get("entity_name", "Unknown"),
                "date":    s.get("file_date", ""),
                "detail":  f"{form} filing — {s.get('entity_name','Unknown')} on {s.get('file_date','')}",
                "url":     f"https://efts.sec.gov/LATEST/search-index?q={q}&forms={form}",
                "texas":   False
            })
        log(f"SEC {form}: {len(results)} results", "✓")
        return results
    except Exception as e:
        log(f"SEC {form} parse error: {e}", "⚠")
        return []

def fetch_sec_formd():
    """Form D = new fund registrations. GP name = PE/hedge fund prospect."""
    log("Fetching SEC Form D (new fund launches)...", "📋")
    results = []
    for state in ["TX", "NY", "IL", "CA", "FL"]:
        url = (
            f"https://efts.sec.gov/LATEST/search-index?q=%22private+equity%22+OR+%22hedge+fund%22"
            f"&dateRange=custom&startdt={CUTOFF.strftime('%Y-%m-%d')}"
            f"&enddt={TODAY.strftime('%Y-%m-%d')}&forms=D"
            f"&hits.hits.total=10"
        )
        raw = fetch(url)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            hits = data.get("hits", {}).get("hits", [])
            for h in hits[:10]:
                s = h.get("_source", {})
                results.append({
                    "source":  "SEC Form D",
                    "type":    "new_fund_launch",
                    "entity":  s.get("entity_name", "Unknown"),
                    "date":    s.get("file_date", ""),
                    "detail":  f"New fund registration — {s.get('entity_name','Unknown')}. GP launching new vehicle.",
                    "url":     "https://efts.sec.gov/LATEST/search-index?forms=D",
                    "texas":   state == "TX"
                })
        except:
            pass
        time.sleep(0.3)
    log(f"SEC Form D: {len(results)} fund launches", "✓")
    return results


# ── 2. GOOGLE NEWS RSS ───────────────────────────────────────────────────────

def parse_rss(raw, source_label, prospect_type, texas=False):
    results = []
    try:
        root = ET.fromstring(raw)
        ns   = {"media": "http://search.yahoo.com/mrss/"}
        for item in root.iter("item"):
            title   = item.findtext("title", "")
            link    = item.findtext("link", "")
            pubdate = item.findtext("pubDate", "")
            desc    = item.findtext("description", "")
            # strip HTML tags from description
            desc_clean = re.sub(r"<[^>]+>", " ", desc).strip()[:300]
            results.append({
                "source":  source_label,
                "type":    prospect_type,
                "entity":  title[:120],
                "date":    pubdate[:20] if pubdate else "",
                "detail":  desc_clean or title,
                "url":     link,
                "texas":   texas
            })
    except Exception as e:
        log(f"RSS parse error ({source_label}): {e}", "⚠")
    return results

def google_news_rss(query, label, prospect_type, texas=False):
    encoded = urllib.parse.quote(query)
    url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    raw     = fetch(url)
    if not raw:
        return []
    results = parse_rss(raw, f"Google News — {label}", prospect_type, texas)[:8]
    log(f"Google News [{label}]: {len(results)} items", "✓")
    return results

def fetch_google_news():
    log("Fetching Google News RSS feeds...", "📰")
    all_results = []

    # ── Big Law partner moves ──────────────────────────────────────────────
    all_results += google_news_rss(
        '"makes partner" OR "promoted to partner" OR "equity partner" law firm',
        "Big Law promotions", "biglaw_partner")
    all_results += google_news_rss(
        '"lateral partner" OR "joins as partner" law firm',
        "Lateral partner moves", "biglaw_partner")
    all_results += google_news_rss(
        '"partner" "law firm" Texas OR Austin OR Houston OR Dallas',
        "Texas law firm partner", "biglaw_partner", texas=True)

    # ── PE / Hedge Fund ────────────────────────────────────────────────────
    all_results += google_news_rss(
        '"private equity" "partner" joins OR departs OR launches',
        "PE partner moves", "pe_hedge")
    all_results += google_news_rss(
        '"hedge fund" launches OR "raises" million',
        "Hedge fund launches", "pe_hedge")
    all_results += google_news_rss(
        " OR ".join([f'"leaves {f}"' for f in BIG_FINANCE[:6]]),
        "Major finance firm departures", "pe_hedge")

    # ── Corporate executives ───────────────────────────────────────────────
    all_results += google_news_rss(
        '"chief executive" OR "CFO" OR "president" retires OR "steps down" OR departs',
        "C-suite departures", "exec_transition")
    all_results += google_news_rss(
        '"executive" Texas retires OR "steps down" OR departs million',
        "Texas exec departures", "exec_transition", texas=True)

    # ── Financial professionals ────────────────────────────────────────────
    all_results += google_news_rss(
        '"managing director" departs OR joins OR launches',
        "MD transitions", "finance_professional")
    all_results += google_news_rss(
        '"portfolio manager" OR "fund manager" launches OR joins OR departs',
        "Fund manager moves", "finance_professional")

    # ── Texas-specific wealth events ───────────────────────────────────────
    all_results += google_news_rss(
        'Texas "acquired" OR "sold" million energy OR "oil and gas" OR ranch OR land',
        "Texas wealth events", "texas_wealth", texas=True)
    all_results += google_news_rss(
        'Austin OR Houston OR Dallas "founder" exit OR acquisition OR "sold company"',
        "Texas founder exits", "tech_exit", texas=True)
    all_results += google_news_rss(
        'Texas "mineral rights" OR "working interest" sold OR acquired',
        "Texas mineral rights", "energy", texas=True)

    # ── Medical / physician ────────────────────────────────────────────────
    all_results += google_news_rss(
        '"physician group" OR "medical group" acquired OR sold Texas OR Houston OR Dallas',
        "Medical group acquisitions", "medical")

    # ── Key national cities ────────────────────────────────────────────────
    for city in ["New York", "Chicago", "Miami"]:
        all_results += google_news_rss(
            f'"{city}" "law firm" partner OR "private equity" OR "hedge fund" joins OR departs OR launches',
            f"{city} financial moves", "national_market")

    log(f"Google News total: {len(all_results)} items", "✓")
    return all_results


# ── 3. PR NEWSWIRE RSS ───────────────────────────────────────────────────────

def fetch_prnewswire():
    log("Fetching PR Newswire RSS...", "📰")
    all_results = []
    queries = [
        ("law firm partner Texas",          "biglaw_partner",       True),
        ("private equity fund Texas",        "pe_hedge",             True),
        ("executive retires Texas",          "exec_transition",      True),
        ("law firm partner joins",           "biglaw_partner",       False),
        ("hedge fund launches",              "pe_hedge",             False),
        ("acquisition Texas million",        "texas_wealth",         True),
    ]
    for query, ptype, texas in queries:
        encoded = urllib.parse.quote(query)
        url     = f"https://www.prnewswire.com/rss/news-releases-list.rss?q={encoded}"
        raw     = fetch(url)
        if raw:
            items = parse_rss(raw, "PR Newswire", ptype, texas)[:5]
            all_results += items
        time.sleep(0.2)
    log(f"PR Newswire: {len(all_results)} items", "✓")
    return all_results


# ── 4. BUSINESS JOURNAL RSS ──────────────────────────────────────────────────

def fetch_bizjournals():
    log("Fetching Business Journal RSS feeds...", "📰")
    feeds = [
        ("https://www.bizjournals.com/austin/feed/news/latest",      "Austin BJ",   True),
        ("https://www.bizjournals.com/houston/feed/news/latest",     "Houston BJ",  True),
        ("https://www.bizjournals.com/dallas/feed/news/latest",      "Dallas BJ",   True),
        ("https://www.bizjournals.com/newyork/feed/news/latest",     "NY BJ",       False),
        ("https://www.bizjournals.com/chicago/feed/news/latest",     "Chicago BJ",  False),
        ("https://www.bizjournals.com/southflorida/feed/news/latest","Miami BJ",    False),
        ("https://www.bizjournals.com/sanfrancisco/feed/news/latest","SF BJ",       False),
    ]
    all_results = []
    keywords = ["partner", "executive", "acquired", "sold", "million", "retires",
                "departs", "joins", "launches", "fund", "equity", "attorney"]
    for url, label, texas in feeds:
        raw = fetch(url)
        if not raw:
            continue
        items = parse_rss(raw, label, "biz_journal", texas)
        # only keep items mentioning wealth-related keywords
        filtered = [i for i in items
                    if any(k.lower() in (i["entity"]+i["detail"]).lower() for k in keywords)]
        all_results += filtered[:6]
        time.sleep(0.2)
    log(f"Business Journals: {len(all_results)} items", "✓")
    return all_results


# ── 5. PROPUBLICA IRS 990 ─────────────────────────────────────────────────────

def fetch_irs_990():
    log("Fetching IRS 990 data...", "📋")
    results  = []
    searches = [
        "Texas energy foundation", "Texas family foundation",
        "Austin foundation",       "Houston foundation",
        "Dallas foundation",       "Texas ranch foundation",
    ]
    for term in searches:
        url = (f"https://projects.propublica.org/nonprofits/api/v2/search.json"
               f"?q={urllib.parse.quote(term)}&state[id]=TX")
        raw = fetch(url)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            for org in data.get("organizations", [])[:4]:
                revenue = org.get("totrevenue", 0) or 0
                assets  = org.get("totassests", 0) or 0
                if revenue < 100000 and assets < 500000:
                    continue  # skip tiny orgs
                results.append({
                    "source":  "IRS 990 / ProPublica",
                    "type":    "philanthropy_signal",
                    "entity":  org.get("name", "Unknown"),
                    "date":    str(org.get("tax_prd_yr", "")),
                    "detail":  (f"{org.get('name','Unknown')} — Texas nonprofit. "
                                f"Revenue: ${revenue:,}. Assets: ${assets:,}. "
                                f"City: {org.get('city','')}"),
                    "url":     f"https://projects.propublica.org/nonprofits/organizations/{org.get('ein','')}",
                    "texas":   True
                })
        except Exception as e:
            log(f"990 parse error: {e}", "⚠")
        time.sleep(0.2)
    log(f"IRS 990: {len(results)} nonprofits", "✓")
    return results


# ── 6. DEDUPLICATE ────────────────────────────────────────────────────────────

def deduplicate(prospects):
    seen, out = set(), []
    for p in prospects:
        key = re.sub(r"\W+", "", p["entity"].lower())[:40]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ── 7. SCORE WITH CLAUDE ──────────────────────────────────────────────────────

def score_with_claude(prospects):
    log(f"Scoring {len(prospects)} signals with Claude...", "🤖")
    if not prospects:
        return []

    # Send in batches of 30 to stay within token limits
    scored_all = []
    batch_size = 15
    for i in range(0, len(prospects), batch_size):
        batch = prospects[i:i+batch_size]
        batch_text = "\n\n".join([
            f"#{j+1} | SOURCE: {p['source']} | TYPE: {p['type']} | TEXAS: {p.get('texas',False)}\n"
            f"ENTITY: {p['entity']}\nDATE: {p['date']}\nDETAIL: {p['detail']}"
            for j, p in enumerate(batch)
        ])

        prompt = f"""You are a senior analyst for a Texas-based financial advisor targeting UHNW individuals ($2M+ wealth events).

Today: {TODAY.strftime('%B %d, %Y')}

TARGET PROFILES (ranked by value):
1. Big law equity partners — capital account payout + high income, often unadvised
2. PE/hedge fund professionals — carry distributions, deferred comp, launching own fund
3. Corporate C-suite executives — deferred comp, LTIP, RSUs on departure
4. Financial professionals leaving major firms (Goldman, Blackstone, KKR etc)
5. Texas energy/mineral rights sellers — Permian, Eagle Ford
6. Texas tech founders post-acquisition
7. Texas ranch/ag land sellers — often first-time liquid
8. Medical group sale participants
9. Large nonprofit founders/donors — philanthropy signal

SCORING RULES:
- HIGH: Clear individual wealth event $2M+, detectable person, actionable now
- MEDIUM: Strong signal but incomplete info, or wealth event likely but not confirmed
- LOW: Weak signal, corporate not individual, or too vague to act on
- SKIP: Clearly irrelevant, no wealth signal, foreign entity

Texas prospects get a small boost but a nationwide HIGH beats a Texas LOW.

For each prospect return a JSON object. Return ONLY a raw JSON array, no markdown, no explanation:
[
  {{
    "rank": "HIGH"|"MEDIUM"|"LOW"|"SKIP",
    "texas": true|false,
    "entity": "name or headline",
    "source": "source name",
    "date": "date string",
    "profile_type": "biglaw"|"pe_hedge"|"exec"|"finance_pro"|"energy"|"tech_exit"|"ag_ranch"|"medical"|"philanthropy"|"other",
    "estimated_wealth_event": "$Xm-$Ym or unknown",
    "why_it_matters": "one sharp sentence for the advisor",
    "action": "specific next step",
    "url": "source url"
  }}
]

RAW SIGNALS:
{batch_text}

URLS: {json.dumps([p.get('url','') for p in batch])}"""

        payload = json.dumps({
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "messages":   [{"role": "user", "content": prompt}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            raw_text = data["content"][0]["text"].strip()
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            batch_scored = json.loads(raw_text.strip())
            scored_all  += batch_scored
            log(f"Batch {i//batch_size+1} scored: {len(batch_scored)} prospects", "✓")
        except Exception as e:
            log(f"Claude scoring error batch {i//batch_size+1}: {e}", "⚠")
        time.sleep(1)

    # Remove SKIPs and sort: Texas HIGH first, then HIGH, then MEDIUM, then LOW
    rank_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "SKIP": 3}
    filtered   = [p for p in scored_all if p.get("rank") != "SKIP"]
    sorted_prospects = sorted(
        filtered,
        key=lambda p: (
            rank_order.get(p.get("rank","LOW"), 2),
            0 if p.get("texas") else 1
        )
    )
    log(f"Final: {len(sorted_prospects)} actionable prospects after scoring", "✓")
    return sorted_prospects


# ── 8. BUILD + SEND EMAIL ─────────────────────────────────────────────────────

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
RANK_LABELS = {"HIGH": "🔴 High Priority", "MEDIUM": "🟡 Worth Watching", "LOW": "⚪ Low Signal"}

def prospect_html(p):
    rank    = p.get("rank","LOW")
    color   = RANK_COLORS.get(rank, "#ccc")
    profile = PROFILE_LABELS.get(p.get("profile_type","other"), "📌 Other")
    texas   = "🤠 Texas" if p.get("texas") else ""
    wealth  = p.get("estimated_wealth_event","")
    return f"""
    <div style="border-left:3px solid {color};padding:12px 16px;margin-bottom:14px;
                background:#fafafa;border-radius:0 6px 6px 0;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px;">
        <div style="font-size:15px;font-weight:600;color:#1a1a1a;flex:1;">{p.get('entity','')[:100]}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <span style="font-size:11px;background:#f0f0f0;padding:2px 8px;border-radius:4px;">{profile}</span>
          {f'<span style="font-size:11px;background:#FFF3CD;padding:2px 8px;border-radius:4px;">{texas}</span>' if texas else ''}
          {f'<span style="font-size:11px;background:#E8F5E9;padding:2px 8px;border-radius:4px;color:#2E7D32;">{wealth}</span>' if wealth and wealth != "unknown" else ''}
        </div>
      </div>
      <div style="font-size:12px;color:#888;margin:4px 0 8px;">{p.get('source','')} · {p.get('date','')[:16]}</div>
      <div style="font-size:14px;color:#333;margin-bottom:8px;line-height:1.5;">{p.get('why_it_matters','')}</div>
      <div style="font-size:13px;color:#555;margin-bottom:8px;">
        <strong style="color:#1a1a1a;">Next step:</strong> {p.get('action','')}
      </div>
      <a href="{p.get('url','#')}" style="font-size:12px;color:#185FA5;text-decoration:none;">View source →</a>
    </div>"""

def build_section(title, prospects):
    if not prospects:
        return ""
    blocks = "".join([prospect_html(p) for p in prospects])
    return f"""
    <h2 style="font-size:16px;font-weight:600;color:#1a1a1a;margin:28px 0 12px;
               padding-bottom:8px;border-bottom:2px solid #eee;">{title} ({len(prospects)})</h2>
    {blocks}"""

def send_digest(scored):
    log("Building and sending email digest...", "📧")

    high   = [p for p in scored if p.get("rank") == "HIGH"]
    medium = [p for p in scored if p.get("rank") == "MEDIUM"]
    low    = [p for p in scored if p.get("rank") == "LOW"]

    # stats by profile type
    from collections import Counter
    type_counts = Counter(p.get("profile_type","other") for p in scored if p.get("rank") in ["HIGH","MEDIUM"])
    top_types   = ", ".join([f"{PROFILE_LABELS.get(k,k)} ({v})" for k,v in type_counts.most_common(3)])
    texas_count = sum(1 for p in scored if p.get("texas") and p.get("rank") in ["HIGH","MEDIUM"])

    stat_box = lambda n, label, color="#1a1a1a": (
        f'<div style="text-align:center;padding:12px;background:#f8f8f8;border-radius:8px;">'
        f'<div style="font-size:24px;font-weight:600;color:{color};">{n}</div>'
        f'<div style="font-size:11px;color:#888;margin-top:2px;">{label}</div></div>'
    )

    html = f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        max-width:660px;margin:0 auto;padding:28px;color:#1a1a1a;">

      <div style="margin-bottom:24px;">
        <h1 style="font-size:22px;font-weight:600;margin-bottom:4px;">UHNW Prospect Digest</h1>
        <p style="font-size:13px;color:#888;">{TODAY.strftime('%A, %B %d, %Y')} · Nationwide + Texas priority</p>
      </div>

      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:24px;">
        {stat_box(len(scored), "Total signals")}
        {stat_box(len(high), "High priority", "#C0392B")}
        {stat_box(texas_count, "Texas signals", "#185FA5")}
        {stat_box(len(medium), "Worth watching", "#E67E22")}
      </div>

      {f'<div style="background:#FFF8E1;border-radius:6px;padding:10px 14px;margin-bottom:20px;font-size:13px;color:#5D4037;"><strong>Top profiles this week:</strong> {top_types}</div>' if top_types else ''}

      {build_section("🔴 High Priority — Act This Week", high)}
      {build_section("🟡 Worth Watching", medium)}
      {build_section("⚪ Low Signal / Background", low) if low else ''}

      <div style="margin-top:32px;padding-top:16px;border-top:1px solid #eee;
                  font-size:11px;color:#aaa;line-height:1.8;">
        Sources: SEC EDGAR (8-K, Form 4, Form D) · IRS 990 / ProPublica · Google News RSS ·
        PR Newswire · Austin/Houston/Dallas/NY/Chicago Business Journals<br>
        Runs every Monday 7:00am Austin time · Reply to unsubscribe
      </div>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (f"Prospects — {len(high)} high priority · "
                      f"{texas_count} Texas · {TODAY.strftime('%b %d')}")
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    log(f"Digest sent → {EMAIL_TO}", "✉️")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    log(f"{'='*55}", "")
    log(f"UHNW Prospect Agent v2 — {TODAY.strftime('%Y-%m-%d %H:%M')} UTC", "🚀")
    log(f"{'='*55}", "")

    raw = []
    raw += fetch_sec("8-K",  "exec departures",     "Texas executive")
    raw += fetch_sec("4",    "insider sales",        "Texas insider")
    raw += fetch_sec_formd()
    raw += fetch_google_news()
    raw += fetch_prnewswire()
    raw += fetch_bizjournals()
    raw += fetch_irs_990()

    log(f"Raw signals collected: {len(raw)}", "📊")
    raw = deduplicate(raw)
    log(f"After deduplication: {len(raw)}", "📊")

    scored = score_with_claude(raw)

    if scored:
        send_digest(scored)
        log("Agent complete ✓", "🏁")
    else:
        log("No scoreable prospects found this run", "⚠")

if __name__ == "__main__":
    main()
