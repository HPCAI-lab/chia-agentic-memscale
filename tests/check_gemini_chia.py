"""Live Gemini -> CHIA MCP tool -> Ray worker integration check."""

import json
import logging
import os
import socket
import tempfile
import uuid
from pathlib import Path

import ray
from chia.base.ChiaFunction import get
from chia.base.tools.ChiaTool import ChiaTool
from chia.models.openai_compat import OpenAICompatLLM


class CheckTool(ChiaTool):
    def setup(self, receipt_path):
        self.receipt_path = receipt_path
        self.mcp.add_tool(self.multiply, name="multiply")

    def multiply(self, a: int, b: int) -> dict:
        """Multiply two integers and return an execution receipt."""
        result = {
            "a": a,
            "b": b,
            "answer": a * b,
            "receipt": uuid.uuid4().hex,
            "worker_pid": os.getpid(),
        }
        with open(self.receipt_path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(result) + "\n")
        print("TOOL EXECUTED:", result, flush=True)
        return result


def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise SystemExit("Missing GEMINI_API_KEY.")
    if not os.environ.get("SLURM_JOB_ID") or not socket.gethostname().startswith("nid"):
        raise SystemExit("Run this inside an allocated Perlmutter compute node.")

    tool = None
    with tempfile.TemporaryDirectory(prefix="chia-check-") as tmp:
        receipt_path = Path(tmp) / "calls.jsonl"
        try:
            print("Starting Ray on", socket.gethostname(), flush=True)
            ray.init(
                address="local",
                num_cpus=4,
                num_gpus=0,
                include_dashboard=False,
                object_store_memory=256 * 1024 * 1024,
                resources={"openai_creds": 1.0},
            )
            tool = CheckTool(
                name="chia_connection_check",
                receipt_path=str(receipt_path),
                task_options={"num_cpus": 1},
                logging_level=logging.WARNING,
            )
            llm = OpenAICompatLLM(
                model="gemini-3.5-flash-lite",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=key,
                timeout_seconds=45,
                retries=1,
                max_tokens=1024,
                max_tool_iterations=3,
                client_kwargs={"max_retries": 0},
                logging_level=logging.WARNING,
            )
            prompt = (
                "Call multiply exactly once with a=16 and b=25. "
                "Then report the answer and the exact receipt string "
                "returned by the tool. You must obtain the receipt from the tool."
            )
            ref = llm.prompt.options(max_retries=0).chia_remote(
                llm, prompt, tools=[tool]
            )
            result = get(ref, timeout=120)
            answer = str(result.result)
            print("Agent response:", answer)

            if not receipt_path.exists():
                raise RuntimeError("No execution receipt: tool was not called.")

            calls = [
                json.loads(line)
                for line in receipt_path.read_text().splitlines()
            ]
            assert len(calls) == 1, f"Expected one tool call, got {len(calls)}"
            call = calls[0]
            assert (call["a"], call["b"], call["answer"]) == (16, 25, 400)
            assert call["worker_pid"] != os.getpid(), "Expected a separate worker"
            assert call["receipt"] in answer, "Missing tool receipt in response"
            assert "400" in answer, "Missing answer in response"
            print("PASS: Gemini called a CHIA tool on a worker and used its result.")
        except Exception as exc:
            print("FAIL:", type(exc).__name__, str(exc).replace(key, "[REDACTED]"))
            raise SystemExit(1)
        finally:
            try:
                if tool is not None:
                    tool.stop()
            finally:
                ray.shutdown()
                print("Ray cleanup complete.")


if __name__ == "__main__":
    main()
