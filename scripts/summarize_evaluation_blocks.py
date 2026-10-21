#!/usr/bin/env python3
"""Audit and summarize policy comparisons by job; plot fixed-selection confirmation."""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.analyze_policy_comparison import analyze_session, pair_sessions, describe, SESSION_FIELDS, PAIR_FIELDS, write_csv
from scripts.run_policy_confirmation import validate_plan, digest
from tools.experiment_session import validate_benchmark, write_json


def blocked_comparison(results_root, jobs):
    sessions = []
    for job in jobs:
        directories = sorted(Path(results_root).glob(f"comparison-*-{job}-*"))
        if not directories:
            raise ValueError(f"No sessions for job {job}")
        sessions.extend(analyze_session(d) for d in directories)
    pairs = pair_sessions(sessions)
    errors = [r["session"] + ": " + r["errors"] for r in sessions if not r["analysis_eligible"]]
    errors.extend(p["reason"] for p in pairs if not p["eligible"])
    blocks = []
    for job in jobs:
        selected = [p for p in pairs if p["block"] == str(job) and p["eligible"]]
        rows = [r for r in sessions if str(r["job_id"]) == str(job) and r["analysis_eligible"]]
        blocks.append({"job_id": str(job), "eligible_pairs": len(selected),
                       "gemini_wins": sum(p["best_throughput_winner"] == "gemini" for p in selected),
                       "random_wins": sum(p["best_throughput_winner"] == "random" for p in selected),
                       "ties": sum(p["best_throughput_winner"] == "tie" for p in selected),
                       "paired_relative_difference_pct": describe(p["best_relative_difference_pct"] for p in selected),
                       "policies": {policy: {key: describe(r[key] for r in rows if r["policy"] == policy)
                           for key in ("best_cases_per_s", "session_elapsed_s", "api_request_elapsed_s", "api_tokens")}
                           for policy in ("gemini", "random")}})
    report = {"audit_passed": not errors, "errors": errors, "blocks": blocks,
              "descriptive_total": {key: sum(b[key] for b in blocks) for key in ("eligible_pairs", "gemini_wins", "random_wins", "ties")},
              "notes": ["Job is the block. Raw timings/throughput and API cost observations are not pooled across jobs with changed instrumentation or environment.",
                        "Repeated seeds reuse random configuration sequences across jobs; six matched pairs are not six independent allocation replicates. There are two job blocks in the published pilot.",
                        "Wins compare measured best throughput, including baseline, with exact numeric ties. Small differences are not significance claims.",
                        "The incomplete validation job is retained as a separate failure, not a completed comparison pair."]}
    return sessions, pairs, report


def confirmation_rows(session):
    session = Path(session)
    plan = json.loads((session / "plan.json").read_text())
    validate_plan(plan)
    state = json.loads((session / "state.json").read_text())
    provenance = json.loads((session / "provenance.json").read_text())
    if state.get("plan_sha256") != plan["plan_sha256"]:
        raise ValueError("Confirmation plan/state digest mismatch")
    if provenance.get("resolved_image_id") not in (plan["image_id"], "id:" + plan["image_id"]):
        raise ValueError("Confirmation image identity mismatch")
    for name, expected in provenance["source_sha256"].items():
        if digest(session / "source-snapshot" / name) != expected:
            raise ValueError("Confirmation source snapshot changed: " + name)
    for name, expected in plan["runtime_sha256"].items():
        if provenance["source_sha256"].get(name) != expected:
            raise ValueError("Confirmation runtime differs from frozen plan")
    trials, attempts = plan["trials"], state["attempts"]
    if len(attempts) != len(trials) or state.get("validation", {}).get("passed") is not True:
        raise ValueError("Confirmation incomplete; do not plot a complete-success figure")
    if json.loads((session / "validation.json").read_text()) != state["validation"]:
        raise ValueError("Confirmation validation report differs")
    rows = []
    for trial, record in zip(trials, attempts):
        if any(record.get(k) != v for k, v in trial.items()) or record["status"] != "success":
            raise ValueError("Confirmation record does not match plan")
        checked = validate_benchmark(session / trial["trial_id"], trial["configuration"])
        if record["aggregate"] != checked["aggregate"]:
            raise ValueError("Confirmation aggregate differs from raw evidence")
        rows.append({"pair_seed": trial["pair_seed"], "repeat": trial["repeat"], "arm": trial["arm"],
                     "configuration": f"E{trial['configuration']['executors']}/B{trial['configuration']['batch_size']}",
                     "throughput_cases_per_s": checked["aggregate"]["throughput_cases_per_s"]})
    return rows


