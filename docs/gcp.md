# Reproduce the GCP native agent loop

## Validated environment

Four independent Gemini/CHIA trajectories completed on September 23, 2026 on an
Ubuntu 22.04.5 x86_64 GCP VM (`n2-standard-16`, 16 logical CPUs / 8 cores,
64 GB advertised RAM, 100 GB disk, `us-central1-a`). Each run selected four
previously untested configurations within its own session. All 16 agent
experiments passed; see [the results report](gcp-results.md).

The recorded stack used Python 3.10.12, Ray 2.54.0, OpenAI SDK 3.15.0,
patched CHIA revision `16c35e92aaaf9511c6453bf94cd5cf589698f4e3`,
Ubuntu `nwchem-openmpi` / `nwchem-data` 7.0.2-3 and `openmpi-bin` 4.1.2-2ubuntu1.
The NWChem executable is `/usr/bin/nwchem.openmpi`, not `nwchem`.
The Gemini model was `gemini-3.5-flash-lite` through the Gemini Developer API's
OpenAI-compatible endpoint. GCP VM billing and the API key's project billing
are separate considerations; the artifact does not establish which account
paid for API calls or the amount spent.

## Setup from a clean Ubuntu 22.04 VM

Use an x86_64 VM exposing at least eight physical cores to the guest. The
native preflight requires six for three concurrent two-rank calculations,
plus two outside the NWChem CPU sets. This run does not require a GPU,
Slurm, Shifter, or a public MCP server firewall rule.

Run in Bash under a regular user with sudo access:

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip build-essential nwchem-openmpi openmpi-bin util-linux

git clone https://github.com/HPCAI-lab/chia-agentic-memscale.git
cd chia-agentic-memscale
python3 -m venv "$HOME/venvs/chia-memscale"
source "$HOME/venvs/chia-memscale/bin/activate"
python -m pip install --upgrade pip

git clone https://github.com/ucb-bar/chia.git ../chia-memscale-upstream
git -C ../chia-memscale-upstream checkout "$(cat provenance/chia-commit.txt)"
git -C ../chia-memscale-upstream apply --check "$PWD/provenance/gemini-tool-signature.patch"
git -C ../chia-memscale-upstream apply "$PWD/provenance/gemini-tool-signature.patch"
python -m pip install -c provenance/gcp-python-constraints.txt -e ../chia-memscale-upstream 'ray==2.54.0' 'openai==3.15.0'
python -m pip check
python -m unittest discover -s tests -p 'test_*.py' -v
```

The original clean VM setup and all 11 tests passed. The constraints file was
subsequently derived from all four matching recorded package snapshots; the
exact revised constrained-install command has not been rerun on a second clean
VM. It excludes CHIA's editable requirement so that the local compatibility
patch remains installed. Apt packages, build tools, and remote model availability
are not made immutable by these Python constraints. Check `dpkg-query -W
nwchem-openmpi nwchem-data openmpi-bin` and record differences.

## Check the real workload

```bash
mkdir -p results
check_dir="$(mktemp -d "$PWD/results/gcp-harness-check-XXXXXX")"
timeout --signal=TERM --kill-after=10s 120s \
  python workload/nwchem_benchmark.py \
  --backend native --input workload/inputs/water_check.nw \
  --partitions 3 --executors 3 --replication 1 --batch-size 3 \
  --mpi-tasks 2 --cpus-per-task 1 \
  --scratch-root "$check_dir/cases" --output "$check_dir/measurement.json"
```

Require `status: success`, three completed cases, and `passed: true` for every
case. This is a scripted harness check, not an agent selection.

## Run the agent

Use a Gemini Developer API key with access and quota for the configured model.
Enter it interactively; never put it in tracked code, logs, or a screenshot.

```bash
read -rsp "Paste Gemini API key: " GEMINI_API_KEY
echo
export GEMINI_API_KEY
bash scripts/run_agent_loop_gcp.sh
```

For a run that continues after a browser SSH disconnect:

```bash
agent_log="$PWD/results/gcp-agent-$(date -u +%Y%m%dT%H%M%SZ).log"
nohup bash scripts/run_agent_loop_gcp.sh > "$agent_log" 2>&1 < /dev/null &
echo "Started PID: $!; log: $agent_log"
tail -f "$agent_log"
```

Ctrl+C stops `tail`, while the background run continues. The wrapper has a
22-minute timeout. A new SSH shell needs the virtual environment reactivated
and the API key re-entered before starting another run.

Each launch creates a unique `results/agent-gcp-*` directory containing a
warm-up, fresh baseline, four selections, raw outputs, measurements, events,
receipts, validation, and provenance. Expect `passed: true`, four measured agent
experiments and `final_receipt_reported: true` in `validation.json`.
Run independent trajectories sequentially to avoid concurrent benchmark load.
Decisions and performance need not match the published runs.

## Scheduling and measurement scope

Every experiment runs exactly three independent water SCF calculations.
Executors limit concurrency and batch size limits cases per wave. Concurrency
is at most `min(executors, batch_size, 3)`: `1E/B2` and `2E/B1` both execute
serially; `2E/B2` uses two waves; `3E/B3` can run all three together.
These are Python harness scheduling controls. The CHIA tool executes in a Ray
worker; executors are not distinct Ray nodes or separate cloud VMs.

Each calculation uses two MPI ranks and one numerical-library thread per rank.
The launcher requests disjoint two-core sets with `taskset` and disables MPI's
own binding. Two guest-visible cores remain outside those sets; CHIA/Ray are
not pinned, so this does not prove exclusive core ownership or isolation from
other processes. Affinity commands and topology are recorded, not a sampled
trace of where every rank actually ran.

Throughput measures three cases divided by harness elapsed time, including
case setup and process launch. It excludes Gemini response time and most loop
startup; `session_elapsed_s` is reported separately. No Perlmutter timings or
previous GCP session results are fed into a fresh native session. NWChem 7.0.2
here differs from Perlmutter's 7.2.0, and launch mechanisms and hardware differ.
Do not interpret their absolute throughput difference as a platform speedup.

## Audit and preserve evidence

```bash
python scripts/summarize_gcp.py
```

This standard-library-only script checks the four specific published sessions,
source hashes, event order, unique choices, receipt chains, final receipts,
measurement consistency and all 72 raw SCF outputs (including warm-ups).
It regenerates summary CSV/JSON files and a checksum manifest. Historical
absolute VM paths in records are retained; the auditor resolves raw outputs
relative to the local session directory. Source changes after publication can
cause the audit to fail, so use the publication commit for historical checking.

Publish the files listed in `results/gcp-summary/publication-files.txt`, along
with the summary directory and source. The list retains raw stdout and input
files but omits regenerable NWChem scratch databases and matrices. Keep an
external backup before the temporary account expires.
