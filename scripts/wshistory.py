#!/usr/bin/env python3
"""Fetch one day of relics.run price history, compress it, publish it to GitHub Releases.

Runs unattended on a Raspberry Pi, once a day, for years. That shapes every decision here:

* **Standard library only.** No pip, no virtualenv, no dependency that can break during an
  unattended OS upgrade eighteen months from now.
* **Idempotent.** Re-running for a day already published is a no-op, not a duplicate. Safe to
  invoke from cron, a retry timer, and by hand on the same day.
* **Fails loudly, never partially.** A day is either fully published and in the manifest, or
  absent. There is no state where the manifest advertises a file that is not there.

See `docs/FORMAT.md` for the published contract. This script is the only writer of it.

Usage:
    wshistory.py publish            # yesterday (UTC), the normal cron invocation
    wshistory.py publish --day 2026-08-16
    wshistory.py backfill --days 30 # one-time, at setup
    wshistory.py check              # is today's upstream file out yet? (for alerting)
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

SCHEMA = 1
SOURCE = "relics.run"
UPSTREAM = "https://relics.run/history/price_history_{day}.json"

# Excluded on purpose — see FORMAT.md. An asking price is what a seller hopes for.
KEEP_ORDER_TYPES = ("closed", "buy")

# Position is the contract. Append only; never reorder, never remove without bumping SCHEMA.
COLUMNS = [
    "item_id", "mod_rank", "subtype", "amber_stars", "cyan_stars", "order_type",
    "volume", "min_price", "max_price", "open_price", "closed_price",
    "avg_price", "wa_price", "median", "moving_avg", "donch_top", "donch_bot",
]

MANIFEST_TAG = "manifest"
MANIFEST_NAME = "manifest.json"

# Identifies us to relics.run so the operator can see who is calling and reach us. Being
# anonymous is what gets a polite integration blocked.
USER_AGENT = (
    "WSHistory/1.0 (+https://github.com/{repo}) "
    "one-request-per-day re-publisher; contact via repo issues"
)


class Fatal(Exception):
    """Anything that should stop the run without publishing a partial day."""


def log(message: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}] {message}", flush=True)


# ── upstream ────────────────────────────────────────────────────────────────────────────────

def fetch_upstream(day: str, repo: str, timeout: int = 120) -> bytes | None:
    """One day's raw JSON, or None when upstream has not published it yet.

    A 404 is the *expected* state for today and for yesterday before ~03:00 UTC — relics.run
    publishes a day early the following morning. Treating it as an error would turn the normal
    case into noise and, worse, into a retry storm.
    """
    url = UPSTREAM.format(day=day)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT.format(repo=repo)})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise Fatal(f"upstream returned HTTP {error.code} for {day}") from error
    except urllib.error.URLError as error:
        raise Fatal(f"upstream unreachable for {day}: {error.reason}") from error


def transform(raw: bytes, day: str, repo: str) -> tuple[bytes, int]:
    """Raw upstream JSON -> the gzipped payload we publish. Returns (bytes, row count).

    Deliberately does no modelling: fields are carried through verbatim, including nulls.
    This project transports and compresses; deriving anything belongs in WarStonks, where the
    catalogue is.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise Fatal(f"upstream JSON for {day} did not parse: {error}") from error
    if not isinstance(parsed, dict) or not parsed:
        raise Fatal(f"upstream payload for {day} is not a non-empty object")

    rows: list[list] = []
    seen_days: set[str] = set()
    for records in parsed.values():
        if not isinstance(records, list):
            continue
        for record in records:
            if record.get("order_type") not in KEEP_ORDER_TYPES:
                continue
            item_id = record.get("item_id")
            if not item_id:
                # Without an id the row cannot be joined to anything downstream.
                continue
            stamp = record.get("datetime")
            if isinstance(stamp, str):
                seen_days.add(stamp[:10])
            rows.append([record.get(column) for column in COLUMNS])

    if not rows:
        raise Fatal(f"no {'/'.join(KEEP_ORDER_TYPES)} rows found for {day} — refusing to publish an empty day")

    # Upstream names the file by day; assert the contents agree, so a mislabelled or shifted
    # archive is caught here rather than silently landing under the wrong date forever.
    if seen_days and seen_days != {day}:
        raise Fatal(f"{day}: payload contains dates {sorted(seen_days)} — refusing to publish")

    document = {
        "schema": SCHEMA,
        "day": day,
        "source": SOURCE,
        "generated_at": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
        "columns": COLUMNS,
        "rows": rows,
    }
    body = json.dumps(document, separators=(",", ":")).encode()
    # mtime=0 so the same input always produces byte-identical output; makes re-runs verifiable.
    payload = gzip.compress(body, compresslevel=9, mtime=0)
    return payload, len(rows)


