"""Offline confirmation tests. Synthetic timings are not research evidence."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import run_policy_confirmation as confirmation
from test_analyze_policy_comparison import fixture, measurement, write


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = self.root / "results"
        self.results.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.g = fixture(self.results, job="58835485")
        self.r = fixture(self.results, policy="random", job="58835485", rates=(1.1, 1.3, 1.2))
        for directory in (self.g, self.r):
            path = directory / "provenance.json"
            data = json.loads(path.read_text())
            data["recorded_image_id"] = "a" * 64
            write(path, data)
        for name in confirmation.RUNTIME_FILES:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.g / "source-snapshot" / name).read_bytes())
        self.plan = confirmation.build_plan(self.results, "58835485", repeats=2, seed=123)

    @staticmethod
    def fake_measurement(project, directory, image, configuration, **kwargs):
        directory.mkdir()
        data = measurement(configuration, float(min(configuration["executors"], configuration["batch_size"])))
        write(directory / "measurement.json", data)
        return {"aggregate": data["aggregate"], "launcher_elapsed_s": .01,
                "measurement_file": str(directory / "measurement.json")}

    def run_mock(self, runner=None, image_check=None):
        with patch.object(confirmation, "require_compute_node"), \
             patch.dict(os.environ, {"SLURM_JOB_ID": "999"}), \
             patch.object(confirmation, "verify_image", side_effect=image_check or (lambda image: image)), \
             patch.object(confirmation, "run_benchmark", side_effect=runner or self.fake_measurement) as launch, \
             contextlib.redirect_stdout(io.StringIO()):
            directory, passed = confirmation.run_confirmation(self.plan, self.results, self.project)
        return directory, passed, launch

    def test_equal_repeats_frozen_winners_and_deterministic_schedule(self):
        pair = self.plan["pairs"][0]
        self.assertEqual(pair["arms"]["gemini"], confirmation.configuration(3, 3))
        self.assertEqual(pair["arms"]["random"], confirmation.configuration(2, 2))
        self.assertEqual(Counter(t["arm"] for t in self.plan["trials"]), {a: 2 for a in confirmation.ARMS})
        again = confirmation.build_plan(self.results, "58835485", repeats=2, seed=123)
        self.assertEqual(self.plan["trials"], again["trials"])
        self.assertEqual(len({t["trial_id"] for t in self.plan["trials"]}), 8)
        confirmation.verify_inputs(self.plan, self.results, self.project)

    def test_missing_policy_is_not_accepted_as_a_confirmation_pair(self):
        self.r.rename(self.results / "not-a-comparison")
        with self.assertRaisesRegex(ValueError, "complete pairs"):
            confirmation.build_plan(self.results, "58835485")

    def test_invalid_repetitions_and_job_id_rejected(self):
        for repeats in (0, 21, True):
            with self.assertRaises(ValueError):
                confirmation.build_plan(self.results, "58835485", repeats=repeats)
        with self.assertRaises(ValueError):
            confirmation.build_plan(self.results, "*")

    def test_changed_plan_digest_is_rejected(self):
        self.plan["trials"].reverse()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            confirmation.validate_plan(self.plan)

    def test_invalid_resigned_schedule_is_still_rejected(self):
        self.plan["trials"][0]["configuration"] = confirmation.configuration(1, 1)
        self.plan["trials"][0]["trial_id"] = "changed"
        self.plan["plan_sha256"] = confirmation.plan_digest(self.plan)
        with self.assertRaisesRegex(ValueError, "schedule"):
            confirmation.validate_plan(self.plan)

    def test_changed_historical_evidence_rejected(self):
        (self.g / "validation.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Historical evidence changed"):
            confirmation.verify_inputs(self.plan, self.results, self.project)

    def test_changed_current_harness_rejected(self):
        (self.project / confirmation.RUNTIME_FILES[0]).write_text("changed runtime")
        with self.assertRaisesRegex(ValueError, "Runtime differs"):
            confirmation.verify_inputs(self.plan, self.results, self.project)

    def test_login_node_guard_and_allocation_checks(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(confirmation.socket, "gethostname", return_value="login35"):
            with self.assertRaisesRegex(RuntimeError, "compute node"):
                confirmation.require_compute_node()
        with patch.dict(os.environ, {"SLURM_JOB_ID": "999", "SLURM_NTASKS": "8", "SLURM_CPUS_PER_TASK": "2"}), \
             patch.object(confirmation.socket, "gethostname", return_value="nid000001"):
            confirmation.require_compute_node()

    def test_pinned_image_identity_verified(self):
        with patch.object(confirmation.subprocess, "check_output", return_value="a" * 64 + "\n") as command:
            self.assertEqual(confirmation.verify_image("a" * 64), "a" * 64)
            self.assertIn("id:" + "a" * 64, command.call_args.args[0])
        with patch.object(confirmation.subprocess, "check_output", return_value="b" * 64):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                confirmation.verify_image("a" * 64)

    def test_success_runs_every_arm_separately_and_exports(self):
        directory, passed, launch = self.run_mock()
        self.assertTrue(passed)
        self.assertEqual(launch.call_count, 9)  # 8 independent trials, plus one warmup.
        state = json.loads((directory / "state.json").read_text())
        self.assertEqual(len(state["attempts"]), 8)
        self.assertTrue(all(r["attempt_elapsed_s"] >= 0 for r in state["attempts"]))
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual(summary["pairs"][0]["arms"]["gemini"]["throughput_cases_per_s"]["n"], 2)
        self.assertEqual(summary["pairs"][0]["paired_repeat_differences_cases_per_s"]["n"], 2)
        self.assertTrue((directory / "trials.csv").exists())
        self.assertTrue((directory / "paired.csv").exists())
        for call in launch.call_args_list:
            self.assertEqual(call.args[2], "id:" + "a" * 64)

    def test_failed_attempt_is_timed_and_remaining_trials_continue(self):
        count = 0
        def runner(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError("synthetic failure")
            return self.fake_measurement(*args, **kwargs)
        directory, passed, launch = self.run_mock(runner)
        self.assertFalse(passed)
        self.assertEqual(launch.call_count, 9)
        state = json.loads((directory / "state.json").read_text())
        self.assertEqual(state["validation"]["failed_trials"], 1)
        self.assertEqual(state["validation"]["successful_trials"], 7)
        self.assertGreaterEqual(state["attempts"][1]["attempt_elapsed_s"], 0)
        self.assertNotIn("aggregate", state["attempts"][1])

    def test_interrupt_preserves_partial_results_and_unstarted_rows(self):
        count = 0
        def runner(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise KeyboardInterrupt()
            return self.fake_measurement(*args, **kwargs)
        directory, passed, _ = self.run_mock(runner)
        self.assertFalse(passed)
        state = json.loads((directory / "state.json").read_text())
        self.assertEqual(state["validation"]["interrupted_trials"], 1)
        self.assertEqual(state["attempts"][-1]["status"], "interrupted")
        self.assertIsNotNone(state["attempts"][-1]["attempt_elapsed_s"])
        import csv
        with (directory / "trials.csv").open() as stream:
            statuses = Counter(row["status"] for row in csv.DictReader(stream))
        self.assertEqual(statuses, {"success": 1, "interrupted": 1, "not_started": 6})

    def test_warmup_failure_preserves_full_unstarted_schedule(self):
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic warmup failure")
        directory, passed, launch = self.run_mock(fail)
        self.assertFalse(passed)
        self.assertEqual(launch.call_count, 1)
        state = json.loads((directory / "state.json").read_text())
        self.assertEqual(state["attempts"], [])
        self.assertEqual(state["warmup"]["status"], "failed")
        self.assertIsNotNone(state["validation"]["warmup_elapsed_s"])

    def test_image_identity_failure_launches_no_workload(self):
        def fail(image):
            raise RuntimeError("image mismatch")
        directory, passed, launch = self.run_mock(image_check=fail)
        self.assertFalse(passed)
        launch.assert_not_called()
        self.assertTrue((directory / "validation.json").exists())

    def test_plan_creation_refuses_to_overwrite_existing_protocol(self):
        output = self.root / "frozen.json"
        output.write_text("original protocol")
        with patch.object(confirmation, "PROJECT", self.project), \
             patch.object(confirmation.sys, "argv", ["confirm", "plan", "--source-job", "58835485",
                 "--results-root", str(self.results), "--out", str(output)]), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                confirmation.main()
        self.assertEqual(exc.exception.code, 2)
        self.assertEqual(output.read_text(), "original protocol")

    def test_submit_supplies_plan_image_id_and_paths(self):
        plan_path = self.root / "plan.json"
        write(plan_path, self.plan)
        with patch.object(confirmation, "PROJECT", self.project), \
             patch.object(confirmation.sys, "argv", ["confirm", "submit", "--plan", str(plan_path),
                 "--results-root", str(self.results)]), \
             patch.object(confirmation.subprocess, "run") as submit:
            confirmation.main()
        command = submit.call_args.args[0]
        self.assertEqual(command[:2], ["sbatch", "--image=id:" + "a" * 64])
        self.assertEqual(command[-2:], [str(plan_path.resolve()), str(self.results.resolve())])


if __name__ == "__main__":
    unittest.main()
