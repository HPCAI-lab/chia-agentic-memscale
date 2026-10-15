"""Offline accounting tests. Synthetic clocks/responses are not measurements."""

import asyncio
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.analyze_policy_comparison import analyze_session, pair_sessions
from tools.policy_costs import Journal, PHASES, TimedCreate, record_session, summarize_costs, token_usage
from test_analyze_policy_comparison import fixture, write


def read(path):
    return json.loads(path.read_text())


def event_pair(identifier, name, seconds, status="success", **extra):
    return [dict(event="start", span_id=identifier, name=name, time_ns=1),
            dict(event="end", span_id=identifier, name=name, time_ns=2,
                 status=status, elapsed_s=seconds, **extra)]


def journal(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def instrumented_fixture(root, policy="gemini"):
    directory = fixture(root, policy=policy)
    provenance = read(directory / "provenance.json")
    provenance.update(cost_schema_version=1, chia_backend_sha256="a" * 64,
                      openai_sdk_version="synthetic-sdk", model_request_timeout_s=60.,
                      sdk_max_retries=0, chia_retries=1)
    for name in ("tools/policy_costs.py", "tools/costed_llm.py"):
        path = directory / "source-snapshot" / name
        path.write_text("synthetic accounting source fixture\n")
        provenance["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(directory / "provenance.json", provenance)
    write(directory / "model_backend.json", {k: provenance[k] for k in ("chia_backend_sha256", "openai_sdk_version")})
    state = read(directory / "state.json")
    for attempt in state["attempts"]:
        attempt["attempt_elapsed_s"] = 5.
    write(directory / "state.json", state)
    elapsed = 120 if policy == "gemini" else 80
    validation = read(directory / "validation.json")
    validation["session_elapsed_s"] = elapsed - 3
    write(directory / "validation.json", validation)
    durations = dict(warmup=5, baseline=5, ray_startup=10, policy_setup=5,
                     policy_loop=elapsed - 40, reporting=2, cleanup=3)
    events = [event_pair("session", "session", elapsed)[0]]
    for name, seconds in durations.items():
        events += event_pair(name, name, seconds)
    events += [event_pair("session", "session", elapsed)[1]]
    journal(directory / "cost_phases.jsonl", events)
    requests = []
    if policy == "gemini":
        for i, seconds in enumerate((2, 3)):
            requests += event_pair(str(i), "model_request", seconds,
                                   usage=dict(input_tokens=2, output_tokens=1, total_tokens=3))
    journal(directory / "model_requests.jsonl", requests)
    write(directory / "costs.json", summarize_costs(directory, policy))
    return directory


class CostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_sdk_wrapper_preserves_arguments_response_and_signature_objects(self):
        path = self.root / "model_requests.jsonl"
        signature = object()
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2, total_tokens=9),
                                   choices=[SimpleNamespace(thought_signature=signature)])
        messages = [{"content": "do-not-record-this-prompt"}]
        async def provider(**kwargs):
            self.assertIs(kwargs["messages"], messages)
            self.assertEqual(kwargs["model"], "test-model")
            return response
        with patch("tools.policy_costs.time.perf_counter", side_effect=[10., 12.5]):
            result = asyncio.run(TimedCreate(provider, path)(messages=messages, model="test-model"))
        self.assertIs(result, response)
        self.assertIs(result.choices[0].thought_signature, signature)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["start", "end"])
        self.assertEqual(events[1]["elapsed_s"], 2.5)
        self.assertEqual(events[1]["usage"]["total_tokens"], 9)
        self.assertNotIn("do-not-record-this-prompt", path.read_text())

    def test_failed_request_is_timed_and_exception_is_preserved_without_message(self):
        path = self.root / "model_requests.jsonl"
        error = RuntimeError("credential-like-text-must-not-be-logged")
        error.status_code = 429
        async def provider():
            raise error
        with patch("tools.policy_costs.time.perf_counter", side_effect=[10., 14.]), self.assertRaises(RuntimeError) as caught:
            asyncio.run(TimedCreate(provider, path)())
        self.assertIs(caught.exception, error)
        end = json.loads(path.read_text().splitlines()[-1])
        self.assertEqual((end["elapsed_s"], end["status"], end["http_status"]), (4., "failed", 429))
        self.assertNotIn("credential-like", path.read_text())
        self.assertNotIn("usage", end)

    def test_missing_invalid_and_zero_usage_remain_distinct(self):
        self.assertTrue(all(v is None for v in token_usage(SimpleNamespace()).values()))
        values = token_usage({"usage": {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 0,
                                       "prompt_tokens_details": {"cached_tokens": "3"}}})
        self.assertIsNone(values["input_tokens"])
        self.assertIsNone(values["output_tokens"])
        self.assertIsNone(values["cached_input_tokens"])
        self.assertEqual(values["total_tokens"], 0)

    def test_cancelled_request_records_interruption(self):
        async def provider():
            raise asyncio.CancelledError()
        path = self.root / "model_requests.jsonl"
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(TimedCreate(provider, path)())
        self.assertEqual(json.loads(path.read_text().splitlines()[-1])["status"], "interrupted")

    def test_failed_early_session_preserves_cost_report_without_state(self):
        with self.assertRaises(SystemExit):
            with record_session(self.root, "gemini") as clock:
                with clock.span("warmup"):
                    raise SystemExit(1)
        report = read(self.root / "costs.json")
        self.assertTrue(report["accounting_valid"], report["errors"])
        self.assertFalse(report["timing_complete"])
        self.assertEqual(report["session"]["failed"], 1)
        self.assertEqual(report["phases"]["warmup"]["failed"], 1)
        self.assertIsNone(report["search_attempts"]["elapsed_s"])

    def test_nested_intervals_are_not_double_counted(self):
        d = instrumented_fixture(self.root)
        c = read(d / "costs.json")
        self.assertTrue(c["accounting_valid"], c["errors"])
        self.assertTrue(c["timing_complete"])
        self.assertEqual(c["session_including_cleanup_s"], 120)
        self.assertEqual(c["driver_other_s"], 10)
        self.assertEqual(c["policy_loop_other_s"], 60)
        self.assertEqual(c["model_requests"]["elapsed_s"], 5)
        self.assertEqual(c["model_requests"]["total_tokens"], 6)
        self.assertIsNone(c["model_requests"]["api_cost_usd"])

    def test_failed_api_usage_is_unknown_but_request_time_is_known(self):
        d = instrumented_fixture(self.root)
        events = [json.loads(line) for line in (d / "model_requests.jsonl").read_text().splitlines()]
        events[-1].update(status="failed")
        del events[-1]["usage"]
        journal(d / "model_requests.jsonl", events)
        c = summarize_costs(d, "gemini")["model_requests"]
        self.assertEqual(c["elapsed_s"], 5)
        self.assertEqual(c["total_tokens_known"], 3)
        self.assertEqual(c["total_tokens_missing_requests"], 1)
        self.assertIsNone(c["total_tokens"])

    def test_hard_stop_retains_unknown_request_and_attempt_durations(self):
        d = instrumented_fixture(self.root)
        path = d / "model_requests.jsonl"
        journal(path, [json.loads(line) for line in path.read_text().splitlines()][:-1])
        state = read(d / "state.json")
        state["attempts"][-1].pop("attempt_elapsed_s")
        state["attempts"][-1]["status"] = "running"
        write(d / "state.json", state)
        c = summarize_costs(d, "gemini")
        self.assertFalse(c["timing_complete"])
        self.assertEqual(c["model_requests"]["known_elapsed_s"], 2)
        self.assertIsNone(c["model_requests"]["elapsed_s"])
        self.assertEqual(c["search_attempts"]["known_elapsed_s"], 10)
        self.assertIsNone(c["search_attempts"]["elapsed_s"])
        self.assertIsNone(c["policy_loop_other_s"])

    def test_corrupt_journal_does_not_turn_partial_observations_into_totals(self):
        d = instrumented_fixture(self.root)
        with (d / "model_requests.jsonl").open("a") as f:
            f.write('{"event":')
        c = summarize_costs(d, "gemini")
        self.assertFalse(c["accounting_valid"])
        self.assertIsNone(c["model_requests"]["elapsed_s"])
        self.assertIsNone(c["model_requests"]["total_tokens"])
        self.assertEqual(c["model_requests"]["total_tokens_known"], 6)

    def test_duplicate_missing_and_nonfinite_events_are_errors(self):
        for change in ("duplicate", "no-start", "nan", "missing-file"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                d = instrumented_fixture(Path(tmp))
                p = d / "model_requests.jsonl"
                events = [json.loads(line) for line in p.read_text().splitlines()]
                if change == "duplicate":
                    events += [events[-1]]
                elif change == "no-start":
                    events.pop(0)
                elif change == "nan":
                    events[-1]["elapsed_s"] = float("nan")
                if change == "missing-file":
                    p.unlink()
                else:
                    journal(p, events)
                self.assertFalse(summarize_costs(d, "gemini")["accounting_valid"])

    def test_remote_work_after_failed_wait_has_no_residual_decomposition(self):
        d = instrumented_fixture(self.root)
        p = d / "cost_phases.jsonl"
        events = [json.loads(line) for line in p.read_text().splitlines()]
        for e in events:
            if e["event"] == "end" and e["name"] == "policy_loop":
                e.update(status="failed", elapsed_s=1)
            if e["event"] == "end" and e["name"] == "session":
                e["status"] = "failed"
        journal(p, events)
        c = summarize_costs(d, "gemini")
        self.assertTrue(c["accounting_valid"], c["errors"])
        self.assertIsNone(c["policy_loop_other_s"])

    def test_legacy_and_instrumented_analysis_keep_cost_boundaries_explicit(self):
        g = analyze_session(instrumented_fixture(self.root))
        r = analyze_session(instrumented_fixture(self.root, "random"))
        self.assertTrue(g["analysis_eligible"], g["errors"])
        self.assertTrue(r["analysis_eligible"], r["errors"])
        self.assertEqual(g["api_tokens"], 6)
        self.assertEqual(g["session_elapsed_s"], 117)
        self.assertEqual(g["session_including_cleanup_s"], 120)
        self.assertEqual((r["api_requests"], r["api_tokens"], r["api_cost_usd"]), (0, 0, 0))
        pair = pair_sessions([g, r])[0]
        self.assertTrue(pair["eligible"], pair["reason"])
        self.assertEqual(pair["api_request_difference_s"], 5)
        self.assertEqual(pair["session_including_cleanup_difference_s"], 40)
        old = analyze_session(fixture(self.root, seed=999))
        self.assertTrue(old["analysis_eligible"], old["errors"])
        self.assertIsNone(old["api_tokens"])
        self.assertIsNone(old["session_including_cleanup_s"])

    def test_cost_summary_tampering_is_detected(self):
        d = instrumented_fixture(self.root)
        costs = read(d / "costs.json")
        costs["model_requests"]["total_tokens"] = 0
        write(d / "costs.json", costs)
        row = analyze_session(d)
        self.assertFalse(row["analysis_eligible"])
        self.assertIn("differs from raw journals", row["errors"])

    def test_worker_identity_and_cost_source_changes_are_detected(self):
        d = instrumented_fixture(self.root)
        write(d / "model_backend.json", {"chia_backend_sha256": "wrong", "openai_sdk_version": "synthetic-sdk"})
        p = d / "source-snapshot/tools/policy_costs.py"
        p.write_text(p.read_text() + "modified")
        row = analyze_session(d)
        self.assertFalse(row["analysis_eligible"])
        self.assertIn("backend identity", row["errors"])
        self.assertIn("source snapshot hash mismatch", row["errors"])

    def test_changed_cost_runtime_is_not_paired(self):
        g = instrumented_fixture(self.root)
        r = instrumented_fixture(self.root, "random")
        provenance = read(r / "provenance.json")
        provenance["openai_sdk_version"] = "different-sdk"
        write(r / "provenance.json", provenance)
        pair = pair_sessions([analyze_session(g), analyze_session(r)])[0]
        self.assertFalse(pair["eligible"])
        self.assertIn("provenance differs", pair["reason"])

    def adapter(self):
        async def original(**kwargs):
            return SimpleNamespace(usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=original)))
        class FakeBase:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def _make_client(self):
                return client
        backend_file = self.root / "fake_backend.py"
        backend_file.write_text("synthetic backend\n")
        backend = SimpleNamespace(OpenAICompatLLM=FakeBase, __file__=str(backend_file))
        path = Path(__file__).resolve().parents[1] / "tools/costed_llm.py"
        spec = importlib.util.spec_from_file_location("costed_adapter_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"chia": SimpleNamespace(), "chia.models": SimpleNamespace(openai_compat=backend)}):
            spec.loader.exec_module(module)
        module.version = lambda name: "synthetic-sdk"
        module.EXPECTED_BACKEND_SHA256 = hashlib.sha256(backend_file.read_bytes()).hexdigest()
        return module, client, backend_file

    def test_adapter_wraps_the_parent_client_and_records_worker_identity(self):
        adapter, client, _ = self.adapter()
        instance = adapter.CostedOpenAICompatLLM(cost_directory=self.root, model="test", retries=1,
                                                client_kwargs={"max_retries": 0, "timeout": 60.})
        self.assertIs(instance._make_client(), client)
        self.assertIsInstance(client.chat.completions.create, TimedCreate)
        self.assertEqual(read(self.root / "model_backend.json"), adapter.backend_identity())

    def test_adapter_rejects_changed_backend_before_client_creation(self):
        adapter, _, backend_file = self.adapter()
        instance = adapter.CostedOpenAICompatLLM(cost_directory=self.root, retries=1,
                                                client_kwargs={"max_retries": 0})
        backend_file.write_text("changed backend\n")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            instance._make_client()
        self.assertFalse((self.root / "model_backend.json").exists())

    def test_adapter_rejects_hidden_sdk_or_outer_retries(self):
        adapter, _, _ = self.adapter()
        for retries, sdk in ((2, 0), (1, 2), (1, None)):
            with self.subTest(retries=retries, sdk=sdk), self.assertRaises(ValueError):
                adapter.CostedOpenAICompatLLM(cost_directory=self.root, retries=retries,
                                             client_kwargs={"max_retries": sdk})


if __name__ == "__main__":
    unittest.main()
