#!/bin/bash
#SBATCH --account=m5289
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=2
#SBATCH --time=00:10:00
#SBATCH --job-name=chia-nwchem-check
#SBATCH --output=results/nwchem-check-%j.log
#SBATCH --error=results/nwchem-check-%j.err
#SBATCH --image=ghcr.io/nwchemgit/nwchem-720.nersc.mpich4.mpi-pr:latest

set -euo pipefail

project_dir="${SLURM_SUBMIT_DIR}"
run_dir="$(mktemp -d "${SCRATCH}/chia-agentic-memscale-check-${SLURM_JOB_ID}-XXXXXX")"

cp "${project_dir}/workload/inputs/water_check.nw" "${run_dir}/"
cd "${run_dir}"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export COMEX_MAX_NB_OUTSTANDING=6
export FI_CXI_RX_MATCH_MODE=hybrid
export COMEX_EAGER_THRESHOLD=16384
export FI_CXI_RDZV_THRESHOLD=16384
export FI_CXI_OFLOW_BUF_COUNT=6
export MPICH_SMP_SINGLE_COPY_MODE=CMA

echo "Compute node: $(hostname)"
echo "Run directory: ${run_dir}"

srun --cpu-bind=cores --kill-on-bad-exit=1 \
  shifter --module=mpich nwchem water_check.nw > nwchem.out 2>&1

cat nwchem.out

python - <<'PYTHON'
import json
import math
import os
import re
from pathlib import Path

text = Path("nwchem.out").read_text(errors="replace")
matches = re.findall(
    r"Total SCF energy\s*=\s*([-+0-9.EeDd]+)",
    text,
)

if not matches:
    raise SystemExit("FAIL: no final SCF energy found")

energy = float(matches[-1].replace("D", "E").replace("d", "e"))
reference = -75.983998
passed = math.isfinite(energy) and abs(energy - reference) < 1e-5

record = {
    "test": "water_6-31g_scf",
    "job_id": os.environ.get("SLURM_JOB_ID"),
    "energy_hartree": energy,
    "reference_energy_hartree": reference,
    "tolerance_hartree": 1e-5,
    "passed": passed,
    "run_directory": str(Path.cwd()),
}

Path("validation.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))

if not passed:
    raise SystemExit("FAIL: energy does not match reference")

print("PASS: real NWChem calculation matches the reference energy")
PYTHON
