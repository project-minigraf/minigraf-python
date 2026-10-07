"""Cursors (#462), open options (#465), the fact log and the log writer (#467)."""

import json
import os

import pytest

import minigraf
from minigraf import (
    FactFilter,
    FactOrder,
    FactRecord,
    LogWriter,
    MiniGrafDb,
    MiniGrafError,
    OpenOptions,
    Value,
)

QUERY = "(query [:find ?e ?n :where [?e :n ?n]])"


def make_db(path, n=25):
    db = MiniGrafDb.open(path)
    for i in range(n):
        db.execute(f"(transact [[:e{i} :n {i}]])")
    return db


def code_of(excinfo):
    return excinfo.value.msg


# ─── Cursor ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("size", [1, 7, 1000])
def test_cursor_batches_match_execute(tmp_path, size):
    db = make_db(str(tmp_path / "c.graph"))
    expected = json.loads(db.execute(QUERY))["results"]
    cursor = db.query(QUERY)
    assert cursor.vars() == ["?e", "?n"]
    rows = []
    while (batch := cursor.next_batch(size)) is not None:
        decoded = json.loads(batch)
        assert 0 < len(decoded) <= size
        rows.extend(decoded)
    assert sorted(rows) == sorted(expected)
    assert cursor.next_batch(size) is None


def test_cursor_iterates_rows_and_closes(tmp_path):
    db = make_db(str(tmp_path / "c.graph"))
    with db.query(QUERY) as cursor:
        assert sorted(r[1] for r in cursor) == list(range(25))

    cursor = db.query(QUERY)
    assert cursor.next_batch(1) is not None
    cursor.close()
    assert cursor.next_batch(1) is None


def test_cursor_answer_is_fixed_at_open(tmp_path):
    db = make_db(str(tmp_path / "c.graph"), n=1)
    cursor = db.query(QUERY)
    db.execute("(transact [[:late :n 99]])")
    assert [r[1] for r in cursor] == [0]


def test_cursor_rejects_non_query():
    db = MiniGrafDb.open_in_memory()
    with pytest.raises(MiniGrafError) as e:
        db.query("(transact [[:a :n 1]])")
    assert code_of(e).startswith("[API-012]")


# ─── Open options ────────────────────────────────────────────────────────────


def test_read_only_open_queries_and_refuses_writes(tmp_path):
    path = str(tmp_path / "ro.graph")
    db = make_db(path, n=3)
    db.checkpoint()
    del db

    ro = minigraf.open(path, read_only=True, page_cache_size=16)
    rows = json.loads(ro.execute(QUERY))["results"]
    assert sorted(r[1] for r in rows) == [0, 1, 2]
    assert sorted(r[1] for r in ro.query(QUERY)) == [0, 1, 2]
    with pytest.raises(MiniGrafError) as e:
        ro.execute("(transact [[:x :n 9]])")
    assert code_of(e).startswith("[API-014]")
    with pytest.raises(MiniGrafError) as e:
        ro.checkpoint()
    assert code_of(e).startswith("[API-014]")


def test_read_only_missing_file(tmp_path):
    path = str(tmp_path / "missing.graph")
    with pytest.raises(MiniGrafError) as e:
        minigraf.open(path, read_only=True)
    assert code_of(e).startswith("[STG-042]")
    assert not os.path.exists(path)


def test_two_read_only_handles_share_a_file(tmp_path):
    path = str(tmp_path / "shared.graph")
    make_db(path, n=2).checkpoint()
    a = minigraf.open(path, read_only=True)
    b = MiniGrafDb.open_with_options(path, OpenOptions(read_only=True))
    assert a.current_tx_count() == b.current_tx_count() == 2
    with pytest.raises(MiniGrafError) as e:
        MiniGrafDb.open(path)
    assert code_of(e).startswith("[STG-02")


def test_open_with_default_options(tmp_path):
    db = MiniGrafDb.open_with_options(str(tmp_path / "d.graph"), OpenOptions())
    db.execute("(transact [[:a :n 1]])")
    assert db.current_tx_count() == 1


# ─── Fact log and log writer ─────────────────────────────────────────────────


def source_db(path):
    """Three transactions: an assertion with a window, a ref, a retraction."""
    db = MiniGrafDb.open(path)
    db.execute(
        '(transact {:valid-from "2024-01-01" :valid-to "2025-01-01"}'
        ' [[:alice :name "Alice"] [:alice :tag :t/admin]])'
    )
    db.execute(
        '(transact [[:bob :friend #uuid "00000000-0000-4000-8000-000000000001"]'
        ' [:bob :score 2.5]])'
    )
    db.execute('(retract [[:alice :name "Alice"]])')
    return db


def read_log(db, **filter_fields):
    with db.fact_log(FactFilter(**filter_fields)) as log:
        return list(log)


