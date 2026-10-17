#!/usr/bin/env python3
"""
scripts/update_readme.py

- Uses GITHUB_TOKEN (env) to read public repos for owner and update README.md between markers.
- Markers in README.md:
  <!-- TECH-STACK:START --> ... <!-- TECH-STACK:END -->
  <!-- PROJECTS:START --> ... <!-- PROJECTS:END -->
  <!-- UPDATED:START --> ... <!-- UPDATED:END -->

Behavior:
- Fetch user's repos (exclude forks and profile repo), aggregate languages (via /languages), compute %.
- Fetch topics per repo and create shields.io topic badges.
- Select top 6 repos by stars and recent push.
- Replace only the marker blocks if content changed and PUT the README via Contents API.

Note: requires GITHUB_TOKEN with repo contents write permission.
"""

import os
import sys
import requests
import base64
import datetime
import time
from collections import Counter

OWNER = os.environ.get("GH_OWNER") or "LnNhutTuong"
REPO = os.environ.get("GH_REPO") or OWNER
README_PATH = "README.md"
GITHUB_API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN")

HEADERS = {"Accept": "application/vnd.github.v3+json"}
if TOKEN:
    HEADERS["Authorization"] = f"token {TOKEN}"


def paged_get(url, params=None):
    items = []
    params = params or {}
    params.update({"per_page": 100})
    while url:
        r = requests.get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            items.extend(data)
        else:
            # sometimes single-object responses
            items.append(data)
        if 'next' in r.links:
            url = r.links['next']['url']
            params = None
            time.sleep(0.2)
        else:
            url = None
    return items


def fetch_repos():
    url = f"{GITHUB_API}/users/{OWNER}/repos"
    repos = paged_get(url, params={"type": "owner", "sort": "pushed"})
    filtered = [r for r in repos if not r.get('fork') and r.get('name','').lower() != REPO.lower()]
    return filtered


def fetch_languages(repo_name):
    url = f"{GITHUB_API}/repos/{OWNER}/{repo_name}/languages"
    r = requests.get(url, headers=HEADERS)
    if r.status_code == 200:
        return r.json()
    return {}


def fetch_topics(repo_name):
    url = f"{GITHUB_API}/repos/{OWNER}/{repo_name}/topics"
    h = HEADERS.copy()
    h['Accept'] = 'application/vnd.github.mercy-preview+json'
    r = requests.get(url, headers=h)
    if r.status_code == 200:
        return r.json().get('names', [])
    return []


def language_color(lang):
    mapping = {
        "JavaScript":"f1e05a","Python":"3572A5","Java":"b07219","C#":"178600",
        "TypeScript":"2b7489","C":"555555","C++":"f34b7d","PHP":"4F5D95","HTML":"e34c26",
        "Shell":"89e051","Go":"00ADD8","Ruby":"701516","CSS":"563d7c","JSON":"292929"
    }
    return mapping.get(lang, "2d2d2d")


def make_lang_badge(lang, pct):
    logo = lang.replace('+', 'p').replace('#', 'sharp').lower()
    color = language_color(lang)
    label = f"{lang} {pct:.0f}%"
    url = f"https://img.shields.io/badge/{requests.utils.quote(label)}-{color}?style=for-the-badge&logo={logo}&logoColor=white"
    return f"![{lang}]({url})"


def make_topic_badge(topic):
    logo = topic.replace(' ', '-').lower()
    color = '0ea5e9'  # sea-blue
    url = f"https://img.shields.io/badge/-{requests.utils.quote(topic)}-{color}?style=for-the-badge&logo={logo}&logoColor=white"
    return f"[![]({url})](https://github.com/topics/{topic})"


def build_tech_stack(lang_agg, repo_topics):
    total = sum(lang_agg.values()) or 1
    items = sorted(lang_agg.items(), key=lambda x: -x[1])
    badges = [make_lang_badge(lang, (cnt/total*100)) for lang, cnt in items[:8]]
    # pick unique topic badges
    topics_flat = []
    for t in repo_topics:
        topics_flat.extend(t)
    topics_counter = Counter(topics_flat)
    topic_badges = [make_topic_badge(t) for t, _ in topics_counter.most_common(6)]
    return ' '.join(badges + topic_badges)


