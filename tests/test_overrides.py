"""Override list, the transaction_chunk_facts and max_iterations open options
and the prepared limit setters (minigraf#872)."""

import json

import pytest

import minigraf
from minigraf import MiniGrafError, Overridable

REACH = "(query [:find ?x ?y :where (reach ?x ?y)])"
GROUPED = "(query [:find ?v (count ?e) :where [?e :v ?v]])"
DERIVED = "(query [:find ?x ?y :where (reach ?x ?y) :max-derived-facts 100])"
MEMORY = "(query [:find ?x ?y :where (reach ?x ?y) :query-memory-limit 1000000])"
NO_LIMIT = 2**63 - 1


def rows(result):
    return json.loads(result)["results"]


def load(db):
    db.execute(
        "(transact [[:a :edge :b] [:b :edge :c] [:c :edge :d] [:d :edge :e]"
        " [:p :v 1] [:q :v 1] [:r :v 2]])"
    )
    db.execute("(rule [(reach ?x ?y) [?x :edge ?y]])")
    db.execute("(rule [(reach ?x ?y) [?x :edge ?z] (reach ?z ?y)])")
    return db


def reach_db(tmp_path, name="db", **options):
    return load(minigraf.open(str(tmp_path / f"{name}.graph"), **options))


def code(excinfo):
    return excinfo.value.msg[1:8]


def test_defaults_are_read_from_core():
    assert minigraf.OVERRIDABLE_DEFAULTS == (
        Overridable.MAX_DERIVED_FACTS,
        Overridable.MAX_RESULTS,
        Overridable.MAX_ITERATIONS,
    )


@pytest.mark.parametrize(
    "allow, derived_ok, memory_ok",
    [
        (None, True, False),
        (minigraf.OVERRIDABLE_DEFAULTS, True, False),
        ([], False, False),
        ([Overridable.QUERY_MEMORY_LIMIT, Overridable.QUERY_MEMORY_LIMIT], False, True),
        (list(Overridable), True, True),
    ],
)
def test_allow_overrides_replaces_the_defaults(tmp_path, allow, derived_ok, memory_ok):
    db = reach_db(tmp_path, allow_overrides=allow)
    for q, ok in ((DERIVED, derived_ok), (MEMORY, memory_ok)):
        if ok:
            assert len(rows(db.execute(q))) == 10
            db.query(q).close()
            db.prepare(q)
        else:
            for call in (db.execute, db.query, db.prepare):
                with pytest.raises(MiniGrafError.Api) as e:
                    call(q)
                assert code(e) == "API-025"


def test_transaction_chunk_facts_commits_a_large_transaction(tmp_path):
    path = str(tmp_path / "chunk.graph")
    db = minigraf.open(path, transaction_chunk_facts=2)
    db.execute("(transact [" + "".join(f"[:e{i} :n {i}]" for i in range(10)) + "])")
    del db
    db = minigraf.open(path)
    assert len(rows(db.execute("(query [:find ?n :where [?e :n ?n]])"))) == 10
    with pytest.raises(MiniGrafError.Api) as e:
        minigraf.open(str(tmp_path / "bad.graph"), transaction_chunk_facts=-1)
    assert code(e) == "API-017"


def test_max_iterations_option(tmp_path):
    assert len(rows(reach_db(tmp_path, "default").execute(REACH))) == 10
    with pytest.raises(MiniGrafError) as e:
        reach_db(tmp_path, "one", max_iterations=1).execute(REACH)
    assert code(e) == "INT-020"
    assert len(rows(reach_db(tmp_path, "max", max_iterations=NO_LIMIT).execute(REACH))) == 10
    with pytest.raises(MiniGrafError.Api) as e:
        minigraf.open(str(tmp_path / "bad.graph"), max_iterations=-1)
    assert code(e) == "API-017"


def test_log_writer_takes_the_new_options(tmp_path):
    out = str(tmp_path / "out.graph")
    options = minigraf.OpenOptions(
        allow_overrides=[], transaction_chunk_facts=2, max_iterations=5
    )
    with minigraf.LogWriter.create(out, options):
        pass
    assert (tmp_path / "out.graph").exists()


@pytest.mark.parametrize(
    "setter, n, query, fails",
    [
        ("with_max_derived_facts", 1, REACH, "INT-020"),
        ("with_max_iterations", 1, REACH, "INT-020"),
        ("with_query_memory_limit", 16, GROUPED, "QRY-012"),
    ],
)
def test_setter_derives_a_new_query(tmp_path, setter, n, query, fails):
    # The setters are not gated: every override keyword is refused here.
    db = reach_db(tmp_path, allow_overrides=[])
    pq = db.prepare(query)
    before = rows(pq.execute())
    derived = getattr(pq, setter)(n)
    with pytest.raises(MiniGrafError) as e:
        derived.execute()
    assert code(e) == fails
    with pytest.raises(MiniGrafError) as e:
        # A cursor may open before it reaches the limit.
        list(derived.query())
    assert code(e) == fails
    assert rows(pq.execute()) == before
    assert len(rows(getattr(derived, setter)(NO_LIMIT).execute())) == len(before)
    with pytest.raises(MiniGrafError.Api) as e:
        getattr(pq, setter)(-1)
    assert code(e) == "API-017"


def test_chained_setters_keep_earlier_limits(tmp_path):
    pq = reach_db(tmp_path).prepare(REACH)
    chained = pq.with_max_derived_facts(1).with_max_iterations(1).with_max_iterations(NO_LIMIT)
    with pytest.raises(MiniGrafError) as e:
        chained.execute()
    assert code(e) == "INT-020"
    assert len(rows(chained.with_max_derived_facts(NO_LIMIT).execute())) == 10


def test_derived_query_keeps_its_database_open(tmp_path):
    db = reach_db(tmp_path)
    pq = db.prepare(REACH)
    with pq.query() as cursor:
        derived = pq.with_max_iterations(10)
        assert len(list(cursor)) == 10
    del pq, db
    assert len(rows(derived.execute())) == 10
