# GCP autonomous experiment results — September 23, 2026

Four separate Gemini/CHIA sessions completed successfully on the GCP native
backend. Each began with a warm-up and a newly measured `3P / 1E / B1` baseline,
then Gemini selected four distinct configurations using measurement feedback.
The budget was four agent experiments per session; history was reset for every
session, with no Perlmutter measurements supplied. See [reproduction](gcp.md).

## All runs

| Run | Session / evidence | Fresh baseline (cases/s) | Best agent selection | Best (cases/s) | Gain over that baseline | Session elapsed (s) |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| 1 | [1790140931374443334](../results/agent-gcp-1790140931374443334/) | 2.021636 | 3P / 2E / B2 | 2.990864 | 47.9% | 21.332 |
| 2 | [1790141191264079437](../results/agent-gcp-1790141191264079437/) | 2.040685 | 3P / 2E / B2 | 2.982902 | 46.2% | 22.050 |
| 3 | [1790141215995608561](../results/agent-gcp-1790141215995608561/) | 2.016003 | 3P / 3E / B3 | 5.798345 | 187.6% | 20.130 |
| 4 | [1790141238772626079](../results/agent-gcp-1790141238772626079/) | 2.023182 | 3P / 3E / B3 | 5.796827 | 186.5% | 21.141 |

Gain is `(best / fresh_baseline - 1) * 100`, using the measurements in that run.
All four sessions passed validation, with 16 measured agent experiments and
four correctly reported final receipts. The 48 agent-selected case outputs,
12 baseline outputs, and 12 warm-up outputs all meet the reference SCF energy
`-75.983998` Hartree within `1e-5` Hartree. Their recorded final energies are
`-75.983997570421` Hartree.

## Complete trajectory of run 4

| Step | Selected by | Configuration | Throughput (cases/s) |
| --- | --- | --- | ---: |
| 0 | Scripted baseline | 3P / 1E / B1 | 2.023182 |
| 1 | Gemini | 3P / 2E / B1 | 2.030462 |
| 2 | Gemini | 3P / 1E / B2 | 2.023746 |
| 3 | Gemini | 3P / 2E / B2 | 3.028421 |
| 4 | Gemini | 3P / 3E / B3 | 5.796827 |

This is one of the four trajectories, not a replacement for the complete
run table. Run 3 reached a slightly higher best throughput than run 4.

## What the evidence supports

The tool traces and receipt chains demonstrate repeated sequential agent
selection and real CHIA-triggered execution with scientific feedback. Runs 1–2
followed one selection sequence and runs 3–4 another. `2E/B2` was measured in
all four runs (2.982902–3.028421 cases/s); `3E/B3` was agent-selected in two
runs (5.796827–5.798345 cases/s). These are descriptive observations from four
sessions on one VM, not randomized trials or independent hardware samples.

This workload is tiny and process launch is a substantial part of elapsed
time. Changing concurrency changes resource use: the baseline launches one
two-rank calculation at a time, while `3E/B3` can launch three. The gain is in
orchestration throughput for fixed work, not a claim of better per-core
scientific computation, energy efficiency, or memory optimization.

The saved Gemini explanations are unedited model output. Some incorrectly
suggest that `1E/B2` or `2E/B1` allows concurrent cases. The actual harness bounds
concurrency by both values; neither configuration permits more than one case
at once. Receipt verification establishes that a preceding result was received;
it does not prove the model's explanation is causally correct. No controlled
comparison with random search, grid search, or a fixed heuristic was performed.
Global optimality, larger NWChem workloads, placement policies and memory use
remain unmeasured. These results must not be combined with the Perlmutter
manual 51.5% comparison or its single agent-run 44.9% gain.

## Auditable artifacts

- [All 20 measured trajectory rows](../results/gcp-summary/experiments.csv) (four baselines and 16 agent choices).
- [Run summary CSV](../results/gcp-summary/runs.csv) and [JSON](../results/gcp-summary/summary.json).
- [Evidence checksums](../results/gcp-summary/evidence-sha256.json) and [publication file list](../results/gcp-summary/publication-files.txt).
- Each session directory above contains `trajectory.csv`, `events.jsonl`,
  `tool_trace.txt`, `agent_response.txt`, `validation.json`, `provenance.json`,
  `python-packages.txt`, `lscpu.txt`, and warm-up/baseline/step measurements with
  raw stdout and inputs.

The run provenance records base commit `a5ae28d` plus an uncommitted native
backend patch. Recorded source hashes match the uploaded executed source;
`source.patch` captures tracked changes, while the publication adds the native
wrapper, tests, and documentation. The package snapshots agree across all four
runs. The recorded model is `gemini-3.5-flash-lite`. No API expenditure or VM
cost was measured. The requested $1,500 project budget is not actual spending.
