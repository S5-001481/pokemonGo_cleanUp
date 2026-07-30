# pokemonGo_cleanUp

````md
[English](README.md) | 简体中文

目标：用ADB截取安卓设备上的Pokémon GO每只宝可梦的详情页面与IV条，帮助整理现有宝可梦的CP，HP，技能，IV等。

## 快速开始

本项目目前推荐在 Windows 的 WSL Ubuntu 环境中运行。

打开 Ubuntu 终端，依次执行：

```bash
git clone https://github.com/S5-001481/pokemonGo_cleanUp.git
cd pokemonGo_cleanUp
bash scripts/setup-wsl.sh
```

安装成功后，终端会显示：

```text
安装完成。

Windows 桌面已经创建：
Pokémon GO Cleanup.bat

以后双击这个图标即可启动。
```

此后可以直接双击 Windows 桌面上的 `Pokémon GO Cleanup.bat` 启动图形界面。

## 界面示例
<img width="1378" height="1142" alt="image" src="https://github.com/user-attachments/assets/44c9fb24-6dec-4de5-a618-96fb26fbc148" />





> [!IMPORTANT]
> OCR 和自动扫描并不是通用功能。目前只针对以下环境完成了校准：
> **Huawei Mate 30、1440 × 3120 分辨率、繁体中文 Pokémon GO 界面、
> WSL Ubuntu、Python 3.12，以及能够从 WSL 调用的 Windows
> `adb.exe`**。



## 兼容范围

| 功能 | 当前支持环境 |
| --- | --- |
| 设备查看与屏幕截图 | Windows 10/11、Python 3.12+、兼容 ADB 的 Android 或 HarmonyOS 设备 |
| 手动引导式扫描 | 与上项相同 |
| OCR 与 IV 识别 | Huawei Mate 30、1440 × 3120、繁体中文界面、WSL Ubuntu、Python 3.12 |
| 自动单只与批量扫描 | 与上项相同，并且 WSL 能调用 Windows `adb.exe` |

所需软硬件：

- Python 3.12 或更高版本；
- Android SDK Platform-Tools（包含 `adb`）；
- 支持数据传输的 USB 线；
- 已在手机上启用 USB 调试。

## 在 WSL Ubuntu 中快速开始

以下命令假设仓库位于
`/home/zhang/projects/pokemonGo_cleanUp`。

```bash
cd /home/zhang/projects/pokemonGo_cleanUp

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev,ocr]"

pokemon-go-cleanup --help
```

如果 WSL 的 `PATH` 中找不到 Windows Platform-Tools，请在子命令之前传入
`adb.exe` 的 WSL 路径：

```bash
pokemon-go-cleanup \
  --adb-path /mnt/c/Android/platform-tools/adb.exe \
  device list
```

识别和自动扫描命令也使用同一个全局选项：

```bash
pokemon-go-cleanup \
  --adb-path /mnt/c/Android/platform-tools/adb.exe \
  scan-auto-one --dry-run --debug
```

## 在 Windows PowerShell 中安装

安装 Python 3.12 或更高版本，然后在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

pokemon-go-cleanup --help
```

如果 PowerShell 阻止虚拟环境激活脚本，可以直接调用虚拟环境中的程序：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\pokemon-go-cleanup.exe --help
```

