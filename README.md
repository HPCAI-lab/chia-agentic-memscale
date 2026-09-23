# CHIA Agentic MemScale

A bounded agent–runtime interface for adaptive execution of scientific HPC workloads, built with Gemini, CHIA MCP tools, Ray, and NWChem.

## Overview and current status

CHIA-Agentic MemScale connects an agent to a controlled experiment runner. Gemini reads experiment history, selects an untested configuration, requests execution through a CHIA tool, receives measured performance and scientific-validation feedback, and selects the next experiment. The runtime validates requests and controls execution; the agent has no unrestricted shell tool.

**The autonomous loop has been validated on NERSC Perlmutter with NWChem 7.2.0.** Job `58656462` completed on September 20, 2026, with exit code `0:0`. Gemini selected four previously untested configurations, and all four experiments passed scientific validation. This is a small water-SCF orchestration demonstration; memory optimization and larger-workload scaling remain research goals.

**GCP validation is also complete:** four independent sessions on September 23, 2026 executed 16 Gemini-selected experiments, all scientifically validated. The best selections improved throughput by 46.2–187.6% over their respective fresh GCP baselines. See [all GCP results and limitations](docs/gcp-results.md), [CSV/JSON evidence](results/gcp-summary/), and [clean VM setup instructions](docs/gcp.md). These are small-workload orchestration measurements, not memory-optimization or cross-platform speedup claims.

## Methodology

The implementation follows a state–action–feedback loop:

1. Gemini inspects structured history, available choices, and the latest execution receipt.
2. Gemini chooses executor count and batch size and supplies an experimental hypothesis.
3. A CHIA MCP tool validates the choice, experiment budget, and receipt before launching the benchmark from a Ray worker.
4. The platform launcher executes NWChem: Slurm/Shifter on Perlmutter, or native OpenMPI on GCP.
5. The tool returns measurements and scientific-validation results for the next decision.

Each experiment performs exactly three independent water 6-31g SCF calculations. Partitions are fixed at 3 and replication at 1. Gemini selects executors and batch size from 1, 2, or 3. Executors bound concurrent launches; batch size controls cases per wave. These are harness scheduling controls, not molecular partitions or NWChem memory-policy controls. Each calculation uses two MPI ranks; Perlmutter requests two CPUs per rank, while the native GCP launcher requests a two-core set per calculation. Numerical-library thread counts are set to one.

The runner requires history inspection, rejects repeated or invalid configurations, enforces a finite experiment budget, and chains unique receipts between decisions. Accepted selections and outcomes are saved. The scientific check compares each final SCF energy with `-75.983998` Hartree within `1e-5` Hartree.

## Validated Perlmutter autonomous trajectory

Step 0 is a fresh scripted baseline after a warm-up. Steps 1–4 are Gemini selections; earlier manual measurements were provided only as historical context.

| Step | Selected by | Configuration | Throughput (cases/s) |
| --- | --- | --- | ---: |
| 0 | Fresh scripted baseline | 3P / 1E / B1 | 0.790497 |
| 1 | Gemini | 3P / 1E / B2 | 0.782891 |
| 2 | Gemini | 3P / 1E / B3 | 0.773051 |
| 3 | Gemini | 3P / 2E / B1 | 0.762315 |
| 4 | Gemini | 3P / 2E / B3 | 1.145505 |

P = independent cases (partitions), E = executors, B = batch size.

The best agent-selected configuration achieved **44.9% higher throughput** than the fresh baseline in this single run: `(1.145505 / 0.790497 - 1) × 100`. The validation record confirms four measured agent experiments and successful reporting of the final receipt.

This demonstrates autonomous sequential experimentation. It does not establish statistical significance, global optimality, or superiority over random or grid search. Timings include launch overhead and run-to-run variation. Memory use is unmeasured (`null`); memory optimization, data-placement policies, and NWChem 1H9T scaling have not been demonstrated.

## Public evidence

The complete published session is in [`results/agent-58656462-1789945660187864036/`](results/agent-58656462-1789945660187864036/).

| Evidence | File |
| --- | --- |
| Agent trajectory | [CSV](results/agent-58656462-1789945660187864036/trajectory.csv) / [Markdown](results/agent-58656462-1789945660187864036/trajectory.md) |
| Decisions and outcomes | [events.jsonl](results/agent-58656462-1789945660187864036/events.jsonl) |
| Actual model/tool exchange | [tool_trace.txt](results/agent-58656462-1789945660187864036/tool_trace.txt) |
| Completion checks | [validation.json](results/agent-58656462-1789945660187864036/validation.json) |
| Best observed configuration | [best_config.json](results/agent-58656462-1789945660187864036/best_config.json) |
| Run provenance | [provenance.json](results/agent-58656462-1789945660187864036/provenance.json) |
| Baseline measurement | [measurement.json](results/agent-58656462-1789945660187864036/baseline/measurement.json) |
| Agent measurements | [Step 1](results/agent-58656462-1789945660187864036/step-1/measurement.json), [Step 2](results/agent-58656462-1789945660187864036/step-2/measurement.json), [Step 3](results/agent-58656462-1789945660187864036/step-3/measurement.json), [Step 4](results/agent-58656462-1789945660187864036/step-4/measurement.json) |
| Slurm completion | [slurm-accounting.txt](results/agent-58656462-1789945660187864036/slurm-accounting.txt) |

