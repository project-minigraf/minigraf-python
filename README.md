# minigraf (Python)

Python binding for [Minigraf](https://github.com/project-minigraf/minigraf) — zero-config,
single-file, embedded bi-temporal graph database with Datalog queries.

## Installation

```bash
pip install minigraf
```

Supports Python 3.9+ on Linux (x86_64, aarch64), macOS (universal2), and Windows (x86_64).

## Quick start

```python
import json
from minigraf import MiniGrafDb

# In-memory database
db = MiniGrafDb.open_in_memory()
db.execute('(transact [[:alice :name "Alice"] [:alice :age 30]])')

result = json.loads(db.execute("(query [:find ?n ?a :where [?e :name ?n] [?e :age ?a]])"))
print(result["results"])  # [["Alice", 30]]

# File-backed (persisted to disk)
db = MiniGrafDb.open("path/to/mydb.graph")
db.execute('(transact [[:bob :name "Bob"]])')
db.checkpoint()
```

## Open options

`minigraf.open()` takes the Rust `OpenOptions` as keyword arguments. An option left out
keeps its default.

```python
import minigraf

# Read-only: shared lock, nothing written. Any number of read-only handles can share
# the file; writes raise MiniGrafError with API-014, a missing file STG-042.
src = minigraf.open("app.graph", read_only=True, page_cache_size=4096)

# Every option: read_only, page_cache_size, allow_unlocked, wal_checkpoint_threshold,
# max_derived_facts, max_results, synchronous (minigraf.SyncMode.FULL / NORMAL).
db = minigraf.MiniGrafDb.open_with_options("app.graph", minigraf.OpenOptions(max_results=10_000))
```

`wal_checkpoint_threshold=minigraf.WAL_CHECKPOINT_NEVER` turns off automatic checkpoints
and the checkpoint when the handle closes, for applications that call `checkpoint()` on
their own schedule.

## Query cursors

`db.query()` returns a cursor whose answer is fixed when it opens. Iterating gives
rows, decoded as in `execute()`; `next_batch(n)` gives a JSON string of up to `n` rows,
or `None` at the end.

```python
with db.query("(query [:find ?n :where [?e :name ?n]])") as cursor:
    print(cursor.vars())  # ["?n"]
    for row in cursor:
        print(row)
```

## Fact log and log writer

`db.fact_log(filter)` streams every fact version (assertions and retractions, with
`tx_count`, `tx_id` and valid-time bounds) as `FactRecord`s. `LogWriter` builds a new
file from records, keeping those bounds: the load step of an offline migration.

```python
from minigraf import FactFilter, LogWriter, OpenOptions

src = minigraf.open("old.graph", read_only=True)
with src.fact_log(FactFilter()) as log, LogWriter.create("new.graph", OpenOptions()) as out:
    batch = []
    for rec in log:
        if not rec.attribute.startswith(":secret/"):
            batch.append(rec)
        if len(batch) == 10_000:
            out.append_batch(batch)
            batch = []
    out.append_batch(batch)
    out.advance_tx_count(src.current_tx_count())
# Leaving the `with` block finishes the file; an exception abandons it and leaves no file.
```

`FactFilter` takes `attributes`, `attribute_prefixes`, `entities` (UUID strings),
`tx_from`/`tx_to` (inclusive), `order` (`FactOrder.TX` or `FactOrder.STORAGE`) and
`window`. A record's `value` is a `minigraf.Value` (`TEXT`, `INT64`, `FLOAT64`, `BOOL`,
`REF`, `KEYWORD`, `NULL`), so a ref and a string stay different. A `valid_to` of
`minigraf.VALID_TIME_FOREVER` means forever.

## Errors

Every error is a `MiniGrafError` whose `msg` starts with its code, such as
`[API-015]`. See the [error reference](https://github.com/project-minigraf/minigraf/blob/main/docs/ERROR_REFERENCE.md).
`append_batch` stops at the first rejected record and ends the message with
`(batch index N)`; the records before it stay appended.

## Building from source

Requires Rust stable toolchain and `maturin`.

```bash
pip install maturin
maturin develop
```

## Cascade release

This repo receives a `core-release` repository_dispatch from the minigraf monorepo
cascade whenever a new version of the `minigraf` core crate is published. The release
workflow pins the new version, commits, tags, builds wheels for all platforms, and
publishes to PyPI.

## License

MIT OR Apache-2.0
