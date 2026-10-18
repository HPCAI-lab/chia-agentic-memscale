# Cost accounting for new policy comparisons

The published pilot reports whole-session time but cannot isolate Gemini request
latency or token usage. New `compare_loop.py` sessions now observe these costs
without changing the CHIA tool loop, search prompt, allowed configurations,
experiment budget, or scientific harness. Historical measurements are not edited.

## Implementation and boundaries

`CostedOpenAICompatLLM` subclasses the installed CHIA backend and wraps only its
SDK `chat.completions.create` callable. Arguments and returned response/tool-call
objects pass through intact, including Gemini thought signatures. The installed
CHIA source must match the reviewed, signature-patched revision:

`4398d31966e471354752373be072dff905f5cc534766bdf29ec51221a25a8671`

Both the driver and model worker record the backend hash and OpenAI SDK version.
There is no change to the installed CHIA package. The existing `timeout_seconds`
attribute was not forwarded to the SDK by the pinned backend. The comparison now
explicitly supplies a 60-second SDK timeout, `max_retries=0`, and one CHIA attempt.
Ray task retries remain disabled. The SDK timeout is a client/network timeout,
not a guaranteed total policy-session deadline; the batch and driver timeouts
remain separate bounds.

| Observation | Meaning |
| --- | --- |
| `warmup`, `baseline` | Driver intervals around the respective workload calls. |
| `ray_startup` | The `ray.init` call. |
| `policy_setup` | Random actor creation and initial history fetch, or Gemini tool/model construction and prompt preparation. |
| `policy_loop` | Driver wait for the complete adaptive search; includes nested requests and experiments. |
| `reporting` | Completion validation and trajectory reporting. Response-file writes outside this interval appear in the driver residual. |
| `cleanup` | Final trajectory export, cancellation/tool shutdown, and Ray shutdown. |
| Model request | SDK call/response/usage-extraction duration; includes client/network/provider time, not isolated inference time. |
| `attempt_elapsed_s` | Workload-runner call and failure handling, including measurement validation; saved for successful, failed, and interrupted search attempts. |
| `random_selection` | Local random-choice computation, nested inside the policy loop. |

The cost session begins after imports, preflight, and provenance preparation. It
ends after cleanup and before `costs.json` export. `validation.json` retains its
earlier timer, ending before cleanup. Allocation wall time must still come from
Slurm accounting. These fields are different timing boundaries, not competing
measurements of the same interval.

**Do not add nested durations twice.** Model requests, search attempts, and
random-choice computation are already inside `policy_loop`. The driver residual
covers work outside the named top-level phases. The successful-policy-loop
residual covers transport, history calls, bookkeeping and other loop work; it is
not pure agent decision time. A failed/interrupted wait has no residual
decomposition because remote work can continue into cancellation/cleanup.

The original successful-process `launcher_elapsed_s` field keeps its old meaning.
It remains unknown for failed launches. The new outer `attempt_elapsed_s` field
captures handled failures, and validation no longer silently substitutes zero
for missing launcher durations. Instrumentation adds journal/checkpoint overhead;
old and newly instrumented sessions must not be pooled as identical protocols.

## Evidence and unknown values

Each instrumented session adds:

- `cost_phases.jsonl`: driver start/end events, including cleanup and failures.
- `model_requests.jsonl`: request start/end events, durations, outcome classes,
  and selected numeric provider usage fields. No prompts, headers, response text,
  credentials, or exception messages are written to this journal.
- `model_backend.json`: the identity observed in the model worker, when started.
- `costs.json`: a report reconstructed from the journals and attempt records.

A durable start event is written before each observed operation. Normal errors
and interruptions retain their elapsed time. A hard process/node kill can leave
an unmatched start; its full duration and usage remain unknown. Known sums are
explicitly partial, not substitutes for complete totals. Corrupt journals are
flagged and their complete aggregates withheld.

Token counts are provider-reported. Missing input/output/total counts remain
`null`; failed requests do not imply zero billed usage. Cached/reasoning counts
are breakdowns, not additional tokens to add to totals. Gemini dollar cost is
unpriced (`null`): these counts are not invoices. A random policy's zero API
usage/cost says nothing about its compute/allocation cost.

