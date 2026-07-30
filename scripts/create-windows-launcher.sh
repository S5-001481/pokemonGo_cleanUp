#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SCRIPT="$PROJECT_ROOT/scripts/start-gui.sh"

if [[ -z "${WSL_DISTRO_NAME:-}" ]]; then
  echo "错误：无法读取 WSL_DISTRO_NAME。"
  echo "请在 WSL Ubuntu 中运行此脚本。"
  exit 1
fi

if ! command -v powershell.exe >/dev/null 2>&1; then
  echo "错误：无法调用 Windows PowerShell。"
  exit 1
fi

DISTRO_NAME="$WSL_DISTRO_NAME"

WINDOWS_DESKTOP="$(
  powershell.exe -NoProfile -Command \
    '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Environment]::GetFolderPath("Desktop")' |
    tr -d '\r' |
    sed '/^[[:space:]]*$/d' |
    tail -n 1
)"

if [[ -z "$WINDOWS_DESKTOP" ]]; then
  echo "错误：无法读取 Windows 桌面路径。"
  exit 1
fi

DESKTOP_WSL="$(wslpath -u "$WINDOWS_DESKTOP")"

if [[ ! -d "$DESKTOP_WSL" ]]; then
  echo "错误：Windows 桌面目录不存在："
  echo "$DESKTOP_WSL"
  exit 1
fi

if [[ ! -f "$START_SCRIPT" ]]; then
  echo "错误：找不到启动脚本："
  echo "$START_SCRIPT"
  exit 1
fi

chmod +x "$START_SCRIPT"

# 转换成可安全放入 bash -lc 命令字符串的路径。
printf -v PROJECT_ROOT_QUOTED '%q' "$PROJECT_ROOT"
printf -v START_SCRIPT_QUOTED '%q' "$START_SCRIPT"

LAUNCHER_PATH="$DESKTOP_WSL/Pokémon GO Cleanup.bat"

{
  printf '@echo off\r\n'
  printf 'chcp 65001 >nul\r\n'
  printf 'title Pokémon GO Cleanup\r\n'
  printf '\r\n'

  # 必须使用 bash -lc，确保 WSLg 图形环境和登录环境正确加载。
  printf 'wsl.exe -- bash -lc "cd %s && exec bash %s"\r\n' \
    "$PROJECT_ROOT_QUOTED" \
    "$START_SCRIPT_QUOTED"

  printf 'set "EXIT_CODE=%%ERRORLEVEL%%"\r\n'
  printf '\r\n'
  printf 'if not "%%EXIT_CODE%%"=="0" (\r\n'
  printf '  echo.\r\n'
  printf '  echo Pokémon GO Cleanup failed. Exit code: %%EXIT_CODE%%\r\n'
  printf '  pause\r\n'
  printf ')\r\n'
} > "$LAUNCHER_PATH"

echo
echo "Windows 桌面启动器已创建："
echo "$WINDOWS_DESKTOP\\Pokémon GO Cleanup.bat"
echo
echo "WSL 发行版：$DISTRO_NAME"
echo "项目目录：$PROJECT_ROOT"
echo "启动脚本：$START_SCRIPT"
