#!/usr/bin/env python3
"""Check installed Ray actor options and serialization without starting any workers."""
import json
import platform
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def main():
    import ray
    from ray import cloudpickle
    from scripts.run_bounded_evaluation import EvaluationWorker, build_plan
    if ray.is_initialized():
        raise SystemExit("Run this preflight in a fresh Python process")
    ray.remote(num_cpus=1, max_restarts=0, max_task_retries=0)(EvaluationWorker)
    plan = build_plan(PROJECT, "landscape", 5, 20261020,
                      (PROJECT / "provenance/nwchem-image-id.txt").read_text().strip())
    worker = EvaluationWorker(str(PROJECT), str(PROJECT / "results"), plan)
    restored = cloudpickle.loads(cloudpickle.dumps(worker))
    if restored.plan != plan or restored.project != PROJECT or ray.is_initialized():
        raise SystemExit("Ray serialization or no-worker preflight invariant failed")
    print("PASS: installed Ray actor options and serialization; no worker, workload, or API request launched")
    print(json.dumps({"python": platform.python_version(), "ray": ray.__version__, "planned_trials": len(plan["trials"])}))


if __name__ == "__main__":
    main()
