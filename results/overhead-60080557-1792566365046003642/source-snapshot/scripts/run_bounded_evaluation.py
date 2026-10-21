#!/usr/bin/env python3
"""Freeze and execute a nine-configuration landscape or paired contract overhead study."""
import argparse
import json
import os
import platform
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.run_policy_confirmation import digest, plan_digest, require_compute_node, verify_image, IMAGE_ID
from scripts.analyze_policy_comparison import valid_config
from tools.experiment_session import ExperimentSession, configuration, launch_benchmark, run_benchmark, validate_benchmark, write_json

RUNTIME_FILES = (
    "tools/experiment_session.py", "workload/nwchem_benchmark.py", "workload/inputs/water_check.nw",
    "scripts/run_bounded_evaluation.py", "scripts/run_bounded_evaluation.sbatch",
    "scripts/run_policy_confirmation.py", "scripts/analyze_policy_comparison.py", "tools/policy_costs.py",
)
CONFIGS = [configuration(e, b) for e in (1, 2, 3) for b in (1, 2, 3)]


def schedule(kind, repeats, seed):
    rng, rows = random.Random(seed), []
    # Overhead alternates the starting arm, with the initial arm randomized.
    first = rng.choice(("bare", "contract"))
    for repeat in range(1, repeats + 1):
        if kind == "landscape":
            block = [{"configuration": dict(c), "arm": "validated"} for c in CONFIGS]
            rng.shuffle(block)
        elif kind == "overhead":
            order = [first, "contract" if first == "bare" else "bare"]
            if repeat % 2 == 0:
                order.reverse()
            block = [{"configuration": configuration(2, 2), "arm": arm} for arm in order]
        else:
            raise ValueError("Unknown study kind")
        for row in block:
            rows.append({"trial_id": f"trial-{len(rows)+1:04d}", "repeat": repeat, **row})
    return rows


def build_plan(project, kind, repeats, seed, image_id):
    if type(repeats) is not int or not 2 <= repeats <= 20 or type(seed) is not int:
        raise ValueError("Require 2–20 repeats and an integer seed")
    plan = {"schema_version": 1, "kind": kind, "repeats": repeats, "seed": seed,
            "image_id": image_id, "backend": "perlmutter", "trial_timeout_s": 120,
            "warmup_configuration": configuration(1, 1),
            "runtime_sha256": {name: digest(Path(project) / name) for name in RUNTIME_FILES},
            "trials": schedule(kind, repeats, seed)}
    plan["plan_sha256"] = plan_digest(plan)
    validate_plan(plan)
    return plan


def validate_plan(plan):
    if (plan.get("schema_version") != 1 or plan.get("backend") != "perlmutter"
            or plan.get("plan_sha256") != plan_digest(plan)):
        raise ValueError("Invalid plan or changed digest")
    if (type(plan.get("repeats")) is not int or not 2 <= plan["repeats"] <= 20
            or type(plan.get("seed")) is not int or not IMAGE_ID.fullmatch(plan.get("image_id", ""))
            or plan.get("trial_timeout_s") != 120):
        raise ValueError("Require 2–20 repeats, integer seed, full image ID and 120s trial timeout")
    if plan.get("warmup_configuration") != configuration(1, 1):
        raise ValueError("Warmup differs")
    if plan.get("trials") != schedule(plan["kind"], plan["repeats"], plan["seed"]):
        raise ValueError("Schedule differs from declared protocol")
    if any(not valid_config(t["configuration"]) or type(t["repeat"]) is not int for t in plan["trials"]):
        raise ValueError("Invalid typed configuration or repeat index")
    if set(plan.get("runtime_sha256", {})) != set(RUNTIME_FILES):
        raise ValueError("Incomplete source provenance")


def verify_inputs(plan, project):
    validate_plan(plan)
    for name, expected in plan["runtime_sha256"].items():
        if digest(Path(project) / name) != expected:
            raise ValueError(f"Runtime changed after freezing plan: {name}")


