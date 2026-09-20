# CHIA Agentic MemScale

Agent-driven memory and runtime optimization for scientific workloads
using CHIA on NERSC Perlmutter.

## Verified progress

- Created the chia-memscale Conda environment with Python 3.10.19.
- Installed CHIA and verified execution in a separate Ray worker.
- Connected to Gemini using gemini-3.5-flash-lite.
- Fixed CHIA tool-call serialization to preserve Gemini thought signatures.
- Passed the live Gemini -> CHIA MCP tool -> Ray worker integration check.

On September 19, 2026, Gemini called the multiplication tool on nid004199.
The tool returned 400 and a unique execution receipt. Gemini reported both
correctly. The test verified worker separation and exactly one tool call.

A real NWChem water-SCF harness has also passed scientific validation. Three
manual benchmark configurations and their measured outputs are committed.
The three-case throughput increased from 0.756051 to 1.145487 cases/s when
moving from one executor / batch 1 to two executors / batch 2 (about 51.5%).
This is a preliminary manual comparison, not an agent-discovered improvement.

The new bounded Gemini/CHIA loop is implemented and awaits live Perlmutter
validation. It measures a fresh baseline and lets Gemini choose four untested
configurations, using returned measurements to select each next experiment.
See [the agent-loop run instructions](docs/agent_loop.md).

## Repository contents

- tests/check_gemini_chia.py: live integration test.
- provenance/chia-commit.txt: upstream CHIA commit used.
- provenance/gemini-tool-signature.patch: local compatibility fix.

## CHIA compatibility fix

Upstream: https://github.com/ucb-bar/chia

The patch preserves complete SDK tool-call objects, including Gemini's
thought signatures, when constructing the next assistant message.

Regression checks passed for standard tool calls and Gemini signature
preservation. The subsequent live integration test also passed.

## Run on Perlmutter

Request an allocation from a login node:

    salloc --account=m5289 --constraint=cpu --qos=interactive --nodes=1 --time=00:20:00

Inside the allocated compute-node shell:

    module load python
    conda activate chia-memscale
    cd ~/chia-agentic-memscale

Enter your API key when prompted. Input remains invisible:

    read -rsp "Paste Gemini API key: " GEMINI_API_KEY
    echo
    export GEMINI_API_KEY

Run the integration test:

    timeout --signal=INT --kill-after=15s 240s python -u tests/check_gemini_chia.py

Requires the patched CHIA installation, the openai Python package,
and Gemini API access with available quota.

Never commit API keys or credential files.

## Validated autonomous experiment trajectory

Perlmutter job `58656462` completed successfully with exit code `0:0`.
Gemini inspected history, selected four untested configurations sequentially,
executed them through CHIA, and received validated NWChem measurements.

| Step | Selected by | Configuration | Throughput (cases/s) |
| --- | --- | --- | --- |
| 0 | Fresh scripted baseline | 3P / 1E / B1 | 0.790497 |
| 1 | Gemini | 3P / 1E / B2 | 0.782891 |
| 2 | Gemini | 3P / 1E / B3 | 0.773051 |
| 3 | Gemini | 3P / 2E / B1 | 0.762315 |
| 4 | Gemini | 3P / 2E / B3 | 1.145505 |

The best agent-selected configuration was 44.9% faster in throughput than
the fresh baseline in this run. This is a preliminary single-run result.
Memory optimization, 1H9T scaling, and global optimality remain future work.

Evidence is stored under
`results/agent-58656462-1789945660187864036/`.
