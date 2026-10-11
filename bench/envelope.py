"""FFI round trip for the performance envelope (minigraf#394).

Runs the query shapes of the core harness (`examples/perf_envelope.rs` in
minigraf) through this binding: `db.query(...)` drained with `next_batch(1000)`,
each batch decoded with `json.loads`. Reports wall-time p50/p99 and peak RSS
next to the Rust numbers from the same report, so the difference is the cost
of the binding.

    cargo run --profile envelope --example perf_envelope -- --scales 1m --json envelope.json
    python bench/envelope.py target/perf-envelope/fixture-v1-1m.graph \\
        --report envelope.json --scale 1m

Each shape runs in its own process, so its peak RSS is its own. The fixture is
opened read-only.
"""

import argparse
import json
import math
import resource
import subprocess
import sys
import time

import minigraf

RULES = [
    "(rule [(boss ?x ?y) [?x :manager ?y]])",
    "(rule [(boss ?x ?z) [?x :manager ?y] (boss ?y ?z)])",
]


def drain(db, q):
    rows = 0
    with db.query(q) as cursor:
        while (batch := cursor.next_batch(1000)) is not None:
            rows += len(json.loads(batch))
    return rows


def percentile(sorted_samples, p):
    # Nearest rank, as in the core harness; with fewer than 100 runs p99 is the maximum.
    rank = max(1, min(len(sorted_samples), math.ceil(p * len(sorted_samples))))
    return sorted_samples[rank - 1]


def peak_rss_kb():
    # ru_maxrss is KiB on Linux, bytes on macOS.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss // 1024 if sys.platform == "darwin" else rss


def measure_one(fixture, query, budget_secs):
    db = minigraf.open(fixture, read_only=True)
    for rule in RULES:
        db.execute(rule)
    q = f"(query {query})"
    start = time.perf_counter()
    rows = drain(db, q)
    if time.perf_counter() - start < 1.0:
        drain(db, q)
        drain(db, q)
    samples = []
    start = time.perf_counter()
    while len(samples) < 200 and (len(samples) < 5 or time.perf_counter() - start < budget_secs):
        t = time.perf_counter()
        r = drain(db, q)
        samples.append((time.perf_counter() - t) * 1000.0)
        assert r == rows, "row count changed"
    samples.sort()
    return {
        "rows": rows,
        "runs": len(samples),
        "p50_ms": percentile(samples, 0.50),
        "p99_ms": percentile(samples, 0.99),
        "peak_rss_kb": peak_rss_kb(),
    }


def fmt_ms(v):
    if v < 1.0:
        return f"{v * 1000:.0f} µs"
    if v < 1000.0:
        return f"{v:.2f} ms"
    return f"{v / 1000:.2f} s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--report", required=True, help="JSON written by perf_envelope --json")
    ap.add_argument("--scale", required=True, help="scale label in the report, e.g. 1m")
    ap.add_argument("--budget-secs", type=float, default=15.0)
    ap.add_argument("--shapes", help="comma-separated shape names (default: all measured)")
    ap.add_argument("--one", help=argparse.SUPPRESS)
    args = ap.parse_args()

    with open(args.report) as f:
        report = json.load(f)
    scale = next(s for s in report["scales"] if s["label"] == args.scale)
    shapes = {q["name"]: q for q in scale["queries"]}

    if args.one:
        print(json.dumps(measure_one(args.fixture, shapes[args.one]["query"], args.budget_secs)))
        return

    names = args.shapes.split(",") if args.shapes else list(shapes)
    print(f"| Shape ({args.scale}) | Rust p50 | Python p50 / p99 | Rows | Rust peak RSS | Python peak RSS |")
    print("|---|---:|---:|---:|---:|---:|")
    for name in names:
        rust = shapes[name]
        if "p50_ms" not in rust:
            print(f"| `{name}` | capped | skipped | | | |")
            continue
        out = subprocess.run(
            [sys.executable, __file__, args.fixture, "--report", args.report,
             "--scale", args.scale, "--budget-secs", str(args.budget_secs), "--one", name],
            check=True, capture_output=True, text=True,
        )
        py = json.loads(out.stdout.strip().splitlines()[-1])
        assert py["rows"] == rust["rows"], f"{name}: row count differs from the Rust run"
        print(
            f"| `{name}` | {fmt_ms(rust['p50_ms'])} | {fmt_ms(py['p50_ms'])} / {fmt_ms(py['p99_ms'])}"
            f" | {py['rows']} | {rust['peak_rss_kb'] // 1024} MiB | {py['peak_rss_kb'] // 1024} MiB |",
            flush=True,
        )


if __name__ == "__main__":
    main()
