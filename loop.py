#!/usr/bin/env python3
"""One Gemini conversation selects 3–5 sequential, measured CHIA experiments."""

import argparse
import hashlib
import inspect
import json
import logging
import os
import platform
import socket
import subprocess
import time
import uuid
from pathlib import Path

from tools.experiment_session import (
    ExperimentSession, configuration, export_trajectory, run_benchmark, write_json,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", type=int, choices=(3, 4, 5), default=4)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--image", default="ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest")
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise SystemExit("Missing GEMINI_API_KEY; export it before sbatch submission.")
    if not os.environ.get("SLURM_JOB_ID") or not socket.gethostname().startswith("nid"):
        raise SystemExit("Run using scripts/run_agent_loop.sbatch on a Perlmutter compute node.")
    if int(os.environ.get("SLURM_NTASKS", "0")) < 8:
        raise SystemExit("Use the agent batch script: it reserves eight task slots.")

    import ray
    from chia.base.ChiaFunction import get
    from chia.models.openai_compat import OpenAICompatLLM
    from tools.memscale_tool import MemScaleTool

    if "model_dump" not in inspect.getsource(OpenAICompatLLM._run_openai_async):
        raise SystemExit("CHIA Gemini signature fix is missing; apply the recorded provenance patch.")

    directory = project / "results" / (
        f"agent-{os.environ['SLURM_JOB_ID']}-{time.time_ns()}"
    )
    directory.mkdir(parents=True)
    print("Session directory:", directory, flush=True)
    source_files = ("workload/nwchem_benchmark.py", "workload/inputs/water_check.nw",
                    "tools/experiment_session.py", "tools/memscale_tool.py", "loop.py")
    metadata = {"model": args.model, "image": args.image, "python": platform.python_version(),
                "job_id": os.environ["SLURM_JOB_ID"], "hostname": socket.gethostname(),
                "experiment_budget": args.experiments,
                "sha256": {name: hashlib.sha256((project / name).read_bytes()).hexdigest()
                           for name in source_files}}
    metadata["project_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    metadata["chia_commit"] = (project / "provenance/chia-commit.txt").read_text().strip()
    metadata["recorded_image_id"] = (project / "provenance/nwchem-image-id.txt").read_text().strip()
    write_json(directory / "provenance.json", metadata)

    tool = ref = session = None
    run_started = time.monotonic()
    try:
        print("Running one warm-up, then a fresh 3P / 1E / B1 baseline...", flush=True)
        baseline_config = configuration(1, 1)
        run_benchmark(project, directory / "warmup", args.image, baseline_config)
        baseline = {"step": 0, "selection_source": "scripted_baseline", "status": "success",
                    "configuration": baseline_config, "receipt": uuid.uuid4().hex,
                    **run_benchmark(project, directory / "baseline", args.image, baseline_config)}
        prior = []
        for job in ("58611234", "58611317", "58611406"):
            historical = json.loads((project / f"results/benchmark-{job}.json").read_text())
            # Different workload sizes must not enter this fixed-work comparison.
            if historical["configuration"]["partitions"] == 3:
                prior.append({"job_id": job, "selection_source": "prior_manual_run",
                              "configuration": historical["configuration"],
                              "aggregate": historical["aggregate"]})
        session = ExperimentSession.create(directory, project, args.image, args.experiments,
                                           baseline, prior)
        export_trajectory(directory, session.load())
        print("Baseline throughput:", baseline["aggregate"]["throughput_cases_per_s"], flush=True)

        ray.init(address="local", num_cpus=4, num_gpus=0, include_dashboard=False,
                 object_store_memory=256 * 1024 * 1024, resources={"openai_creds": 1.0},
                 runtime_env={"env_vars": {"PYTHONPATH": str(project) + os.pathsep
                                          + os.environ.get("PYTHONPATH", "")}})
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
Partitions=3, replication=1, MPI ranks per case=2, and CPU threads per rank=2 remain fixed.
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
        trace = str(getattr(result, "stream_result", "") or "").replace(key, "[REDACTED]")
        (directory / "tool_trace.txt").write_text(trace + "\n")
        # Store the final response, with credential redaction as a precaution.
        (directory / "agent_response.txt").write_text(answer.replace(key, "[REDACTED]") + "\n")
        state = session.load()
        attempts = state["attempts"]
        complete = (len(attempts) == args.experiments
                    and all(r["status"] == "success" for r in attempts)
                    and all(r["worker_pid"] != os.getpid() for r in attempts)
                    and attempts[-1]["receipt"] in answer)
        write_json(directory / "validation.json", {
            "passed": complete, "requested_experiments": args.experiments,
            "measured_agent_experiments": sum(r["status"] == "success" for r in attempts),
            "final_receipt_reported": bool(attempts and attempts[-1]["receipt"] in answer),
            "session_elapsed_s": round(time.monotonic() - run_started, 3),
            "note": "Tool enforces history inspection, unique selections, budget and receipt chaining."})
        if not complete:
            print("RAW CHIA TOOL TRACE:\n" + trace[-20000:], flush=True)
            raise RuntimeError("Agent trajectory incomplete; inspect validation.json and saved results")
        print((directory / "trajectory.md").read_text(), flush=True)
        print("PASS: Gemini selected sequential experiments through CHIA and received measurements.")
    except Exception as exc:
        message = str(exc).replace(key, "[REDACTED]")
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
            ray.shutdown()
        print("Results preserved in:", directory, flush=True)


if __name__ == "__main__":
    main()
