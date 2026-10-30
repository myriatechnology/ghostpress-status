# GhostPress status

The public status page for GhostPress: https://status.ghostpress.app
(https://myriatechnology.github.io/ghostpress-status/ redirects there).

Every five minutes `.github/workflows/status.yml` runs `probe.py` on a GitHub runner, outside
GhostPress's own Azure environment, and deploys the page to GitHub Pages. GitHub may start a
scheduled run several minutes late.

| Component | Check |
|---|---|
| Dashboard and API | `GET https://a.ghostpress.app/api/health` answers 200 with `status: ok` |
| Database | `GET https://a.ghostpress.app/api/ready` reports `db: ok` |
| Writing and scouting jobs | the same answer lists every queue with a depth |
| Website | `GET https://ghostpress.app/` answers 200 |
| The GhostPress Edition | `GET https://ghostpress.app/blog` answers 200 |

One failed check shows a component as degraded (so does an answer slower than 4 s); two in a
row show an outage. The model provider has no public probe and is not shown.

## History

There is no database and nothing is committed by the probe. The page's `status.json` holds 90
days of daily check counts per component; each run reads the live copy, adds its checks and
deploys again. If the live copy cannot be read (anything but a 404 on the first deploy) the
run fails without deploying, so the history is never overwritten by an empty one.

## Posting an incident

Edit `incidents.json` on GitHub (the pencil icon) and commit to `main`; the page redeploys
within a minute or two. Newest updates can go anywhere in the list; they are sorted by time.

```json
[
  {
    "title": "Drafts are slow to appear",
    "status": "investigating",
    "started": "2026-11-02T14:05:00Z",
    "resolved": null,
    "updates": [
      {"at": "2026-11-02T14:05:00Z", "text": "Writing jobs are taking longer than usual. We are looking into it."}
    ]
  }
]
```

`status` is `investigating`, `identified`, `monitoring` or `resolved`. A resolved incident
(with `resolved` set) stays under "Past incidents" for 30 days. Times are UTC.

## Keepalive

GitHub turns off scheduled workflows in a public repository after 60 days without a commit.
`.github/workflows/keepalive.yml` commits `.keepalive` on the first of each month.

## Custom domain

`status.ghostpress.app` is a CNAME to `myriatechnology.github.io` in the Microsoft 365 admin
center (ghostpress.app DNS), set as the custom domain under Settings › Pages with HTTPS
enforced. The repository variable `STATUS_URL` names it, and so does `STATUS_PAGE_URL` on the
GhostPress web app.