# ── github ──────────────────────────────────────────────────────────────────────────────────

class GitHub:
    """The slice of the GitHub REST API this needs, over urllib."""

    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.token = token

    def _call(self, method: str, url: str, *, data: bytes | None = None,
              content_type: str | None = None, timeout: int = 120):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"WSHistory/1.0 (+https://github.com/{self.repo})",
        }
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # One retry on 5xx/network: a Pi on domestic broadband will hit transient failures, and
        # losing a day to a blip is worse than one extra call.
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                if error.code >= 500 and attempt == 1:
                    time.sleep(5)
                    continue
                raise Fatal(f"GitHub {method} {url} -> HTTP {error.code}: "
                            f"{error.read()[:300].decode('utf-8', 'replace')}") from error
            except urllib.error.URLError as error:
                if attempt == 1:
                    time.sleep(5)
                    continue
                raise Fatal(f"GitHub unreachable: {error.reason}") from error

    def release_by_tag(self, tag: str):
        try:
            return self._call("GET", f"https://api.github.com/repos/{self.repo}/releases/tags/{tag}")
        except Fatal as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def ensure_release(self, tag: str, name: str) -> dict:
        existing = self.release_by_tag(tag)
        if existing:
            return existing
        log(f"creating release {tag}")
        return self._call(
            "POST", f"https://api.github.com/repos/{self.repo}/releases",
            data=json.dumps({"tag_name": tag, "name": name,
                             "body": f"Daily price history. Source: {SOURCE}. See docs/FORMAT.md."}).encode(),
            content_type="application/json",
        )

    def upload_asset(self, release: dict, filename: str, payload: bytes,
                     content_type: str, *, replace: bool) -> str:
        for asset in release.get("assets", []):
            if asset["name"] == filename:
                if not replace:
                    return asset["browser_download_url"]
                self._call("DELETE", f"https://api.github.com/repos/{self.repo}/releases/assets/{asset['id']}")
                break
        upload_url = release["upload_url"].split("{")[0]
        created = self._call("POST", f"{upload_url}?name={filename}",
                             data=payload, content_type=content_type)
        return created["browser_download_url"]


# ── manifest ────────────────────────────────────────────────────────────────────────────────

