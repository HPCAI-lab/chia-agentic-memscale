#!/bin/bash
set -euo pipefail
for pair in 1 2 3; do
    seed=$((100 + pair))
    if (( pair % 2 )); then
        policies=(gemini random)
    else
        policies=(random gemini)
    fi
    for policy in "${policies[@]}"; do
        echo "PAIR=$pair POLICY=$policy SEED=$seed"
        python -u compare_loop.py --policy "$policy" --seed "$seed" --experiments 4
    done
done