从
[Android 官方开发者页面](https://developer.android.com/tools/releases/platform-tools)
下载最新稳定版 **SDK Platform-Tools for Windows**，解压到固定目录，例如
`C:\Android\platform-tools`，然后验证：

```powershell
adb version
adb devices
```

也可以明确指定可执行文件：

```powershell
pokemon-go-cleanup `
  --adb-path "C:\Android\platform-tools\adb.exe" `
  device list
```

## 准备手机

不同设备和系统版本的菜单名称可能略有差异。Huawei 设备的常见操作路径是：

1. 打开 **设置 > 关于手机**。
2. 连续点击 **版本号**，直到开发者模式启用。
3. 打开 **设置 > 系统和更新 > 开发人员选项**。
4. 启用 **USB 调试**。
5. 使用支持数据传输的 USB 线连接已解锁的手机，并在提示时选择
   **传输文件**。
6. 运行 `adb devices`。
7. 在手机上核对 RSA 指纹，并允许这台可信计算机。
8. 再次运行 `adb devices`，设备状态应为 `device`。

如果状态是 `unauthorized`，请解锁手机、撤销旧的 USB 调试授权、重新连接，
然后接受新的授权提示。

## 命令概览

### 查看设备

```bash
pokemon-go-cleanup device list
pokemon-go-cleanup device info
pokemon-go-cleanup device info --serial ABC123
```

`device list` 会显示可用、未授权和离线设备。如果同时存在多台可用设备，请使用
`--serial` 选择目标设备。

### 截取一个页面

```bash
pokemon-go-cleanup capture
pokemon-go-cleanup capture --serial ABC123
```

每次截图会生成一个 PNG 和一个 UTF-8 JSON sidecar，其中包含截取时间、设备
序列号、分辨率、文件路径、文件大小和 SHA-256 摘要。

如需使用其他本地数据目录，全局选项必须写在子命令之前：

```bash
pokemon-go-cleanup --data-dir "/path/to/local-data" capture
```

### 手动执行三页面扫描

```bash
pokemon-go-cleanup scan-one --guided
```

每次截图前，命令都会等待用户按 Enter。请手动准备以下页面：

1. `summary.png`：宝可梦详情页顶部；
2. `moves.png`：全部招式可见的详情页面；
3. `appraisal.png`：攻击、防御和 HP 条都可见的评价页面。

手动模式不会发送点击、滑动或 BACK 输入。

可选参数：

```bash
pokemon-go-cleanup scan-one --guided \
  --serial ABC123 \
  --notes "社区日" \
  --output "/path/to/local-data"
```

`--output` 表示本次扫描的数据目录基准，最终文件位于
`OUTPUT/scans/` 下。

### 读取一组已校准扫描

先安装 OCR 可选依赖：

```bash
python -m pip install -e ".[ocr]"
```

然后读取完整的三页面扫描：

```bash
pokemon-go-cleanup read-scan "data/scans/YYYY-MM-DD/<scan_id>"
pokemon-go-cleanup read-scan "data/scans/YYYY-MM-DD/<scan_id>" --debug
```

名称、CP 和招式使用 RapidOCR；IV 使用 OpenCV 的颜色与条形几何分析，而不是
OCR。无法识别或置信度较低的值会保留，并附带 warning。

`--debug` 会把 OCR 裁剪和 IV 检测覆盖图保存到该扫描目录下已被 Git 忽略的
`debug/` 目录。

### 从完整扫描生成 CSV

```bash
pokemon-go-cleanup read-dataset --csv inventory.csv
```

命令会递归查找当前 `data/scans/` 根目录中的完整扫描，每组写入一行，并保存对应
的 `recognition.json`。

### 自动扫描一只宝可梦

首先手动打开一只符合校准条件的宝可梦详情页顶部。

每次都应先运行完全不发送设备输入的 dry run：

```bash
pokemon-go-cleanup scan-auto-one --dry-run --debug
```

dry run 会截取并判断当前页面、打印计划操作，并生成一组供检查的 incomplete
扫描。它不会发送 tap、swipe 或 BACK。

允许实时输入前，请检查：

```text
data/scans/YYYY-MM-DD/<scan_id>/
├── manifest.json
└── debug/
    └── automation/
        └── plan.json
```

确认 dry run 与真实页面一致后，再执行：

```bash
pokemon-go-cleanup scan-auto-one --debug
```

实时状态机会在每次输入前检查页面。成功时会保存三张截图、安全退出评价、生成
`recognition.json`，并把 manifest 标记为 complete。遇到页面不符、超时、失败
或 Ctrl+C 时会停止，保留已有证据，并把 manifest 标记为 incomplete。

### 扫描有限数量的连续宝可梦

请先打开第一只宝可梦的详情页顶部。首次使用时先进行小数量实机校准：

```bash
pokemon-go-cleanup scan-batch \
  --limit 2 \
  --csv batch-test.csv \
  --debug \
  --delay 2
```

每只扫描完成后，命令会安全退出评价、原子更新 CSV、确认已经回到详情页，再左滑
到下一只。只有页面状态和本地指纹都表明宝可梦已经变化时，程序才会继续。

默认上限是 20。已有 CSV 不会被静默覆盖，只有明确传入 `--resume` 才会继续：

```bash
pokemon-go-cleanup scan-batch \
  --limit 20 \
  --csv inventory.csv \
  --resume \
  --debug
```

Ctrl+C 或后续扫描失败时，之前已经完成的 CSV 行和扫描文件都会保留。

### 校验本地数据

```bash
pokemon-go-cleanup dataset status
pokemon-go-cleanup dataset validate
```

扫描根目录不在默认位置时使用 `--output`：

```bash
pokemon-go-cleanup dataset validate \
  --output "/path/to/local-data/scans"
```

扫描状态：

- `complete`：必需文件齐全且全部有效；
- `incomplete`：缺少必需文件，或 manifest 尚未 complete；
- `invalid`：PNG 解码、图片尺寸、JSON、manifest 或标注校验失败。

只要存在至少一组 invalid 扫描，`dataset validate` 就会返回非零退出码。

### 添加人工 ground truth

```bash
pokemon-go-cleanup annotate "data/scans/YYYY-MM-DD/<scan_id>"
```

交互式命令会显示三张截图的完整路径，并逐项询问带类型的标注值。已有有效值时，
可以直接按 Enter 保留。

非交互式输入：

```bash
pokemon-go-cleanup annotate \
  "data/scans/YYYY-MM-DD/<scan_id>" \
  --input-json annotation-input.json
```

只有在明确需要无确认覆盖现有 `ground_truth.json` 时才使用 `--force`。

## 配置

全局选项必须写在子命令之前：

```bash
pokemon-go-cleanup \
  --data-dir "/path/to/local-data" \
  --adb-path "/path/to/adb.exe" \
  --adb-timeout 30 \
  --log-level INFO \
  --log-format text \
  device list
```

环境变量：

| 变量 | 用途 |
| --- | --- |
| `POKEMON_GO_CLEANUP_DATA_DIR` | 本地截图和扫描数据目录 |
| `POKEMON_GO_CLEANUP_ADB_PATH` | 明确指定 `adb` 或 `adb.exe` 路径 |
| `POKEMON_GO_CLEANUP_ADB_TIMEOUT_SECONDS` | ADB 命令超时秒数 |

## 本地数据目录

一组完整的手动或自动扫描结构如下：

```text
data/
└── scans/
    └── YYYY-MM-DD/
        └── <scan_id>/
            ├── summary.png
            ├── moves.png
            ├── appraisal.png
            ├── manifest.json
            ├── recognition.json   # 由识别命令创建
            ├── ground_truth.json  # 可选，由 annotate 创建
            └── debug/             # 可选，已被 Git 忽略
```

单次截图的 PNG 与同名 JSON sidecar 位于：

```text
data/screenshots/YYYY-MM-DD/
```

`manifest.json` 记录扫描进度与设备元数据；
`recognition.json` 保存机器识别值和 warning；
`ground_truth.json` 独立保存经过校验的人工输入值。

## 安全、隐私与功能边界
仓库不包含任何 Pokémon 图片、图标或截图。Pokémon 是其权利人的商标；本项目是
独立项目，与其权利人没有隶属或背书关系。

## 许可证
本项目采用 [Apache License 2.0](LICENSE)。
