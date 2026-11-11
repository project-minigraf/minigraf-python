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
# max_derived_facts, max_results, synchronous (minigraf.SyncMode.FULL / NORMAL),
# query_memory_limit, allow_overrides, transaction_chunk_facts, max_iterations.
db = minigraf.MiniGrafDb.open_with_options("app.graph", minigraf.OpenOptions(max_derived_facts=10_000))
```

`query_memory_limit` is the most bytes one query may hold, counted per entry by the
operators that keep state (join build sides, negation and `or` tables, distinct sets,
aggregate groups, window rows, rule-derived facts). A query that would hold more raises
`MiniGrafError` with QRY-012 and frees what it held. The default, and `2**63 - 1`, is no
limit. Rows a cursor returns are not counted, so read a large answer with `db.query()`.

`wal_checkpoint_threshold=minigraf.WAL_CHECKPOINT_NEVER` turns off automatic checkpoints
and the checkpoint when the handle closes, for applications that call `checkpoint()` on
their own schedule.

## Override list and limits

Query text may override some settings with keywords such as `:max-derived-facts 1000`.
`allow_overrides` lists the `minigraf.Overridable` keywords it may use
(`MAX_DERIVED_FACTS`, `MAX_RESULTS`, `QUERY_MEMORY_LIMIT`, `MAX_ITERATIONS`). Left out, the
defaults apply: `minigraf.OVERRIDABLE_DEFAULTS`, read from the core library (today
`MAX_DERIVED_FACTS`, `MAX_RESULTS` and `MAX_ITERATIONS`). A list replaces the defaults, it
does not add to them, and `[]` refuses every keyword: a query that uses one raises
`MiniGrafError.Api` with `[API-025]` from `execute()`, `query()` and `prepare()`.

```python
from minigraf import Overridable

# Query text may set only :query-memory-limit.
db = minigraf.open("app.graph", allow_overrides=[Overridable.QUERY_MEMORY_LIMIT])
# The defaults plus :query-memory-limit.
db = minigraf.open(
    "app.graph",
    allow_overrides=[*minigraf.OVERRIDABLE_DEFAULTS, Overridable.QUERY_MEMORY_LIMIT],
)
```

`max_iterations` is the most rounds each stratum of a rule query may run (default
1,000,000; more raise `MiniGrafError` with `[INT-020]`). `transaction_chunk_facts` is how
many facts one transaction on a file database stages before it is written in chunks
(default 65,536, clamped to 1..=1,000,000). A negative count raises `[API-017]`.

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

## Prepared queries

`db.prepare()` parses a query once; `execute()` and `query()` run it with its `$slots`
bound, from keyword arguments or a dict (names without the `$`). Each run reads the
database as it is then. A prepared query keeps its database open until both are freed.

```python
pq = db.prepare("(query [:find ?n :where [?e :age $age] [?e :name ?n]])")
print(json.loads(pq.execute(age=30))["results"])
print(json.loads(pq.execute({"age": 40}))["results"])
with pq.query(age=30) as cursor:
    for row in cursor:
        print(row)
```

`bool`, `int`, `float`, `str` and `None` bind as values, and a `uuid.UUID` as an entity
(it also fits a value slot as a ref). For the other kinds use `minigraf.Value.KEYWORD(":k")`,
`minigraf.Value.REF(uuid_str)`, `minigraf.Entity(uuid)`, `minigraf.TxCount(n)` or
`minigraf.Timestamp(ms)` for `:as-of`, and `minigraf.ANY_VALID_TIME` for `:valid-at`. Any
other Python type raises `TypeError`. A missing slot raises `MiniGrafError.Api` with
`[API-022]`, a slot in an attribute position `[API-023]`, and a value of the wrong kind
for its slot `[API-024]`.

A `set` or `frozenset` binds a set value, for `in?` and the other set predicates or a
set-valued attribute: `pq.execute(ids={1, 2, 3})`. Its elements are `bool`, `int`, `float`,
`str`, `None` or `uuid.UUID` (a ref). `minigraf.Value` is not hashable, so a set with keyword
elements is written `minigraf.Value.SET([minigraf.Value.KEYWORD(":role/admin")])`. A set
inside a set raises `MiniGrafError.Api` with `[API-021]`; a `list` is not a set. Set values
come back as JSON arrays from `execute()` and as `Value.SET([...])` in fact records.

### Limits of one prepared query

`with_max_derived_facts(n)`, `with_query_memory_limit(bytes)` and `with_max_iterations(n)`
each return a new prepared query with that limit; the one they are called on is
unchanged. They are application code, so `allow_overrides` does not apply to them, and an
allowed keyword in the query text still overrides them. `2**63 - 1` means no limit and a
negative value raises `[API-017]`. A derived query keeps its database open, as any
prepared query does.

```python
reach = db.prepare("(query [:find ?x ?y :where (reach ?x ?y)])")
small = reach.with_max_derived_facts(10_000).with_query_memory_limit(16 << 20)
small.execute()  # [INT-020] or [QRY-012] past a limit; reach.execute() is unchanged
```

## User functions

A predicate subclasses `minigraf.Predicate` and is called in Datalog as `[(name ?v)]`.
An aggregate subclasses `minigraf.Aggregate`; its `accumulator()` returns a new
`minigraf.Accumulator` (`step(value)`, `finish(count)`) for each group or window
partition, and it is used as `(name ?v)` in `:find` or with `:over`. Values arrive as
`minigraf.Value` (`Value.INT64`, `Value.TEXT`, …, with the value in `.value`), and
`finish` returns one.

```python
from minigraf import Accumulator, Aggregate, Predicate, UdfError, Value

