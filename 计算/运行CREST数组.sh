#!/bin/bash
#SBATCH --job-name=tpu_crest
#SBATCH --partition=192c
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=1-00:00:00
#SBATCH --chdir=/home/zhanhao/TPU高通量筛选
#SBATCH --output=计算/日志/crest_%j.log

set -euo pipefail

ROOT="/home/zhanhao/TPU高通量筛选"
CALC_ROOT="$ROOT/计算"
ENV_ROOT="/home/zhanhao/software/quantum-cpu"

source "/home/zhanhao/software/miniforge3/etc/profile.d/conda.sh"
conda activate "$ENV_ROOT"

THREADS_PER_TASK=4
MAX_PARALLEL_TASKS=$((SLURM_CPUS_PER_TASK / THREADS_PER_TASK))

export OMP_NUM_THREADS="$THREADS_PER_TASK"
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export OMP_STACKSIZE=2G

cd "$ROOT"
mkdir -p "$CALC_ROOT/结果" "$CALC_ROOT/日志"

run_one() {
  python "代码/运行CREST任务.py" \
  --根目录 "$CALC_ROOT" \
  --索引 "$1" \
  --线程 "$THREADS_PER_TASK" \
  --crest "$ENV_ROOT/bin/crest"
}

# 同一节点分配内先验证两类代表性构件；任一失败都会阻止全量阶段。
run_one 0 &
SMOKE_PID_1=$!
run_one 44 &
SMOKE_PID_2=$!
wait "$SMOKE_PID_1"
wait "$SMOKE_PID_2"

export -f run_one
export ROOT CALC_ROOT ENV_ROOT THREADS_PER_TASK
seq 0 85 | xargs -P "$MAX_PARALLEL_TASKS" -I {} bash -c 'run_one "$1"' _ {}

python "代码/汇总CREST结果.py" --根目录 "$CALC_ROOT"
