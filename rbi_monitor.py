#!/usr/bin/env python3
"""
RBI Regulatory Monitor v2 — Legodesk Academy
=============================================
RBI blocks direct RSS/HTML scraping (HTTP 403).
This version uses multiple reliable sources:

  1. Web search via SerpAPI/DuckDuckGo (primary)
  2. ibclaw.in  — aggregates RBI circulars reliably
  3. Google News RSS — indexes RBI press releases
  4. taxmann.com / taxguru.in RSS — financial regulation aggregators
  5. Fintech India aggregators

Author: Legodesk / Osettl Technologies
"""

import os, json, re, hashlib, smtplib, time
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
import xml.etree.ElementTree as ET

# ── Config ─────────────────────────────────────────────────────────────────
GOOGLE_CHAT_WEBHOOK = os.environ.get('GOOGLE_CHAT_WEBHOOK', '')
ANTHROPIC_API_KEY   = os.environ.get('ANTHROPIC_API_KEY', '')
SMTP_HOST           = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_USER           = os.environ.get('SMTP_USER', '')
SMTP_PASS           = os.environ.get('SMTP_PASS', '')
NOTIFY_EMAIL        = os.environ.get('NOTIFY_EMAIL', '')
STATE_FILE          = Path('data/seen_items.json')

# ── Source 1: Google News RSS (reliable, no bot blocking) ──────────────────
GOOGLE_NEWS_QUERIES = [
    "RBI circular notification lender NBFC bank",
    "RBI directions banking regulation 2025 2026",
    "Reserve Bank India NPA recovery SARFAESI",
    "RBI Fair Practices Code collections",
    "RBI NBFC directions penalty circular",
]

def google_news_rss_url(query):
    import urllib.parse
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}+when:7d&hl=en-IN&gl=IN&ceid=IN:en"

# ── Source 2: Third-party aggregators (allow scraping) ─────────────────────
AGGREGATOR_FEEDS = [
    {
        'name': 'Taxmann — RBI Updates',
        'rss': 'https://www.taxmann.com/rss/rss-updates.aspx?category=RBI',
        'type': 'aggregator',
    },
    {
        'name': 'TaxGuru — RBI',
        'rss': 'https://taxguru.in/category/rbi/feed/',
        'type': 'aggregator',
    },
    {
        'name': 'IBClaw — RBI Circulars',
        'rss': 'https://ibclaw.in/category/rbi/feed/',
        'type': 'aggregator',
    },
    {
        'name': 'Fintech India — Regulatory',
        'rss': 'https://inc42.com/tag/rbi/feed/',
        'type': 'aggregator',
    },
    {
        'name': 'Taxmann — NBFC',
        'rss': 'https://www.taxmann.com/rss/rss-updates.aspx?category=NBFC',
        'type': 'aggregator',
    },
]

# ── Lender relevance keywords ───────────────────────────────────────────────
LENDER_KEYWORDS = [
    'npa', 'non-performing', 'stressed asset', 'default', 'loan recovery',
    'debt recovery', 'recovery agent', 'collection', 'bank', 'nbfc',
    'arc', 'asset reconstruction', 'sarfaesi', 'drt', 'drat',
    'ibc', 'insolvency', 'section 138', 'wilful defaulter', 'fraud account',
    'fair practices code', 'fpc', 'recovery practices', 'outsourcing',
    'third party', 'grievance', 'mortgage', 'housing loan', 'personal loan',
    'credit card', 'microfinance', 'msme', 'priority sector',
    'provisioning', 'prudential', 'income recognition', 'asset classification',
    'restructuring', 'resolution', 'one time settlement', 'ots',
    'penalty', 'enforcement', 'directions', 'master direction',
    'lender', 'lending', 'borrower', 'credit', 'loan',
    'rbi circular', 'rbi notification', 'rbi directions',
    'reserve bank', 'monetary policy', 'liquidity', 'interest rate',
]

# ── State ───────────────────────────────────────────────────────────────────
def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_seen(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=2))

def item_id(item):
    return hashlib.md5(f"{item.get('link','')}{item.get('title','')}".encode()).hexdigest()[:12]

def is_lender_relevant(item):
    text = (item.get('title','') + ' ' + item.get('description','')).lower()
    matches = [kw for kw in LENDER_KEYWORDS if kw in text]
    return len(matches) > 0, matches[:5]

