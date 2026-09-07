#!/usr/bin/env python3
"""One-shot attention snapshot for claude-drift. Read-only. Needs `gh` (logged in) and network."""

from __future__ import annotations

import json
import subprocess
import urllib.request

REPO = "mandu5/claude-drift"


def gh(path: str) -> dict | list | None:
    try:
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "claude-drift-attention/1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


print("== GitHub ==")
r = gh(f"repos/{REPO}")
if isinstance(r, dict):
    print(
        f"stars {r['stargazers_count']}  forks {r['forks_count']}  watchers {r['subscribers_count']}  open issues {r['open_issues_count']}"
    )
for kind in ("views", "clones"):
    t = gh(f"repos/{REPO}/traffic/{kind}")
    if isinstance(t, dict):
        print(f"{kind} 14d: {t['count']} (unique {t['uniques']})")
refs = gh(f"repos/{REPO}/traffic/popular/referrers")
if isinstance(refs, list):
    for x in refs[:8]:
        print(f"  referrer {x['referrer']}: {x['count']}")

print("== PyPI (pypistats, about one day lag) ==")
p = get("https://pypistats.org/api/packages/claude-drift/recent")
if p and "data" in p:
    d = p["data"]
    print(
        f"downloads: day {d.get('last_day')}  week {d.get('last_week')}  month {d.get('last_month')}"
    )
else:
    print("no data yet")

print("== Hacker News ==")
h = get("https://hn.algolia.com/api/v1/search?query=claude-drift&tags=story")
hits = [
    x
    for x in (h or {}).get("hits", [])
    if "claude-drift" in (x.get("title", "") + str(x.get("url", ""))).lower()
]
for x in hits[:5]:
    print(
        f"  {x['points']:>4} pts {x['num_comments']:>3} c  {x['title'][:70]}  https://news.ycombinator.com/item?id={x['objectID']}"
    )
if not hits:
    print("  none")

print("== Reddit ==")
rd = get("https://www.reddit.com/search.json?q=claude-drift&sort=new&limit=5")
kids = ((rd or {}).get("data") or {}).get("children", [])
for c in kids:
    d = c["data"]
    print(f"  {d['score']:>4} pts r/{d['subreddit']}  {d['title'][:70]}")
if not kids:
    print("  none (or blocked)")
