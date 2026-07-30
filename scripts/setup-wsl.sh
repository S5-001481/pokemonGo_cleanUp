#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f "pyproject.toml" ]]; then
  echo "错误：项目根目录中没有找到 pyproject.toml："
  echo "$PROJECT_ROOT"
  exit 1
fi

if [[ ! -f "src/pokemon_go_cleanup/gui.py" ]]; then
  echo "错误：没有找到 GUI 文件："
  echo "$PROJECT_ROOT/src/pokemon_go_cleanup/gui.py"
  exit 1
fi

echo "===== 1/5 安装 Ubuntu 系统依赖 ====="
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive \
  apt-get install -y \
  python3-venv \
  python3-tk

echo
echo "===== 2/5 建立 Python 虚拟环境 ====="
if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
  echo "已建立：$PROJECT_ROOT/.venv"
else
  echo "虚拟环境已经存在，继续使用：$PROJECT_ROOT/.venv"
fi

echo
echo "===== 3/5 安装项目和 OCR 依赖 ====="
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[ocr]"

echo
echo "===== 4/5 查找 Windows ADB ====="

WINDOWS_ADB=""

if command -v powershell.exe >/dev/null 2>&1; then
  WINDOWS_ADB="$(
    powershell.exe -NoProfile -Command '
      [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

      $candidates = @()

      $command = Get-Command adb.exe -ErrorAction SilentlyContinue
      if ($command) {
        $candidates += $command.Source
      }

      $candidates += "C:\Tools\platform-tools\adb.exe"
      $candidates += (
        Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
      )

      $candidates |
        Where-Object { $_ -and (Test-Path $_) } |
        Select-Object -First 1
    ' |
      tr -d '\r' |
      sed '/^[[:space:]]*$/d' |
      tail -n 1
  )"
fi

if [[ -n "$WINDOWS_ADB" ]]; then
  WSL_ADB="$(wslpath -u "$WINDOWS_ADB")"

  mkdir -p "$HOME/.local/bin"

  {
    printf '#!/usr/bin/env bash\n'
    printf 'exec %q "$@"\n' "$WSL_ADB"
  } > "$HOME/.local/bin/adb"

  chmod +x "$HOME/.local/bin/adb"

  echo "已找到 Windows ADB："
  echo "$WINDOWS_ADB"
  echo
  echo "已建立 WSL 转发："
  echo "$HOME/.local/bin/adb"
else
  echo "警告：没有找到 Windows adb.exe。"
  echo
  echo "请安装 Android Platform-Tools，例如解压到："
  echo 'C:\Tools\platform-tools'
  echo
  echo "安装后重新运行："
  echo "bash scripts/setup-wsl.sh"
fi

echo
echo "===== 5/5 创建 Windows 桌面启动器 ====="

chmod +x \
  scripts/setup-wsl.sh \
  scripts/start-gui.sh \
  scripts/create-windows-launcher.sh

bash scripts/create-windows-launcher.sh

echo
echo "========================================"
echo "安装完成"
echo "========================================"
echo
echo "以后可以双击 Windows 桌面的："
echo "Pokémon GO Cleanup.bat"
echo
echo "也可以在 Ubuntu 中运行："
echo "bash $PROJECT_ROOT/scripts/start-gui.sh"
echo
echo "启动前请确认："
echo "1. 手机已连接数据线"
echo "2. 已开启 USB 调试"
echo "3. 手机上已允许当前电脑的 RSA 授权"
echo "4. adb devices 显示的状态是 device"
