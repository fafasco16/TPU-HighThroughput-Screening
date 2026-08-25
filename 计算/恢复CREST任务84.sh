#!/bin/bash
#SBATCH --job-name=tpu_crest_84
#SBATCH --partition=192c,256c
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=2-00:00:00
#SBATCH --chdir=/home/zhanhao/TPU高通量筛选
#SBATCH --output=计算/日志/crest84_%j.log

set -euo pipefail

ROOT="/home/zhanhao/TPU高通量筛选"
CALC_ROOT="$ROOT/计算"
ENV_ROOT="/home/zhanhao/software/quantum-cpu"

source "/home/zhanhao/software/miniforge3/etc/profile.d/conda.sh"
conda activate "$ENV_ROOT"

export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export OMP_STACKSIZE=2G

cd "$ROOT"
mkdir -p "$CALC_ROOT/结果" "$CALC_ROOT/日志"

# 旧attempt目录永不覆盖。包装器会把本次运行写入下一个attempt编号，
# 只有退出码为0、结果文件存在且输入哈希匹配时才写completed终态。
python "代码/运行CREST任务.py" \
  --根目录 "$CALC_ROOT" \
  --索引 84 \
  --线程 "$SLURM_CPUS_PER_TASK" \
  --crest "$ENV_ROOT/bin/crest"

python "代码/汇总CREST结果.py" \
  --根目录 "$CALC_ROOT" \
  --输出 "$CALC_ROOT/CREST恢复任务84_${SLURM_JOB_ID}.csv"