def plot_confirmation(rows, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    arms = ("baseline", "gemini", "random", "fixed_e3b3")
    titles = ("Baseline", "Gemini pick", "Random pick", "Fixed E3/B3")
    colors = ("#64748b", "#2563eb", "#d97706", "#15803d")
    seeds = sorted({r["pair_seed"] for r in rows})
    fig, axes = plt.subplots(1, len(seeds), figsize=(11.5, 4.2), sharey=True, squeeze=False)
    stats = []
    for ax, seed in zip(axes[0], seeds):
        labels = []
        for x, (arm, title, color) in enumerate(zip(arms, titles, colors)):
            group = sorted([r for r in rows if r["pair_seed"] == seed and r["arm"] == arm], key=lambda r: r["repeat"])
            values = [r["throughput_cases_per_s"] for r in group]
            summary = describe(values)
            stats.append({"pair_seed": seed, "arm": arm, "configuration": group[0]["configuration"], **summary})
            offsets = [(i - (len(values)-1)/2) * .045 for i in range(len(values))]
            ax.scatter([x + d for d in offsets], values, c=color, s=22, alpha=.65, zorder=3)
            ax.errorbar([x], [summary["mean"]], yerr=[summary["sample_sd"]], fmt="D", color=color,
                        capsize=5, markersize=6, linewidth=1.8, zorder=4)
            labels.append(title + "\n" + group[0]["configuration"])
        ax.set_title(f"Original pair {seed}", fontsize=11, weight="bold")
        ax.set_xticks(range(4), labels, fontsize=8)
        ax.set_xlim(-.5, 3.5)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=.2)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0][0].set_ylabel("Throughput (cases/s)")
    fig.suptitle("Repeated confirmation of fixed configurations", fontsize=14, weight="bold")
    fig.text(.5, .015, "Points: five technical repeats per arm. Diamonds: means; error bars: sample SD. One allocation, job 59798274.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=(0, .07, 1, .92))
    for extension in ("png", "pdf", "svg"):
        fig.savefig(out / ("confirmation." + extension), dpi=220, bbox_inches="tight")
    svg = out / "confirmation.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    plt.close(fig)
    return stats


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-root", type=Path, default=Path("results"))
    p.add_argument("--jobs", nargs="+", default=["58835485", "59936561"])
    p.add_argument("--confirmation", type=Path, default=Path("results/confirmation-59798274-1792042495419462330"))
    p.add_argument("--out-dir", type=Path, default=Path("results/oct28-existing-evidence"))
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sessions, pairs, report = blocked_comparison(args.results_root, args.jobs)
    write_csv(args.out_dir / "sessions.csv", SESSION_FIELDS, sessions)
    write_csv(args.out_dir / "paired.csv", PAIR_FIELDS, pairs)
    write_json(args.out_dir / "blocks.json", report)
    if not report["audit_passed"]:
        raise SystemExit("Blocked comparison audit failed; inspect blocks.json")
    rows = confirmation_rows(args.confirmation)
    write_csv(args.out_dir / "confirmation-trials.csv", list(rows[0]), rows)
    if not args.no_plot:
        stats = plot_confirmation(rows, args.out_dir)
        write_csv(args.out_dir / "confirmation-means.csv", list(stats[0]), stats)
    write_json(args.out_dir / "evidence-manifest.json", {
        "purpose": "Offline reanalysis; no new experiments", "source_jobs": args.jobs,
        "confirmation_session": str(args.confirmation),
        "sha256": {str(path): digest(path) for root in [Path(r["session"]) for r in sessions] + [args.confirmation]
                   for path in sorted(root.rglob("*")) if path.is_file()},
        "analysis_source_sha256": {name: digest(PROJECT / name) for name in (
            "scripts/summarize_evaluation_blocks.py", "scripts/analyze_policy_comparison.py", "tools/experiment_session.py")}})
    print(json.dumps(report["descriptive_total"], sort_keys=True))
    print("PASS: comparison blocks and confirmation raw evidence audited.")


if __name__ == "__main__":
    main()