# ── Fetch RSS (generic) ─────────────────────────────────────────────────────
def fetch_rss(url, feed_name, feed_type):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'en-IN,en;q=0.9',
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()

        # Handle both RSS and Atom
        raw_str = raw.decode('utf-8', errors='replace')
        # Strip encoding declaration if present (causes ET parse errors)
        raw_str = re.sub(r"<\?xml[^>]*\?>", "", raw_str, count=1).strip()

        root = ET.fromstring(raw_str)
        items = []

        # RSS format
        for item in root.findall('.//item'):
            title_el = item.find('title')
            link_el  = item.find('link')
            desc_el  = item.find('description')
            date_el  = item.find('pubDate')
            if date_el is None:
                date_el = item.find('{http://purl.org/dc/elements/1.1/}date')

            title = (title_el.text or '').strip() if title_el is not None else ''
            link  = (link_el.text  or '').strip() if link_el  is not None else ''
            desc  = (desc_el.text  or '').strip() if desc_el  is not None else ''
            date  = (date_el.text  or '').strip() if date_el  is not None else ''

            desc = re.sub(r'<[^>]+>', '', desc).strip()[:500]
            if title:
                items.append({
                    'title': title, 'link': link,
                    'description': desc, 'date': date,
                    'feed_type': feed_type, 'feed_name': feed_name,
                })

        # Atom format fallback
        if not items:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('.//atom:entry', ns) or root.findall('.//{http://www.w3.org/2005/Atom}entry'):
                title_el = entry.find('{http://www.w3.org/2005/Atom}title')
                link_el  = entry.find('{http://www.w3.org/2005/Atom}link')
                sum_el   = entry.find('{http://www.w3.org/2005/Atom}summary') or entry.find('{http://www.w3.org/2005/Atom}content')
                date_el  = entry.find('{http://www.w3.org/2005/Atom}published') or entry.find('{http://www.w3.org/2005/Atom}updated')

                title = (title_el.text or '').strip() if title_el is not None else ''
                link  = link_el.get('href','') if link_el is not None else ''
                desc  = re.sub(r'<[^>]+>', '', (sum_el.text or '') if sum_el is not None else '').strip()[:500]
                date  = (date_el.text or '').strip() if date_el is not None else ''

                if title:
                    items.append({
                        'title': title, 'link': link,
                        'description': desc, 'date': date,
                        'feed_type': feed_type, 'feed_name': feed_name,
                    })

        print(f"  ✅ {feed_name}: {len(items)} items")
        return items

    except Exception as e:
        print(f"  ⚠️  {feed_name}: {e}")
        return []

# ── Web search fallback via Claude ─────────────────────────────────────────
def search_via_claude():
    """Use Claude with web_search tool to find recent RBI updates."""
    if not ANTHROPIC_API_KEY:
        return []

    today = datetime.now(timezone.utc).strftime('%d %B %Y')
    prompt = f"""Search for the latest RBI (Reserve Bank of India) regulatory updates published in the last 7 days as of {today}.

Focus specifically on:
1. RBI circulars and notifications affecting banks, NBFCs, and lenders
2. New directions on debt recovery, collections, or NPA resolution
3. Changes to Fair Practices Code, SARFAESI, or DRT procedures
4. Penalty orders against banks/NBFCs for collection practices
5. New master directions or amendments to existing ones

For each update found, provide:
- Title of the circular/notification
- Date of issue
- Brief summary (2-3 sentences)
- Direct URL to the RBI document if available
- Category: circular / notification / press_release / master_direction / penalty

Return as JSON array:
[{{"title":"...","date":"...","summary":"...","url":"...","category":"..."}}]

If no lender-relevant updates in the last 7 days, return empty array: []"""

    try:
        payload = json.dumps({
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 1500,
            'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}],
            'messages': [{'role': 'user', 'content': prompt}]
        }).encode()

        req = Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'anthropic-beta': 'web-search-2025-03-05',
            },
            method='POST'
        )
        with urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())

        # Extract text from response
        text = ''
        for block in data.get('content', []):
            if block.get('type') == 'text':
                text += block.get('text', '')

        # Parse JSON from response
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if not json_match:
            print("  ⚠️  Claude search: no JSON array in response")
            return []

        raw = json.loads(json_match.group())
        items = []
        for r in raw:
            if r.get('title'):
                items.append({
                    'title': r.get('title', ''),
                    'link':  r.get('url', ''),
                    'description': r.get('summary', ''),
                    'date': r.get('date', ''),
                    'feed_type': r.get('category', 'circular'),
                    'feed_name': 'Claude Web Search',
                })
        print(f"  ✅ Claude web search: {len(items)} RBI items found")
        return items

    except Exception as e:
        print(f"  ⚠️  Claude web search: {e}")
        return []

