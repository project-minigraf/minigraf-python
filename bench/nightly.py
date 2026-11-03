"""#776 P16: real Python/UniFFI crossings; --test never reads a timer.

Fixture/prepare/registration and correctness checks are outside measurement.
Each sample averages 20 foreign API round trips, including JSON decode.
"""
import argparse
import json
import statistics
import time
from pathlib import Path

from minigraf import Accumulator, Aggregate, MiniGrafDb, Predicate, Value

N = 1000


def rows(raw):
    return json.loads(raw)["results"]


def summary(answer):
    return [len(answer), sum(row[0] for row in answer)]


class Even(Predicate):
    def test(self, value):
        return value.value % 2 == 0


class TotalAcc(Accumulator):
    def __init__(self):
        self.total = 0

    def step(self, value):
        self.total += value.value

    def finish(self, count):
        return Value.INT64(self.total)


class Total(Aggregate):
    def accumulator(self):
        return TotalAcc()


def cases():
    db = MiniGrafDb.open_in_memory()
    db.execute("(transact [" + " ".join(f"[:e{i} :n {i}]" for i in range(N)) + "])")
    db.execute('(transact [[:tags :tags #{1 "x"}]])')
    db.register_predicate("bench-even?", Even())
    db.register_aggregate("bench-total", Total())
    scan = db.prepare("(query [:find ?n :where [?e :n ?n] [(>= ?n $min)]])")
    point = db.prepare("(query [:find ?n :where [?e :n $n] [?e :n ?n]])")
    sets = db.prepare("(query [:find ?s :where [:tags :tags $s] [:tags :tags ?s]])")
    predicate = db.prepare("(query [:find ?n :where [?e :n ?n] [(bench-even? ?n)]])")
    aggregate = db.prepare("(query [:find (bench-total ?n) :where [?e :n ?n]])")

    def drain(batch_size):
        answer = []
        with scan.query(min=0) as cursor:
            while (batch := cursor.next_batch(batch_size)) is not None:
                answer.extend(json.loads(batch))
        return summary(answer)

    return [
        ("prepared_execute/1k", lambda: rows(point.execute(n=500)), [[500]]),
        ("cursor_batch_1/1k", lambda: drain(1), [N, N * (N - 1) // 2]),
        ("cursor_batch_256/1k", lambda: drain(256), [N, N * (N - 1) // 2]),
        ("set_roundtrip/2", lambda: rows(sets.execute(s={1, "x"})), [[["x", 1]]]),
        ("predicate/1k", lambda: summary(rows(predicate.execute())), [500, 249500]),
        ("aggregate/1k", lambda: rows(aggregate.execute()), [[499500]]),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--output", default="binding-results.json")
    args = parser.parse_args()
    metrics = {}
    for name, operation, expected in cases():
        name = "binding/python/" + name
        samples = []
        if not args.test:
            for _ in range(3):
                assert operation() == expected, f"incorrect result: {name}"
        for _ in range(1 if args.test else 10):
            if args.test:
                actual = operation()
            else:
                start = time.perf_counter_ns()
                for _ in range(20):
                    actual = operation()
                samples.append((time.perf_counter_ns() - start) / 20)
            assert actual == expected, f"incorrect result: {name}"
        print("ok: " + name, flush=True)
        if samples:
            metrics[name] = {"latency": {"value": statistics.median(samples)}}
    # Test mode writes no fake timing data.
    if not args.test:
        Path(args.output).write_text(json.dumps(metrics, indent=2) + "\n")


if __name__ == "__main__":
    main()
