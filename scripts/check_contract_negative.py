#!/usr/bin/env python3
"""Export a reproducible offline fault-injection table. Executes no Ray/NWChem jobs."""
import argparse
import hashlib
import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from scripts.analyze_policy_comparison import write_csv


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append({"test": test.id(), "passed": True, **test.evidence})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append({"test": test.id(), "passed": False})

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append({"test": test.id(), "passed": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("results/contract-negative-tests"))
    args = parser.parse_args()
    suite = unittest.TestLoader().discover(str(PROJECT / "tests"), pattern="test_contract_injections.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fields = ("test", "passed", "injected_fault", "detection_boundary", "benchmark_calls_after_injection", "outcome", "evidence_kind")
    write_csv(args.out_dir / "injected-failures.csv", fields, result.rows)
    sources = ("scripts/check_contract_negative.py", "tests/test_contract_injections.py", "tests/test_agent_session.py",
               "tests/test_analyze_policy_comparison.py", "tools/experiment_session.py", "scripts/analyze_policy_comparison.py",
               "tools/policy_costs.py", "workload/nwchem_benchmark.py")
    passed = result.wasSuccessful() and result.testsRun == 7 and len(result.rows) == 7
    report = {"passed": passed, "tests_run": result.testsRun, "rows": result.rows,
              "source_sha256": {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() for name in sources},
              "scope": "Offline synthetic fault injection. Zero benchmark calls means zero additional calls after injection at the spied boundary; it does not assert that no Ray actor existed. SCF corruption is necessarily detected after output exists. These are correctness tests, not HPC performance measurements."}
    (args.out_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Offline contract fault injection", "", report["scope"], "",
             "| Injected fault | Detection boundary | Benchmark calls after injection | Outcome |",
             "| --- | --- | ---: | --- |"]
    lines += [f"| {r.get('injected_fault', r['test'])} | {r.get('detection_boundary', '')} | {r.get('benchmark_calls_after_injection', '')} | {r.get('outcome', 'FAILED')} |" for r in result.rows]
    (args.out_dir / "injected-failures.md").write_text("\n".join(lines) + "\n")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
