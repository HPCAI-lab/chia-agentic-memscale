# Four agent-selected NWChem experiments

This implements the requested sequence: Gemini inspects history, selects an
untested configuration, calls a CHIA MCP tool, receives measurements, and selects
the next configuration. Live validation passed in Perlmutter job 58656462 on September 20, 2026.

The earlier 51.5% throughput difference is a manual configuration comparison.
Those measurements remain historical context. They are never relabeled as
agent-selected points in the new trajectory.

## Run from the Perlmutter login shell

```bash
cd ~/chia-agentic-memscale
module load python
conda activate chia-memscale
mkdir -p results
python -m unittest discover -s tests -p 'test_agent_session.py' -v
python -m py_compile loop.py tools/experiment_session.py tools/memscale_tool.py
bash -n scripts/run_agent_loop.sbatch
read -rsp "Paste Gemini API key: " GEMINI_API_KEY
echo
export GEMINI_API_KEY
sbatch --export=ALL scripts/run_agent_loop.sbatch
```

The batch job activates the host Conda environment and uses Shifter for each
NWChem calculation. It reserves eight task slots with two CPUs each. Each
calculation requests two MPI ranks and two CPUs per rank; at most three
calculations run concurrently. The image must already be READY in Shifter.
The CHIA thought-signature patch and previously verified Gemini API access are
required. No additional Python dependencies are introduced by this loop.

The script has a 25-minute allocation and a 22-minute outer timeout. Each
experiment is limited to 120 seconds. The agent conversation has a 960-second
wait limit, no automatic request retries, and a tool-iteration limit.

## Experimental meaning

- Fixed work: exactly three independent water 6-31g SCF calculations per experiment.
- `partitions=3` counts independent calculations; `replication=1` stays fixed.
- Gemini chooses `executors` from 1, 2, 3 and `batch_size` from 1, 2, 3.
- Executors limit concurrent launches; batch size determines cases per wave.
- These are harness scheduling controls, not modifications of NWChem internals.
- Configurations with the same effective concurrency may behave similarly. That
  is a legitimate result, and the search space is deliberately small.
- One warm-up is excluded, then step 0 remeasures 3P / 1E / B1 on the same node.
- Steps 1–4 are chosen by Gemini, with no fallback that supplies choices for it.
- The original two three-case manual configurations are excluded from new choices.
- Objective: maximize measured completed cases per second; all three energies
  must match the reference within 1e-5 Hartree.
- Latency and elapsed time include Slurm/container startup. Memory remains null.

## Evidence and output

Each job creates a fresh `results/agent-JOBID-TIMESTAMP/` directory containing:

- `trajectory.md` and `trajectory.csv`: fresh scripted baseline plus agent steps.
- `state.json`: manual history, baseline, chosen configurations and measurements.
- `events.jsonl`: history reads, selections recorded before execution, outcomes.
- `best_config.json`: best observed result including the baseline if it wins.
- `step-N/measurement.json` and `step-N/launcher.log`: raw harness measurements/logs.
- `agent_response.txt`: the final model response, when available.
- `validation.json`: live completion checks; `failure.json` if an exception occurs.
- `provenance.json`: model, image tag, recorded image ID, job/node, source hashes.

The tool requires the latest unique receipt on the next call. This rejects stale
parallel proposals, provides evidence of sequential observation, and is checked
together with unique configurations and a hard experiment budget. Failures also
consume budget. The model must include the final receipt in its response.
The tool exposes only history and bounded experiments, with no shell tool.

Success is printed only when all requested agent experiments pass scientific
validation, run in a separate CHIA worker, and the final receipt is reported.
Partial progress is preserved if the API, allocation, or workload fails; a
partial run is not marked as complete. A new submission starts a new session.

## Reporting

Report both the baseline and actual agent-selected points. Four successful
adaptive selections demonstrate an agentic loop even if throughput does not
improve. Do not transfer the historical 51.5% result to the agent's trajectory.
This is a small water-SCF orchestration demonstration. Repeated controlled
trials, larger inputs, memory measurements, and search baselines are separate
work needed for stronger performance or memory co-design claims.

The original result files are retained. New session results remain ignored by
Git until explicitly selected for publication with `git add -f`.