# ── AI Analysis ────────────────────────────────────────────────────────────
def analyse_with_claude(items):
    if not ANTHROPIC_API_KEY or not items:
        return None

    items_text = '\n\n'.join([
        f"ITEM {i+1}:\nTitle: {item['title']}\nDate: {item['date']}\nDescription: {item['description']}\nURL: {item['link']}"
        for i, item in enumerate(items[:6])
    ])

    prompt = f"""You are an expert in Indian banking regulations and debt recovery law.

Analyse these new RBI updates relevant to lenders and recovery professionals:

{items_text}

For each item provide:
1. 2-sentence plain-English summary
2. Impact on: tele-collection teams, field agents, legal executives
3. Compliance action and timeline
4. Course module title + 3 learning points for Legodesk Academy

Respond ONLY in valid JSON:
{{"analysis":[{{"item_index":1,"summary":"...","impact":{{"tele_collections":"...","field_collections":"...","legal_team":"..."}},"compliance_action":"...","course_module":{{"title":"...","learning_points":["...","...","..."]}}}}],"overall_severity":"low|medium|high","headline":"..."}}"""

    try:
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
            data = json.loads(resp.read())

        text = (data.get('content', [{}])[0].get('text', '')).strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        print(f"  ⚠️  AI analysis failed: {e}")
        return None

# ── Google Chat ────────────────────────────────────────────────────────────
def send_google_chat(new_items, analysis):
    if not GOOGLE_CHAT_WEBHOOK:
        print("  ⚠️  No Google Chat webhook")
        return

    today = datetime.now(timezone.utc).strftime('%d %b %Y')
    sev = (analysis or {}).get('overall_severity', 'low')
    sev_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(sev, '🟢')
    headline = (analysis or {}).get('headline', f"{len(new_items)} new RBI update(s) affecting lenders")

    text_lines = [
        f"{sev_emoji} *RBI Regulatory Monitor — {today}*",
        f"📋 {headline}",
        "",
    ]

    for i, item in enumerate(new_items[:5]):
        text_lines.append(f"*{i+1}. {item['title'][:100]}*")
        if item.get('date'):
            text_lines.append(f"📅 {item['date'][:30]}  |  🏷️ {item['feed_name']}")
        if item.get('link'):
            text_lines.append(f"🔗 {item['link']}")

        if analysis:
            for a in analysis.get('analysis', []):
                if a.get('item_index') == i + 1:
                    text_lines.append(f"💡 {a.get('summary', '')}")
                    tc = a.get('impact', {}).get('tele_collections', '')
                    lt = a.get('impact', {}).get('legal_team', '')
                    if tc: text_lines.append(f"📞 Tele: {tc}")
                    if lt: text_lines.append(f"⚖️ Legal: {lt}")
                    ca = a.get('compliance_action', '')
                    if ca: text_lines.append(f"✅ Action: {ca}")
        text_lines.append("")

    text_lines.append("📚 Updates being added to Legodesk Academy as course material.")

    try:
        payload = json.dumps({"text": "\n".join(text_lines)}).encode()
        req = Request(GOOGLE_CHAT_WEBHOOK, data=payload,
                      headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=10) as resp:
            print(f"  ✅ Google Chat sent (HTTP {resp.status})")
    except Exception as e:
        print(f"  ❌ Google Chat failed: {e}")

