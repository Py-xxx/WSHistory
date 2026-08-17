# WSHistory

Daily Warframe market price history, re-published in a small, pre-filtered form for
[WarStonks](https://github.com/) to consume.

**The data is not ours.** It is aggregated and published by **[relics.run](https://relics.run)**,
which derives it from [Warframe.Market](https://warframe.market). This repository is a
re-publisher: it fetches one file per day, drops what WarStonks does not need, compresses it,
and serves it as a GitHub Release asset. Every file records `"source": "relics.run"`.

## Why it exists

WarStonks needs a price for *every* item in the game, not just the ones a user happened to
scan manually. relics.run already publishes exactly that, but consuming it directly would mean
every user fetching **3.9 MB per day** from a community-run static host that serves no gzip.

This moves that to **one fetch per day, globally**:

| | Direct from relics.run | Via WSHistory |
|---|---|---|
| Requests to relics.run | 1 per user per day | **1 per day, total** |
| Client transfer per day | 3.9 MB | **136 KiB** (28× less) |
| New-user backfill (30 days) | ~117 MB | **~4 MB** |

It also gives a place to monitor upstream and alert when a day is late.

## What is published

See **[docs/FORMAT.md](docs/FORMAT.md)** for the contract — read that before writing a consumer.

- `manifest.json` — index of available days, on the stable `manifest` release.
- `wsh_YYYY-MM-DD.json.gz` — one UTC day, on that month's `data-YYYY-MM` release.

Only `closed` (what people actually paid) and `buy` (standing offers, a floor for rare items)
are kept. `sell` is excluded: an asking price is what a seller hopes for, nobody has to accept
it, and it is where 999p troll listings live.

**Assets are served from GitHub Releases, never `raw.githubusercontent.com`** — raw and
codeload were observed returning HTTP 429/503 on a first request from a South African edge,
while release assets succeeded and honoured conditional GETs. See FORMAT.md.

## Setup on the Pi

Python 3.9+ and nothing else. No pip, no virtualenv — deliberately, so an unattended box does
not break during an OS upgrade two years from now.

### 1. Create the repository

Public, so the app can download release assets without shipping a token.

### 2. Create a fine-grained token

Scope it to *this repository only*, with **Contents: read and write** (that covers Releases).
Nothing else.

### 3. Configure

```bash
sudo install -d -m 0755 /opt/wshistory
sudo cp scripts/wshistory.py /opt/wshistory/
sudo chmod +x /opt/wshistory/wshistory.py

sudo tee /etc/wshistory.env >/dev/null <<'EOF'
WSH_REPO=your-name/WSHistory
WSH_TOKEN=github_pat_...
EOF
sudo chmod 600 /etc/wshistory.env    # the token is in here
```

### 4. Backfill once

This is the only time relics.run is fetched in bulk — once, ever, for everyone.

```bash
set -a; . /etc/wshistory.env; set +a
/opt/wshistory/wshistory.py backfill --days 30
```

Takes a few minutes; it sleeps between fetches on purpose.

### 5. Schedule the daily run

Upstream publishes a day at roughly 03:00 UTC the following morning. Running hourly is fine —
`publish` is idempotent and exits quietly when the day is already done or not yet upstream, so
the retry-on-failure behaviour is simply "run again next hour".

```ini
# /etc/systemd/system/wshistory.service
[Unit]
Description=Publish daily Warframe price history
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/wshistory.env
ExecStart=/opt/wshistory/wshistory.py publish
```

```ini
# /etc/systemd/system/wshistory.timer
[Unit]
Description=Hourly attempt to publish yesterday's price history

[Timer]
OnCalendar=*-*-* *:07:00
Persistent=true          # catches up if the Pi was off

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now wshistory.timer
```

`Persistent=true` matters: if the Pi is off overnight it runs on next boot rather than silently
skipping the day.

### 6. Alerting on a late upstream

`check` exits non-zero when yesterday's file is still missing, so anything that reacts to exit
codes will do:

```bash
/opt/wshistory/wshistory.py check || notify-send "relics.run is late"
```

Run it late enough in the day that "late" really means late — say 09:00 UTC, six hours past the
usual publish.

## Commands

| Command | Purpose |
|---|---|
| `publish` | Publish yesterday (UTC). The normal scheduled invocation. |
| `publish --day 2026-08-16` | Publish a specific day. |
| `backfill --days 30` | One-time bulk publish, newest first. |
| `check` | Exit 1 if upstream has not published yet. For alerting. |

`publish` is idempotent — a day already in the manifest is skipped, so running it by hand, on a
timer, and after a failure cannot produce duplicates.

## Operational notes

- **Dated files are immutable.** Once a day is published it is never rewritten; consumers cache
  it forever.
- **Nothing is deleted.** Git retains blobs regardless, so pruning old days reclaims nothing
  (~48 MB of objects per year at this size) while destroying the ability to offer deeper
  history later.
- **Asset first, manifest second.** The manifest is the index clients trust, so it must never
  advertise a file that is not there. The reverse is harmless and self-corrects.
- **A 404 from upstream is normal**, not a fault — today's file does not exist until tomorrow.
