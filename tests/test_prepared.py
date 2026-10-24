"""Prepared queries (#181): prepare once, execute with different bindings."""

import json
import uuid

import pytest

import minigraf
from minigraf import MiniGrafDb, MiniGrafError, Value

A = str(uuid.uuid5(uuid.NAMESPACE_OID, ":a"))


@pytest.fixture
def db():
    db = MiniGrafDb.open_in_memory()
    db.execute(
        '(transact [[:a :n 1] [:b :n 2] [:c :n 3]'
        ' [:a :s "x"] [:a :f 2.5] [:a :w 2.0] [:a :t true] [:a :k :kw] [:a :z nil]])'
    )
    return db


def rows(result):
    return json.loads(result)["results"]


def test_prepared_query_runs_again_with_new_bindings(db):
    pq = db.prepare("(query [:find ?x :where [?e :n $n] [?e :n ?x]])")
    for n in (1, 2, 3):
        assert rows(pq.execute(n=n)) == [[n]]
        assert rows(pq.execute({"n": n})) == [[n]]
    assert rows(pq.execute(n=9)) == []


@pytest.mark.parametrize(
    "attr, value",
    [
        (":n", 1),
        (":s", "x"),
        (":f", 2.5),
        (":t", True),
        (":z", None),
        (":w", 2.0),
    ],
)
def test_every_plain_value_binds(db, attr, value):
    pq = db.prepare(f"(query [:find ?e :where [?e {attr} $v]])")
    assert len(rows(pq.execute(v=value))) == 1


def test_bool_is_not_an_integer(db):
    # True is an int subclass in Python; it must bind a boolean, not 1.
    pq = db.prepare("(query [:find ?e :where [?e :n $v]])")
    assert rows(pq.execute(v=True)) == []


def test_uuid_binds_an_entity_anywhere(db):
    entity_slot = db.prepare("(query [:find ?x :where [$e :n ?x]])")
    assert rows(entity_slot.execute(e=uuid.UUID(A))) == [[1]]

    db.execute(f'(transact [[:b :friend #uuid "{A}"]])')
    db.execute("(rule [(friend-of ?x ?f) [?x :friend ?f]])")
    value_slot = db.prepare("(query [:find ?x :where [?x :friend $f]])")
    rule_arg = db.prepare("(query [:find ?x :where (friend-of ?x $f)])")
    for pq in (value_slot, rule_arg):
        assert len(rows(pq.execute(f=uuid.UUID(A)))) == 1


@pytest.mark.parametrize(
    "query, value",
    [
        ("(query [:find ?e :where [?e :k $v]])", Value.KEYWORD(":kw")),
        ("(query [:find ?x :where [$v :n ?x]])", Value.REF(A)),
        ("(query [:find ?x :where [$v :n ?x]])", minigraf.Entity(A)),
        ("(query [:find ?x :as-of $v :where [?e :n ?x]])", minigraf.TxCount(1)),
        ("(query [:find ?x :as-of $v :where [?e :n ?x]])", minigraf.Timestamp(2**62)),
        ("(query [:find ?x :valid-at $v :where [:a :n ?x]])", minigraf.ANY_VALID_TIME),
    ],
)
def test_every_typed_value_binds(db, query, value):
    pq = db.prepare(query)
    assert rows(pq.execute(v=value))


def test_slot_named_bindings_works_as_a_keyword(db):
    pq = db.prepare("(query [:find ?e :where [?e :n $bindings]])")
    assert len(rows(pq.execute(bindings=1))) == 1


def test_prepared_cursor_matches_execute(db):
    pq = db.prepare("(query [:find ?e ?n :where [?e :n ?n] [(>= ?n $min)]])")
    expected = rows(pq.execute(min=2))
    with pq.query(min=2) as cursor:
        assert cursor.vars() == ["?e", "?n"]
        assert sorted(cursor) == sorted(expected)
    assert len(expected) == 2


def test_unsupported_plain_value_is_a_type_error(db):
    pq = db.prepare("(query [:find ?e :where [?e :n $n]])")
    with pytest.raises(TypeError):
        pq.execute(n=[1])
    with pytest.raises(TypeError):
        pq.execute(n=b"bytes")


