# Repeated confirmation of the existing pilot selections

This phase implements repeated measurements of the settings selected in job
`58835485`. It reuses the original three-water-calculation NWChem harness.
It does not run a new Gemini/random search, add independent policy sessions,
change memory policies, or adapt an application while it is running.

The prior analysis must already be installed. The original 29 tests plus 16
confirmation tests give 45 offline tests. No live confirmation measurements were
performed when preparing this implementation.

## Protocol fixed before new measurements

For each of the three historical pairs, execute these four arms five times:

| Pair seed | Baseline | Gemini selection | Random selection | Fixed concurrency control |
| --- | --- | --- | --- | --- |
| 101 | E1/B1 | E2/B2 | E3/B2 | E3/B3 |
| 102 | E1/B1 | E2/B2 | E2/B2 | E3/B3 |
| 103 | E1/B1 | E2/B3 | E3/B3 | E3/B3 |

Selections are read from the audited historical records, not hardcoded into the
runner. Best selection includes the baseline if it was best. Identical settings
under different arm labels are executed separately. Their different labels do
not make them distinct configurations or additional selection evidence.

Each repetition contains all twelve pair/arm combinations, shuffled with a
declared random seed. The complete 60-trial order is saved in a plan before
submission. One E1/B1 warmup is excluded from measured-trial summaries. Every
trial still contains exactly three independent SCF calculations (183 calculations
including warmup if the full run succeeds). The `replication` workload knob
remains 1; repeating measurements does not change work per experiment.

The E3/B3 control follows directly from the maximum available concurrency in
this small independent-case harness. This protocol was designed after inspecting
the pilot and must be described as a pilot-informed confirmation phase.

## Install and validate

Extract the four files in `chia-policy-confirmation.zip` into the repository,
preserving their paths. They are new files; no historical runtime file is changed.
From the repository root with `chia-memscale` active:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
bash -n scripts/run_policy_confirmation.sbatch
```

## Create the plan on the login node

Planning/checking are lightweight offline operations suitable for a login node.

```bash
python scripts/run_policy_confirmation.py plan --source-job 58835485 --repeats 5 --seed 20261015 --out results/confirmation-plan-58835485.json
python scripts/run_policy_confirmation.py check --plan results/confirmation-plan-58835485.json
```

Plan creation audits the old sessions, selects their best configurations, saves
the trial order, and records hashes of the historical evidence and harness.
Existing plan files are never overwritten. Reuse the existing plan for the same
protocol. If a new protocol is needed, use a new filename and disclose the change.

Inspect the printed configuration table and 60-trial count before submission.
Commit the protocol and scripts before seeing new measurements. The repository
ignores `results/`, so the plan requires an explicit `git add -f` if committing it.

## Submit a compute-node job

Review the batch script's account `m5289`, CPU constraint, debug QOS, and 25-minute
limit against your allocation. These match the previous comparison script.
The workload is guarded against execution on a login node.

```bash
python scripts/run_policy_confirmation.py submit --plan results/confirmation-plan-58835485.json
```

Submission supplies `sbatch --image=id:<full ID>` from the plan. The runner uses
that same ID for every workload launch and checks `SHIFTER_IMAGE` inside the
container before starting the warmup. The ID is taken from the pilot's recorded
image identity. Historical metadata did not establish the actual image used in
each old run; pinning it now does not retroactively prove that identity.

NERSC documents image-ID syntax and `SHIFTER_IMAGE` at:
https://docs.nersc.gov/development/containers/shifter/how-to-use/#looking-up-images-by-id

Pinning also changes image lookup behavior relative to the historical launcher.
All confirmation arms use the same launch procedure. Compare configurations
within this new run; do not interpret a raw historical/new timing difference as
a policy improvement.

The job requests one CPU node, eight task slots, and two CPUs per task. It uses
the existing two-MPI-rank calculation launcher and the same thread/MPI settings.
There are no Gemini API calls and no Ray startup in this confirmation phase.
Its wall-time budget is 25 minutes with an interrupt requested after 22 minutes.
Every workload attempt has the existing 120-second timeout.

## Outputs and failures

Each execution creates a unique `results/confirmation-JOB-TIMESTAMP/` directory:

| File | Purpose |
| --- | --- |
| `plan.json` | Exact frozen input protocol used for this run. |
| `provenance.json`, `source-snapshot/` | Requested/resolved image ID, source hashes, runtime environment and source. |
| `state.json` | Atomic checkpoints before and after attempts, including failed/interrupted attempts. |
| `warmup/`, `trial-NNNN/` | Launcher logs, scientific outputs and measurements from the shared harness. |
| `trials.csv` | All planned trials, including failed and not-started rows, plus measured costs. |
| `paired.csv` | Within-pair/repetition Gemini-minus-random throughput differences where both succeeded. |
| `summary.json` | Per-pair/per-arm counts, means, medians, sample SDs, and interpretation limits. |
| `validation.json` | Completion and scientific-success counts and timing totals. |

Attempt time is measured even when the benchmark raises an exception. A failed
warmup stops the run; a failed measured trial is recorded and later planned
trials continue. Interrupts retain partial evidence. A hard SIGKILL/node failure
cannot guarantee a final timestamp; a running checkpoint represents an unknown
duration, not zero. Rebuild reports from the preserved state if necessary:

```bash
python scripts/run_policy_confirmation.py report --session results/confirmation-JOB-TIMESTAMP
```

The report command reconstructs reports from the saved runner state; it does not
independently re-audit raw measurement/console files. Preserve the full directory.
Do not silently replace an incomplete run with a successful rerun. A rerun is a
new run with its own evidence directory and must be disclosed.

Session time includes image verification, warmup and trial execution, ending
before final report export. This phase records confirmation costs. It does not
repair missing historical costs or measure new policy decision/token costs.

## Interpretation

Use the repeats to assess whether the selected settings' performance differences
persist and whether the fixed concurrency control is competitive. Report failures
alongside success-only throughput summaries. The five repeats per arm quantify
within-run timing variation; they are not five independent agent-selection
sessions. There are still only three historical selection pairs and one new
confirmation allocation/node. Broader policy claims require additional independent
search sessions and workloads, with their own complete cost accounting.