# ── Email ──────────────────────────────────────────────────────────────────
def send_email(new_items, analysis):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return
    try:
        today = datetime.now(timezone.utc).strftime('%d %b %Y')
        lines = [f"RBI Monitor — {today}", "="*50, ""]
        if analysis:
            lines += [f"Headline: {analysis.get('headline','')}",
                      f"Severity: {analysis.get('overall_severity','').upper()}", ""]
        for i, item in enumerate(new_items):
            lines += [f"{i+1}. {item['title']}", f"   {item['date']}", f"   {item['link']}", ""]

        msg = EmailMessage()
        msg['Subject'] = f"[RBI Monitor] {len(new_items)} new update(s) — {today}"
        msg['From'] = SMTP_USER
        msg['To'] = NOTIFY_EMAIL
        msg.set_content('\n'.join(lines))
        with smtplib.SMTP(SMTP_HOST, 587) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print(f"  ✅ Email sent to {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"  ⚠️  Email failed: {e}")

# ── Academy queue ──────────────────────────────────────────────────────────
def queue_academy_page(item, analysis):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    slug  = re.sub(r'-+', '-', re.sub(r'[^a-z0-9]', '-', item['title'][:50].lower())).strip('-')
    data  = {
        'id': f"RBI-{today}-{slug[:20]}",
        'title': item['title'], 'date': today,
        'source_url': item['link'], 'feed_type': item['feed_type'],
        'description': item['description'],
        'generated': datetime.now(timezone.utc).isoformat(),
    }
    if analysis:
        for a in analysis.get('analysis', []):
            data.update({
                'ai_summary': a.get('summary',''),
                'impact': a.get('impact',{}),
                'compliance_action': a.get('compliance_action',''),
                'course_module': a.get('course_module',{}),
            })
            break
    q = Path('data/pending_modules')
    q.mkdir(parents=True, exist_ok=True)
    (q / f"{today}-{slug[:30]}.json").write_text(json.dumps(data, indent=2))
    print(f"  📝 Queued: {slug[:40]}")

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"RBI Monitor v2 — {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    print(f"{'='*60}\n")

    seen = load_seen()
    all_items = []

    # Source 1: Google News RSS
    print("Fetching Google News RSS feeds...")
    for query in GOOGLE_NEWS_QUERIES:
        url = google_news_rss_url(query)
        items = fetch_rss(url, f"Google News: {query[:40]}", 'press_release')
        all_items.extend(items)
        time.sleep(0.5)

    # Source 2: Third-party aggregators
    print("\nFetching aggregator RSS feeds...")
    for feed in AGGREGATOR_FEEDS:
        items = fetch_rss(feed['rss'], feed['name'], feed['type'])
        all_items.extend(items)
        time.sleep(0.5)

    # Source 3: Claude web search (most reliable for recent RBI content)
    print("\nRunning Claude web search for RBI updates...")
    search_items = search_via_claude()
    all_items.extend(search_items)

    # Deduplicate and filter
    print(f"\nTotal items fetched: {len(all_items)}")
    new_items = []
    for item in all_items:
        iid = item_id(item)
        if iid in seen:
            continue
        relevant, keywords = is_lender_relevant(item)
        if relevant:
            item['matched_keywords'] = keywords
            new_items.append(item)
            seen[iid] = {
                'title': item['title'],
                'date': item['date'],
                'seen_at': datetime.now(timezone.utc).isoformat(),
            }

    # Remove duplicates by title similarity
    deduped = []
    titles_seen = set()
    for item in new_items:
        title_key = re.sub(r'[^a-z0-9]', '', item['title'][:40].lower())
        if title_key not in titles_seen:
            titles_seen.add(title_key)
            deduped.append(item)
    new_items = deduped

    print(f"New lender-relevant items (deduplicated): {len(new_items)}")

    if not new_items:
        print("✅ No new updates today.")
        # Print parseable line for GitHub Actions
        print("Headline: No new lender-relevant RBI updates found")
        if datetime.now(timezone.utc).weekday() == 0 and GOOGLE_CHAT_WEBHOOK:
            try:
                payload = json.dumps({
                    "text": f"✅ *RBI Monitor* — {datetime.now(timezone.utc).strftime('%d %b %Y')} — No new lender-relevant updates. Monitoring active."
                }).encode()
                urlopen(Request(GOOGLE_CHAT_WEBHOOK, data=payload,
                         headers={'Content-Type':'application/json'}, method='POST'), timeout=10)
                print("  ✅ Weekly all-clear sent to Google Chat")
            except Exception as e:
                print(f"  ⚠️  {e}")
        save_seen(seen)
        return

    # Analyse
    print("\nRunning AI analysis...")
    analysis = analyse_with_claude(new_items)
    if analysis:
        print(f"  ✅ Severity: {analysis.get('overall_severity')}")
        print(f"Headline: {analysis.get('headline','N/A')}")

    # Notify
    print("\nSending notifications...")
    send_google_chat(new_items, analysis)
    send_email(new_items, analysis)

    # Queue Academy content
    print("\nQueuing Academy content...")
    for item in new_items:
        queue_academy_page(item, analysis)

    save_seen(seen)
    print(f"\n{'='*60}")
    print(f"Done. {len(new_items)} new items processed.")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    main()
