#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "$0")" && pwd)"
runner="$project_root/batch_analyze_galfits_mcp.sh"

usage() {
  cat <<'EOF'
用法：
  batch_analyze_galfits_workflow_pilot.sh DATA_ROOT RUN_DIR [RUNNER_OPTIONS...]

参数：
  DATA_ROOT  多波段星系数据根目录。
  RUN_DIR    独立的 workflow 状态和审计产物目录；续跑时必须复用同一路径。

常用选项：
  --dry-run              只检查对象和输入，不执行拟合。
  --resume               从 RUN_DIR 的 batch_manifest.json 和对象状态继续。
  --object-id OBJECT_ID  只运行或续跑一个对象。
  --max-objects N        新建 manifest 时只选择排序后的前 N 个对象。

示例：
  # 先检查一个新批次。
  ./batch_analyze_galfits_workflow_pilot.sh /data/galaxies /tmp/my-run --dry-run

  # 使用同一个 RUN_DIR 开始或继续批次。
  ./batch_analyze_galfits_workflow_pilot.sh /data/galaxies /tmp/my-run --resume

  # 只恢复一个对象，不调度 manifest 中的其他对象。
  ./batch_analyze_galfits_workflow_pilot.sh /data/galaxies /tmp/my-run --resume --object-id 104

该脚本固定使用多波段结构化 workflow pilot、VLM 和串行 GPU 默认配置。
实际 Image、SED、Image-SED 拟合仍由项目现有 MCP 工具执行。
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi

data_root="$1"
run_dir="$2"
shift 2

if [[ ! -d "$data_root" ]]; then
  echo "数据根目录不存在：$data_root" >&2
  exit 2
fi

if [[ ! -x "$runner" ]]; then
  echo "底层 workflow runner 不可执行：$runner" >&2
  exit 1
fi

exec "$runner" \
  --root "$data_root" \
  --run-dir "$run_dir" \
  --mode multiband \
  --pilot 1 \
  --use-vlm \
  --resource-profile serial-gpu \
  --max-rounds 10 \
  "$@"
