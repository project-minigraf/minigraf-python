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
