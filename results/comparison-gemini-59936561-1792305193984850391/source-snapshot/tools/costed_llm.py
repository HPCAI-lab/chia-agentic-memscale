"""Cost observation around the existing CHIA SDK client, preserving its tool loop."""

import hashlib
from importlib.metadata import version
from pathlib import Path

from chia.models import openai_compat
from tools.policy_costs import TimedCreate
from tools.experiment_session import write_json


EXPECTED_BACKEND_SHA256 = "4398d31966e471354752373be072dff905f5cc534766bdf29ec51221a25a8671"


def backend_identity():
    actual = hashlib.sha256(Path(openai_compat.__file__).read_bytes()).hexdigest()
    if actual != EXPECTED_BACKEND_SHA256:
        raise RuntimeError("CHIA backend differs from the reviewed Gemini-signature-patched revision")
    return {"chia_backend_sha256": actual, "openai_sdk_version": version("openai")}


class CostedOpenAICompatLLM(openai_compat.OpenAICompatLLM):
    def __init__(self, *, cost_directory, **kwargs):
        if kwargs.get("client_kwargs", {}).get("max_retries") != 0 or kwargs.get("retries") != 1:
            raise ValueError("Costed comparisons require SDK max_retries=0 and CHIA retries=1")
        super().__init__(**kwargs)
        self.cost_directory = str(Path(cost_directory).resolve())

    def _make_client(self):
        identity = backend_identity()
        write_json(Path(self.cost_directory) / "model_backend.json", identity)
        client = super()._make_client()
        client.chat.completions.create = TimedCreate(
            client.chat.completions.create, Path(self.cost_directory) / "model_requests.jsonl")
        return client