Earlier **manual** benchmarks are saved in [experiments.csv](results/experiments.csv) and JSON records for jobs [58611234](results/benchmark-58611234.json), [58611317](results/benchmark-58611317.json), and [58611406](results/benchmark-58611406.json). Their approximately 51.5% three-case throughput difference is a manual configuration comparison, separate from the autonomous trajectory above.

## Reproduction on Perlmutter

Prerequisites: a Perlmutter account with CPU allocation access, Conda through the Python module, Shifter, and a Gemini API key with access to the configured model. The published run used Python 3.10.19, Ray 2.54.0, OpenAI SDK 3.15.0, and `gemini-3.5-flash-lite`.

The following setup is for new checkout directories and a new `chia-memscale` environment. Existing users should reuse their patched installation rather than recreate it. These instructions describe the recorded setup; a fresh end-to-end installation has not yet been independently validated. Dependencies are not fully locked, and model availability may change.

```bash
git clone https://github.com/HPCAI-lab/chia-agentic-memscale.git
cd chia-agentic-memscale
module load python
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create -y -n chia-memscale python=3.10.19
conda activate chia-memscale

git clone https://github.com/ucb-bar/chia.git ../chia-memscale-upstream
git -C ../chia-memscale-upstream checkout "$(cat provenance/chia-commit.txt)"
git -C ../chia-memscale-upstream apply --check "$PWD/provenance/gemini-tool-signature.patch"
git -C ../chia-memscale-upstream apply "$PWD/provenance/gemini-tool-signature.patch"
python -m pip install -e ../chia-memscale-upstream 'ray==2.54.0' 'openai==3.15.0'
python -m pip check
mkdir -p results
TMPDIR="$PWD/results" python -m unittest discover -s tests -p 'test_agent_session.py' -v
```

The CHIA patch preserves complete SDK tool-call objects, including Gemini thought signatures, when sending the next assistant message. The upstream commit and compatibility patch are recorded under `provenance/`.

Prepare the NWChem image and wait for `READY`:

```bash
shifterimg -v pull ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest
```

The historical image ID is in [provenance/nwchem-image-id.txt](provenance/nwchem-image-id.txt). The launcher currently uses a mutable `latest` tag; compare the resolved image ID with the recorded ID before claiming the identical environment.

Review `scripts/run_agent_loop.sbatch` and change its `--account=m5289` directive if needed for your allocation. From the project directory on a login node:

```bash
read -rsp "Paste Gemini API key: " GEMINI_API_KEY
echo
export GEMINI_API_KEY
sbatch --export=ALL scripts/run_agent_loop.sbatch
```

The batch job activates the environment and executes the workload on a compute node. Do not run the live workload directly on a login node. Each submission creates a new session directory; agent decisions and exact performance may differ. Never commit API keys or credential files.

See [docs/agent_loop.md](docs/agent_loop.md) for resource requirements, timeouts, output files, and completion criteria. The earlier multiplication integration check is in [tests/check_gemini_chia.py](tests/check_gemini_chia.py).

## Repository contents

- `loop.py`: agent orchestration, warm-up, baseline, and session validation.
- `tools/`: bounded CHIA tool and experiment-session state management.
- `workload/`: NWChem input and measurement harness.
- `scripts/`: Perlmutter and GCP launchers, plus the GCP evidence auditor.
- `tests/`: session tests and live integration check.
- `provenance/`: upstream CHIA revision, compatibility patch, and image identity.
- `results/`: selected published measurements and agent evidence.
- `docs/`: detailed run instructions.

## Planned evaluation and expected results

Four GCP agent trajectories have now been recorded. Further evaluation will test more repetitions, broader configuration spaces, and larger scientific workloads. Planned comparisons include random search, grid search, and offline tuning under comparable experiment budgets. Additional action controls, including partition count and memory/data-placement policies, require further implementation and validation.

Evaluation will measure throughput, experiments required to reach a strong configuration, cumulative execution cost, adaptation overhead, rejected actions, and scientific correctness. The expected outcome is a reproducible, auditable framework and evidence about when agent-based selection helps. Improved performance across workloads is a research question, not an established result.

The native GCP backend has been validated on a standalone VM. It uses NWChem 7.0.2 rather than the Perlmutter container's 7.2.0; compare configurations within each environment, not absolute throughput across platforms. The exact constrained-install recipe added after the runs has not yet been revalidated on a second clean VM.

## Proposed compute budget

The project proposal requests **$1,500 in short-term compute funding** for cloud VMs and Gemini API usage supporting repeated NWChem experiments, CHIA/MCP services, and Ray workers. This is a requested budget, not measured expenditure or a provider price quote. CHIA, Ray, NWChem, and the existing implementation will be reused. Actual usage and costs remain to be recorded during the expanded evaluation.
