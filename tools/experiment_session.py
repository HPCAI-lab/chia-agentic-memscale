"""Bounded, auditable experiments; no model or CHIA dependency in this module."""

import csv
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def configuration(executors, batch_size):
    return dict(partitions=3, executors=executors, replication=1,
                batch_size=batch_size, placement="single-node")


def config_key(config):
    return tuple(config[name] for name in
                 ("partitions", "executors", "replication", "batch_size"))


def run_benchmark(project, directory, image, config, timeout_s=120):
    """Run the real harness, keeping raw output and terminating timed-out steps."""
    directory = Path(directory)
    directory.mkdir()
    output = directory / "measurement.json"
    command = [sys.executable, str(Path(project) / "workload/nwchem_benchmark.py"),
               "--input", str(Path(project) / "workload/inputs/water_check.nw"),
               "--image", image, "--partitions", str(config["partitions"]),
               "--executors", str(config["executors"]),
               "--replication", str(config["replication"]),
               "--batch-size", str(config["batch_size"]),
               "--placement", "single-node", "--mpi-tasks", "2",
               "--cpus-per-task", "2", "--output", str(output)]
    started = time.monotonic()
    with (directory / "launcher.log").open("w") as stream:
        child = subprocess.Popen(command, cwd=project, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            returncode = child.wait(timeout=timeout_s)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            except ProcessLookupError:
                child.wait()
            raise
    if returncode != 0:
        raise RuntimeError(f"Benchmark exit {returncode}; inspect {directory / 'launcher.log'}")
    data = json.loads(output.read_text())
    metrics = data["aggregate"]
    cases = data["cases"]
    if (data["configuration"] != config or metrics["status"] != "success"
            or metrics["cases_total"] != 3 or metrics["cases_completed"] != 3
            or len(cases) != 3 or {c["case_id"] for c in cases} != {"p0-r0", "p1-r0", "p2-r0"}):
        raise RuntimeError("Benchmark did not validate exactly the requested three cases")
    for case in cases:
        energy = case.get("energy_hartree")
        if (not case["passed"] or case["returncode"] != 0
                or not isinstance(energy, (int, float)) or not math.isfinite(energy)
                or abs(energy - (-75.983998)) >= 1e-5):
            raise RuntimeError("Scientific energy validation failed")
    for name in ("throughput_cases_per_s", "experiment_elapsed_s", "latency_mean_ms"):
        value = metrics[name]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"Invalid measured {name}")
    return {"aggregate": metrics, "measurement_file": str(output),
            "launcher_elapsed_s": round(time.monotonic() - started, 3)}


class ExperimentSession:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.state_path = self.directory / "state.json"

    @classmethod
    def create(cls, directory, project, image, budget, baseline, prior):
        session = cls(directory)
        state = {"project": str(Path(project).resolve()), "image": image,
                 "budget": budget, "baseline": baseline, "prior_history": prior,
                 "attempts": [], "history_inspected": False}
        write_json(session.state_path, state)
        return session

    @contextmanager
    def locked(self, timeout_s=180):
        # Exclusive directory creation avoids the unsupported flock operation.
        # Never steal an existing lock.
        lock = self.directory / "session.lockdir"
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                lock.mkdir(mode=0o700)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Session lock is occupied: {lock}")
                time.sleep(0.05)
        try:
            yield
        finally:
            lock.rmdir()

    def load(self):
        return json.loads(self.state_path.read_text())

    def event(self, kind, **fields):
        with (self.directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"event": kind, "time_ns": time.time_ns(),
                                     **fields}, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def last_record(state):
        return state["attempts"][-1] if state["attempts"] else state["baseline"]

    def history(self):
        with self.locked():
            state = self.load()
            state["history_inspected"] = True
            write_json(self.state_path, state)
            self.event("history_read", completed_attempts=len(state["attempts"]))
            used = {config_key(row["configuration"]) for row in
                    [state["baseline"], *state["prior_history"], *state["attempts"]]}
            return {"objective": "maximize throughput for exactly three water calculations",
                    "remaining_budget": state["budget"] - len(state["attempts"]),
                    "latest_receipt": self.last_record(state)["receipt"],
                    "baseline": state["baseline"], "prior_manual_history": state["prior_history"],
                    "agent_attempts": state["attempts"],
                    "untested_configurations": [configuration(e, b) for e in (1, 2, 3)
                                                for b in (1, 2, 3)
                                                if config_key(configuration(e, b)) not in used],
                    "measurement_note": "End-to-end elapsed includes launch overhead; memory is unavailable."}

    def experiment(self, executors, batch_size, reason, observed_receipt):
        # The lock serializes launches even if the model requests parallel calls.
        with self.locked():
            state = self.load()
            def reject(message):
                self.event("rejected", message=message)
                return {"status": "rejected", "message": message}

            if type(executors) is not int or executors not in (1, 2, 3):
                return reject("executors must be 1, 2, or 3")
            if type(batch_size) is not int or batch_size not in (1, 2, 3):
                return reject("batch_size must be 1, 2, or 3")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                return reject("Provide a short experimental rationale (1–2000 characters)")
            if not state["history_inspected"]:
                return reject("Call get_experiment_history before choosing an experiment")
            if len(state["attempts"]) >= state["budget"]:
                return reject("Experiment budget exhausted; summarize the measurements")
            if observed_receipt != self.last_record(state)["receipt"]:
                return reject("Read the latest result/history and pass its exact latest receipt")
            config = configuration(executors, batch_size)
            previous = [state["baseline"], *state["prior_history"], *state["attempts"]]
            if config_key(config) in {config_key(r["configuration"]) for r in previous}:
                return reject("Already tested; choose an untested configuration")
            step = len(state["attempts"]) + 1
            record = {"step": step, "selection_source": "gemini",
                      "configuration": config, "reason": reason,
                      "observed_receipt": observed_receipt, "receipt": uuid.uuid4().hex,
                      "worker_pid": os.getpid(), "hostname": socket.gethostname(),
                      "status": "running"}
            state["attempts"].append(record)
            # Reserve the attempt before execution; failures also consume the budget.
            write_json(self.state_path, state)
            self.event("experiment_selected", **record)
            try:
                measurement = run_benchmark(state["project"], self.directory / f"step-{step}",
                                            state["image"], config)
                record.update(measurement, status="success")
            except Exception as exc:
                # Avoid copying environment/credential values into an agent-visible error.
                record.update(status="failed", error_type=type(exc).__name__,
                              diagnostic_directory=str(self.directory / f"step-{step}"))
            write_json(self.state_path, state)
            self.event("experiment_finished", **record)
            export_trajectory(self.directory, state)
            return record


