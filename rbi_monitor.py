#!/usr/bin/env python3
"""
RBI Regulatory Monitor — Legodesk Academy
==========================================
Runs daily via GitHub Actions.
1. Scrapes RBI website for new circulars, directions, press releases, draft guidelines
2. Filters for lender-relevant items (banks, NBFCs, ARCs, debt recovery)
3. Sends formatted notification to Google Chat
4. Generates an Academy course page for significant updates
5. Queues content for Case Study Engine

Author: Legodesk / Osettl Technologies
"""

import os, json, re, hashlib, smtplib, time, sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError
import xml.etree.ElementTree as ET

# ── Config ─────────────────────────────────────────────────────────────────
GOOGLE_CHAT_WEBHOOK   = os.environ.get('GOOGLE_CHAT_WEBHOOK', '')
ANTHROPIC_API_KEY     = os.environ.get('ANTHROPIC_API_KEY', '')
SMTP_HOST             = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_USER             = os.environ.get('SMTP_USER', '')
SMTP_PASS             = os.environ.get('SMTP_PASS', '')
NOTIFY_EMAIL          = os.environ.get('NOTIFY_EMAIL', '')
STATE_FILE            = Path('data/seen_items.json')

# RBI RSS/Notification feeds
RBI_FEEDS = [
    {
        'name': 'RBI Press Releases',
        'url': 'https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx',
        'rss': 'https://rbi.org.in/scripts/rss.aspx?Id=3',
        'type': 'press_release',
    },
    {
        'name': 'RBI Notifications & Circulars',
        'url': 'https://www.rbi.org.in/scripts/BS_CircularIndexDisplay.aspx',
        'rss': 'https://rbi.org.in/scripts/rss.aspx?Id=2',
        'type': 'circular',
    },
    {
        'name': 'RBI Draft Guidelines',
        'url': 'https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx',
        'rss': 'https://rbi.org.in/scripts/rss.aspx?Id=27',
        'type': 'draft',
    },
    {
        'name': 'RBI Master Directions',
        'url': 'https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx',
        'rss': 'https://rbi.org.in/scripts/rss.aspx?Id=25',
        'type': 'master_direction',
    },
]

# Keywords indicating lender relevance
LENDER_KEYWORDS = [
    # Core lending
    'npa', 'non-performing asset', 'stressed asset', 'default',
    'loan recovery', 'debt recovery', 'recovery agent', 'collections',
    # Lender types
    'bank', 'nbfc', 'arc', 'asset reconstruction', 'scheduled commercial bank',
    'co-operative bank', 'urban co-operative', 'small finance bank',
    # Legal instruments
    'sarfaesi', 'drt', 'drat', 'ibc', 'insolvency', 'bankruptcy',
    'section 138', 'wilful defaulter', 'fraud account',
    # Regulatory
    'fair practices code', 'fpc', 'recovery practices', 'collection agent',
    'outsourcing', 'third party', 'grievance redressal',
    # Products
    'mortgage', 'housing loan', 'personal loan', 'credit card', 'microfinance',
    'msme', 'priority sector', 'priority sector lending',
    # Misc
    'provisioning', 'prudential norms', 'income recognition', 'asset classification',
    'restructuring', 'resolution', 'one time settlement', 'ots',
    'penalty', 'enforcement', 'directions', 'master direction',
    'guidelines for lenders', 'lender', 'lending',
]

# ── State management ────────────────────────────────────────────────────────
def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_seen(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=2))

def item_id(item):
    return hashlib.md5(f"{item.get('link','')}{item.get('title','')}".encode()).hexdigest()[:12]

# ── RSS fetcher ─────────────────────────────────────────────────────────────
def fetch_rss(feed):
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; LegodeskMonitor/1.0; +https://legodesk.com)',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    }
    try:
        req = Request(feed['rss'], headers=headers)
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        ns = {'dc': 'http://purl.org/dc/elements/1.1/'}

        for item in root.findall('.//item'):
            title_el = item.find('title')
            link_el  = item.find('link')
            desc_el  = item.find('description')
            date_el  = item.find('pubDate')

            title = title_el.text.strip() if title_el is not None and title_el.text else ''
            link  = link_el.text.strip()  if link_el  is not None and link_el.text  else ''
            desc  = desc_el.text.strip()  if desc_el  is not None and desc_el.text  else ''
            date  = date_el.text.strip()  if date_el  is not None and date_el.text  else ''

            # Clean HTML from description
            desc = re.sub(r'<[^>]+>', '', desc).strip()

            items.append({
                'title': title,
                'link': link,
                'description': desc[:500],
                'date': date,
                'feed_type': feed['type'],
                'feed_name': feed['name'],
            })
        print(f"  ✅ {feed['name']}: {len(items)} items")
        return items
    except Exception as e:
        print(f"  ⚠️  {feed['name']}: failed — {e}")
        return []

