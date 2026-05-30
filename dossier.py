"""
UHNW Prospect Dossier Generator
Triggered by Pipedream when advisor approves a prospect from the weekly email.
Receives prospect data, researches them, builds a full dossier, emails it.

Pipedream sends prospect data as environment variables:
PROSPECT_NAME, PROSPECT_TYPE, PROSPECT_SOURCE, PROSPECT_WEALTH,
PROSPECT_WHY, PROSPECT_URL, PROSPECT_TEXAS, PROSPECT_DATE
"""

import os, json, urllib.request, urllib.parse, sys
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SENDGRID_API_KEY  = os.environ["SENDGRID_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_TO          = os.environ["EMAIL_TO"]

# Prospect data passed from Pipedream
PROSPECT = {
    "name":    os.environ.get("PROSPECT_NAME", "Unknown prospect"),
    "type":    os.environ.get("PROSPECT_TYPE", "other"),
    "source":  os.environ.get("PROSPECT_SOURCE", ""),
    "wealth":  os.environ.get("PROSPECT_WEALTH", "unknown"),
    "why":     os.environ.get("PROSPECT_WHY", ""),
    "url":     os.environ.get("PROSPECT_URL", ""),
    "texas":   os.environ.get("PROSPECT_TEXAS", "False") == "True",
    "date":    os.environ.get("PROSPECT_DATE", ""),
}

TODAY = datetime.now(timezone.utc).replace(tzinfo=None)

PROFILE_LABELS = {
    "biglaw":       "Big Law Partner",
    "pe_hedge":     "PE / Hedge Fund",
    "exec":         "Corporate Executive",
    "finance_pro":  "Finance Professional",
    "energy":       "Energy / O&G",
    "tech_exit":    "Tech Exit",
    "ag_ranch":     "Ag / Ranch",
    "medical":      "Medical",
    "philanthropy": "Philanthropy Signal",
    "equity_grant": "Equity Grant",
    "concentrated_holder": "Concentrated Holder",
    "other":        "Other",
}


def log(msg):
    print(f"[{TODAY.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(url, timeout=15):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DossierAgent/1.0 contact@prospecting.local"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        log(f"Fetch error {url[:60]}: {e}")
        return None


def research_prospect(prospect):
    """
    Use Claude to build a comprehensive dossier.
    Pulls context from the prospect data and generates full research brief.
    """
    log(f"Building dossier for: {prospect['name']}")

    profile_label = PROFILE_LABELS.get(prospect["type"], "Other")
    texas_note    = "Texas-based or Texas-connected." if prospect["texas"] else "Nationwide prospect."

    prompt = f"""You are a senior research analyst for a Texas-based UHNW financial advisor.
The advisor has approved this prospect for a full dossier. Build the most detailed, actionable research brief possible.

PROSPECT DATA:
Name/Description: {prospect['name']}
Profile Type: {profile_label}
Signal Source: {prospect['source']}
Estimated Wealth: {prospect['wealth']}
Signal Date: {prospect['date']}
Geography: {texas_note}
Why Flagged: {prospect['why']}
Source URL: {prospect['url']}

Today's date: {TODAY.strftime('%B %d, %Y')}

Build a comprehensive dossier. Be specific, direct, and advisor-focused. No filler.

Return ONLY a valid JSON object with exactly these fields. ASCII only, no special characters:

{{
  "name": "full name or best description",
  "subtitle": "role, company, location, trigger event in one line",
  "profile_type": "{prospect['type']}",
  "priority": "HIGH|MEDIUM|LOW",
  "estimated_wealth": "specific range with reasoning e.g. $5M-$10M based on equity grant size",
  "urgency_window": "specific window e.g. Act within 30 days — pre-vesting conversation",
  "advisor_fit_score": 8,
  "why_right_now": "2-3 sentences on why the timing is right. What happened, why it creates a planning need, why they are likely unadvised right now.",
  "background": "2-3 sentences on who this person is, career arc, and what makes them notable.",
  "wealth_signals": [
    "specific signal 1 with source",
    "specific signal 2 with source",
    "specific signal 3 with source",
    "specific signal 4 with source"
  ],
  "planning_needs": "2-3 sentences on the specific financial planning issues this person faces RIGHT NOW. Be specific to their profile type — not generic.",
  "approach_angle": "The single most compelling reason to reach out and what to lead with. One sharp sentence.",
  "what_not_to_do": "The one mistake advisors make with this profile type. One sentence.",
  "first_message": "A warm, personalized 4-5 sentence first outreach message. Not salesy. Educational. References their specific trigger event. Written as if from the advisor. Ready to send with minor edits.",
  "follow_up_message": "A follow-up message for 2 weeks later if no response. Different angle. Still warm.",
  "research_links": [
    "LinkedIn search: [name] + [company]",
    "SEC EDGAR: [specific search]",
    "Google: [specific search string]",
    "Other specific source"
  ],
  "conversation_starters": [
    "Topic 1 to bring up in first call",
    "Topic 2",
    "Topic 3"
  ],
  "red_flags": "Any reasons to be cautious or lower priority. One sentence or None."
}}"""

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
        start = raw_text.find("{")
        end   = raw_text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw_text[start:end+1])
    except Exception as e:
        log(f"Claude error: {e}")
    return None


def build_dossier_email(d, prospect):
    """Build a rich HTML dossier email."""

    def section(title, icon, content_html):
        return f'''
        <div style="margin-bottom:20px;padding:16px 20px;background:#fafafa;
                    border-radius:8px;border:0.5px solid #e8e8e8;">
          <div style="font-size:11px;font-weight:600;color:#888;text-transform:uppercase;
                      letter-spacing:0.06em;margin-bottom:10px;">
            {icon} {title}
          </div>
          {content_html}
        </div>'''

    def bullet_list(items):
        if not items:
            return ""
        lis = "".join(
            f'<li style="padding:5px 0;border-bottom:0.5px solid #eee;font-size:14px;'
            f'color:#333;line-height:1.6;">{item}</li>'
            for item in items
        )
        return f'<ul style="list-style:none;padding:0;margin:0;">{lis}</ul>'

    def message_box(text, color="#E6F1FB", text_color="#185FA5"):
        return (
            f'<div style="background:{color};border-radius:6px;padding:14px 16px;'
            f'font-size:14px;color:#333;line-height:1.8;white-space:pre-wrap;'
            f'border-left:3px solid {text_color};">{text}</div>'
        )

    priority     = d.get("priority", "MEDIUM")
    p_color      = {"HIGH": "#C0392B", "MEDIUM": "#E67E22", "LOW": "#95A5A6"}.get(priority, "#888")
    profile      = PROFILE_LABELS.get(d.get("profile_type", "other"), "Other")
    texas_badge  = '<span style="background:#FFF3CD;color:#856404;font-size:11px;padding:3px 8px;border-radius:4px;margin-left:6px;">Texas</span>' if prospect["texas"] else ""
    fit_score    = d.get("advisor_fit_score", 0)
    fit_color    = "#0F6E56" if fit_score >= 8 else "#E67E22" if fit_score >= 6 else "#95A5A6"

    red_flags_html = ""
    if d.get("red_flags") and d["red_flags"].lower() not in ("none", "n/a", ""):
        red_flags_html = section("Red flags", "⚠️",
            f'<p style="font-size:14px;color:#A32D2D;margin:0;">{d["red_flags"]}</p>')

    html = f'''<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
        max-width:680px;margin:0 auto;padding:28px;color:#1a1a1a;background:#fff;">

      <div style="margin-bottom:6px;font-size:12px;color:#aaa;letter-spacing:0.04em;">
        PROSPECT DOSSIER · {TODAY.strftime("%B %d, %Y").upper()}
      </div>

      <div style="margin-bottom:20px;padding-bottom:20px;border-bottom:2px solid #f0f0f0;">
        <h1 style="font-size:24px;font-weight:600;margin:0 0 6px;">{d.get("name","")}</h1>
        <p style="font-size:14px;color:#555;margin:0 0 12px;">{d.get("subtitle","")}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <span style="background:{p_color};color:#fff;font-size:12px;font-weight:600;
                       padding:4px 10px;border-radius:4px;">{priority} PRIORITY</span>
          <span style="background:#f0f0f0;color:#555;font-size:12px;padding:4px 10px;border-radius:4px;">{profile}</span>
          {texas_badge}
          <span style="font-size:12px;color:{fit_color};font-weight:600;">Advisor fit: {fit_score}/10</span>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px;">
        <div style="text-align:center;padding:12px;background:#f8f8f8;border-radius:8px;">
          <div style="font-size:16px;font-weight:600;">{d.get("estimated_wealth","")}</div>
          <div style="font-size:11px;color:#888;margin-top:2px;">Estimated wealth</div>
        </div>
        <div style="text-align:center;padding:12px;background:#f8f8f8;border-radius:8px;">
          <div style="font-size:16px;font-weight:600;">{d.get("urgency_window","")}</div>
          <div style="font-size:11px;color:#888;margin-top:2px;">Window</div>
        </div>
        <div style="text-align:center;padding:12px;background:#f8f8f8;border-radius:8px;">
          <div style="font-size:16px;font-weight:600;">{prospect["source"].split("—")[0].strip()}</div>
          <div style="font-size:11px;color:#888;margin-top:2px;">Signal source</div>
        </div>
      </div>

      {section("Why right now", "⏰", f'<p style="font-size:14px;color:#333;line-height:1.7;margin:0;">{d.get("why_right_now","")}</p>')}

      {section("Background", "👤", f'<p style="font-size:14px;color:#333;line-height:1.7;margin:0;">{d.get("background","")}</p>')}

      {section("Wealth signals", "📡", bullet_list(d.get("wealth_signals",[])))}

      {section("Planning needs right now", "📊", f'<p style="font-size:14px;color:#333;line-height:1.7;margin:0;">{d.get("planning_needs","")}</p>')}

      {section("Lead with this", "🎯",
        f'<div style="border-left:3px solid #378ADD;padding:10px 14px;background:#E6F1FB;border-radius:0 6px 6px 0;margin-bottom:10px;">'
        f'<p style="font-size:14px;color:#0C447C;margin:0;line-height:1.6;">{d.get("approach_angle","")}</p></div>'
        f'<div style="border-left:3px solid #E24B4A;padding:10px 14px;background:#FCEBEB;border-radius:0 6px 6px 0;">'
        f'<p style="font-size:12px;color:#A32D2D;font-weight:600;margin:0 0 4px;">DO NOT</p>'
        f'<p style="font-size:14px;color:#791F1F;margin:0;line-height:1.6;">{d.get("what_not_to_do","")}</p></div>'
      )}

      {section("First message — ready to send", "✉️", message_box(d.get("first_message","")))}

      {section("Follow-up message — 2 weeks later", "↩️", message_box(d.get("follow_up_message",""), "#F1EFE8", "#5F5E5A"))}

      {section("Conversation starters for first call", "💬", bullet_list(d.get("conversation_starters",[])))}

      {section("Where to research further", "🔍", bullet_list(d.get("research_links",[])))}

      {red_flags_html}

      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;
                  font-size:11px;color:#aaa;line-height:1.8;">
        Dossier generated {TODAY.strftime("%B %d, %Y at %I:%M %p")} UTC ·
        Signal: {prospect["source"]} · {prospect["date"]} ·
        <a href="{prospect["url"]}" style="color:#aaa;">View original source</a>
      </div>
    </body></html>'''

    return html


def send_dossier(html, prospect):
    """Send the dossier email via SendGrid."""
    name    = prospect["name"][:60]
    subject = f"Dossier: {name} · {TODAY.strftime('%b %d')}"

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
            log(f"Dossier sent → {EMAIL_TO} (HTTP {r.status})")
    except urllib.error.HTTPError as e:
        log(f"SendGrid error {e.code}: {e.read().decode()[:200]}")
        raise


def main():
    log("=" * 50)
    log(f"Dossier Generator — {TODAY.strftime('%Y-%m-%d %H:%M')} UTC")
    log(f"Prospect: {PROSPECT['name']}")
    log("=" * 50)

    dossier = research_prospect(PROSPECT)

    if not dossier:
        log("Failed to generate dossier — no output from Claude")
        sys.exit(1)

    html = build_dossier_email(dossier, PROSPECT)
    send_dossier(html, PROSPECT)
    log("Done ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
