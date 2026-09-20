#!/bin/bash
#SBATCH --account=m5289
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=2
#SBATCH --time=00:15:00
#SBATCH --job-name=chia-nwchem-bench
#SBATCH --output=results/nwchem-bench-%j.log
#SBATCH --error=results/nwchem-bench-%j.err
#SBATCH --image=ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"

module load python

partitions="${PARTITIONS:-1}"
executors="${EXECUTORS:-1}"
replication="${REPLICATION:-1}"
batch_size="${BATCH_SIZE:-1}"
output="${OUTPUT_JSON:-results/benchmark-${SLURM_JOB_ID}.json}"

echo "Compute node: $(hostname)"
echo "Configuration: partitions=${partitions} executors=${executors} replication=${replication} batch_size=${batch_size}"

python workload/nwchem_benchmark.py \
  --input workload/inputs/water_check.nw \
  --image ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest \
  --partitions "${partitions}" \
  --executors "${executors}" \
  --replication "${replication}" \
  --batch-size "${batch_size}" \
  --placement single-node \
  --mpi-tasks 2 \
  --cpus-per-task 2 \
  --output "${output}"
