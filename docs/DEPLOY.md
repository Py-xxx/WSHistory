# Deploying on the Pi (pm2)

Python 3.9+ and pm2. No pip, no virtualenv — deliberately, so an unattended box does not break
during an OS upgrade two years from now.

## 1. Install

First decide **which user runs pm2**, because it determines ownership below. pm2 runs a separate
daemon per user, so if your existing apps are listed by a plain `pm2 ls`, they run as that user
and this one must too — mixing `sudo pm2` and `pm2` silently creates a second daemon and the app
will not appear alongside the others.

```bash
pm2 ls            # if this lists your apps without sudo, use that user below (assumed: pi)
```

```bash
sudo install -d -m 0755 /opt/wshistory
sudo cp scripts/wshistory.py ecosystem.config.js /opt/wshistory/
sudo chmod +x /opt/wshistory/wshistory.py

# pm2 writes the logs, so the log directory must be owned by the user pm2 runs as —
# root-owned 0755 gives "EACCES: permission denied, open '/var/log/wshistory/out.log'".
# /opt/wshistory can stay root-owned: the script only needs read + execute.
sudo install -d -m 0755 -o pi -g pi /var/log/wshistory
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
/opt/wshistory/wshistory.py backfill --days 30
```

Takes a few minutes; it sleeps between fetches on purpose. Re-running is safe — days already in
the manifest are skipped.

## 4. Start under pm2

```bash
cd /opt/wshistory
pm2 start ecosystem.config.js
pm2 save
```

Use the same user as your other pm2 apps — **no `sudo`** if they run as `pi`.

**`pm2 save` only persists what is currently running.** If the start failed and you saved
anyway, the dump records a list *without* this app and it will not come back after a reboot —
so fix the failure, start it, and save again. Confirm with `pm2 ls` that `wshistory` is in the
list before saving.

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
/opt/wshistory/wshistory.py publish --window 30
```

## Verifying it works

```bash
# What is published right now
curl -s https://github.com/Py-xxx/WSHistory/releases/download/manifest/manifest.json | head -20

# Force a specific day (idempotent — skipped if already published)
/opt/wshistory/wshistory.py publish --day 2026-08-16
```

## Failures seen in practice

Every one of these was hit during the first real deployment.

| Symptom | Cause | Fix |
|---|---|---|
| `EACCES: permission denied, open '/var/log/wshistory/out.log'` | Log directory owned by root, pm2 runs as `pi` | `sudo chown -R pi:pi /var/log/wshistory` |
| App missing after a reboot | `pm2 save` ran while the app was not started, so the dump recorded a list without it | Start it, confirm in `pm2 ls`, then `pm2 save` again |
| `WSH_TOKEN is required to publish` | `pi` cannot read `/etc/wshistory.env` | `sudo chown pi:pi /etc/wshistory.env` (keep it `600`) |
| App missing from your usual `pm2 ls` | Started under `sudo`, creating a second daemon under root | `sudo pm2 delete wshistory`, then start again without `sudo` |
| `--window` not in `publish --help` | An older copy of the script is still in `/opt/wshistory` | Re-copy from the repo |
