# Archive shards (`data/archive-raw`)

Data only: gzip JSONL shards of the replay archive built by `python -m eval.archive.collect` (code on
branch `ws/archive`, W-A). No code lives on this branch.

* `shards/<namespace>/<table>/<YYYY-MM-DD>[-NNN].jsonl.gz`: one partition per UTC day, rows sorted by
  primary key. Each collection host writes its own namespace (`direct`: sources reachable from the cloud
  container; `wayback`: web.archive.org sources collected on a laptop), so pushes never overwrite each other.
* `shards/<namespace>/manifest.json`: row and file counts per table at that namespace's last export.

Rebuild the SQLite archive (all namespaces merged, idempotent upserts):

```bash
python -m eval.archive.rebuild --shards-dir <checkout>/shards --db eval/data/archive.sqlite
```

Provenance matters: `observations.provenance` says whether rank / engagement are *as of* `observed_at`
(`wayback`, `live`, `river_post_time`, `feed_pubdate`) or were captured later (`hindsight_listing`,
`api_created_at`: then they sit in `extra_json.hindsight` and are for labels only). See
`docs/archive-datasheet.md` and `eval/archive/README.md` on `ws/archive`.