# ── Relevance filter ────────────────────────────────────────────────────────
def is_lender_relevant(item):
    text = (item['title'] + ' ' + item['description']).lower()
    matches = [kw for kw in LENDER_KEYWORDS if kw in text]
    return len(matches) > 0, matches[:5]

# ── AI analysis ────────────────────────────────────────────────────────────
def analyse_with_claude(items):
    """Use Claude to analyse items, extract key implications and generate course content."""
    if not ANTHROPIC_API_KEY or not items:
        return None

    items_text = '\n\n'.join([
        f"ITEM {i+1}:\nTitle: {item['title']}\nType: {item['feed_type']}\nDate: {item['date']}\nDescription: {item['description']}\nURL: {item['link']}"
        for i, item in enumerate(items[:8])  # cap at 8 to save tokens
    ])

    prompt = f"""You are an expert in Indian banking regulations and debt recovery law.

The following new RBI notifications/circulars have been published today that are relevant to lenders, banks, NBFCs, and debt recovery professionals:

{items_text}

For each item, provide:
1. A 2-sentence plain-English summary of what changed
2. The specific impact on: (a) tele-collection teams, (b) field collection teams, (c) legal/recovery executives
3. Compliance action required (if any) and suggested timeline
4. A course module title and 3 key learning points for the Legodesk Academy

Respond in valid JSON only. Format:
{{
  "analysis": [
    {{
      "item_index": 1,
      "summary": "...",
      "impact": {{
        "tele_collections": "...",
        "field_collections": "...",
        "legal_team": "..."
      }},
      "compliance_action": "...",
      "course_module": {{
        "title": "...",
        "learning_points": ["...", "...", "..."]
      }}
    }}
  ],
  "overall_severity": "low|medium|high",
  "headline": "One sentence summary of today's most important RBI update"
}}"""

    try:
        import json as _json
        payload = json.dumps({
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': prompt}]
        }).encode()

        req = Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        with urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        text = data['content'][0]['text'].strip()
        # Strip markdown code fences if present
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return _json.loads(text)
    except Exception as e:
        print(f"  ⚠️  Claude analysis failed: {e}")
        return None

