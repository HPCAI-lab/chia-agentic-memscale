#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ENERGY = re.compile(r"Total SCF energy\s*=\s*([-+0-9.EeDd]+)")
RSS = re.compile(r"MAXRSS_KB=(\d+)")
REFERENCE = -75.983998
TOLERANCE = 1e-5


def run_case(case_id, partition, replica, args, scratch):
    directory = Path(tempfile.mkdtemp(
        prefix=f"nwchem-{case_id}-",
        dir=scratch,
    ))
    shutil.copy2(args.input, directory / args.input.name)

    command = [
        "srun", "--exclusive", "--exact", "--nodes=1",
        "--ntasks", str(args.mpi_tasks),
        "--cpus-per-task", str(args.cpus_per_task),
        "--cpu-bind=cores",
        "--kill-on-bad-exit=1",
        "shifter", "--image", args.image, "--module=mpich",
        "nwchem", args.input.name,
    ]

    start = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
    )
    latency_ms = (time.perf_counter() - start) * 1000

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    (directory / "output.txt").write_text(output)

    energies = ENERGY.findall(output)
    energy = None
    if energies:
        energy = float(energies[-1].replace("D", "E").replace("d", "e"))

    rss_values = [int(x) for x in RSS.findall(output)]
    memory_mb = max(rss_values) / 1024 if rss_values else None

    passed = (
        result.returncode == 0
        and energy is not None
        and math.isfinite(energy)
        and abs(energy - REFERENCE) < TOLERANCE
    )

    return {
        "case_id": case_id,
        "partition": partition,
        "replica": replica,
        "returncode": result.returncode,
        "latency_ms": round(latency_ms, 3),
        "energy_hartree": energy,
        "memory_mb": round(memory_mb, 3) if memory_mb else None,
        "passed": passed,
        "run_directory": str(directory),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--partitions", type=int, required=True)
    parser.add_argument("--executors", type=int, required=True)
    parser.add_argument("--replication", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--placement", default="single-node")
    parser.add_argument("--mpi-tasks", type=int, default=2)
    parser.add_argument("--cpus-per-task", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.input = args.input.resolve()
    if not args.input.exists():
        parser.error(f"Missing input: {args.input}")

    scratch = Path(os.environ.get("SCRATCH", tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)

    cases = [
        (f"p{p}-r{r}", p, r)
        for p in range(args.partitions)
        for r in range(args.replication)
    ]

    start = time.perf_counter()
    results = []

    for offset in range(0, len(cases), args.batch_size):
        wave = cases[offset:offset + args.batch_size]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.executors, len(wave))
        ) as pool:
            futures = [
                pool.submit(run_case, case_id, p, r, args, scratch)
                for case_id, p, r in wave
            ]
            results.extend(f.result() for f in futures)

    elapsed = time.perf_counter() - start
    successful = [x for x in results if x["passed"]]
    latencies = [x["latency_ms"] for x in successful]
    memories = [
        x["memory_mb"] for x in successful
        if x["memory_mb"] is not None
    ]

    record = {
        "workload": "nwchem_water_6-31g_scf",
        "configuration": {
            "partitions": args.partitions,
            "executors": args.executors,
            "replication": args.replication,
            "batch_size": args.batch_size,
            "placement": args.placement,
        },
        "aggregate": {
            "status": "success"
            if len(successful) == len(results)
            else "failed",
            "cases_completed": len(successful),
            "cases_total": len(results),
            "experiment_elapsed_s": round(elapsed, 3),
            "latency_mean_ms": round(sum(latencies) / len(latencies), 3)
            if latencies else None,
            "throughput_cases_per_s": round(len(successful) / elapsed, 6)
            if elapsed else None,
            "memory_peak_mb": round(max(memories), 3)
            if memories else None,
        },
        "scientific_validation": {
            "reference_energy_hartree": REFERENCE,
            "tolerance_hartree": TOLERANCE,
        },
        "cases": results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))

    if record["aggregate"]["status"] != "success":
        raise SystemExit("At least one NWChem case failed")


if __name__ == "__main__":
    main()
