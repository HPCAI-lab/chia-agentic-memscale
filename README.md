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

Scientific workload integration and optimization experiments remain pending.
The multiplication check is an infrastructure test, not a performance result.

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
