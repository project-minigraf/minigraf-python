"""The consumer contract every binding checks (spec §4): in memory,
file-backed across a reopen, and MiniGrafError for invalid Datalog.

Usage: python contract.py <empty directory>
"""

import json
import os
import sys

from minigraf import MiniGrafDb, MiniGrafError

QUERY = "(query [:find ?n :where [?e :name ?n]])"


def names(db):
    return [row[0] for row in json.loads(db.execute(QUERY))["results"]]


mem = MiniGrafDb.open_in_memory()
mem.execute('(transact [[:alice :name "Alice"]])')
assert names(mem) == ["Alice"], "query in memory returns Alice"
print("ok: in memory")

try:
    mem.execute("(query [:find")
except MiniGrafError:
    print("ok: invalid Datalog raises MiniGrafError")
else:
    sys.exit("invalid Datalog did not raise MiniGrafError")

path = os.path.join(sys.argv[1], "test.graph")
db = MiniGrafDb.open(path)
db.execute('(transact [[:bob :name "Bob"]])')
db.checkpoint()
del db  # releases the file lock; the binding has no close()
again = MiniGrafDb.open(path)
assert names(again) == ["Bob"], "query after reopen returns Bob"
print("ok: file-backed survives reopen")
