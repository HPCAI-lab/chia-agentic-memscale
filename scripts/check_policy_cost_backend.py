#!/usr/bin/env python3
"""Offline installed-SDK/CHIA serialization and recorder check; no API or Ray job."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def main():
    import ray.cloudpickle as cloudpickle
    from tools.costed_llm import CostedOpenAICompatLLM, backend_identity
    from tools.policy_costs import TimedCreate

    identity = backend_identity()
    with tempfile.TemporaryDirectory(prefix="cost-backend-check-") as temporary:
        llm = CostedOpenAICompatLLM(
            cost_directory=temporary, model="offline-check", api_key="offline-placeholder",
            base_url="https://example.invalid/v1/", retries=1,
            client_kwargs={"max_retries": 0, "timeout": 60.0}, logging_level=50)
        restored = cloudpickle.loads(cloudpickle.dumps(llm))
        client = restored._make_client()
        if not isinstance(client.chat.completions.create, TimedCreate) or client.max_retries != 0:
            raise RuntimeError("SDK client was not instrumented as expected")

        expected = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3))
        async def fake_response(*args, **kwargs):
            return expected

        # Replace the underlying SDK operation before invoking the recorder.
        # No live transport is called, and the placeholder is not a credential.
        client.chat.completions.create.original = fake_response
        async def check():
            try:
                actual = await client.chat.completions.create(model="offline-check", messages=[])
                if actual is not expected:
                    raise RuntimeError("Recorder changed the response object")
            finally:
                await client.close()
        asyncio.run(check())
        events = [json.loads(line) for line in (Path(temporary) / "model_requests.jsonl").read_text().splitlines()]
        if len(events) != 2 or events[-1]["usage"]["total_tokens"] != 3:
            raise RuntimeError("Usage events were not preserved")
    print("PASS: installed CHIA/SDK recorder and Ray serialization; no network requests")
    print(json.dumps(identity, sort_keys=True))


if __name__ == "__main__":
    main()
