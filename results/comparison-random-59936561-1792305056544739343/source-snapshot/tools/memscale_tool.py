"""Expose the fixed-work NWChem experiment session through CHIA MCP."""

import logging

from chia.base.tools.ChiaTool import ChiaTool
from tools.experiment_session import ExperimentSession


class MemScaleTool(ChiaTool):
    def setup(self, session_directory):
        self.session = ExperimentSession(session_directory)
        self.mcp.add_tool(self.get_experiment_history, name="get_experiment_history")
        self.mcp.add_tool(self.run_memscale_experiment, name="run_memscale_experiment")

    def get_experiment_history(self) -> dict:
        """Read measured history, remaining budget, untested choices and latest receipt."""
        print("ENTER get_experiment_history", flush=True)
        try:
            return self.session.history()
        except Exception:
            logging.exception("get_experiment_history failed inside the worker")
            raise

    def run_memscale_experiment(self, executors: int, batch_size: int,
                               reason: str, observed_receipt: str) -> dict:
        """Run one untested configuration of exactly three NWChem calculations.

        executors and batch_size are integers 1–3; partitions=3 and replication=1.
        Give a brief experimental rationale and the receipt from the latest result
        or history. Wait for the measurement before choosing the next experiment.
        """
        print("ENTER run_memscale_experiment", flush=True)
        try:
            return self.session.experiment(executors, batch_size, reason, observed_receipt)
        except Exception:
            logging.exception("run_memscale_experiment failed inside the worker")
            raise
