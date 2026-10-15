"""Exercise the comparison driver with fake Ray/CHIA and scientific execution."""

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import compare_loop
from scripts.analyze_policy_comparison import analyze_session
from tools.experiment_session import ExperimentSession, write_json
from tools.policy_costs import TimedCreate
from test_analyze_policy_comparison import measurement


IDENTITY = {"chia_backend_sha256": "a" * 64, "openai_sdk_version": "synthetic-sdk"}


class RemoteActor:
    def __init__(self, cls):
        self.cls = cls

    def remote(self, *args):
        instance = self.cls(*args)
        return SimpleNamespace(history=SimpleNamespace(remote=instance.history),
                               experiment=SimpleNamespace(remote=instance.experiment))


class FakeBase:
    def _run_openai_async(self):
        """Synthetic stand-in with the required model_dump signature marker."""


class FakePrompt:
    def options(self, **kwargs):
        if kwargs != {"max_retries": 0}:
            raise AssertionError("Ray retries must remain disabled")
        return self

    def chia_remote(self, llm, prompt, tools):
        directory = Path(llm.kwargs["cost_directory"])
        write_json(directory / "model_backend.json", IDENTITY)
        session = tools[0].session
        async def provider():
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7))
        create = TimedCreate(provider, directory / "model_requests.jsonl")
        history = session.history()
        for e, b in ((1, 2), (1, 3), (2, 1), (3, 3)):
            asyncio.run(create())
            session.experiment(e, b, "synthetic agent choice", history["latest_receipt"])
            history = session.history()
        asyncio.run(create())
        return SimpleNamespace(result="Complete: " + history["latest_receipt"], stream_result="synthetic trace")


class FakeCostedLLM:
    def __init__(self, **kwargs):
        if kwargs["client_kwargs"] != {"max_retries": 0, "timeout": 60.0} or kwargs["retries"] != 1:
            raise AssertionError("SDK retry/timeout boundary differs from the declared configuration")
        self.kwargs = kwargs
        self.prompt = FakePrompt()


class FakeTool:
    def __init__(self, *, session_directory, **kwargs):
        self.session = ExperimentSession(session_directory)

    def stop(self):
        pass


class DriverCostTests(unittest.TestCase):
    def exercise(self, policy, warmup_failure=False):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for name in ("workload/nwchem_benchmark.py", "workload/inputs/water_check.nw",
                         "tools/experiment_session.py", "tools/memscale_tool.py", "compare_loop.py",
                         "tools/policy_costs.py", "tools/costed_llm.py", "provenance/chia-commit.txt",
                         "provenance/nwchem-image-id.txt"):
                p = project / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("synthetic fixture, not executable research evidence\n")

            def benchmark(project, directory, image, config, **kwargs):
                if warmup_failure:
                    raise RuntimeError("synthetic warmup failure")
                directory = Path(directory)
                directory.mkdir()
                data = measurement(config, 1.)
                write_json(directory / "measurement.json", data)
                return {"aggregate": data["aggregate"], "measurement_file": str(directory / "measurement.json"),
                        "launcher_elapsed_s": 0., "worker_pid": os.getpid() + 1}

            ray = SimpleNamespace(init=Mock(), shutdown=Mock(), kill=Mock(), cancel=Mock(),
                                  get=lambda value, **kwargs: value,
                                  remote=lambda **kwargs: RemoteActor)
            backend = SimpleNamespace(OpenAICompatLLM=FakeBase)
            modules = {
                "ray": ray, "chia": SimpleNamespace(), "chia.base": SimpleNamespace(),
                "chia.base.ChiaFunction": SimpleNamespace(get=lambda value, **kwargs: value),
                "chia.models": SimpleNamespace(openai_compat=backend),
                "chia.models.openai_compat": backend,
                "tools.memscale_tool": SimpleNamespace(MemScaleTool=FakeTool),
                "tools.costed_llm": SimpleNamespace(CostedOpenAICompatLLM=FakeCostedLLM,
                                                    backend_identity=lambda: dict(IDENTITY)),
            }
            with patch.dict("sys.modules", modules), \
                    patch.dict(os.environ, {"SLURM_JOB_ID": "9999", "SLURM_NTASKS": "8", "GEMINI_API_KEY": "offline-placeholder"}), \
                    patch("sys.argv", ["compare_loop.py", "--policy", policy]), \
                    patch.object(compare_loop, "__file__", str(project / "compare_loop.py")), \
                    patch("compare_loop.socket.gethostname", return_value="nid-synthetic"), \
                    patch("compare_loop.subprocess.check_output", return_value="synthetic-commit\n"), \
                    patch("compare_loop.run_benchmark", side_effect=benchmark), \
                    patch("tools.experiment_session.run_benchmark", side_effect=benchmark), \
                    contextlib.redirect_stdout(io.StringIO()):
                if warmup_failure:
                    with self.assertRaises(SystemExit):
                        compare_loop.main()
                else:
                    compare_loop.main()
            directory = next((project / "results").glob("comparison-*"))
            costs = json.loads((directory / "costs.json").read_text())
            self.assertTrue(costs["accounting_valid"], costs["errors"])
            if warmup_failure:
                self.assertFalse(costs["timing_complete"])
                self.assertTrue((directory / "failure.json").exists())
                self.assertEqual(costs["phases"]["warmup"]["failed"], 1)
                self.assertEqual(costs["phases"]["cleanup"]["finished"], 1)
            else:
                row = analyze_session(directory)
                self.assertTrue(row["analysis_eligible"], row["errors"])
                self.assertTrue(row["cost_timing_complete"])
                self.assertEqual(row["api_requests"], 5 if policy == "gemini" else 0)
                self.assertEqual(row["api_tokens"], 35 if policy == "gemini" else 0)
                self.assertEqual(costs["search_attempts"]["attempts"], 4)
                self.assertGreater(costs["search_attempts"]["elapsed_s"], 0)
                self.assertEqual(costs["random_selection"]["started"], 4 if policy == "random" else 0)

    def test_gemini_driver_exports_complete_auditable_costs(self):
        self.exercise("gemini")

    def test_random_driver_exports_zero_model_usage_and_timed_choices(self):
        self.exercise("random")

    def test_driver_preserves_costs_when_warmup_fails(self):
        self.exercise("gemini", warmup_failure=True)


if __name__ == "__main__":
    unittest.main()