class Adult(Predicate):
    def test(self, value):
        if not isinstance(value, Value.INT64):
            raise UdfError.Failed("age is not an integer")
        return value.value >= 18

class Product(Aggregate):
    def accumulator(self):
        return ProductAcc()

class ProductAcc(Accumulator):
    def __init__(self):
        self.product = 1
    def step(self, value):
        self.product *= value.value
    def finish(self, count):  # count: values stepped
        return Value.INT64(self.product)

db.register_predicate("adult?", Adult())
db.register_aggregate("product", Product())
db.execute("(query [:find ?n :where [?e :age ?a] [(adult? ?a)] [?e :name ?n]])")
db.execute("(query [:find ?g (product ?a) :where [?e :group ?g] [?e :age ?a]])")
db.execute("(query [:find ?a (product ?a :over (:order-by ?a)) :where [?e :age ?a]])")
```

- **Failures.** A callback that raises stops the query with `QRY-014`, raised as
  `MiniGrafError.Query`; it is never read as `False` or a missing value. Raise
  `UdfError.Failed(msg)` to choose the message. Any other exception also fails the call,
  with its `repr` (such as `ValueError('bad')`) as the message. A `Value.REF` from
  `finish` whose value is not a UUID is a failing call too.
- **Lifecycle.** The database keeps a strong reference to each registered object until
  the database and every cursor and prepared query opened from it are freed. There is
  no unregister. Predicates and aggregates have separate names, and registering a name
  twice in either is an error (`INT-040`). A query sees the functions registered when it
  opened.
- **Threading.** A callback runs on the thread that calls `execute` (of the database or
  a prepared query) or a cursor's `next_batch`, including a prepared query's cursor.
  The binding releases the GIL while the core runs and the callback re-acquires it, so
  queries on different threads may call the same object at once; it must be thread-safe.
  One accumulator is used by one query on one thread at a time.
- **Cost.** Each call crosses the FFI once: one Python call per value.
- **Re-entry.** A callback may query the same database, open a cursor or register another
  function. Inside a write transaction, the calls the core refuses there still return
  `INT-001`.

## Write transactions

A write transaction stages several `transact`, `retract` and `query` statements, with
queries seeing what it staged, and then commits them as one transaction or rolls them
back (minigraf#824). First in the release built on core v3.0.0 (core commit
`CORE_COMMIT`).

```python
import json
import minigraf

db = minigraf.open("bank.graph")
db.execute("(transact [[:alice :balance 100] [:bob :balance 50]])")

# The with block rolls back on exit unless the transaction was committed,
# also when an exception leaves it.
with db.begin_write() as tx:
    tx.execute("(transact [[:alice :balance 70] [:bob :balance 80]])")
    tx.execute("(retract [[:alice :balance 100] [:bob :balance 50]])")
    rows = json.loads(tx.execute("(query [:find ?b :where [:alice :balance ?b]])"))["results"]
    if rows[0][0] >= 0:
        tx.commit()
```

- `db.begin_write(synchronous=None, chunk_facts=None)`: an option left `None` is the
  database's. `synchronous` (`SyncMode.FULL` / `NORMAL`) is this commit's WAL sync;
  `chunk_facts` the facts staged before a file database writes the transaction in
  chunks (a negative count is API-017). Read-only databases raise API-014.
- `tx.execute(datalog)` returns what `db.execute` returns; a staged write returns
  `{"ok": true}`. A statement that fails stages nothing and leaves the transaction open.
  There are no cursors or prepared queries inside a transaction.
- `tx.commit()` applies everything as one transaction; a commit that fails rolls back.
  `tx.rollback()` discards it. Either way the transaction is finished: `execute` and
  `commit` then raise API-018, and `rollback` does nothing.
- Commit is always explicit: leaving the `with` block, or the transaction being garbage
  collected, rolls back. Python may collect it late, so end every transaction with
  `commit()`, `rollback()` or a `with` block.
- An open transaction holds the database's write lock. On the thread that began it,
  database calls (`execute`, `query`, `prepare`, `fact_log`, `checkpoint`, another
  `begin_write`, prepared queries) raise INT-001; so do user functions it runs. On other
  threads, writes and `begin_write` wait until it ends, and queries read the committed
  state. Use a transaction on the thread that began it: a thread that uses one it did
  not begin and then writes through `db` waits on that transaction.
- Rules registered inside a transaction stay registered after a rollback.
- The transaction keeps its database open until it is freed.

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
`[API-015]`. Its class follows the code's category: `MiniGrafError.Parse`, `.Query`,
`.Storage`, `.Api` (a bad argument, a closed object, a bind mistake) or `.Other`. See the [error reference](https://github.com/project-minigraf/minigraf/blob/main/docs/ERROR_REFERENCE.md).
`append_batch` stops at the first rejected record and ends the message with
`(batch index N)`; the records before it stay appended.

## Benchmarks

`bench/envelope.py` measures the FFI round trip for minigraf's performance envelope
(minigraf#394): it runs the core harness's query shapes through `db.query(...)` and
`next_batch(1000)` and prints wall-time p50/p99 and peak RSS next to the Rust numbers.
Results are published in minigraf's `docs/BENCHMARKS.md`.

```bash
# In minigraf: build the fixtures and the report.
cargo run --profile envelope --example perf_envelope -- --scales 1m --json envelope.json
# Here, with the binding installed (maturin develop --release):
python bench/envelope.py ../minigraf/target/perf-envelope/fixture-v1-1m.graph \
    --report ../minigraf/envelope.json --scale 1m
```

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