def export_trajectory(directory, state):
    records = [state["baseline"], *state["attempts"]]
    rows = []
    baseline_throughput = state["baseline"]["aggregate"]["throughput_cases_per_s"]
    best = baseline_throughput
    for record in records:
        metrics = record.get("aggregate", {})
        throughput = metrics.get("throughput_cases_per_s")
        if record["status"] == "success" and throughput is not None:
            best = max(best, throughput)
        rows.append({"step": record["step"], "selection_source": record["selection_source"],
                     **record["configuration"], "status": record["status"],
                     "throughput_cases_per_s": throughput,
                     "experiment_elapsed_s": metrics.get("experiment_elapsed_s"),
                     "latency_mean_ms": metrics.get("latency_mean_ms"),
                     "memory_peak_mb": metrics.get("memory_peak_mb"),
                     "best_so_far_cases_per_s": best,
                     "best_gain_vs_fresh_baseline_pct": 100 * (best / baseline_throughput - 1),
                     "reason": record.get("reason", ""), "receipt": record["receipt"],
                     "observed_receipt": record.get("observed_receipt", ""),
                     "measurement_file": record.get("measurement_file", "")})
    with (Path(directory) / "trajectory.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    successful = [r for r in records if r["status"] == "success"]
    winner = max(successful, key=lambda r: r["aggregate"]["throughput_cases_per_s"])
    write_json(Path(directory) / "best_config.json", {
        "interpretation": "Best observed in this session; one measurement per configuration",
        "baseline": state["baseline"], "best": winner,
        "best_gain_vs_fresh_baseline_pct": 100 * (best / baseline_throughput - 1),
        "agent_attempts": len(state["attempts"]),
        "agent_successes": sum(r["status"] == "success" for r in state["attempts"]),
    })
    lines = ["# Measured agent trajectory", "", "Step 0 is a freshly measured scripted baseline.",
             "Steps 1+ are Gemini selections. Historical manual runs are context only.", "",
             "| Step | Selected by | Configuration | Status | Throughput (cases/s) |",
             "| --- | --- | --- | --- | --- |"]
    for row in rows:
        value = row["throughput_cases_per_s"]
        displayed = "unavailable" if value is None else f"{value:.6f}"
        lines.append(f"| {row['step']} | {row['selection_source']} | "
                     f"3P / {row['executors']}E / B{row['batch_size']} | "
                     f"{row['status']} | {displayed} |")
    lines.extend(["", "This small water-SCF demonstration measures orchestration throughput.",
                  "Memory use, data-placement policies, 1H9T scaling, and global optimality are unmeasured.",
                  "Differences include launch overhead and run-to-run variation; repeated trials are needed",
                  "before making a robust performance claim.", ""])
    (Path(directory) / "trajectory.md").write_text("\n".join(lines))