class EvaluationWorker:
    """One warmed Ray actor; same CPU allocation and workload in both overhead arms."""
    def __init__(self, project, directory, plan):
        self.project, self.directory, self.plan = Path(project), Path(directory), plan
        self.baseline = None

    def warmup(self):
        self.baseline = {"step": 0, "selection_source": "scripted_baseline", "status": "success",
                         "configuration": configuration(1, 1), "receipt": "overhead-warmup",
                         **run_benchmark(self.project, self.directory / "warmup", "id:" + self.plan["image_id"], configuration(1, 1))}
        return self.baseline

    def prepare(self, trial):
        if trial["arm"] == "contract":
            directory = self.directory / (trial["trial_id"] + "-contract")
            directory.mkdir()
            session = ExperimentSession.create(directory, self.project, "id:" + self.plan["image_id"],
                                               1, self.baseline, [], policy="random")
            session.history()
        return os.getpid()

    def execute(self, trial):
        started = time.perf_counter()
        target = self.directory / trial["trial_id"]
        config = trial["configuration"]
        if trial["arm"] == "contract":
            session = ExperimentSession(self.directory / (trial["trial_id"] + "-contract"))
            result = session.experiment(config["executors"], config["batch_size"],
                                        "Frozen contract overhead probe", self.baseline["receipt"])
            if result["status"] != "success":
                raise RuntimeError("Contract probe failed; saved in contract session")
        elif trial["arm"] == "bare":
            result = launch_benchmark(self.project, target, "id:" + self.plan["image_id"], config)
        else:
            result = run_benchmark(self.project, target, "id:" + self.plan["image_id"], config)
        return {**result, "worker_pid": os.getpid(), "worker_elapsed_s": time.perf_counter() - started}


def measurement_directory(session, trial):
    if trial["arm"] == "contract":
        return Path(session) / (trial["trial_id"] + "-contract") / "step-1"
    return Path(session) / trial["trial_id"]


