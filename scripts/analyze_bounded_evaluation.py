#!/usr/bin/env python3
"""Audit new frozen studies; report empirical landscape, retrospective regret, or overhead."""
import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.analyze_policy_comparison import analyze_session, describe, number, write_csv
from scripts.run_bounded_evaluation import validate_plan, digest, measurement_directory, RUNTIME_FILES
from tools.experiment_session import validate_benchmark, write_json


def label(config):
    return f"E{config['executors']}/B{config['batch_size']}"


def percentile(values, q):
    values = sorted(values)
    index = (len(values) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def audit_study(directory):
    directory = Path(directory)
    plan = json.loads((directory / "plan.json").read_text())
    validate_plan(plan)
    state = json.loads((directory / "state.json").read_text())
    provenance = json.loads((directory / "provenance.json").read_text())
    errors, rows = [], []
    if state.get("plan_sha256") != plan["plan_sha256"]:
        errors.append("state/plan digest mismatch")
    if provenance.get("resolved_image_id", "") not in (plan["image_id"], "id:" + plan["image_id"]):
        errors.append("running image identity is unverified or mismatched")
    if not provenance.get("job_id") or not str(provenance.get("hostname", "")).startswith("nid"):
        errors.append("missing compute-node/job provenance")
    for name in RUNTIME_FILES:
        path = directory / "source-snapshot" / name
        if (not path.is_file() or digest(path) != plan["runtime_sha256"][name]
                or provenance.get("source_sha256", {}).get(name) != plan["runtime_sha256"][name]):
            errors.append("source hash mismatch: " + name)
    try:
        warm = validate_benchmark(directory / "warmup", plan["warmup_configuration"])
        if (state["warmup"]["aggregate"] != warm["aggregate"]
                or state["warmup"]["artifact_sha256"] != warm["artifact_sha256"]):
            raise ValueError("warmup record differs")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append("warmup audit: " + str(exc))
    attempts = state.get("attempts", [])
    planned_ids = [t["trial_id"] for t in plan["trials"]]
    if [r.get("trial_id") for r in attempts] != planned_ids[:len(attempts)]:
        errors.append("attempt order, duplicate ID, or unexpected attempt")
    if len(attempts) != len(plan["trials"]):
        errors.append("study incomplete")
    by_id = {r.get("trial_id"): r for r in attempts}
    for trial in plan["trials"]:
        record = by_id.get(trial["trial_id"], {})
        row = {"trial_id": trial["trial_id"], "repeat": trial["repeat"], "arm": trial["arm"],
               "configuration": label(trial["configuration"]), "status": record.get("status", "not_started"),
               "throughput_cases_per_s": None, "ray_roundtrip_s": record.get("ray_roundtrip_s"),
               "worker_elapsed_s": record.get("worker_elapsed_s"), "contract_components_s": None, "errors": ""}
        problems = []
        if record:
            if any(record.get(k) != v for k, v in trial.items()):
                problems.append("record differs from frozen trial")
            if record.get("worker_pid") == provenance.get("driver_pid") or type(record.get("worker_pid")) is not int:
                problems.append("worker identity is unverified")
        if row["status"] == "success":
            try:
                checked = validate_benchmark(measurement_directory(directory, trial), trial["configuration"])
                if record.get("aggregate") != checked["aggregate"] or record.get("artifact_sha256") != checked["artifact_sha256"]:
                    raise ValueError("saved result or artifact digest differs from raw evidence")
                row["throughput_cases_per_s"] = checked["aggregate"]["throughput_cases_per_s"]
                if (not number(row["worker_elapsed_s"], positive=True) or not number(row["ray_roundtrip_s"], positive=True)
                        or row["worker_elapsed_s"] > row["ray_roundtrip_s"] + .001):
                    raise ValueError("invalid worker/Ray timing boundary")
                if trial["arm"] == "contract":
                    contract_dir = directory / (trial["trial_id"] + "-contract")
                    contract_state = json.loads((contract_dir / "state.json").read_text())
                    if len(contract_state["attempts"]) != 1:
                        raise ValueError("Contract session attempt count differs")
                    attempt = contract_state["attempts"][0]
                    if (attempt["observed_receipt"] != contract_state["baseline"]["receipt"] or attempt["step"] != 1
                            or attempt["status"] != "success" or attempt["configuration"] != trial["configuration"]
                            or attempt["aggregate"] != checked["aggregate"] or attempt["artifact_sha256"] != checked["artifact_sha256"]):
                        raise ValueError("Contract state/receipt differs from frozen trial or measurement")
                    events = [json.loads(line) for line in (contract_dir / "events.jsonl").read_text().splitlines()]
                    finished = [e for e in events if e.get("event") == "experiment_finished"]
                    if len(finished) != 1 or {k: v for k, v in finished[0].items() if k not in ("event", "time_ns")} != attempt:
                        raise ValueError("Finished receipt event differs from contract state")
                    profiles = [e for e in events if e.get("event") == "contract_profile"]
                    if len(profiles) != 1 or profiles[0].get("step") != 1 or profiles[0].get("status") != "success":
                        raise ValueError("missing/duplicate contract profile")
                    values = profiles[0]["components_s"]
                    keys = {"admission_including_lock", "reservation_writes", "completion_writes", "trajectory_export", "validation", "hashing"}
                    if set(values) != keys or not all(number(v) for v in values.values()):
                        raise ValueError("invalid contract components")
                    if sum(values.values()) > row["worker_elapsed_s"] + .001:
                        raise ValueError("component timings exceed worker interval")
                    row["contract_components_s"] = sum(values.values())
                    row.update({k + "_s": v for k, v in values.items()})
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                problems.append(str(exc))
        else:
            problems.append("trial not successful")
        row["errors"] = "; ".join(problems)
        errors.extend(f"{trial['trial_id']}: {p}" for p in problems)
        rows.append(row)
    validation = json.loads((directory / "validation.json").read_text())
    if (validation != state.get("validation") or validation.get("passed") is not True
            or validation.get("planned_trials") != len(plan["trials"])
            or validation.get("attempted_trials") != len(attempts)
            or validation.get("successful_trials") != sum(r.get("status") == "success" for r in attempts)):
        errors.append("validation report is incomplete or inconsistent")
    return plan, rows, errors


def landscape(rows, repeats, bootstrap_samples=10000, seed=20261020):
    configs = sorted({r["configuration"] for r in rows})
    if len(configs) != 9:
        raise ValueError("Expected nine configurations")
    values = {(r["repeat"], r["configuration"]): r["throughput_cases_per_s"] for r in rows}
    if len(values) != len(rows) or len(rows) != repeats * 9 or any(r["errors"] or r["status"] != "success" for r in rows):
        raise ValueError("Complete balanced measurements are required for ranking")
    if any((repeat, c) not in values for c in configs for repeat in range(1, repeats + 1)):
        raise ValueError("Missing repeat/configuration cell")
    means = {c: statistics.mean(values[(i, c)] for i in range(1, repeats + 1)) for c in configs}
    draws, top = {c: [] for c in configs}, Counter()
    rng = random.Random(seed)
    for _ in range(bootstrap_samples):
        block = [rng.randrange(1, repeats + 1) for _ in range(repeats)]
        sample = {c: statistics.mean(values[(i, c)] for i in block) for c in configs}
        maximum = max(sample.values())
        winners = [c for c in configs if sample[c] == maximum]
        for c in configs:
            draws[c].append(sample[c])
        for c in winners:
            top[c] += 1 / len(winners)
    best = max(means.values())
    output = []
    for c in sorted(configs, key=lambda c: (-means[c], c)):
        output.append({"configuration": c, "rank": 1 + sum(v > means[c] for v in means.values()),
                       **describe(values[(i, c)] for i in range(1, repeats + 1)),
                       "empirical_regret_pct": 100 * (best - means[c]) / best,
                       "bootstrap_mean_low": percentile(draws[c], .025),
                       "bootstrap_mean_high": percentile(draws[c], .975),
                       "bootstrap_top_fraction": top[c] / bootstrap_samples})
    return output


def retrospective_regret(reference, results_root, jobs):
    means = {r["configuration"]: r["mean"] for r in reference}
    maximum = max(means.values())
    tops = {c for c, v in means.items() if v == maximum}
    rows = []
    for job in jobs:
        directories = sorted(Path(results_root).glob(f"comparison-*-{job}-*"))
        if not directories:
            raise ValueError(f"No sessions for source job {job}")
        for directory in directories:
            checked = analyze_session(directory)
            row = {"source_job": str(job), "session": directory.name, "policy": checked["policy"],
                   "pair_seed": checked["pair_seed"], "eligible": checked["analysis_eligible"],
                   "recommended_configuration": None, "reference_regret_cases_per_s": None,
                   "reference_regret_pct": None, "best_visited_reference_regret_pct": None,
                   "recommended_top": None, "visited_top_including_baseline": None,
                   "errors": checked["errors"]}
            if checked["analysis_eligible"]:
                recommended = f"E{checked['best_executors']}/B{checked['best_batch_size']}"
                state = json.loads((directory / "state.json").read_text())
                visited = {label(r["configuration"]) for r in [state["baseline"], *state["attempts"]] if r["status"] == "success"}
                row.update(recommended_configuration=recommended,
                           reference_regret_cases_per_s=maximum - means[recommended],
                           reference_regret_pct=100 * (maximum - means[recommended]) / maximum,
                           best_visited_reference_regret_pct=100 * (maximum - max(means[c] for c in visited)) / maximum,
                           recommended_top=recommended in tops, visited_top_including_baseline=bool(visited & tops))
            rows.append(row)
    return rows


def overhead(rows):
    pairs = []
    for repeat in sorted({r["repeat"] for r in rows}):
        group = [r for r in rows if r["repeat"] == repeat]
        if len(group) != 2 or {r["arm"] for r in group} != {"bare", "contract"} or any(r["errors"] for r in group):
            raise ValueError("Overhead requires complete paired trials")
        bare, contract = next(r for r in group if r["arm"] == "bare"), next(r for r in group if r["arm"] == "contract")
        pairs.append({"repeat": repeat, "bare_roundtrip_s": bare["ray_roundtrip_s"],
                      "contract_roundtrip_s": contract["ray_roundtrip_s"],
                      "roundtrip_difference_s": contract["ray_roundtrip_s"] - bare["ray_roundtrip_s"],
                      "roundtrip_difference_pct": 100 * (contract["ray_roundtrip_s"] / bare["ray_roundtrip_s"] - 1),
                      "instrumented_components_s": contract["contract_components_s"],
                      "components_pct_of_contract_worker": 100 * contract["contract_components_s"] / contract["worker_elapsed_s"]})
    return pairs


def export_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    write_csv(Path(path), fields, rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--results-root", type=Path, default=Path("results"))
    p.add_argument("--source-jobs", nargs="*", default=[])
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        plan, rows, errors = audit_study(args.session)
        export_csv(args.out_dir / "trials.csv", rows)
        report = {"plan_sha256": plan["plan_sha256"], "kind": plan["kind"], "audit_passed": not errors, "errors": errors}
        if not errors and plan["kind"] == "landscape":
            reference = landscape(rows, plan["repeats"])
            export_csv(args.out_dir / "landscape.csv", reference)
            report.update(configurations=reference, bootstrap_samples=10000, bootstrap_seed=20261020,
                          note="Empirical ranking in one allocation. Whole repeat blocks are resampled; bootstrap fractions are not probabilities of a globally true optimum. Five repeats give limited uncertainty resolution.")
            if args.source_jobs:
                regrets = retrospective_regret(reference, args.results_root, args.source_jobs)
                export_csv(args.out_dir / "regret.csv", regrets)
                report["regret_scope"] = "Retrospective mapping of historical selected configurations onto this new allocation's empirical reference; not historical ground-truth regret. Baseline retained as a candidate. Historical image/runtime conditions may differ."
                report["policy_blocks"] = [{"job": job, "policy": policy,
                    "eligible_sessions": len(group), "recommended_top_count": sum(r["recommended_top"] for r in group),
                    "visited_top_count": sum(r["visited_top_including_baseline"] for r in group),
                    "reference_regret_pct": describe(r["reference_regret_pct"] for r in group)}
                    for job in args.source_jobs for policy in ("gemini", "random")
                    for group in [[r for r in regrets if r["source_job"] == job and r["policy"] == policy and r["eligible"]]]]
                errors.extend("historical session: " + r["errors"] for r in regrets if not r["eligible"])
        elif not errors:
            pairs = overhead(rows)
            export_csv(args.out_dir / "paired.csv", pairs)
            report["paired_metrics"] = {key: describe(r[key] for r in pairs) for key in pairs[0] if key != "repeat"}
            report["components"] = {key: describe(r.get(key + "_s") for r in rows if r["arm"] == "contract") for key in (
                "admission_including_lock", "reservation_writes", "completion_writes", "trajectory_export", "validation", "hashing")}
            report["startup_source_verification_s"] = json.loads((args.session / "state.json").read_text()).get("startup_source_verification_s")
            report["note"] = "One warmed Ray actor, identical E2/B2 workload, alternating paired order. Bare includes harness scientific checking; contract additionally admits, revalidates raw results, hashes artifacts, writes state/receipts and exports trajectory. Session creation/history, startup source verification and common post-timing audit are excluded from per-call timing. Profile-journal write and lock release are included in roundtrip/worker totals, excluded from direct component sum. Pair differences include workload noise; negative values are retained. No overhead threshold assumed."
        report["audit_passed"] = not errors
        write_json(args.out_dir / "summary.json", report)
        print("PASS" if not errors else "AUDIT FAILED", args.out_dir)
        if errors:
            raise SystemExit(2)
    except (OSError, KeyError, ValueError, TypeError, RuntimeError) as exc:
        write_json(args.out_dir / "audit-failure.json", {"audit_passed": False, "error": str(exc)})
        p.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