def select_top_projects(repos, top=6):
    repos_sorted = sorted(repos, key=lambda r: (-r.get('stargazers_count',0), r.get('pushed_at','')),)
    selected = repos_sorted[:top]
    rows = []
    for r in selected:
        rows.append({
            'name': r['name'],
            'desc': r.get('description') or '',
            'lang': r.get('language') or '-',
            'stars': r.get('stargazers_count',0),
            'url': r.get('html_url')
        })
    return rows


def render_projects_table(projects):
    if not projects:
        return '_No projects found._'
    lines = ['<table align="center">']
    for i in range(0, len(projects), 2):
        left = projects[i]
        right = projects[i+1] if i+1 < len(projects) else None
        lines.append('  <tr>')
        def cell(p):
            return f'''    <td align="left" valign="top">
<strong><a href="{p['url']}">{p['name']}</a></strong><br/>
<small>{p['desc']}</small><br/>
<small>🔧 {p['lang']} • ⭐ {p['stars']}</small>
    </td>'''
        lines.append(cell(left))
        if right:
            lines.append(cell(right))
        else:
            lines.append('    <td></td>')
        lines.append('  </tr>')
    lines.append('</table>')
    return '\n'.join(lines)


def get_readme():
    url = f"{GITHUB_API}/repos/{OWNER}/{REPO}/contents/{README_PATH}"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def update_readme_in_repo(new_content_b64, sha, message="chore: update README (auto)"):
    url = f"{GITHUB_API}/repos/{OWNER}/{REPO}/contents/{README_PATH}"
    payload = {
        "message": message,
        "content": new_content_b64,
        "sha": sha
    }
    r = requests.put(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def replace_block(original, start_marker, end_marker, new_block):
    import re
    pattern = re.compile(rf"({start_marker})(.*?)(\s*{end_marker})", re.S)
    replacement = f"{start_marker}\n{new_block}\n{end_marker}"
    new_text, count = pattern.subn(replacement, original)
    if count == 0:
        new_text = original + "\n" + replacement
    return new_text


def main(dry=False):
    print('Collecting repos...')
    repos = fetch_repos()
    print(f'Found {len(repos)} repos')
    lang_agg = Counter()
    all_topics = []
    for r in repos:
        name = r['name']
        try:
            langs = fetch_languages(name)
            for k, v in langs.items():
                lang_agg[k] += v
        except Exception as e:
            print('lang fetch failed for', name, e)
        try:
            topics = fetch_topics(name)
            all_topics.append(topics)
        except Exception as e:
            print('topics fetch failed for', name, e)
    tech_md = build_tech_stack(lang_agg, all_topics) or '_No languages detected yet._'
    projects = select_top_projects(repos, top=6)
    projects_md = render_projects_table(projects)
    updated = f"`Updated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC`"

    readme = get_readme()
    sha = readme['sha']
    decoded = base64.b64decode(readme['content']).decode(readme.get('encoding','utf-8'))
    new = decoded
    new = replace_block(new, '<!-- TECH-STACK:START -->', '<!-- TECH-STACK:END -->', tech_md)
    new = replace_block(new, '<!-- PROJECTS:START -->', '<!-- PROJECTS:END -->', projects_md)
    new = replace_block(new, '<!-- UPDATED:START -->', '<!-- UPDATED:END -->', updated)

    if new == decoded:
        print('No changes detected.')
        return 0
    if dry or not TOKEN:
        print('DRY RUN or missing GITHUB_TOKEN; output preview below:')
        print(new)
        return 0
    new_b64 = base64.b64encode(new.encode('utf-8')).decode('utf-8')
    resp = update_readme_in_repo(new_b64, sha, message='chore: automated README update')
    print('README updated:', resp.get('commit', {}).get('html_url'))
    return 0


if __name__ == '__main__':
    dry = os.environ.get('DRY_RUN', '').lower() in ('1','true')
    sys.exit(main(dry=dry))
