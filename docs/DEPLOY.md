# Deploying on the Pi (pm2)

Python 3.9+ and pm2. No pip, no virtualenv — deliberately, so an unattended box does not break
during an OS upgrade two years from now.

## 1. Install

```bash
sudo install -d -m 0755 /opt/wshistory
sudo install -d -m 0755 /var/log/wshistory
sudo cp scripts/wshistory.py /opt/wshistory/
sudo cp ecosystem.config.js /opt/wshistory/
sudo chmod +x /opt/wshistory/wshistory.py
```

## 2. Configure

The script reads `/etc/wshistory.env` at runtime if the variables are not already in the
environment. Keep the token here rather than in `ecosystem.config.js`:

```bash
sudo tee /etc/wshistory.env >/dev/null <<'EOF'
WSH_REPO=Py-xxx/WSHistory
WSH_TOKEN=github_pat_...
EOF
sudo chmod 600 /etc/wshistory.env
```

`ecosystem.config.js` is committed to a public repo, and `pm2 save` writes the captured
environment into `~/.pm2/dump.pm2` — a token in either would end up in two places that are easy
to forget when rotating. One root-only file is simpler to reason about.

> **On the existing general token.** A token that "can do anything" works fine here, but it
> means a compromise of the Pi is a compromise of every repo on the account. A fine-grained
> token scoped to `WSHistory` with **Contents: read and write** is enough for Releases and
> limits the blast radius. Your call — worth five minutes if the Pi is exposed at all.

## 3. Backfill once

The only time relics.run is fetched in bulk — once, ever, for every user.

```bash
sudo /opt/wshistory/wshistory.py backfill --days 30
```

Takes a few minutes; it sleeps between fetches on purpose. Re-running is safe — days already in
the manifest are skipped.

## 4. Start under pm2

```bash
cd /opt/wshistory
sudo pm2 start ecosystem.config.js
sudo pm2 save
```

Run it as whichever user owns your other pm2 apps; it needs read access to
`/etc/wshistory.env`, so either run as root or relax that file's ownership to that user
(keeping it `600`).

If pm2 is not yet set to survive reboots:

```bash
sudo pm2 startup      # prints a command to run
```

### What "stopped" means here

Between runs `pm2 ls` shows `wshistory` as **stopped**, with a restart count that climbs by one
each hour. That is correct. It is a oneshot job — `autorestart: false` stops pm2 restarting it
the instant it exits, and `cron_restart` starts it again on schedule.

```bash
pm2 logs wshistory --lines 50     # what it did
pm2 describe wshistory            # next cron firing, last exit code
```

## 5. Alerting when upstream is late

`check` exits non-zero when yesterday's file still is not published, so anything that reacts to
an exit code works:

```bash
/opt/wshistory/wshistory.py check || <your notifier>
```

Run it late enough that "late" means late — 09:00 UTC is about six hours past the usual publish.
Add it as a second pm2 app with `cron_restart: '0 9 * * *'`, or as a plain crontab line; it
needs no token, so it is the one part that can run unprivileged.

## Why a missed run does not lose a day

pm2 has **no equivalent of systemd's `Persistent=true`** — if the Pi is off at 03:07, that
firing is simply skipped, with no catch-up.

So `publish` does not just do yesterday. It fills **any missing day in the last 7** (`--window`),
newest first. Correctness therefore does not depend on the scheduler having run: whatever fires
next picks up the gap, whether the Pi was off for an hour or three days.

That also makes the hourly schedule cheap — most runs find nothing to do and exit in well under
a second, having made one conditional request.

If the Pi is off for longer than the window, catch up by hand:

```bash
sudo /opt/wshistory/wshistory.py publish --window 30
```

## Verifying it works

```bash
# What is published right now
curl -s https://github.com/Py-xxx/WSHistory/releases/download/manifest/manifest.json | head -20

# Force a specific day (idempotent — skipped if already published)
sudo /opt/wshistory/wshistory.py publish --day 2026-08-16
```
