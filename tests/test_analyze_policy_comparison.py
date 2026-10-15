"""Synthetic analysis fixtures are tests, never research measurements."""

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_policy_comparison import SOURCE_FILES, analyze, analyze_session, describe, pair_sessions


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def config(e, b):
    return dict(partitions=3, executors=e, replication=1, batch_size=b, placement="single-node")


def measurement(configuration, throughput):
    return {
        "backend": "perlmutter", "workload": "nwchem_water_6-31g_scf", "configuration": configuration,
        "aggregate": {"status": "success", "cases_total": 3, "cases_completed": 3,
                      "throughput_cases_per_s": throughput, "experiment_elapsed_s": round(3 / throughput, 3),
                      "latency_mean_ms": 1000, "memory_peak_mb": None},
        "cases": [{"case_id": f"p{i}-r0", "passed": True, "returncode": 0,
                   "energy_hartree": -75.983998} for i in range(3)],
    }


def fixture(root, policy="gemini", seed=101, job="job1", rates=(.8, 1.2, 1.5)):
    directory = root / f"comparison-{policy}-{job}-{seed}"
    directory.mkdir()
    hashes = {}
    for name in SOURCE_FILES:
        path = directory / "source-snapshot" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic analysis fixture, not executable source\n")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = {"policy": policy, "backend": "perlmutter", "job_id": job, "hostname": "test-node",
                  "comparison_pair_seed": seed, "random_seed": seed if policy == "random" else None,
                  "experiment_budget": 3, "python": "3.10.19", "chia_commit": "test-chia",
                  "model": "test-model", "image": "test-image", "recorded_image_id": "test-image-id",
                  "sha256": hashes, "history_scope": "fresh"}
    write(directory / "provenance.json", provenance)
    records = []
    for step, (choice, rate) in enumerate(zip([config(1, 1), config(1, 2), config(2, 2), config(3, 3)], [1., *rates])):
        data = measurement(choice, rate)
        record = {"step": step, "configuration": choice, "selection_source": policy if step else "scripted_baseline",
                  "status": "success", "receipt": f"receipt-{step}", "aggregate": data["aggregate"],
                  "launcher_elapsed_s": 5., "measurement_file": "/unavailable/original-host/measurement.json"}
        if step:
            record["observed_receipt"] = f"receipt-{step-1}"
        records.append(record)
        write(directory / (f"step-{step}" if step else "baseline") / "measurement.json", data)
    write(directory / "warmup/measurement.json", measurement(config(1, 1), 1.))
    write(directory / "state.json", {"policy": policy, "backend": "perlmutter", "budget": 3,
                                     "prior_history": [], "history_inspected": True,
                                     "baseline": records[0], "attempts": records[1:]})
    write(directory / "validation.json", {"policy": policy, "passed": True, "requested_experiments": 3,
              "measured_agent_experiments": 3, "final_receipt_reported": True,
              "session_elapsed_s": 100 if policy == "gemini" else 60, "search_launcher_elapsed_s": 15.})
    (directory / "events.jsonl").write_text('{"event":"history_read"}\n{"event":"rejected"}\n')
    return directory


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def change(self, directory, file, update):
        path = directory / file
        value = json.loads(path.read_text())
        update(value)
        write(path, value)

    def test_real_schema_metrics_paired_signs_and_outputs(self):
        fixture(self.root)
        fixture(self.root, policy="random", rates=(1.1, 1.2, 1.25))
        rows, pairs, summary = analyze(self.root, self.root / "analysis")
        g = next(r for r in rows if r["policy"] == "gemini")
        self.assertTrue(g["analysis_eligible"], g["errors"])
        self.assertEqual((g["best_gain_pct"], g["best_step"], g["first_improvement_step"]), (50, 3, 2))
        self.assertAlmostEqual(g["mean_best_so_far_cases_per_s"], (1 + 1.2 + 1.5) / 3)
        self.assertEqual(g["rejected_actions"], 1)
        p = pairs[0]
        self.assertTrue(p["eligible"], p["reason"])
        self.assertAlmostEqual(p["best_relative_difference_pct"], 20.)
        self.assertEqual(p["best_difference_cases_per_s"], .25)
        self.assertEqual(p["gain_difference_percentage_points"], 25.)
        self.assertEqual(p["session_difference_s"], 40.)
        self.assertEqual(summary["counts"]["eligible_pairs"], 1)
        with (self.root / "analysis/sessions.csv").open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 2)
        self.assertNotIn("NaN", (self.root / "analysis/summary.json").read_text())

    def test_baseline_retained_when_all_choices_are_worse(self):
        row = analyze_session(fixture(self.root, rates=(.8, .9, .7)))
        self.assertTrue(row["analysis_eligible"], row["errors"])
        self.assertEqual((row["best_cases_per_s"], row["best_step"], row["best_gain_pct"]), (1, 0, 0))
        self.assertIsNone(row["first_improvement_step"])

    def test_failed_attempt_consumes_budget_and_unknown_cost_is_not_zero(self):
        directory = fixture(self.root)
        def fail(state):
            attempt = state["attempts"][1]
            attempt["status"] = "failed"
            del attempt["aggregate"], attempt["launcher_elapsed_s"]
        self.change(directory, "state.json", fail)
        self.change(directory, "validation.json", lambda v: v.update(passed=False, measured_agent_experiments=2, search_launcher_elapsed_s=10.))
        write(directory / "failure.json", {"error_type": "RuntimeError", "message": "one experiment failed"})
        row = analyze_session(directory)
        self.assertTrue(row["analysis_eligible"], row["errors"])
        self.assertTrue(row["budget_completed"])
        self.assertEqual(row["failures"], 1)
        self.assertEqual(row["search_launcher_known_s"], 10.)
        self.assertIsNone(row["search_launcher_elapsed_s"])
        self.assertTrue(row["failure_file_present"])
        self.assertFalse(row["validation_passed"])

    def test_incomplete_attempts_remain_visible_and_do_not_pair(self):
        directory = fixture(self.root)
        random_dir = fixture(self.root, policy="random")
        self.change(directory, "state.json", lambda s: s["attempts"].pop())
        row = analyze_session(directory)
        self.assertFalse(row["analysis_eligible"])
        self.assertEqual(row["attempts"], 2)
        self.assertIn("budget not completed", row["errors"])
        self.assertFalse(pair_sessions([row, analyze_session(random_dir)])[0]["eligible"])

    def test_same_seed_in_other_allocation_is_not_paired(self):
        rows = [analyze_session(fixture(self.root)),
                analyze_session(fixture(self.root, policy="random", job="job2"))]
        pairs = pair_sessions(rows)
        self.assertEqual(len(pairs), 2)
        self.assertFalse(any(p["eligible"] for p in pairs))

    def test_duplicate_policy_reruns_are_not_silently_selected(self):
        first = fixture(self.root)
        duplicate = self.root / "comparison-gemini-rerun"
        shutil.copytree(first, duplicate)
        fixture(self.root, policy="random")
        rows, pairs, summary = analyze(self.root, self.root / "out")
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["counts"]["eligible_pairs"], 0)
        self.assertIn("exactly one", pairs[0]["reason"])

    def test_recorded_environment_mismatch_is_not_paired_or_pooled(self):
        g = fixture(self.root)
        r = fixture(self.root, policy="random")
        self.change(r, "provenance.json", lambda p: p.update(recorded_image_id="other-image"))
        rows, pairs, summary = analyze(self.root, self.root / "out")
        self.assertTrue(all(r["analysis_eligible"] for r in rows))
        self.assertFalse(pairs[0]["eligible"])
        self.assertIn("provenance differs", pairs[0]["reason"])
        self.assertEqual(len(summary["cohorts"]), 2)

    def test_no_block_id_requires_explicit_pairing_metadata(self):
        g = fixture(self.root)
        r = fixture(self.root, policy="random")
        for directory in (g, r):
            self.change(directory, "provenance.json", lambda p: p.update(job_id=None))
        pairs = pair_sessions([analyze_session(g), analyze_session(r)])
        self.assertEqual(len(pairs), 2)
        self.assertFalse(any(p["eligible"] for p in pairs))
        for directory in (g, r):
            self.change(directory, "provenance.json", lambda p: p.update(comparison_group_id="declared-block"))
        self.assertTrue(pair_sessions([analyze_session(g), analyze_session(r)])[0]["eligible"])

    def test_bad_energy_and_nonfinite_measurements_do_not_enter_summary(self):
        for seed, value in ((101, 0.), (102, float("nan"))):
            directory = fixture(self.root, seed=seed)
            self.change(directory, "step-3/measurement.json", lambda m: m["cases"][0].update(energy_hartree=value))
        rows, _, summary = analyze(self.root, self.root / "out")
        self.assertFalse(any(r["analysis_eligible"] for r in rows))
        self.assertEqual(summary["cohorts"], {})
        self.assertTrue(all("scientific validation failed" in r["errors"] for r in rows))

    def test_missing_raw_measurement_and_source_hash_mismatch_reported(self):
        directory = fixture(self.root)
        (directory / "step-1/measurement.json").unlink()
        (directory / "source-snapshot" / SOURCE_FILES[0]).write_text("changed")
        row = analyze_session(directory)
        self.assertFalse(row["analysis_eligible"])
        self.assertIn("hash mismatch", row["errors"])
        self.assertIn("FileNotFoundError", row["errors"])

    def test_state_measurement_disagreement_and_receipt_error_reported(self):
        directory = fixture(self.root)
        def corrupt(state):
            state["attempts"][0]["aggregate"]["throughput_cases_per_s"] = 99.
            state["attempts"][0]["observed_receipt"] = "stale"
        self.change(directory, "state.json", corrupt)
        row = analyze_session(directory)
        self.assertIn("receipt chain", row["errors"])
        self.assertIn("state aggregate differs", row["errors"])

    def test_malformed_and_missing_json_sessions_are_retained(self):
        directory = self.root / "comparison-gemini-incomplete"
        directory.mkdir()
        (directory / "state.json").write_text("{")
        rows, _, summary = analyze(self.root, self.root / "out")
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["counts"]["ineligible_sessions"], 1)
        self.assertIn("JSONDecodeError", rows[0]["errors"])

    def test_malformed_pairing_metadata_does_not_crash_the_report(self):
        directory = fixture(self.root)
        self.change(directory, "provenance.json", lambda p: p.update(comparison_pair_seed=[101], hostname={"bad": "type"}))
        rows, pairs, summary = analyze(self.root, self.root / "out")
        self.assertEqual(summary["counts"]["ineligible_sessions"], 1)
        self.assertFalse(pairs[0]["eligible"])

    def test_sample_sd_and_null_singleton_sd(self):
        self.assertAlmostEqual(describe([1., 2., 3.])["sample_sd"], 1.)
        self.assertIsNone(describe([1.])["sample_sd"])
        self.assertIsNone(describe([])["mean"])

    def test_cli_strict_reports_errors_after_writing_results(self):
        fixture(self.root)  # One-sided pair must fail strict mode.
        script = Path(__file__).resolve().parents[1] / "scripts/analyze_policy_comparison.py"
        result = subprocess.run([sys.executable, str(script), "--results-root", str(self.root),
                                 "--out-dir", str(self.root / "out"), "--strict"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue((self.root / "out/summary.json").exists())

    def test_empty_results_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "No comparison"):
            analyze(self.root, self.root / "out")


if __name__ == "__main__":
    unittest.main()
