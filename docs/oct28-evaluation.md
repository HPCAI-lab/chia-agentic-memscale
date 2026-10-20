# Evaluation extension for the October 28 deadline

This extends the existing runner and preserves historical evidence. New landscape
and overhead measurements must be collected on Perlmutter. Offline tests and the
historical figure are not new performance results.

## Exhaustive empirical reference

`run_bounded_evaluation.py` freezes all E1–E3 / B1–B3 combinations, five
measurements per combination, in five independently shuffled repeat blocks.
There are **45 measured trials plus one excluded E1/B1 warmup**, each containing
three independent water SCF calculations. One Ray actor and one pinned Shifter
image are used in one allocation. No model request or API key is needed.

The plan contains a SHA256 digest, exact schedule, image identity, source hashes,
and a fixed trial timeout. Commit the code and plan before submission. Source,
schedule, or image mismatches fail closed. Each run uses a new directory;
interrupted runs retain state and the planned unstarted trials. A timed-out Ray
call stops the study rather than launching the next trial while earlier work
might still be running.

With `chia-memscale` activated, from the repository root:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/check_evaluation_backend.py
bash -n scripts/run_bounded_evaluation.sbatch
python scripts/check_contract_negative.py
python scripts/run_bounded_evaluation.py plan --kind landscape --repeats 5 --seed 20261020 --out results/landscape-plan-oct28.json
python scripts/run_bounded_evaluation.py check --plan results/landscape-plan-oct28.json
git add -f -- results/landscape-plan-oct28.json
git commit -m "Freeze exhaustive nine-configuration landscape protocol"
python scripts/run_bounded_evaluation.py submit --plan results/landscape-plan-oct28.json
```

The batch job reserves 30 minutes and gives the driver 27 minutes plus cleanup.
45 × 27 seconds is 20 minutes 15 seconds before startup and warmup. This is a
planning estimate, not a guarantee. Review the account directive if using a
different allocation. Workloads are blocked on login nodes.

After success, use the exact printed session directory:

```bash
python scripts/analyze_bounded_evaluation.py --session results/landscape-JOB-TIMESTAMP --out-dir results/landscape-analysis-JOB --results-root results --source-jobs 58835485 59936561
```

The audit checks schedule, source snapshots, running image identity, worker
identity, measurements, original SCF text, and artifact digests. `trials.csv`
retains every planned trial, including missing/failed trials. Ranking is refused
for incomplete or corrupted studies. `landscape.csv` includes means, sample SD,
empirical ranks, and seeded whole-block bootstrap mean intervals and top-rank
fractions (10,000 resamples). With five repeat blocks these uncertainty estimates
have limited resolution; they do not establish a globally true ranking or a
Bayesian probability of optimality.

Reference regret for c is `max(mean throughput) - mean throughput(c)`; relative
regret divides by the maximum reference mean. `regret.csv` distinguishes the
final recommended configuration from the best reference configuration visited.
It records whether a policy visited or recommended a top reference configuration,
retaining the fresh baseline as a candidate. Recommendation uses the original
session's best observed result; it is not retroactively changed by the sweep.

Historical configurations are mapped onto this **new allocation's reference**.
This is retrospective reference regret, not historical ground-truth regret:
nodes, images, and instrumentation may differ. Old throughput is never subtracted
from new throughput. Report policy outcomes by source-job block and disclose the
reference transfer. A stronger future study would collect policy runs and the
reference sweep in each allocation.

## Contract enforcement and measurement boundary

Existing tests already exercised out-of-space actions, duplicate selections,
and stale receipts. `check_contract_negative.py` exports an explicit table with
launcher spies, also covering edited records, reordered receipts, exhausted
budget, and corrupted raw SCF output. Reports are in
`results/contract-negative-tests/`. These are offline injected failures, not
adversarial-LLM observations or live rejection-rate measurements.

Admission failures cause zero additional benchmark calls. A CHIA/Ray actor may
already exist to handle the request. Bad SCF output is necessarily checked after
output exists; the offline audit launches no additional workload. Do not claim
bad output was detected before the original workload launched. Map tests to
manuscript invariant labels only after checking their exact definitions.

The runtime now checks final energies in original case output against the JSON
and hashes the accepted measurement and raw artifacts. Receipt tokens remain
unique UUIDs linked by `observed_receipt`, not cryptographic signatures. Hashes
detect edits relative to preserved records, not a coordinated rewrite of all
records, digests, and the trusted publication. The tool caller has no filesystem
or shell access; host/runtime and the preserved evidence anchor are trusted.

## Paired contract overhead

Freeze a separate job with ten pairs: 20 measured trials plus warmup.

```bash
python scripts/run_bounded_evaluation.py plan --kind overhead --repeats 10 --seed 20261020 --out results/overhead-plan-oct28.json
python scripts/run_bounded_evaluation.py check --plan results/overhead-plan-oct28.json
git add -f -- results/overhead-plan-oct28.json
git commit -m "Freeze paired contract overhead protocol"
python scripts/run_bounded_evaluation.py submit --plan results/overhead-plan-oct28.json
```

Each pair uses the same E2/B2 workload and warmed Ray actor. The starting arm is
randomized, then alternates across pairs. Bare calls the existing harness;
contract calls `ExperimentSession.experiment`. Both retain the harness's own
scientific checks. Contract adds admission, outer raw-output validation, artifact
hashing, state/receipt persistence, and trajectory export. The bare entry point
is a benchmark control, not an agent tool.

Worker and driver roundtrip timers cover the call, including the profile-observer
write. Session creation/history setup is recorded as `prepare_s` and excluded
from per-call comparisons. Ray startup, image checking and warmup are recorded
separately; one-time source verification is `startup_source_verification_s`.

Direct timers cover admission (including lock wait and receipt-token generation),
reservation writes, completion writes, trajectory export, scientific validation
including reads, and hashing loaded artifact bytes. Their sum excludes the final
observer write and lock release, which remain in whole-call timing. Components
are nested, and must not be added again to the total. Identical post-timing audits
run for both arms outside the roundtrip interval; both must pass.

```bash
python scripts/analyze_bounded_evaluation.py --session results/overhead-JOB-TIMESTAMP --out-dir results/overhead-analysis-JOB
```

Reports give paired differences and direct component costs with means, sample SD,
median and range. Differences can be negative due to workload noise; they are not
clipped. Direct component sums and differences between independent executions are
different estimands. No "under 1%" claim is assumed. This measures the current
instrumented contract, not the historical implementation, and excludes Gemini
reasoning and MCP transport costs.

## Two comparison blocks and confirmation

Reanalysis of preserved evidence gives:

| Job block | Matched pairs | Gemini wins | Random wins | Ties |
| --- | ---: | ---: | ---: | ---: |
| 58835485 | 3 | 1 | 2 | 0 |
| 59936561 | 3 | 2 | 1 | 0 |
| Descriptive total | 6 | 3 | 3 | 0 |

There are two independent allocation blocks, not six independent allocations.
Repeated random seeds reuse configuration sequences across jobs. Raw throughput
and API-cost means stay separate by job because runtime/image conditions and
instrumentation differ. A measured win is not a significance claim. Incomplete
validation job 59800416 remains disclosed separately.

`summarize_evaluation_blocks.py` audits comparisons and all 60 confirmation
measurements, including raw SCF text, then exports tables, a source manifest,
and PNG/PDF/SVG figures. Matplotlib is only needed to regenerate figures;
`--no-plot` audits and exports tables using the standard library. The supplied
figure is `results/oct28-existing-evidence/confirmation.png` (also PDF and SVG).
Points are individual repeats, diamonds are means, error bars are sample SD.
Pair 102 uses E2/B2 for both policies, so its timing difference cannot be attributed
to selection. Pair 103 uses E3/B3 for random and the fixed control.

## Naming, model scope, and optional larger workload

The displayed project title is **CHIA: Bounded Agentic Scientific Experimentation**.
`MemScaleTool` is a compatibility name for the MCP interface; its implemented
controls are case concurrency and wave size, not memory policies. The manuscript
should likewise omit MemScale from the title unless a concrete implemented
contribution can be demonstrated. No manuscript source was present in this
repository, so this change updates repository wording only.

The evaluation uses a lightweight Flash-Lite agent; report its measured request
time and token usage and limit conclusions to that model. No stronger-model
comparison was performed. Avoid assigning a historical selection rationale or
dollar cost that was not recorded.

Only the water 6-31g input is in the repository; no validated 1H9T input/reference
is present. The optional larger-workload phase needs a validated input with
charge/spin/basis settings, a reference energy and tolerance, resource limits,
and a single-configuration Slurm calibration. A larger basis on a small molecule
is sufficient if it yields stable minutes-scale experiments. Do not reuse the
current water reference energy for another workload. After calibration, freeze a
workload-specific extension and measure the decision/execution ratio; do not
assume it shrinks before measuring. Bayesian optimization and stronger models
follow the exhaustive reference, enforcement evidence and overhead in priority.
