#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1

if [[ ! -x ".venv/bin/python" ]]; then
  echo "错误：没有找到项目虚拟环境："
  echo "$PROJECT_ROOT/.venv"
  echo
  echo "请先运行："
  echo "bash $PROJECT_ROOT/scripts/setup-wsl.sh"
  exit 1
fi

if [[ ! -f "src/pokemon_go_cleanup/gui.py" ]]; then
  echo "错误：没有找到 GUI 文件："
  echo "$PROJECT_ROOT/src/pokemon_go_cleanup/gui.py"
  exit 1
fi

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "错误：当前 WSL 没有可用的图形显示环境。"
  echo "请确认使用 WSL 2，并在 Windows PowerShell 中运行："
  echo "wsl --update"
  echo "wsl --shutdown"
  exit 1
fi

exec .venv/bin/python -m pokemon_go_cleanup.gui
