"""Probe GhostPress and render the status page into site/.

Run every five minutes by .github/workflows/status.yml. The history (90 days of daily
check counts per component) lives in the deployed site's status.json: each run reads the
live copy, adds this run's checks and writes the page again, so nothing is committed.
Standard library only.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site"
PAGE_URL = (os.environ.get("STATUS_URL") or "https://myriatechnology.github.io/ghostpress-status").rstrip("/")
APP = "https://a.ghostpress.app"
SITE = "https://ghostpress.app"
DAYS = 90
TIMEOUT = 10
SLOW_MS = 4000
USER_AGENT = "GhostPress-Status/1 (+https://github.com/myriatechnology/ghostpress-status)"


def fetch(url: str) -> dict:
    """One GET: status code, elapsed milliseconds, parsed JSON (or None), transport error name."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            code, body = resp.status, resp.read(500_000)
    except urllib.error.HTTPError as exc:
        code, body = exc.code, (exc.read(500_000) if exc.fp else b"")
    except Exception as exc:  # noqa: BLE001 - any transport failure is a failed check
        return {"code": None, "ms": None, "json": None, "error": type(exc).__name__}
    ms = round((time.monotonic() - start) * 1000)
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return {"code": code, "ms": ms, "json": data, "error": None}


def _queues_ok(ready: dict) -> bool:
    queues = (ready.get("json") or {}).get("queues") if isinstance(ready.get("json"), dict) else None
    return isinstance(queues, dict) and bool(queues) and all(isinstance(v, int) for v in queues.values())


def _note(result: dict) -> str:
    if result["error"]:
        return f"no answer ({result['error']})"
    return f"HTTP {result['code']}"


def run_checks() -> list[dict]:
    """The components, in page order: key, name, what it covers, and this run's result."""
    health = fetch(f"{APP}/api/health")
    ready = fetch(f"{APP}/api/ready")
    home = fetch(f"{SITE}/")
    blog = fetch(f"{SITE}/blog")
    ready_json = ready["json"] if isinstance(ready["json"], dict) else {}
    health_json = health["json"] if isinstance(health["json"], dict) else {}
    return [
        {"key": "app", "name": "Dashboard and API", "covers": "a.ghostpress.app: sign-in, review, the API",
         "ok": health["code"] == 200 and health_json.get("status") == "ok", "ms": health["ms"], "note": _note(health)},
        {"key": "database", "name": "Database", "covers": "drafts, backlog, settings",
         "ok": ready_json.get("db") == "ok", "ms": ready["ms"], "note": _note(ready)},
        {"key": "queues", "name": "Writing and scouting jobs", "covers": "the queues the agents work from",
         "ok": _queues_ok(ready), "ms": ready["ms"], "note": _note(ready)},
        {"key": "website", "name": "Website", "covers": "ghostpress.app",
         "ok": home["code"] == 200, "ms": home["ms"], "note": _note(home)},
        {"key": "blog", "name": "The GhostPress Edition", "covers": "ghostpress.app/blog",
         "ok": blog["code"] == 200, "ms": blog["ms"], "note": _note(blog)},
    ]


def load_previous() -> dict:
    """The live status.json. A missing page (first deploy) starts fresh; any other failure
    stops the run so a Pages hiccup never overwrites the history."""
    req = urllib.request.Request(f"{PAGE_URL}/status.json?t={int(time.time())}",
                                 headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("no previous status.json: starting the history fresh")
            return {}
        raise SystemExit(f"could not read the previous status.json (HTTP {exc.code}); not deploying")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"could not read the previous status.json ({type(exc).__name__}); not deploying")


def update_state(previous: dict, checks: list[dict], now: dt.datetime) -> dict:
    today = now.date().isoformat()
    oldest = (now.date() - dt.timedelta(days=DAYS - 1)).isoformat()
    old = previous.get("components") or {}
    components = {}
    for check in checks:
        prior = old.get(check["key"]) or {}
        fails = 0 if check["ok"] else int(prior.get("fails") or 0) + 1
        days = {d: v for d, v in (prior.get("days") or {}).items() if d >= oldest}
        ok_count, total = days.get(today, [0, 0])
        days[today] = [ok_count + (1 if check["ok"] else 0), total + 1]
        if fails >= 2:
            status = "outage"
        elif fails == 1 or (check["ms"] or 0) > SLOW_MS:
            status = "degraded"
        else:
            status = "operational"
        components[check["key"]] = {"name": check["name"], "status": status, "fails": fails, "ms": check["ms"],
                                    "note": check["note"], "checked": now.isoformat(timespec="seconds"),
                                    "days": dict(sorted(days.items()))}
    return {"updated": now.isoformat(timespec="seconds"), "components": components}


# ── rendering ─────────────────────────────────────────────────────

LABELS = {"operational": "Operational", "degraded": "Degraded", "outage": "Outage"}
INCIDENT_LABELS = {"investigating": "Investigating", "identified": "Identified", "monitoring": "Monitoring",
                   "resolved": "Resolved"}


def _when(value: str | None) -> str:
    if not value:
        return ""
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return html.escape(value)
    return f'<time datetime="{moment.isoformat()}">{moment:%a %d %b %Y, %H:%M} UTC</time>'


