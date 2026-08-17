# WSHistory file format

The contract between the publisher (a Raspberry Pi running `scripts/wshistory.py`) and the
consumer (WarStonks). Both sides are written against this document; change it deliberately.

## Why this format exists

WarStonks needs a price for **every** item in the game, not just the ones a user happened to
scan. [relics.run](https://relics.run) publishes exactly that as a daily JSON archive, but
fetching it per user costs ~3.9 MB/day/user against a community-run static host that serves no
gzip. Publishing one pre-filtered, pre-compressed copy per day moves that to **one fetch per
day, globally**, and cuts the client's transfer by roughly 50×.

## Attribution

The underlying data is aggregated and published by **relics.run**, which in turn derives it
from Warframe.Market. This project is a re-publisher, not the origin. Every file carries
`"source": "relics.run"`, and that attribution must survive any change to this format.

## Distribution

Files are published as **GitHub Release assets**, never via `raw.githubusercontent.com`.

That is not a style preference. `raw.githubusercontent.com` and `codeload.github.com` were
observed returning HTTP 429/503 on a *first* request from a South African edge (`cache-jnb…`),
while release-asset downloads from `objects.githubusercontent.com` succeeded and honoured
`If-None-Match` with a 0-byte 304. Release assets are the supported path for file distribution
and are not metered the same way.

Consumers must **not** call `api.github.com` for discovery either — it is 60 requests/hour
unauthenticated *per IP*, which is a poor dependency for a desktop app behind NAT. Discovery
goes through `manifest.json` (below), which is itself a release asset at a stable URL.

| Artifact | Location |
|---|---|
| `manifest.json` | asset on the `manifest` release (stable tag, updated in place) |
| `wsh_YYYY-MM-DD.json.gz` | asset on the `data-YYYY-MM` release for that month |

Monthly releases keep asset counts bounded; the manifest stays at one fixed URL forever.

## `manifest.json`

Uncompressed (it is small, and it is fetched most often). Fetch it with `If-None-Match`; a
304 means nothing new and costs zero bytes.

```json
{
  "schema": 1,
  "source": "relics.run",
  "updated_at": "2026-08-17T03:12:44Z",
  "days": [
    {
      "day": "2026-08-16",
      "file": "wsh_2026-08-16.json.gz",
      "url": "https://github.com/<owner>/<repo>/releases/download/data-2026-08/wsh_2026-08-16.json.gz",
      "bytes": 96421,
      "sha256": "9f2c…",
      "rows": 7431
    }
  ]
}
```

`days` is sorted **newest first**, so a client wanting "the last N days" reads a prefix and
stops. `url` is absolute so the client never has to construct one.

## Daily file

`wsh_YYYY-MM-DD.json.gz` — gzip of a single JSON object. One file covers one **UTC day**.

```json
{
  "schema": 1,
  "day": "2026-08-16",
  "source": "relics.run",
  "generated_at": "2026-08-17T03:12:44Z",
  "columns": ["item_id","mod_rank","subtype","amber_stars","cyan_stars","order_type",
              "volume","min_price","max_price","open_price","closed_price",
              "avg_price","wa_price","median","moving_avg","donch_top","donch_bot"],
  "rows": [
    ["54aae292e7798909064f1575",null,null,null,null,"closed",17,28.0,30.0,30.0,30.0,29.0,29.882,30.0,30.0,40.0,20.0]
  ]
}
```

### Rows are arrays, and `columns` is authoritative

Array rows rather than objects because the key names dominate the payload otherwise — the
difference is roughly 14× before compression. The cost is that position matters, so:

- **`columns` is written into every file.** A consumer must read the order from the file, not
  hardcode it. Appending a column is then a non-breaking change.
- A consumer that does not recognise a column name should ignore that column, not fail.

### Fields

Carried through from relics.run unchanged, including nulls. Nothing is recomputed here — this
project transports and compresses, it does not derive. Any modelling belongs in WarStonks,
where the catalogue lives.

| Field | Meaning |
|---|---|
| `item_id` | Warframe.Market item id. **Identical to WarStonks' `item_key`** — verified 3837/3837 against the v2 catalogue with zero drift in either direction. |
| `mod_rank` | Mod/arcane rank, or `null`. Maps to WarStonks' `rank:N` / `base`. |
| `subtype` | e.g. `basic` / `adorned` / `magnificent`, or `null`. **WarStonks has no `variant_key` concept for this yet.** |
| `amber_stars`, `cyan_stars` | Ayatan sculpture star counts, or `null`. Same gap as `subtype`. |
| `order_type` | `closed` or `buy`. See below. |
| `volume` … `donch_bot` | Price aggregates, verbatim. |

**Raw dimensions are published, not WarStonks' `variant_key`.** Mapping belongs in the app,
where the catalogue is. If a mapping turns out wrong we fix the app rather than republishing
history — and `subtype`/stars have no representation in `variant_key` today, so freezing a
guess into the archive would be the expensive mistake.

### `closed` and `buy` only — never `sell`

- `closed` is what people **actually paid**. It is the honest price and the primary signal.
- `buy` is a standing offer — someone committing to pay. Kept as a *floor* for the long tail,
  and WarStonks already renders bid-derived prices differently from traded ones.
- `sell` is excluded. An asking price is what a seller hopes for, nobody has to accept it, and
  it is where trolls park 999p listings. It is also the largest of the three.

Measured over 30 days, this is the coverage trade:

| Included | Item-variants covered | Rows/30d |
|---|---|---|
| `closed` only | 85.4% | 97k |
| **`closed` + `buy`** | **92.9%** | 222k |
| everything | 100% | 400k |

### An item with no trades that day has no row

`closed` records only exist for items that actually traded. A single day covers ~53% of
item-variants; 7 days ~73%; 30 days ~85%. **Consumers must accumulate across days and take the
most recent row per (item, variant)** — never assume one file prices everything.

## Rules that keep this cheap

1. **A dated file is immutable.** Once published for a day it is never rewritten. Consumers may
   cache it forever and must never re-fetch a day they already hold.
2. **Every fetch is conditional.** `If-None-Match` on the manifest; 304 is the normal case.
3. **relics.run is fetched once per day, globally** — by the Pi, never by the app.
4. **Nothing is deleted.** Git retains blobs regardless, so pruning old days reclaims nothing
   (~26 MB of objects per year at this size) while destroying the ability to offer deeper
   history later. Keep everything.

## Versioning

`schema` is an integer in both the manifest and every daily file.

- Adding a column, or a new optional top-level key → same `schema`.
- Changing a column's meaning, removing one, or changing row structure → increment.

A consumer must refuse a `schema` it does not know rather than guess.