# ── Google Chat notification ────────────────────────────────────────────────
def send_google_chat(new_items, analysis):
    if not GOOGLE_CHAT_WEBHOOK:
        print("  ⚠️  No Google Chat webhook configured")
        return False

    today = datetime.now(timezone.utc).strftime('%d %b %Y')
    severity_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(
        analysis.get('overall_severity', 'low') if analysis else 'low', '🟢'
    )

    # Build card message
    sections = []

    # Header card
    header_text = analysis.get('headline', f"{len(new_items)} new RBI update(s) affecting lenders") if analysis else f"{len(new_items)} new RBI update(s) affecting lenders"

    sections.append({
        "header": f"*{severity_emoji} RBI Regulatory Update — {today}*",
        "widgets": [
            {"textParagraph": {"text": f"📋 {header_text}"}}
        ]
    })

    # Individual items
    for i, item in enumerate(new_items[:6]):
        item_text = f"*{item['title'][:120]}*"
        if item['date']:
            item_text += f"\n📅 {item['date'][:30]}"
        item_text += f"\n🏷️ {item['feed_name']}"
        if item['link']:
            item_text += f"\n🔗 <{item['link']}|Read on RBI website>"

        # Add AI analysis if available
        if analysis and analysis.get('analysis'):
            for a in analysis['analysis']:
                if a.get('item_index') == i + 1:
                    item_text += f"\n\n💡 *What changed:* {a.get('summary', '')}"
                    if a['impact'].get('tele_collections'):
                        item_text += f"\n📞 *Tele-Collections:* {a['impact']['tele_collections']}"
                    if a['impact'].get('legal_team'):
                        item_text += f"\n⚖️ *Legal Team:* {a['impact']['legal_team']}"
                    if a.get('compliance_action'):
                        item_text += f"\n✅ *Action:* {a['compliance_action']}"

        sections.append({
            "widgets": [
                {"textParagraph": {"text": item_text}},
                {"divider": {}}
            ]
        })

    # Footer
    sections.append({
        "widgets": [
            {"textParagraph": {"text": "📚 These updates will be added to Legodesk Academy as course material. Check the platform for new modules."}}
        ]
    })

    message = {"cards": [{"sections": sections}]}

    try:
        payload = json.dumps(message).encode()
        req = Request(
            GOOGLE_CHAT_WEBHOOK,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urlopen(req, timeout=10) as resp:
            status = resp.status
        print(f"  ✅ Google Chat notification sent (HTTP {status})")
        return True
    except Exception as e:
        print(f"  ❌ Google Chat failed: {e}")
        return False

# ── Email fallback ──────────────────────────────────────────────────────────
def send_email(new_items, analysis):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return
    try:
        today = datetime.now(timezone.utc).strftime('%d %b %Y')
        body_lines = [f"RBI Regulatory Monitor — {today}", "=" * 50, ""]

        if analysis:
            body_lines.append(f"HEADLINE: {analysis.get('headline', '')}")
            body_lines.append(f"SEVERITY: {analysis.get('overall_severity', 'unknown').upper()}")
            body_lines.append("")

        for i, item in enumerate(new_items):
            body_lines.append(f"{i+1}. {item['title']}")
            body_lines.append(f"   Type: {item['feed_name']}")
            body_lines.append(f"   Date: {item['date']}")
            body_lines.append(f"   URL: {item['link']}")
            body_lines.append("")

        msg = EmailMessage()
        msg['Subject'] = f"[RBI Monitor] {len(new_items)} new update(s) — {today}"
        msg['From'] = SMTP_USER
        msg['To'] = NOTIFY_EMAIL
        msg.set_content('\n'.join(body_lines))

        with smtplib.SMTP(SMTP_HOST, 587) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print(f"  ✅ Email sent to {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"  ⚠️  Email failed: {e}")

# ── Academy course page generator ──────────────────────────────────────────
def generate_academy_page(item, ai_analysis):
    """Generate a JSON file for new Academy pages — to be processed by the Academy build pipeline."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    slug = re.sub(r'[^a-z0-9-]', '-', item['title'][:60].lower()).strip('-')
    slug = re.sub(r'-+', '-', slug)

    page_data = {
        'id': f"RBI-{today}-{slug[:20]}",
        'title': item['title'],
        'date': today,
        'source_url': item['link'],
        'feed_type': item['feed_type'],
        'description': item['description'],
        'generated': datetime.now(timezone.utc).isoformat(),
    }

    if ai_analysis:
        for a in ai_analysis.get('analysis', []):
            page_data['ai_summary'] = a.get('summary', '')
            page_data['impact'] = a.get('impact', {})
            page_data['compliance_action'] = a.get('compliance_action', '')
            page_data['course_module'] = a.get('course_module', {})
            break

    # Write to pending queue
    queue_dir = Path('data/pending_modules')
    queue_dir.mkdir(parents=True, exist_ok=True)
    fname = queue_dir / f"{today}-{slug[:30]}.json"
    fname.write_text(json.dumps(page_data, indent=2))
    print(f"  📝 Academy page queued: {fname.name}")
    return page_data

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"RBI Regulatory Monitor — {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    print(f"{'='*60}\n")

    seen = load_seen()
    all_new = []

    print("Fetching RBI feeds...")
    for feed in RBI_FEEDS:
        items = fetch_rss(feed)
        for item in items:
            iid = item_id(item)
            if iid not in seen:
                relevant, keywords = is_lender_relevant(item)
                if relevant:
                    item['matched_keywords'] = keywords
                    all_new.append(item)
                    seen[iid] = {
                        'title': item['title'],
                        'date': item['date'],
                        'seen_at': datetime.now(timezone.utc).isoformat(),
                    }

    print(f"\n{'='*60}")
    print(f"New lender-relevant items: {len(all_new)}")

    if not all_new:
        print("✅ No new updates today.")
        # Send a "all clear" message on Mondays to confirm monitoring is running
        if datetime.now(timezone.utc).weekday() == 0 and GOOGLE_CHAT_WEBHOOK:
            try:
                payload = json.dumps({
                    "text": f"✅ *RBI Monitor* — {datetime.now(timezone.utc).strftime('%d %b %Y')} — No new lender-relevant updates today. Monitoring is active."
                }).encode()
                req = Request(GOOGLE_CHAT_WEBHOOK, data=payload,
                              headers={'Content-Type': 'application/json'}, method='POST')
                urlopen(req, timeout=10)
                print("  ✅ Weekly all-clear sent to Google Chat")
            except Exception as e:
                print(f"  ⚠️  Weekly all-clear failed: {e}")
        save_seen(seen)
        return

    # AI analysis
    print("\nRunning AI analysis...")
    analysis = analyse_with_claude(all_new) if ANTHROPIC_API_KEY else None
    if analysis:
        print(f"  ✅ Analysis complete — severity: {analysis.get('overall_severity', '?')}")
        print(f"  📌 Headline: {analysis.get('headline', 'N/A')}")
    else:
        print("  ℹ️  AI analysis skipped (no API key or error)")

    # Notify
    print("\nSending notifications...")
    send_google_chat(all_new, analysis)
    send_email(all_new, analysis)

    # Generate Academy content
    print("\nQueuing Academy content...")
    for item in all_new:
        generate_academy_page(item, analysis)

    # Save state
    save_seen(seen)

    print(f"\n{'='*60}")
    print(f"Done. {len(all_new)} new items processed.")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