def _bars(days: dict, now: dt.datetime) -> tuple[str, str]:
    cells, ok_sum, total_sum = [], 0, 0
    for back in range(DAYS - 1, -1, -1):
        day = (now.date() - dt.timedelta(days=back)).isoformat()
        ok_count, total = days.get(day, [0, 0])
        ok_sum, total_sum = ok_sum + ok_count, total_sum + total
        if not total:
            cls, tip = "none", f"{day}: no data"
        else:
            share = ok_count / total
            cls = "up" if share >= 0.999 else ("part" if share >= 0.95 else "down")
            tip = f"{day}: {share * 100:.2f}% of {total} checks"
        cells.append(f'<span class="cell {cls}" title="{tip}"></span>')
    uptime = f"{ok_sum / total_sum * 100:.2f}% uptime" if total_sum else "no data yet"
    return "".join(cells), uptime


def _incident(item: dict) -> str:
    status = str(item.get("status") or "investigating")
    updates = sorted(item.get("updates") or [], key=lambda u: str(u.get("at") or ""), reverse=True)
    rows = "".join(f'<li><span class="at">{_when(u.get("at"))}</span><p>{html.escape(str(u.get("text") or ""))}</p></li>'
                   for u in updates)
    return (f'<article class="incident {html.escape(status)}"><header><h3>{html.escape(str(item.get("title") or "Incident"))}</h3>'
            f'<span class="tag">{INCIDENT_LABELS.get(status, html.escape(status))}</span></header>'
            f'<p class="span">Started {_when(item.get("started"))}'
            + (f' · resolved {_when(item.get("resolved"))}' if item.get("resolved") else "")
            + f'</p><ol class="updates">{rows}</ol></article>')


def render(state: dict, incidents: list[dict], now: dt.datetime) -> str:
    comps = state["components"]
    open_incidents = [i for i in incidents if str(i.get("status")) != "resolved"]
    cutoff = (now - dt.timedelta(days=30)).isoformat()
    past = [i for i in incidents if str(i.get("status")) == "resolved" and str(i.get("resolved") or "") >= cutoff]
    worst = "outage" if any(c["status"] == "outage" for c in comps.values()) else (
        "degraded" if any(c["status"] == "degraded" for c in comps.values()) or open_incidents else "operational")
    headline = {"operational": "Every system is running.", "degraded": "Some systems are slow or under watch.",
                "outage": "Something is down. We are on it."}[worst]
    rows = []
    for key, comp in comps.items():
        bars, uptime = _bars(comp["days"], now)
        rows.append(
            f'<li class="component {comp["status"]}"><div class="line"><span class="dot" aria-hidden="true"></span>'
            f'<span class="name">{html.escape(comp["name"])}</span><span class="state">{LABELS[comp["status"]]}</span></div>'
            f'<div class="bars" role="img" aria-label="{html.escape(comp["name"])}: {uptime} over {DAYS} days">{bars}</div>'
            f'<div class="axis"><span>{DAYS} days ago</span><span>{uptime}</span><span>Today</span></div></li>')
    open_html = "".join(_incident(i) for i in open_incidents)
    past_html = "".join(_incident(i) for i in sorted(past, key=lambda i: str(i.get("started") or ""), reverse=True))
    return TEMPLATE.format(
        worst=worst, headline=html.escape(headline), updated=_when(state["updated"]),
        open_incidents=f'<section class="incidents" aria-label="Open incidents">{open_html}</section>' if open_html else "",
        components="".join(rows),
        past=past_html or '<p class="quiet">No incidents in the last 30 days.</p>')


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>GhostPress status</title>
<meta name="description" content="Whether GhostPress is running: the dashboard, the API, the database, the writing queues and ghostpress.app.">
<link rel="icon" href="favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Special+Elite&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
</head>
<body>
<main class="page">
  <header class="masthead">
    <a class="brand" href="https://ghostpress.app/">GhostPress</a>
    <span class="label">Status</span>
  </header>
  <section class="summary {worst}">
    <span class="dot" aria-hidden="true"></span>
    <h1>{headline}</h1>
    <p class="updated">Checked {updated}. Every five minutes from outside our own servers.</p>
  </section>
  {open_incidents}
  <ul class="components">{components}</ul>
  <section class="past">
    <h2>Past incidents</h2>
    {past}
  </section>
  <footer class="foot">
    <p>Questions about an incident: <a href="mailto:info@ghostpress.app">info@ghostpress.app</a></p>
    <p><a href="https://a.ghostpress.app/">Sign in</a> · <a href="https://ghostpress.app/">ghostpress.app</a> · <a href="status.json">status.json</a></p>
  </footer>
</main>
</body>
</html>
"""


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    checks = run_checks()
    for check in checks:
        print(f"{check['key']:10} {'ok  ' if check['ok'] else 'FAIL'} {check['ms']} ms  {check['note']}")
    state = update_state(load_previous(), checks, now)
    incidents = json.loads((ROOT / "incidents.json").read_text(encoding="utf-8"))
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "static", OUT)
    (OUT / "status.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    (OUT / "index.html").write_text(render(state, incidents, now), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
