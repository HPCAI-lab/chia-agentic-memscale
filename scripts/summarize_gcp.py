#!/usr/bin/env python3
"""Audit the four recorded GCP sessions and regenerate their publication tables."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = (
    "agent-gcp-1790140931374443334",
    "agent-gcp-1790141191264079437",
    "agent-gcp-1790141215995608561",
    "agent-gcp-1790141238772626079",
)
ENERGY = re.compile(r"Total SCF energy\s*=\s*([-+0-9.EeDd]+)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    summaries, experiments, files = [], [], []
    checked_cases = 0
    for number, name in enumerate(SESSIONS, 1):
        folder = ROOT / "results" / name
        read = lambda filename: json.loads((folder / filename).read_text())
        validation, state, provenance = map(read, ("validation.json", "state.json", "provenance.json"))
        require(validation["passed"] is True and validation["final_receipt_reported"] is True,
                f"{name}: incomplete session")
        require(validation["requested_experiments"] == validation["measured_agent_experiments"] == 4,
                f"{name}: wrong experiment count")
        require(state["backend"] == "native" and state["prior_history"] == [], "Unexpected history")
        require(state["history_inspected"] and len(state["attempts"]) == 4, "Invalid session state")
        for filename, digest in provenance["sha256"].items():
            require(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest,
                    f"Source differs from recorded run: {filename}")
        records = [state["baseline"], *state["attempts"]]
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        require([e["event"] for e in events] ==
                ["history_read"] + [x for _ in range(4) for x in ("experiment_selected", "experiment_finished")],
                f"{name}: unexpected event ordering")
        trace = (folder / "tool_trace.txt").read_text()
        require(trace.count("[Tool Call: nwchem_optimizer__run_memscale_experiment]") == 4,
                f"{name}: unexpected trace call count")
        seen = set()
        for step, record in enumerate(records):
            require(record["step"] == step and record["status"] == "success", "Invalid record")
            expected_source = "gemini" if step else "scripted_baseline"
            require(record["selection_source"] == expected_source, "Invalid selection source")
            config = record["configuration"]
            key = (config["executors"], config["batch_size"])
            require(key not in seen, "Repeated configuration")
            seen.add(key)
            if step:
                require(record["observed_receipt"] == records[step-1]["receipt"], "Broken receipt chain")
                require(events[2*step-1]["receipt"] == events[2*step]["receipt"] == record["receipt"],
                        "Event receipt mismatch")
                require(events[2*step-1]["configuration"] == config, "Event configuration mismatch")
            relative = ("baseline" if step == 0 else f"step-{step}") + "/measurement.json"
            measurement = read(relative)
            require(measurement["configuration"] == config, "Measurement configuration mismatch")
            require(measurement["aggregate"] == record["aggregate"], "Aggregate mismatch")
            experiments.append(dict(session=name, step=step, selection_source=expected_source,
                executors=key[0], batch_size=key[1],
                throughput_cases_per_s=record["aggregate"]["throughput_cases_per_s"],
                measurement_file=f"results/{name}/{relative}"))
        require(records[-1]["receipt"] in (folder / "agent_response.txt").read_text(), "Missing final receipt")
        # Check every raw result, including the warm-up, independently of saved passed flags.
        for measurement_file in sorted(folder.glob("*/measurement.json")):
            data = json.loads(measurement_file.read_text())
            require(data["aggregate"]["status"] == "success" and len(data["cases"]) == 3,
                    "Invalid scientific measurement")
            for case in data["cases"]:
                directory = measurement_file.parent / "cases" / Path(case["run_directory"]).name
                matches = ENERGY.findall((directory / "output.txt").read_text())
                require(bool(matches), "No raw SCF energy")
                energy = float(matches[-1].replace("D", "E").replace("d", "e"))
                require(math.isfinite(energy) and abs(energy + 75.983998) < 1e-5,
                        "Raw SCF energy outside tolerance")
                require(case["returncode"] == 0 and case["passed"] is True and
                        abs(energy - case["energy_hartree"]) < 1e-10, "Case validation mismatch")
                checked_cases += 1
        baseline = records[0]["aggregate"]["throughput_cases_per_s"]
        best = max(records, key=lambda r: r["aggregate"]["throughput_cases_per_s"])
        throughput = best["aggregate"]["throughput_cases_per_s"]
        summaries.append(dict(run=number, session=name, baseline_cases_per_s=baseline,
            best_executors=best["configuration"]["executors"], best_batch_size=best["configuration"]["batch_size"],
            best_cases_per_s=throughput, gain_pct=100*(throughput/baseline-1),
            session_elapsed_s=validation["session_elapsed_s"], passed=True))
        # Publish records, input decks and stdout, excluding regenerable NWChem scratch binaries.
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".csv", ".md", ".txt", ".log", ".patch", ".nw"}:
                files.append(path)
    output = ROOT / "results/gcp-summary"
    output.mkdir(exist_ok=True)
    for filename, rows in (("runs.csv", summaries), ("experiments.csv", experiments)):
        with (output / filename).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    (output / "summary.json").write_text(json.dumps(dict(runs=summaries,
        successful_agent_experiments=16, checked_raw_cases_including_warmups=checked_cases), indent=2)+"\n")
    checksums = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (output / "evidence-sha256.json").write_text(json.dumps(checksums, indent=2)+"\n")
    (output / "publication-files.txt").write_text("\n".join(checksums)+"\n")
    for row in summaries:
        print(f"Run {row['run']}: {row['baseline_cases_per_s']:.6f} -> {row['best_cases_per_s']:.6f} cases/s; +{row['gain_pct']:.1f}%")
    print(f"PASS: 4 sessions, 16 agent experiments, {checked_cases} raw cases; {len(files)} evidence files")


if __name__ == "__main__":
    main()