def load_manifest(github: GitHub) -> dict:
    release = github.release_by_tag(MANIFEST_TAG)
    for asset in (release or {}).get("assets", []):
        if asset["name"] == MANIFEST_NAME:
            request = urllib.request.Request(
                asset["browser_download_url"],
                headers={"User-Agent": f"WSHistory/1.0 (+https://github.com/{github.repo})"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
    return {"schema": SCHEMA, "source": SOURCE, "days": []}


def write_manifest(github: GitHub, manifest: dict) -> None:
    manifest["schema"] = SCHEMA
    manifest["source"] = SOURCE
    manifest["updated_at"] = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
    # Newest first, so a client wanting the last N days reads a prefix and stops.
    manifest["days"].sort(key=lambda entry: entry["day"], reverse=True)
    release = github.ensure_release(MANIFEST_TAG, "Manifest")
    github.upload_asset(release, MANIFEST_NAME,
                        json.dumps(manifest, indent=1).encode(),
                        "application/json", replace=True)


# ── commands ────────────────────────────────────────────────────────────────────────────────

def publish_day(github: GitHub, manifest: dict, day: str) -> bool:
    """Publish one day. Returns False when there is nothing to do."""
    if any(entry["day"] == day for entry in manifest["days"]):
        log(f"{day}: already published, skipping")
        return False

    raw = fetch_upstream(day, github.repo)
    if raw is None:
        log(f"{day}: not published upstream yet")
        return False

    payload, row_count = transform(raw, day, github.repo)
    filename = f"wsh_{day}.json.gz"
    tag = f"data-{day[:7]}"

    # Asset first, manifest second, always. The manifest is the index clients trust, so it must
    # never advertise a file that is not there; the reverse (an asset not yet indexed) is
    # harmless and self-corrects on the next run.
    release = github.ensure_release(tag, f"Price history {day[:7]}")
    url = github.upload_asset(release, filename, payload, "application/gzip", replace=False)

    manifest["days"].append({
        "day": day,
        "file": filename,
        "url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rows": row_count,
    })
    log(f"{day}: published {row_count:,} rows, {len(payload)/1024:.0f} KiB "
        f"({len(raw)/len(payload):.0f}x smaller than upstream)")
    return True


def command_publish(args, github: GitHub) -> int:
    day = args.day or f"{dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)}"
    manifest = load_manifest(github)
    if publish_day(github, manifest, day):
        write_manifest(github, manifest)
        return 0
    # Nothing published is not a failure — cron runs before upstream is ready most mornings.
    return 0


def command_backfill(args, github: GitHub) -> int:
    manifest = load_manifest(github)
    today = dt.datetime.now(dt.timezone.utc).date()
    published = 0
    # Newest first: if the run is interrupted, what survives is the most useful window.
    for offset in range(1, args.days + 1):
        day = f"{today - dt.timedelta(days=offset)}"
        try:
            if publish_day(github, manifest, day):
                published += 1
                write_manifest(github, manifest)   # after each day, so a crash loses one at most
                time.sleep(args.delay)             # deliberately unhurried against a free host
        except Fatal as error:
            log(f"{day}: FAILED — {error}")
    log(f"backfill complete: {published} day(s) published")
    return 0


def command_check(args, github: GitHub) -> int:
    """Is upstream late? Exit 1 when it is, so cron/systemd can alert.

    'Late' means yesterday's file still is not up well past the usual ~03:00 UTC publish.
    """
    day = args.day or f"{dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)}"
    url = UPSTREAM.format(day=day)
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT.format(repo=github.repo)})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            log(f"{day}: upstream present ({response.headers.get('Content-Length')} bytes)")
            return 0
    except urllib.error.HTTPError as error:
        if error.code == 404:
            log(f"{day}: UPSTREAM LATE — not published yet")
            return 1
        log(f"{day}: upstream HTTP {error.code}")
        return 1
    except urllib.error.URLError as error:
        log(f"{day}: upstream unreachable — {error.reason}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("WSH_REPO"),
                        help="owner/name (env: WSH_REPO)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("publish"); p.add_argument("--day"); p.set_defaults(fn=command_publish)
    p = sub.add_parser("backfill")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--delay", type=float, default=3.0, help="seconds between upstream fetches")
    p.set_defaults(fn=command_backfill)
    p = sub.add_parser("check"); p.add_argument("--day"); p.set_defaults(fn=command_check)

    args = parser.parse_args()
    if not args.repo:
        print("error: --repo or WSH_REPO is required", file=sys.stderr)
        return 2
    token = os.environ.get("WSH_TOKEN", "")
    if not token and args.command != "check":
        print("error: WSH_TOKEN is required to publish", file=sys.stderr)
        return 2

    try:
        return args.fn(args, GitHub(args.repo, token))
    except Fatal as error:
        log(f"FATAL: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