def test_fact_log_records(tmp_path):
    db = source_db(str(tmp_path / "s.graph"))
    records = read_log(db)
    assert [r.tx_count for r in records] == sorted(r.tx_count for r in records)
    assert len(records) == 5
    retraction = [r for r in records if not r.asserted]
    assert len(retraction) == 1 and retraction[0].value == Value.TEXT("Alice")
    friend = next(r for r in records if r.attribute == ":friend")
    assert friend.value == Value.REF("00000000-0000-4000-8000-000000000001")
    tag = next(r for r in records if r.attribute == ":tag")
    assert tag.value == Value.KEYWORD(":t/admin")
    assert tag.valid_to != minigraf.VALID_TIME_FOREVER
    score = next(r for r in records if r.attribute == ":score")
    assert score.valid_to == minigraf.VALID_TIME_FOREVER

    assert {r.attribute for r in read_log(db, attributes=[":name"])} == {":name"}
    assert {r.tx_count for r in read_log(db, tx_from=2, tx_to=2)} == {2}
    assert len(read_log(db, order=FactOrder.STORAGE)) == 5
    assert len(read_log(db, entities=[friend.entity])) == 2


def test_fact_log_rejects_bad_entity(tmp_path):
    db = source_db(str(tmp_path / "s.graph"))
    with pytest.raises(MiniGrafError) as e:
        db.fact_log(FactFilter(entities=["alice"]))
    assert code_of(e).startswith("[API-017]")


def test_log_writer_round_trip(tmp_path):
    src = source_db(str(tmp_path / "s.graph"))
    src.checkpoint()
    records = read_log(src)
    out = str(tmp_path / "out.graph")
    with LogWriter.create(out, OpenOptions()) as w:
        w.append(records[0])
        w.append_batch(records[1:])
        w.advance_tx_count(src.current_tx_count())
        assert w.tx_count() == 3
    assert not os.path.exists(out + ".partial")

    copy = minigraf.open(out, read_only=True)
    assert copy.current_tx_count() == src.current_tx_count()
    assert read_log(copy) == records
    q = '(query [:find ?n :any-valid-time :where [?e :name ?n]] )'
    assert copy.execute(q) == src.execute(q)


def test_log_writer_hole_from_skipped_transaction(tmp_path):
    src = source_db(str(tmp_path / "s.graph"))
    records = [r for r in read_log(src) if r.tx_count != 2]
    out = str(tmp_path / "hole.graph")
    with LogWriter.create(out, OpenOptions()) as w:
        w.append_batch(records)
        w.advance_tx_count(3)
    copy = minigraf.open(out, read_only=True)
    assert copy.current_tx_count() == 3
    assert {r.tx_count for r in read_log(copy)} == {1, 3}
    as_of_2 = json.loads(copy.execute("(query [:find ?a :as-of 2 :any-valid-time :where [?e ?a _]])"))
    as_of_1 = json.loads(copy.execute("(query [:find ?a :as-of 1 :any-valid-time :where [?e ?a _]])"))
    assert as_of_2 == as_of_1


def test_log_writer_errors(tmp_path):
    src = source_db(str(tmp_path / "s.graph"))
    records = read_log(src)
    out = str(tmp_path / "err.graph")
    w = LogWriter.create(out, OpenOptions())
    w.append(records[-1])
    with pytest.raises(MiniGrafError) as e:
        w.append(records[0])
    assert code_of(e).startswith("[API-015]")
    with pytest.raises(MiniGrafError) as e:
        w.append_batch([records[-1], records[0]])
    assert code_of(e).startswith("[API-015]")
    assert code_of(e).endswith("(batch index 1)")
    bad = FactRecord(
        entity="alice",
        attribute=":n",
        value=Value.INT64(1),
        tx_count=9,
        tx_id=1,
        valid_from=0,
        valid_to=minigraf.VALID_TIME_FOREVER,
        asserted=True,
    )
    with pytest.raises(MiniGrafError) as e:
        w.append(bad)
    assert code_of(e).startswith("[API-017]")
    w.finish()
    with pytest.raises(MiniGrafError) as e:
        w.append(records[-1])
    assert code_of(e).startswith("[API-018]")
    with pytest.raises(MiniGrafError) as e:
        w.finish()
    assert code_of(e).startswith("[API-018]")
    w.close()

    with pytest.raises(MiniGrafError) as e:
        LogWriter.create(out, OpenOptions())
    assert code_of(e).startswith("[STG-043]")
    with pytest.raises(MiniGrafError) as e:
        LogWriter.create(str(tmp_path / "ro.graph"), OpenOptions(read_only=True))
    assert code_of(e).startswith("[API-014]")


def test_log_writer_without_finish_leaves_no_file(tmp_path):
    src = source_db(str(tmp_path / "s.graph"))
    records = read_log(src)

    out = str(tmp_path / "closed.graph")
    w = LogWriter.create(out, OpenOptions())
    w.append_batch(records)
    w.close()
    assert not os.path.exists(out) and not os.path.exists(out + ".partial")

    out = str(tmp_path / "raised.graph")
    with pytest.raises(RuntimeError):
        with LogWriter.create(out, OpenOptions()) as w:
            w.append_batch(records)
            raise RuntimeError("abort the build")
    assert not os.path.exists(out) and not os.path.exists(out + ".partial")