def code(excinfo):
    return excinfo.value.msg[:9]


def test_bind_mistakes_are_api_errors(db):
    pq = db.prepare("(query [:find ?e :where [?e :n $n]])")
    with pytest.raises(MiniGrafError.Api) as e:
        pq.execute()
    assert code(e) == "[API-022]"
    with pytest.raises(MiniGrafError.Api) as e:
        pq.query()
    assert code(e) == "[API-022]"
    with pytest.raises(MiniGrafError.Api) as e:
        pq.execute(n=minigraf.TxCount(1))
    assert code(e) == "[API-024]"
    with pytest.raises(MiniGrafError.Api) as e:
        db.prepare("(query [:find ?v :where [?e $a ?v]])")
    assert code(e) == "[API-023]"
    with pytest.raises(MiniGrafError.Api) as e:
        pq.execute(n=minigraf.TxCount(-1))
    assert code(e) == "[API-017]"
    with pytest.raises(MiniGrafError.Api) as e:
        pq.execute(n=2**63)
    assert code(e) == "[API-017]"
    with pytest.raises(MiniGrafError.Api) as e:
        pq.execute(n=minigraf.Entity("not-a-uuid"))
    assert code(e) == "[API-017]"


def test_prepared_query_keeps_its_database_alive():
    db = MiniGrafDb.open_in_memory()
    db.execute("(transact [[:a :n 1]])")
    pq = db.prepare("(query [:find ?e :where [?e :n $n]])")
    del db
    assert len(rows(pq.execute(n=1))) == 1


def test_prepared_query_sees_later_writes(db):
    pq = db.prepare("(query [:find ?e :where [?e :n $n]])")
    assert rows(pq.execute(n=4)) == []
    db.execute("(transact [[:d :n 4]])")
    assert len(rows(pq.execute(n=4))) == 1


# ─── Sets (#316) ─────────────────────────────────────────────────────────────


def test_python_sets_bind_set_values(db):
    member = db.prepare("(query [:find ?e :where [?e :n ?n] [(in? ?n $s)]])")
    assert len(rows(member.execute(s={1, 3}))) == 2
    assert len(rows(member.execute(s=frozenset([2])))) == 1
    assert rows(member.execute(s=set())) == []
    typed = Value.SET([Value.INT64(1), Value.INT64(1)])
    assert len(rows(member.execute(s=typed))) == 1


def test_set_values_come_back_typed_and_as_arrays(db):
    db.execute('(transact [[:a :tags #{1 :k "x"}]])')
    equal = db.prepare("(query [:find ?x :where [?e :tags $s] [?e :n ?x]])")
    tags = Value.SET([Value.INT64(1), Value.TEXT("x"), Value.KEYWORD(":k")])
    assert rows(equal.execute(s=tags)) == [[1]]
    # A string is no keyword.
    assert rows(equal.execute(s={1, "x", ":k"})) == []
    out = db.prepare("(query [:find ?t :where [:a :tags ?t]])")
    assert rows(out.execute()) == [["x", 1, ":k"]]
    records = [r for r in db.fact_log(minigraf.FactFilter(attributes=[":tags"]))]
    assert records[0].value == Value.SET(
        [Value.TEXT("x"), Value.INT64(1), Value.KEYWORD(":k")]
    )


def test_set_element_forms(db):
    pq = db.prepare("(query [:find ?x :where [$e :n ?x] [(in? $e $s)]])")
    # A UUID element is a ref.
    hit = {uuid.UUID(A)}
    assert rows(pq.execute(e=minigraf.Entity(A), s=hit)) == [[1]]
    with pytest.raises(MiniGrafError.Api, match=r"^\[API-021\]"):
        pq.execute(e=minigraf.Entity(A), s={frozenset([1])})
    with pytest.raises(MiniGrafError.Api, match=r"^\[API-021\]"):
        pq.execute(e=minigraf.Entity(A), s=Value.SET([Value.SET([])]))
    with pytest.raises(TypeError):
        pq.execute(e=minigraf.Entity(A), s={(1, 2)})
    # A list is not a set.
    with pytest.raises(TypeError):
        pq.execute(e=minigraf.Entity(A), s=[1])
