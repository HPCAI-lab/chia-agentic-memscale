# Small equal-budget policy comparison

Run `sbatch scripts/run_policy_comparison.sbatch` from the project root with
GEMINI_API_KEY exported and the existing Perlmutter environment activated.
This is a new experiment; previous GCP and Perlmutter trajectories are not controls.

Three pairs run sequentially in one allocation. Order is Gemini/random,
random/Gemini, Gemini/random. Random seeds are 101, 102, 103, predeclared.
Each session has one warmup, one fresh E1/B1 baseline, then four search attempts.
Both policies start with empty historical context and the same eight remaining
E=1..3, B=1..3 combinations. P=3, replication=1, input, image, resources and
scientific validation are fixed. Sampling is uniform without replacement.
Failures consume a search attempt. A failed session stops the batch for inspection;
retain it and disclose any reruns rather than silently replacing it.

Gemini runs through CHIA MCP. Random runs through a one-CPU Ray actor calling
the same ExperimentSession and NWChem runner. Thus execution/validation is shared,
but decision transport differs. Policy end-to-end times include that difference.
Both initialize Ray with four CPUs; experiments run sequentially across sessions.
Each policy uses its own freshly measured baseline configuration, not the identical
numerical baseline measurement. Random seeds do not make Gemini deterministic.

Every session writes provenance, a snapshot of executed source, raw measurements,
receipts, events, and best-so-far trajectory CSV. The old GCP audit checks current
source hashes, so it will not pass against the modified session module; audit those
historical results in their original published checkout.

Report best-so-far including baseline, first strictly measured improvement (noise
can cause small changes), first step attaining session best, validation counts and
per-policy mean/sample SD across the three sessions. These are a small exploratory
sample, not proof that either policy is generally superior. Session time includes
warmup, baseline, startup, decisions, launches, and tool traffic; do not label it
pure Gemini latency. The workload measures small-water orchestration throughput,
not memory optimization or larger-system scaling.

Local verification: 13 mock/control-flow tests passed; Python syntax and shell
syntax checked. Live Gemini authentication, CHIA/Ray execution and Slurm scheduling
must still be validated on Perlmutter.
