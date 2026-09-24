#!/usr/bin/env python3
"""Fresh-history equal-budget Gemini or seeded random search."""

import argparse
import hashlib
import inspect
import json
import logging
import os
import platform
import random
import socket
import sys
import subprocess
import time
import uuid
from pathlib import Path

from tools.experiment_session import (
    ExperimentSession, configuration, export_trajectory, run_benchmark, write_json,
)


class RandomWorker:
    """One Ray worker uses the same bounded session and scientific harness."""
    def __init__(self, directory):
        self.session = ExperimentSession(directory)

    def history(self):
        return self.session.history()

    def experiment(self, choice, receipt, seed):
        return self.session.experiment(choice["executors"], choice["batch_size"],
            f"Uniform random choice without replacement; seed={seed}", receipt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", type=int, choices=(3, 4, 5), default=4)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--image", default="ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest")
    parser.add_argument("--backend", choices=("perlmutter", "native"), default="perlmutter")
    parser.add_argument("--policy", choices=("gemini", "random"), required=True)
    parser.add_argument("--seed", type=int, default=101)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    key = os.environ.get("GEMINI_API_KEY", "")
    if args.policy == "gemini" and not key:
        raise SystemExit("Missing GEMINI_API_KEY; export it before sbatch submission.")
    if args.backend == "perlmutter":
        if not os.environ.get("SLURM_JOB_ID") or not socket.gethostname().startswith("nid"):
            raise SystemExit("Run using scripts/run_agent_loop.sbatch on a Perlmutter compute node.")
        if int(os.environ.get("SLURM_NTASKS", "0")) < 8:
            raise SystemExit("Use the agent batch script: it reserves eight task slots.")
    else:
        from workload.nwchem_benchmark import native_cpu_slots
        native_cpu_slots(3, 2)  # Validate capacity before starting an experiment.

    import ray
    from chia.base.ChiaFunction import get
    from chia.models.openai_compat import OpenAICompatLLM
    from tools.memscale_tool import MemScaleTool

    if "model_dump" not in inspect.getsource(OpenAICompatLLM._run_openai_async):
        raise SystemExit("CHIA Gemini signature fix is missing; apply the recorded provenance patch.")

    directory = project / "results" / (
        f"comparison-{args.policy}-{os.environ.get('SLURM_JOB_ID', 'gcp')}-{time.time_ns()}"
    )
    directory.mkdir(parents=True)
    print("Session directory:", directory, flush=True)
    source_files = ("workload/nwchem_benchmark.py", "workload/inputs/water_check.nw",
                    "tools/experiment_session.py", "tools/memscale_tool.py", "compare_loop.py")
    metadata = {"model": args.model, "image": args.image, "python": platform.python_version(),
                "backend": args.backend, "job_id": os.environ.get("SLURM_JOB_ID"), "hostname": socket.gethostname(),
                "experiment_budget": args.experiments,
                "sha256": {name: hashlib.sha256((project / name).read_bytes()).hexdigest()
                           for name in source_files}}
    metadata.update(comparison_pair_seed=args.seed, policy=args.policy, random_seed=args.seed if args.policy == "random" else None,
                    history_scope="fresh session only; identical eight non-baseline choices",
                    transport="CHIA MCP" if args.policy == "gemini" else "Ray actor, same ExperimentSession",
                    budget_note="four search attempts plus one warmup and one fresh baseline per policy")
    metadata["project_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    metadata["chia_commit"] = (project / "provenance/chia-commit.txt").read_text().strip()
    metadata["recorded_image_id"] = (project / "provenance/nwchem-image-id.txt").read_text().strip()
    if args.backend == "native":
        metadata["image"] = None
        metadata["recorded_image_id"] = None
        metadata["nwchem_executable"] = "/usr/bin/nwchem.openmpi"
        metadata["mpi_ranks_per_case"] = 2
        metadata["threads_per_rank"] = 1
        metadata["history_scope"] = "fresh native session only; no Perlmutter timings"
        metadata["packages"] = subprocess.check_output(
            ["dpkg-query", "-W", "nwchem-openmpi", "nwchem-data", "openmpi-bin"], text=True)
        (directory / "lscpu.txt").write_text(subprocess.check_output(["lscpu"], text=True))
        (directory / "python-packages.txt").write_text(subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze"], text=True))
        (directory / "source.patch").write_text(subprocess.check_output(
            ["git", "diff", "HEAD", "--", *source_files], cwd=project, text=True))
    (directory / "source-snapshot").mkdir()
    for name in source_files:
        target = directory / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / name).read_bytes())
    write_json(directory / "provenance.json", metadata)

    tool = ref = session = random_worker = None
    run_started = time.monotonic()
    try:
        print("Running one warm-up, then a fresh 3P / 1E / B1 baseline...", flush=True)
        baseline_config = configuration(1, 1)
        run_benchmark(project, directory / "warmup", args.image, baseline_config, backend=args.backend)
        baseline = {"step": 0, "selection_source": "scripted_baseline", "status": "success",
                    "configuration": baseline_config, "receipt": uuid.uuid4().hex,
                    **run_benchmark(project, directory / "baseline", args.image, baseline_config, backend=args.backend)}
        prior = []
        session = ExperimentSession.create(directory, project, args.image, args.experiments,
                                           baseline, prior, backend=args.backend, policy=args.policy)
        export_trajectory(directory, session.load())
        print("Baseline throughput:", baseline["aggregate"]["throughput_cases_per_s"], flush=True)

        ray.init(address="local", num_cpus=4, num_gpus=0, include_dashboard=False,
                 object_store_memory=256 * 1024 * 1024, resources={"openai_creds": 1.0},
                 runtime_env={"env_vars": {"PYTHONPATH": str(project) + os.pathsep
                                          + os.environ.get("PYTHONPATH", "")}})
        if args.policy == "random":
            random_worker = ray.remote(num_cpus=1)(RandomWorker).remote(str(directory))
            rng = random.Random(args.seed)
            history = ray.get(random_worker.history.remote(), timeout=180)
            for _ in range(args.experiments):
                choice = rng.choice(history["untested_configurations"])
                record = ray.get(random_worker.experiment.remote(
                    choice, history["latest_receipt"], args.seed), timeout=180)
                if record["status"] == "rejected":
                    raise RuntimeError("Random policy request rejected")
                history = ray.get(random_worker.history.remote(), timeout=180)
            answer = "Random search completed; final receipt: " + history["latest_receipt"]
            trace = "Uniform random selection without replacement; measurements in state.json"
            (directory / "agent_response.txt").write_text(answer + "\n")
            (directory / "tool_trace.txt").write_text(trace + "\n")
        else:
            tool = MemScaleTool(name="nwchem_optimizer", session_directory=str(directory),
                                task_options={"num_cpus": 1}, logging_level=logging.WARNING)
            llm = OpenAICompatLLM(
                model=args.model, base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=key, timeout_seconds=60, retries=1, max_tokens=2048,
                max_tool_iterations=2 * args.experiments + 6,
                client_kwargs={"max_retries": 0}, logging_level=logging.WARNING)
            prompt = f"""Optimize throughput of exactly three NWChem water SCF calculations.
    First call get_experiment_history. Historical manual results are context, not your selections.
    Choose and run exactly {args.experiments} UNTESTED configurations using run_memscale_experiment.
    Select executors and batch_size yourself from the allowed choices in the history tool.
    Partitions=3, replication=1, MPI ranks per case=2, and numerical threads per rank=1 remain fixed.
    Give a brief experimental hypothesis for each choice, citing the observed results.
    Supply observed_receipt from the latest result/history. Make ONE experiment call at a time.
    Wait for its measurements, compare them, and THEN choose the next configuration.
    Do not plan or submit several configurations together. Do not repeat manual or current configs.
    Failures consume budget. Request history if you need to recover after a rejected request.
    Aim to improve throughput; lower throughput is valid evidence, not a reason to invent a result.
    After {args.experiments} attempts, summarize the measured trajectory and the best observed
    configuration versus the freshly measured baseline. Include the exact final receipt string.
    Memory is unmeasured. Do not claim memory optimization, global optimality, or a statistically
    established speedup. No shell access or credentials are needed in your response.
    """
            (directory / "prompt.txt").write_text(prompt)
            print(f"Gemini is choosing up to {args.experiments} experiments through CHIA...", flush=True)
            ref = llm.prompt.options(max_retries=0).chia_remote(llm, prompt, tools=[tool])
            result = get(ref, timeout=960)
            ref = None  # Completed successfully; no cancellation is needed during cleanup.
            answer = str(result.result)
            trace = str(getattr(result, "stream_result", "") or "").replace(key or "__NO_CREDENTIAL_PRESENT__", "[REDACTED]")
            (directory / "tool_trace.txt").write_text(trace + "\n")
            # Store the final response, with credential redaction as a precaution.
            (directory / "agent_response.txt").write_text(answer.replace(key or "__NO_CREDENTIAL_PRESENT__", "[REDACTED]") + "\n")
        state = session.load()
        attempts = state["attempts"]
        complete = (len(attempts) == args.experiments
                    and all(r["status"] == "success" for r in attempts)
                    and all(r["worker_pid"] != os.getpid() for r in attempts)
                    and attempts[-1]["receipt"] in answer)
        write_json(directory / "validation.json", {
            "policy": args.policy, "passed": complete, "requested_experiments": args.experiments,
            "measured_agent_experiments": sum(r["status"] == "success" for r in attempts),
            "final_receipt_reported": bool(attempts and attempts[-1]["receipt"] in answer),
            "session_elapsed_s": round(time.monotonic() - run_started, 3),
            "search_launcher_elapsed_s": sum(r.get("launcher_elapsed_s", 0) for r in attempts),
            "timing_note": "Session includes warmup, baseline, startup, decisions and tools; not pure API latency.",
            "note": "Tool enforces history inspection, unique selections, budget and receipt chaining."})
        if not complete:
            print("RAW CHIA TOOL TRACE:\n" + trace[-20000:], flush=True)
            raise RuntimeError("Agent trajectory incomplete; inspect validation.json and saved results")
        print((directory / "trajectory.md").read_text(), flush=True)
        print(f"PASS: {args.policy} completed four sequential validated experiments.")
    except Exception as exc:
        message = str(exc).replace(key or "__NO_CREDENTIAL_PRESENT__", "[REDACTED]")
        write_json(directory / "failure.json", {"error_type": type(exc).__name__, "message": message})
        print("FAIL:", type(exc).__name__, message, flush=True)
        raise SystemExit(1)
    finally:
        if session is not None:
            export_trajectory(directory, session.load())
        try:
            if ref is not None:
                ray.cancel(ref, force=True)
            if tool is not None:
                tool.stop()
        finally:
            if random_worker is not None:
                ray.kill(random_worker)
            ray.shutdown()
        print("Results preserved in:", directory, flush=True)


if __name__ == "__main__":
    main()
