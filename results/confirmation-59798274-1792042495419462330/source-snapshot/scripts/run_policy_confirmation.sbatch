#!/bin/bash
#SBATCH --account=m5289
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=2
#SBATCH --hint=nomultithread
#SBATCH --time=00:25:00
#SBATCH --job-name=chia-confirm
#SBATCH --output=results/policy-confirm-%j.log
#SBATCH --error=results/policy-confirm-%j.err

# Submit via run_policy_confirmation.py submit: it supplies the frozen image ID.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
plan_path=${1:?Pass the frozen plan path}
results_path=${2:?Pass the results directory}
module load python
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate chia-memscale

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export COMEX_MAX_NB_OUTSTANDING=6
export FI_CXI_RX_MATCH_MODE=hybrid
export COMEX_EAGER_THRESHOLD=16384
export FI_CXI_RDZV_THRESHOLD=16384
export FI_CXI_OFLOW_BUF_COUNT=6
export MPICH_SMP_SINGLE_COPY_MODE=CMA

timeout --signal=INT --kill-after=30s 22m python -u scripts/run_policy_confirmation.py run \
    --plan "$plan_path" --results-root "$results_path"
