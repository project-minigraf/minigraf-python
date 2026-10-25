"""User functions (#180): predicates and aggregates written in Python."""

import json
import threading

import pytest

from minigraf import (
    Accumulator,
    Aggregate,
    MiniGrafDb,
    MiniGrafError,
    Predicate,
    UdfError,
    Value,
)


@pytest.fixture
def db():
    db = MiniGrafDb.open_in_memory()
    db.execute(
        "(transact [[:a :n 1] [:b :n 2] [:c :n 3] [:d :n 4]"
        " [:a :g 1] [:b :g 1] [:c :g 2] [:d :g 2]])"
    )
    return db


def rows(result):
    return sorted(json.loads(result)["results"])


class IsEven(Predicate):
    def test(self, value):
        if not isinstance(value, Value.INT64):
            raise UdfError.Failed("not an integer")
        return value.value % 2 == 0


class Total(Aggregate):
    """Sums integers; step raises on ``fail_on``."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on

    def accumulator(self):
        return TotalAcc(self.fail_on)


class TotalAcc(Accumulator):
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.sum = 0

    def step(self, value):
        if value.value == self.fail_on:
            raise UdfError.Failed(f"refused {value.value}")
        self.sum += value.value

    def finish(self, count):
        return Value.INT64(self.sum)


def test_predicate_filters_rows(db):
    db.register_predicate("even?", IsEven())
    assert rows(db.execute("(query [:find ?n :where [?e :n ?n] [(even? ?n)]])")) == [[2], [4]]


def test_aggregate_in_find_and_over(db):
    db.register_aggregate("total", Total())
    assert rows(db.execute("(query [:find (total ?n) :where [?e :n ?n]])")) == [[10]]
    over = db.execute(
        "(query [:find ?n (total ?n :over (:partition-by ?g :order-by ?n))"
        " :where [?e :n ?n] [?e :g ?g]])"
    )
    # A running total per group: 1, 1+2 in group 1; 3, 3+4 in group 2.
    assert rows(over) == [[1, 1], [2, 3], [3, 3], [4, 7]]


def test_udf_error_fails_the_query_with_qry_014(db):
    db.register_predicate("even?", IsEven())
    db.execute('(transact [[:e :n "x"]])')
    with pytest.raises(MiniGrafError.Query) as e:
        db.execute("(query [:find ?n :where [?e :n ?n] [(even? ?n)]])")
    assert e.value.msg.startswith("[QRY-014]")
    assert "not an integer" in e.value.msg


def test_any_exception_fails_the_query_with_qry_014(db):
    class Broken(Predicate):
        def test(self, value):
            raise ValueError("own exception")

    db.register_predicate("broken?", Broken())
    with pytest.raises(MiniGrafError.Query) as e:
        db.execute("(query [:find ?n :where [?e :n ?n] [(broken? ?n)]])")
    assert e.value.msg.startswith("[QRY-014]")
    assert "own exception" in e.value.msg


def test_aggregate_step_error_is_qry_014(db):
    db.register_aggregate("total", Total(fail_on=2))
    with pytest.raises(MiniGrafError.Query) as e:
        db.execute("(query [:find (total ?n) :where [?e :n ?n]])")
    assert e.value.msg.startswith("[QRY-014]")
    assert "refused 2" in e.value.msg


def test_bad_ref_from_finish_is_qry_014(db):
    class BadRef(Aggregate):
        def accumulator(self):
            return BadRefAcc()

    class BadRefAcc(Accumulator):
        def step(self, value):
            pass

        def finish(self, count):
            return Value.REF("nope")

    db.register_aggregate("bad", BadRef())
    with pytest.raises(MiniGrafError.Query) as e:
        db.execute("(query [:find (bad ?n) :where [?e :n ?n]])")
    assert e.value.msg.startswith("[QRY-014]")


def test_predicate_may_query_the_same_database(db):
    class InGroup2(Predicate):
        def test(self, value):
            found = db.execute(
                f"(query [:find ?e :where [?e :n {value.value}] [?e :g 2]])"
            )
            return len(json.loads(found)["results"]) > 0

    db.register_predicate("g2?", InGroup2())
    assert rows(db.execute("(query [:find ?n :where [?e :n ?n] [(g2? ?n)]])")) == [[3], [4]]


def test_a_name_registers_once(db):
    db.register_predicate("even?", IsEven())
    with pytest.raises(MiniGrafError) as e:
        db.register_predicate("even?", IsEven())
    assert e.value.msg.startswith("[INT-040]")


def test_two_threads_share_one_predicate(db):
    calls = []

    class Counting(Predicate):
        def test(self, value):
            calls.append(value.value)  # list.append is atomic under the GIL
            return value.value % 2 == 0

    db.register_predicate("even?", Counting())
    results, errors = [], []

    def run():
        try:
            for _ in range(50):
                results.append(
                    rows(db.execute("(query [:find ?n :where [?e :n ?n] [(even? ?n)]])"))
                )
        except Exception as e:  # surfaced below; a thread's exception is lost otherwise
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results == [[[2], [4]]] * 100
    assert len(calls) == 400
