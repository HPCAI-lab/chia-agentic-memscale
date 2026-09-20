#!/bin/bash
#SBATCH --account=m5289
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=2
#SBATCH --hint=nomultithread
#SBATCH --time=00:25:00
#SBATCH --job-name=chia-nwchem-agent
#SBATCH --output=results/agent-loop-%j.log
#SBATCH --error=results/agent-loop-%j.err
#SBATCH --image=ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
module load python
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate chia-memscale
: "${GEMINI_API_KEY:?Export GEMINI_API_KEY before submitting this job}"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export COMEX_MAX_NB_OUTSTANDING=6
export FI_CXI_RX_MATCH_MODE=hybrid
export COMEX_EAGER_THRESHOLD=16384
export FI_CXI_RDZV_THRESHOLD=16384
export FI_CXI_OFLOW_BUF_COUNT=6
export MPICH_SMP_SINGLE_COPY_MODE=CMA

timeout --signal=INT --kill-after=20s 22m python -u loop.py --experiments 4