def run_study(plan, results_root, project=PROJECT):
    require_compute_node()
    phase = time.perf_counter()
    verify_inputs(plan, project)
    source_verification_s = time.perf_counter() - phase
    import ray
    directory = Path(results_root).resolve() / f"{plan['kind']}-{os.environ['SLURM_JOB_ID']}-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "plan.json", plan)
    state = {"plan_sha256": plan["plan_sha256"], "attempts": [], "warmup": None,
             "startup_source_verification_s": source_verification_s}
    provenance = {"job_id": os.environ["SLURM_JOB_ID"], "hostname": socket.gethostname(),
                  "python": platform.python_version(), "ray_version": ray.__version__,
                  "driver_pid": os.getpid(), "resolved_image_id": None,
                  "source_sha256": plan["runtime_sha256"],
                  "environment": {k: os.environ.get(k) for k in (
                      "SLURM_NTASKS", "SLURM_CPUS_PER_TASK", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                      "COMEX_MAX_NB_OUTSTANDING", "FI_CXI_RX_MATCH_MODE", "COMEX_EAGER_THRESHOLD", "FI_CXI_RDZV_THRESHOLD",
                      "FI_CXI_OFLOW_BUF_COUNT", "MPICH_SMP_SINGLE_COPY_MODE")}}
    for name in RUNTIME_FILES:
        target = directory / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((Path(project) / name).read_bytes())
    provenance["project_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    write_json(directory / "provenance.json", provenance)
    write_json(directory / "state.json", state)
    actor = None
    started = time.monotonic()
    old_handlers = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, interrupted)
    print("Session:", directory, flush=True)
    try:
        phase = time.perf_counter()
        provenance["resolved_image_id"] = verify_image(plan["image_id"])
        provenance["image_verification_s"] = time.perf_counter() - phase
        write_json(directory / "provenance.json", provenance)
        phase = time.perf_counter()
        ray.init(address="local", num_cpus=4, num_gpus=0, include_dashboard=False,
                 object_store_memory=256 * 1024 * 1024,
                 runtime_env={"env_vars": {"PYTHONPATH": str(project) + os.pathsep + os.environ.get("PYTHONPATH", "")}})
        actor = ray.remote(num_cpus=1, max_restarts=0, max_task_retries=0)(EvaluationWorker).remote(str(project), str(directory), plan)
        state["warmup"] = ray.get(actor.warmup.remote(), timeout=180)
        state["startup_and_warmup_s"] = time.perf_counter() - phase
        write_json(directory / "state.json", state)
        for trial in plan["trials"]:
            record = {**trial, "status": "running", "ray_roundtrip_s": None}
            state["attempts"].append(record)
            write_json(directory / "state.json", state)
            try:
                phase = time.perf_counter()
                ray.get(actor.prepare.remote(trial), timeout=30)
                record["prepare_s"] = time.perf_counter() - phase
                phase = time.perf_counter()
                result = ray.get(actor.execute.remote(trial), timeout=150)
                record["ray_roundtrip_s"] = time.perf_counter() - phase
                record.update(result)
                if result["worker_pid"] == os.getpid():
                    raise RuntimeError("Workload did not execute in a Ray worker")
                # Identical post-timing audit for both arms. Bare retains the
                # harness check but excludes outer runtime verification/writes.
                checked = validate_benchmark(measurement_directory(directory, trial), trial["configuration"])
                if trial["arm"] != "bare" and any(result.get(k) != checked[k] for k in ("aggregate", "artifact_sha256")):
                    raise RuntimeError("Worker result changed before post-timing audit")
                record.update(aggregate=checked["aggregate"], artifact_sha256=checked["artifact_sha256"], status="success")
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__)
                # A timed-out/failed Ray task might still run; stop this study.
                raise
            except BaseException as exc:
                record.update(status="interrupted", error_type=type(exc).__name__)
                raise
            finally:
                write_json(directory / "state.json", state)
            print(record["trial_id"], record["configuration"], record["arm"], record["status"], flush=True)
    except BaseException as exc:
        state["fatal_error_type"] = type(exc).__name__
    finally:
        try:
            if actor is not None:
                ray.kill(actor)
            ray.shutdown()
        finally:
            passed = ("fatal_error_type" not in state and len(state["attempts"]) == len(plan["trials"])
                      and all(r["status"] == "success" for r in state["attempts"]))
            state["validation"] = {"passed": passed, "planned_trials": len(plan["trials"]),
                                   "attempted_trials": len(state["attempts"]),
                                   "successful_trials": sum(r["status"] == "success" for r in state["attempts"]),
                                   "session_including_cleanup_s": time.monotonic() - started}
            write_json(directory / "state.json", state)
            write_json(directory / "validation.json", state["validation"])
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
    print("PASS" if passed else "INCOMPLETE/FAILED", directory, flush=True)
    return directory, passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("plan")
    create.add_argument("--kind", choices=("landscape", "overhead"), required=True)
    create.add_argument("--repeats", type=int)
    create.add_argument("--seed", type=int, default=20261020)
    create.add_argument("--image-id")
    create.add_argument("--out", type=Path, required=True)
    for name in ("check", "submit", "run"):
        p = sub.add_parser(name)
        p.add_argument("--plan", type=Path, required=True)
        p.add_argument("--results-root", type=Path, default=Path("results"))
    args = parser.parse_args()
    try:
        if args.command == "plan":
            repeats = args.repeats if args.repeats is not None else (5 if args.kind == "landscape" else 10)
            image_id = args.image_id or (PROJECT / "provenance/nwchem-image-id.txt").read_text().strip()
            plan = build_plan(PROJECT, args.kind, repeats, args.seed, image_id)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x") as stream:
                json.dump(plan, stream, indent=2, allow_nan=False)
                stream.write("\n")
            print(f"Frozen {args.kind}: {len(plan['trials'])} measured trials + one warmup")
            print("SHA256:", plan["plan_sha256"])
        else:
            plan = json.loads(args.plan.read_text())
            verify_inputs(plan, PROJECT)
            if args.command == "check":
                print("PASS: frozen plan and current sources match")
            elif args.command == "submit":
                args.results_root.mkdir(parents=True, exist_ok=True)
                root = args.results_root.resolve()
                subprocess.run(["sbatch", "--image=id:" + plan["image_id"],
                                "--output=" + str(root / (plan["kind"] + "-%j.log")),
                                "--error=" + str(root / (plan["kind"] + "-%j.err")),
                                str(PROJECT / "scripts/run_bounded_evaluation.sbatch"),
                                str(args.plan.resolve()), str(root)], cwd=PROJECT, check=True)
            else:
                _, passed = run_study(plan, args.results_root)
                if not passed:
                    raise SystemExit(1)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
