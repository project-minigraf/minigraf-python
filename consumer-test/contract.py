"""The consumer contract every binding checks (spec §4): in memory,
file-backed across a reopen, and MiniGrafError for invalid Datalog.

Usage: python contract.py <empty directory>
"""

import json
import os
import sys

from minigraf import MiniGrafDb, MiniGrafError, Value

QUERY = "(query [:find ?n :where [?e :name ?n]])"


def names(db):
    return [row[0] for row in json.loads(db.execute(QUERY))["results"]]


mem = MiniGrafDb.open_in_memory()
mem.execute('(transact [[:alice :name "Alice"]])')
assert names(mem) == ["Alice"], "query in memory returns Alice"
print("ok: in memory")

mem.execute('(transact [[:bob :name "Bob"]])')
prepared = mem.prepare("(query [:find ?n :where [?e :name $name] [?e :name ?n]])")
for name in ("Alice", "Bob"):
    found = json.loads(prepared.execute(name=name))["results"]
    assert found == [[name]], f"prepared query finds {name}"
typed = json.loads(prepared.execute(name=Value.TEXT("Alice")))["results"]
assert typed == [["Alice"]], "prepared query takes a typed value"
print("ok: prepared query runs with plain and typed bind values")

mem.execute('(transact [[:alice :tags #{1 "x"}]])')
tags = json.loads(mem.execute("(query [:find ?t :where [?e :tags ?t]])"))["results"]
assert tags == [["x", 1]], "a set value comes back as an array"
member = mem.prepare("(query [:find ?t :where [?e :tags ?t] [(in? 1 $s)]])")
assert json.loads(member.execute(s={1, 2}))["results"] == [["x", 1]], "a set binds"
print("ok: set values come back and bind")

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