The analysis script rechecks the journals, derived report, worker identity, and
additional source snapshots for instrumented sessions. Pairing distinguishes SDK,
backend, timeout/retry configuration, and instrumentation source changes. Old
sessions remain readable with unknown API usage; their missing history is not
reconstructed or imputed.

To rebuild the derived cost report after a stopped instrumented session:

```bash
python -m tools.policy_costs --session results/comparison-POLICY-JOB-TIMESTAMP
```

This updates `costs.json` from preserved events. It cannot recover unrecorded
requests or durations. Incomplete sessions remain incomplete in policy analysis.

## Offline validation before a live study

With the existing `chia-memscale` environment active, run:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/check_policy_cost_backend.py
python scripts/analyze_policy_comparison.py --results-root results --out-dir results/policy-analysis-cost-check --strict
```

The installed-backend check round-trips the model object through Ray's
serializer, constructs the SDK client, replaces its underlying request with a
synthetic response, and checks usage recording. It submits no Ray task, calls no
API, and executes no NWChem workload. The historical analysis uses a new output
directory so published pilot reports are preserved.

The offline checks validate instrumentation and control flow. The live
validation below adds measured timing and usage evidence. A broader policy
evaluation still needs a separately frozen follow-up protocol. Include the
fixed E3/B3 control, independent allocation blocks, equal search budgets, an
explicit policy order, pinned image identity, failure accounting, and repeated
evaluation of selected configurations in that protocol.

The shared session module now includes an outer attempt timer. Therefore the old
confirmation plan correctly rejects this changed source. Reproduce that historical
protocol in its original checkout, or create a new disclosed plan for new runs;
do not edit its stored hashes to bypass the check.

## Live validation: job 59936561

Code commit `0b12aff` passed 68 tests and the installed-backend check before
submission. Slurm job `59936561` completed on `nid006242` with exit code `0:0`
in 8 minutes 23 seconds. Each of six sessions ran a warmup, a fresh baseline,
and four search attempts. The audit accepted all six sessions and three matched
pairs, with 24 successful search attempts and complete cost timing.

The following are descriptive means across three sessions per policy:

| Metric | Gemini | Random |
| --- | ---: | ---: |
| Best observed throughput (cases/s) | 0.754008 | 0.944467 |
| Session time before cleanup (s) | 86.823333 | 62.367000 |
| Session time including cleanup (s) | 88.814695 | 64.348196 |
| Search-attempt time (s) | 23.370570 | 22.876614 |
| API-request time (s) | 5.897827 | 0.000000 |
| Provider-reported total tokens | 14273.666667 | 0 |

| Pair seed | Gemini best (cases/s) | Random best (cases/s) | Gemini relative to random |
| --- | ---: | ---: | ---: |
| 101 | 0.746707 | 0.744003 | +0.36% |
| 102 | 0.763837 | 0.751623 | +1.63% |
| 103 | 0.751479 | 1.337775 | -43.83% |

Gemini made 18 successful API requests and used 42,821 provider-reported tokens
in total. Random made no model requests; it still incurred workload and
orchestration costs. API duration includes SDK, network, and provider time.
The full session-time difference cannot be attributed to model requests.
Request and search-attempt times are already included in session time; do not
add them to it. Monetary cost remains unpriced.

The earlier validation attempt, job `59800416`, failed on its first Gemini
request with `APIConnectionError` after 0.157599 seconds. It completed no
agent-selected experiments. Token usage for that failed request is unknown,
not zero; its root cause was not established. The
[failed session](../results/comparison-gemini-59800416-1792047720663158387/)
and both jobs' logs and accounting records are retained.

The [analysis outputs](../results/policy-cost-analysis-59936561/) explicitly
scope their six sessions to job `59936561` and identify the earlier failure.
The full-results `--strict` command above will flag the retained incomplete
session; that is expected and must not be resolved by deleting its evidence.

This was a live cost-recorder validation with three pairs in one allocation.
It does not establish a Gemini performance advantage. The comparison launcher
used a mutable image tag; this was not the earlier pinned-image confirmation
protocol. There was no separate fixed E3/B3 arm in this run. Instrumentation,
request settings, and timing boundaries differ from the historical pilot, so
these reports are kept separate. A future frozen study should include the
fixed control, multiple independent allocations, and repeated evaluation of
selected configurations. Memory optimization and adaptation of a running
application remain unimplemented research goals.
