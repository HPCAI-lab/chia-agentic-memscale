"""Mock control-flow tests, never performance evidence."""
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from compare_loop import RandomWorker
from tools.experiment_session import ExperimentSession, configuration
from test_agent_session import measurement

class PolicyTests(unittest.TestCase):
    def test_equal_space_random_budget_receipts_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = dict(step=0, selection_source="scripted_baseline", status="success",
                            configuration=configuration(1,1), receipt="baseline", **measurement())
            for policy in ("gemini", "random"):
                directory = root / policy
                directory.mkdir()
                session = ExperimentSession.create(directory, root, "image", 4, baseline, [], policy=policy)
                worker = RandomWorker(directory)
                history = worker.history()
                self.assertEqual(len(history["untested_configurations"]), 8)
                self.assertIn(configuration(2,2), history["untested_configurations"])
                rng = random.Random(101)
                selected = []
                with patch("tools.experiment_session.run_benchmark", side_effect=[
                        RuntimeError("simulated failed run"), measurement(), measurement(), measurement()]):
                    for step in range(4):
                        choice = rng.choice(history["untested_configurations"])
                        selected.append(choice)
                        record = worker.experiment(choice, history["latest_receipt"], 101)
                        self.assertEqual(record["selection_source"], policy)
                        self.assertEqual(record["observed_receipt"], history["latest_receipt"])
                        self.assertEqual(record["status"], "failed" if step == 0 else "success")
                        history = worker.history()
                self.assertEqual(len({(c["executors"], c["batch_size"]) for c in selected}), 4)
                self.assertEqual(history["remaining_budget"], 0)
                rejected = worker.experiment(history["untested_configurations"][0], history["latest_receipt"], 101)
                self.assertEqual(rejected["status"], "rejected")
                self.assertIn(f"Steps 1+ are {policy}", (directory/'trajectory.md').read_text())

    def test_invalid_policy_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                ExperimentSession.create(tmp, tmp, "image", 4, {}, [], policy="unknown")
