#!/usr/bin/env python3
"""
Academy Page Builder — RBI Monitor Queue Processor
===================================================
Reads pending_modules/*.json (created by rbi_monitor.py)
and generates proper Legodesk Academy HTML pages.

Run after the RBI monitor populates the queue.
Can be triggered as a second GitHub Actions step or run manually.
"""

import os, json, re
from pathlib import Path
from datetime import datetime

QUEUE_DIR  = Path('data/pending_modules')
OUTPUT_DIR = Path('academy_pages')  # copy these to academy-v2/tracks/rbi/

def slugify(text):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]', '-', text[:50].lower())).strip('-')

def build_page(item_data):
    title    = item_data.get('title', 'RBI Update')
    date_str = item_data.get('date', datetime.now().strftime('%Y-%m-%d'))
    src_url  = item_data.get('source_url', '#')
    desc     = item_data.get('description', '')
    summary  = item_data.get('ai_summary', '')
    impact   = item_data.get('impact', {})
    action   = item_data.get('compliance_action', '')
    course   = item_data.get('course_module', {})
    feed_type = item_data.get('feed_type', 'circular')

    type_badges = {
        'circular': ('📋', 'RBI Circular', '#2563eb'),
        'press_release': ('📰', 'Press Release', '#0891b2'),
        'draft': ('📝', 'Draft Guidelines', '#d97706'),
        'master_direction': ('📖', 'Master Direction', '#7c3aed'),
    }
    icon, badge_text, badge_color = type_badges.get(feed_type, ('📋', 'RBI Update', '#374151'))

    lp = course.get('learning_points', [])
    lp_html = '\n'.join([f'<li>{lp_item}</li>' for lp_item in lp]) if lp else ''

    impact_html = ''
    if impact.get('tele_collections'):
        impact_html += f'''
        <div style="margin-bottom:12px;padding:12px 16px;background:#f0f9ff;border-left:3px solid #0ea5e9;border-radius:0 6px 6px 0">
          <div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#0891b2;margin-bottom:4px">📞 Tele-Collections</div>
          <div style="font-size:.85rem;color:#1e3a5f">{impact["tele_collections"]}</div>
        </div>'''
    if impact.get('field_collections'):
        impact_html += f'''
        <div style="margin-bottom:12px;padding:12px 16px;background:#f0fdf4;border-left:3px solid #22c55e;border-radius:0 6px 6px 0">
          <div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#16a34a;margin-bottom:4px">🚗 Field Collections</div>
          <div style="font-size:.85rem;color:#1e3a5f">{impact["field_collections"]}</div>
        </div>'''
    if impact.get('legal_team'):
        impact_html += f'''
        <div style="margin-bottom:12px;padding:12px 16px;background:#fef3c7;border-left:3px solid #f59e0b;border-radius:0 6px 6px 0">
          <div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#d97706;margin-bottom:4px">⚖️ Legal Team</div>
          <div style="font-size:.85rem;color:#1e3a5f">{impact["legal_team"]}</div>
        </div>'''

    return f'''<!-- RBI Monitor Auto-Generated — {date_str} -->
<div class="page-header">
  <div class="breadcrumb">
    <a href="/home.html">Home</a> <span>›</span>
    <a href="/learning-centre.html">Learning Centre</a> <span>›</span>
    <a href="/tracks/rbi-updates.html">RBI Updates</a> <span>›</span>
    {date_str}
  </div>
  <div style="display:inline-flex;align-items:center;gap:6px;background:{badge_color}1a;color:{badge_color};border:1px solid {badge_color}40;border-radius:4px;padding:3px 10px;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px">
    {icon} {badge_text}
  </div>
  <div class="page-title">{title}</div>
  <div class="page-subtitle">Published: {date_str} &nbsp;·&nbsp; <a href="{src_url}" target="_blank" rel="noopener" style="color:#2563eb">View on RBI Website ↗</a></div>
</div>

{"<div class='callout'><strong>AI Summary:</strong> " + summary + "</div>" if summary else ""}

<div class="section-title">Original RBI Text</div>
<div class="content-block">
  <p>{desc}</p>
  <div style="margin-top:14px">
    <a href="{src_url}" target="_blank" rel="noopener" class="btn btn-outline">Read Full Circular on RBI Website ↗</a>
  </div>
</div>

{"<div class='section-title'>Impact by Team</div><div class='content-block'>" + impact_html + "</div>" if impact_html else ""}

{"<div class='section-title'>Compliance Action Required</div><div class='content-block'><div class='callout'>" + action + "</div></div>" if action else ""}

{"<div class='section-title'>Course Module: " + course.get('title','') + "</div><div class='content-block'><h3>Key Learning Points</h3><ul>" + lp_html + "</ul></div>" if course.get('title') else ""}

<div class="section-title">Practice This Scenario</div>
<div class="content-block">
  <p>Test your understanding of this regulatory update with AI-generated case studies in the Case Study Engine.</p>
  <div class="action-strip">
    <a href="/tools/case-engine.html?context=rbi&update={slugify(title)}" class="btn btn-gold">Practice in Case Engine →</a>
  </div>
</div>
'''

def main():
    if not QUEUE_DIR.exists():
        print("No queue directory. Nothing to process.")
        return

    pending = list(QUEUE_DIR.glob('*.json'))
    if not pending:
        print("Queue is empty. Nothing to process.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    built = 0

    for json_file in sorted(pending):
        try:
            data = json.loads(json_file.read_text())
            slug = slugify(data.get('title', 'rbi-update'))
            date_str = data.get('date', 'unknown')
            html_content = build_page(data)

            out_file = OUTPUT_DIR / f"{date_str}-{slug}.html"
            out_file.write_text(html_content)
            print(f"  ✅ {out_file.name}")
            built += 1

        except Exception as e:
            print(f"  ❌ {json_file.name}: {e}")

    print(f"\nBuilt {built} Academy pages → {OUTPUT_DIR}/")
    print("Copy these HTML snippet files into the Academy pipeline to publish them.")

if __name__ == '__main__':
    main()
