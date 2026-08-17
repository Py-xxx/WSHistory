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


