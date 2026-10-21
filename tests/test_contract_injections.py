"""Synthetic injected faults; launcher spies distinguish admission from post-run audits."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.experiment_session import ExperimentSession, configuration, validate_benchmark
from scripts.analyze_policy_comparison import analyze_session
from test_agent_session import measurement as result
from test_analyze_policy_comparison import fixture, measurement, write


def raw_fixture(directory, config=None, rate=1.):
    config = config or configuration(2, 2)
    data = measurement(config, rate)
    data["scientific_validation"] = {"reference_energy_hartree": -75.983998, "tolerance_hartree": 1e-5}
    for case in data["cases"]:
        path = directory / "cases" / ("nwchem-" + case["case_id"] + "-synthetic")
        path.mkdir(parents=True, exist_ok=True)
        case["run_directory"] = str(path)
        (path / "output.txt").write_text("Synthetic unit-test output, never a research measurement\nTotal SCF energy = -75.983998\n")
    write(directory / "measurement.json", data)
    return data


class InjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def session(self, budget=4):
        base = {"step": 0, "selection_source": "scripted_baseline", "status": "success",
                "configuration": configuration(1, 1), "receipt": "baseline", **result()}
        session = ExperimentSession.create(self.root, self.root, "synthetic", budget, base, [])
        session.history()
        return session

    def record(self, fault, boundary, calls, outcome):
        self.evidence = {"injected_fault": fault, "detection_boundary": boundary,
                         "benchmark_calls_after_injection": calls, "outcome": outcome,
                         "evidence_kind": "offline synthetic injection with launcher spy"}

    def test_out_of_space_before_launch(self):
        session = self.session()
        with patch("tools.experiment_session.run_benchmark") as launcher:
            for e, b in ((4, 1), (1, 0), (True, 2), (2.0, 2)):
                self.assertEqual(session.experiment(e, b, "injected action", "baseline")["status"], "rejected")
        launcher.assert_not_called()
        self.assertEqual(session.load()["attempts"], [])
        self.record("out-of-space / wrong-type action", "runtime admission", launcher.call_count, "rejected; budget unchanged")

    def test_duplicate_before_launch(self):
        session = self.session()
        with patch("tools.experiment_session.run_benchmark", return_value=result()) as launcher:
            valid = session.experiment(2, 2, "control", "baseline")
            launcher.reset_mock()
            bad = session.experiment(2, 2, "duplicate", valid["receipt"])
        self.assertEqual(bad["status"], "rejected")
        launcher.assert_not_called()
        self.assertEqual(len(session.load()["attempts"]), 1)
        self.record("duplicate configuration", "runtime admission", launcher.call_count, "rejected; no additional attempt")

    def test_stale_receipt_before_launch(self):
        session = self.session()
        with patch("tools.experiment_session.run_benchmark", return_value=result()) as launcher:
            session.experiment(2, 2, "control", "baseline")
            launcher.reset_mock()
            bad = session.experiment(3, 3, "stale receipt", "baseline")
        self.assertEqual(bad["status"], "rejected")
        launcher.assert_not_called()
        self.record("stale receipt", "runtime admission", launcher.call_count, "rejected")

    def test_edited_record_is_audit_failure(self):
        directory = fixture(self.root)
        state = json.loads((directory / "state.json").read_text())
        state["attempts"][0]["aggregate"]["throughput_cases_per_s"] *= 2
        write(directory / "state.json", state)
        with patch("tools.experiment_session.run_benchmark") as launcher:
            row = analyze_session(directory)
        self.assertFalse(row["analysis_eligible"])
        self.assertIn("state aggregate differs", row["errors"])
        launcher.assert_not_called()
        self.record("edited result record", "offline consistency audit", launcher.call_count, "excluded from policy analysis")

    def test_reordered_receipt_is_audit_failure(self):
        directory = fixture(self.root)
        state = json.loads((directory / "state.json").read_text())
        state["attempts"][0], state["attempts"][1] = state["attempts"][1], state["attempts"][0]
        write(directory / "state.json", state)
        with patch("tools.experiment_session.run_benchmark") as launcher:
            row = analyze_session(directory)
        self.assertFalse(row["analysis_eligible"])
        self.assertIn("receipt chain", row["errors"])
        launcher.assert_not_called()
        self.record("reordered records / receipts", "offline sequence audit", launcher.call_count, "excluded from policy analysis")

    def test_corrupted_scf_text_is_rejected(self):
        directory = self.root / "trial"
        data = raw_fixture(directory)
        # JSON still claims the correct energy; original text has been corrupted.
        (Path(data["cases"][0]["run_directory"]) / "output.txt").write_text("Total SCF energy = -74.0\n")
        with patch("tools.experiment_session.launch_benchmark") as launcher:
            with self.assertRaisesRegex(RuntimeError, "Raw SCF output"):
                validate_benchmark(directory, configuration(2, 2))
        launcher.assert_not_called()
        self.record("corrupted SCF output with unchanged JSON", "completion validation / offline audit", launcher.call_count,
                    "rejected after simulated output exists; no new workload launched by audit")

    def test_budget_exhausted_before_launch(self):
        session = self.session(budget=1)
        with patch("tools.experiment_session.run_benchmark", return_value=result()) as launcher:
            valid = session.experiment(2, 2, "control", "baseline")
            launcher.reset_mock()
            bad = session.experiment(3, 3, "over budget", valid["receipt"])
        self.assertEqual(bad["status"], "rejected")
        launcher.assert_not_called()
        self.record("exhausted attempt budget", "runtime admission", launcher.call_count, "rejected")


if __name__ == "__main__":
    unittest.main()
