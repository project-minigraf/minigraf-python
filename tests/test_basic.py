import json
import os
import pytest
import minigraf
from minigraf import MiniGrafDb, MiniGrafError


def test_open_in_memory():
    db = MiniGrafDb.open_in_memory()
    assert db is not None


def test_transact_and_query():
    db = MiniGrafDb.open_in_memory()
    result = json.loads(db.execute('(transact [[:alice :name "Alice"]])'))
    assert "transacted" in result

    result = json.loads(db.execute("(query [:find ?n :where [?e :name ?n]])"))
    assert result["variables"] == ["?n"]
    assert result["results"][0][0] == "Alice"


def test_invalid_datalog_raises():
    db = MiniGrafDb.open_in_memory()
    with pytest.raises(MiniGrafError):
        db.execute("not valid datalog !!!")


def test_file_backed_roundtrip(tmp_path):
    path = str(tmp_path / "test.graph")

    db = MiniGrafDb.open(path)
    db.execute('(transact [[:bob :name "Bob"]])')
    db.checkpoint()
    del db

    db2 = MiniGrafDb.open(path)
    result = json.loads(db2.execute("(query [:find ?n :where [?e :name ?n]])"))
    assert result["results"][0][0] == "Bob"


def test_query_memory_limit_raises_qry_012(tmp_path):
    """#549: a query that would hold more than query_memory_limit bytes fails."""
    product = "(query [:find ?a ?b :where [?a :n _] [?b :m _]])"

    db = minigraf.open(str(tmp_path / "small.graph"), query_memory_limit=64)
    db.execute("(transact [[:a :n 1] [:b :m 2]])")
    with pytest.raises(MiniGrafError.Query) as e:
        db.execute(product)
    assert e.value.msg.startswith("[QRY-012]")

    db = minigraf.open(str(tmp_path / "max.graph"), query_memory_limit=2**63 - 1)
    db.execute("(transact [[:a :n 1] [:b :m 2]])")
    assert len(json.loads(db.execute(product))["results"]) == 1
