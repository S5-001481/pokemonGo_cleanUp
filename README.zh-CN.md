# pokemonGo_cleanUp

[English](README.md) | 简体中文

`pokemonGo_cleanUp` 是一个本地优先的 Windows 命令行工具。它通过 ADB
截取 Android 或 HarmonyOS 设备的当前屏幕，并将 PNG 截图保存在本地。
每张截图旁边会生成一个 UTF-8 JSON 文件，记录设备序列号、分辨率、截取时间、
文件路径、文件大小和 SHA-256 摘要。
引导式扫描还可以把同一台设备上的详情顶部、招式和评价截图归入同一个扫描 ID，
并用一份持续更新的 manifest 记录整个过程。
数据集命令会解码并验证这些本地文件；人工标注命令可以在不使用 OCR 的情况下
写入类型严格的 ground truth。

Python 包名是 `pokemon_go_cleanup`，安装后的命令是
`pokemon-go-cleanup`。

## 功能范围与隐私

当前版本支持：

- 检测 ADB 是否可用，并列出已连接设备的状态；
- 连接运行兼容 ADB 的 HarmonyOS 系统的 Huawei Mate 30；
- 读取设备当前分辨率；
- 通过 `adb exec-out screencap -p` 截取当前屏幕；
- 保持 `scan-one --guided` 为完全手动模式，不发送设备输入；
- 使用本地 OCR 和 IV 条几何读取固定的 1440x3120 繁体中文界面；
- 为这一个固定界面提供安全状态门控的 `scan-auto-one`；
- 将同一次扫描的三个截图和 manifest 保存在同一目录；
- 递归校验截图、manifest 和已有标注；
- 将用户手动输入的标注保存为可读的 UTF-8 JSON；
- 将截图和元数据保存在本地计算机。

程序不会自动切换下一只、批量扫描、传送、强化、进化、改名、解锁招式或战斗。
它不访问 Pokémon GO 账号或私有 API，不分析网络流量，也不读取登录凭据。
自动输入仅存在于文档所述的单只状态机；页面不匹配时会在下一次输入前停止。
截图和标注可能包含个人信息，因此整个 `data/` 目录及生成的识别/调试文件均被
Git 忽略。分享任何本地文件前请先检查内容。

仓库不包含任何 Pokémon 图片、图标或截图。Pokémon 是其权利人的商标；
本项目是独立项目，与其权利人没有隶属或背书关系。

## 运行要求

- Windows 10 或 Windows 11
- Python 3.12 或更高版本
- Android SDK Platform-Tools（包含 `adb`）
- 支持数据传输的 USB 线
- 已在手机上启用 USB 调试

## 在 Windows 上安装

以下命令应在 **Windows PowerShell** 中运行。

1. 从 [python.org](https://www.python.org/downloads/windows/) 安装 Python
   3.12 或更高版本。安装时启用从命令行运行 Python 的选项。
2. 下载或克隆本仓库，然后在项目根目录打开 PowerShell。
3. 创建独立虚拟环境并安装项目：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pokemon-go-cleanup --help
```

如果 PowerShell 阻止虚拟环境激活脚本，可以不修改执行策略，直接运行虚拟环境
中的程序：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\pokemon-go-cleanup.exe --help
```

## 在 Windows 上安装 Android Platform-Tools

1. 从
   [Android 官方开发者页面](https://developer.android.com/tools/releases/platform-tools)
   下载最新版 **SDK Platform-Tools for Windows**。
2. 将压缩包解压到稳定的本地目录，例如
   `C:\Tools\platform-tools`。
3. 将该目录添加到用户 `Path`，然后关闭并重新打开 PowerShell。
4. 验证安装：

```powershell
adb version
adb devices
```

如果不想修改 `Path`，可以直接指定 `adb.exe`：

```powershell
pokemon-go-cleanup --adb-path "C:\Tools\platform-tools\adb.exe" device list
```

建议使用最新稳定版 Platform-Tools。Google 文档说明，新版 ADB 会保持对较旧
Android 设备的向后兼容。

## 准备 Huawei Mate 30 / HarmonyOS 设备

不同 HarmonyOS 或 EMUI 版本的菜单名称可能略有差异。Huawei 文档提供的常见
操作路径如下：

1. 打开 **设置 > 关于手机**。
2. 连续点击 **版本号**，直到系统提示开发者模式已启用。根据提示输入锁屏密码。
3. 打开 **设置 > 系统和更新 > 开发人员选项**。
4. 启用 **USB 调试**。
5. 使用支持数据传输的 USB 线连接已解锁的手机。如果系统询问 USB 用途，选择
   **传输文件**，不要只选择充电。
6. 在 Windows PowerShell 中运行：

```powershell
adb devices
```

7. 在手机上核对计算机的 RSA 指纹。只有在使用自己的可信计算机时，才选择记住
   此计算机，然后点击 **允许**。
8. 再次运行 `adb devices`。设备状态应为 `device`，而不是
   `unauthorized`。

Huawei 的
[官方设备开发指南](https://developer.huawei.com/consumer/en/codelab/theme/index.html)
也说明了开发者模式和 USB 调试的启用路径。如果找不到对应菜单，可以在系统设置
中搜索“USB 调试”，并查看设备所在地区的 Huawei 支持文档。

如需撤销旧计算机的授权，请在开发人员选项中选择 **撤销 USB 调试授权**，
重新连接 USB 线，然后接受新的指纹提示。

## 命令用法

列出全部设备，包括 `unauthorized` 和 `offline` 状态：

```powershell
pokemon-go-cleanup device list
```

查看唯一可用设备的身份信息和当前分辨率：

```powershell
pokemon-go-cleanup device info
```

截取当前屏幕：

```powershell
pokemon-go-cleanup capture
```

为一只宝可梦执行三截图引导式扫描。每一步都会等待你按 Enter 后再截图：

```powershell
pokemon-go-cleanup scan-one --guided
```

第一次请准备详情页顶部，保存为 `summary.png`；第二次向下滚动到所有招式可见，
保存为 `moves.png`；第三次打开宝可梦评价并推进对话，直到攻击、防御和 HP
个体值条全部可见，再保存为 `appraisal.png`。滚动、打开评价和推进对话都由你在
手机上手动完成，程序不会替你点击或滑动。

可以同时使用本命令的设备、备注和输出目录选项：

```powershell
pokemon-go-cleanup scan-one --guided --serial ABC123 --notes "社区日" --output "D:\Local Captures\宝可梦整理"
```

`--output` 表示本次扫描的数据根目录，最终文件保存在 `OUTPUT/scans/` 下。

如果连接了多台可用设备，请通过 ADB 序列号选择目标设备：

```powershell
pokemon-go-cleanup device info --serial ABC123
pokemon-go-cleanup capture --serial ABC123
```

将截图保存到其他本地目录。全局选项必须写在子命令之前：

```powershell
pokemon-go-cleanup --data-dir "D:\Local Captures\宝可梦整理" capture
```

输出信息级结构化 JSON 日志：

```powershell
pokemon-go-cleanup --log-level INFO --log-format json capture
```

也可以通过环境变量提供配置：

- `POKEMON_GO_CLEANUP_DATA_DIR`
- `POKEMON_GO_CLEANUP_ADB_PATH`
- `POKEMON_GO_CLEANUP_ADB_TIMEOUT_SECONDS`

## 读取已校准的 Huawei 截图（OCR MVP）

本次迭代仅支持现有的 Huawei Mate 30 扫描：1440x3120 像素、繁体中文
Pokémon GO、WSL Ubuntu 和 Python 3.12。请在项目虚拟环境中安装本地 OCR
可选依赖：

```bash
python -m pip install -e ".[ocr]"
```

读取一组扫描并保存 `recognition.json`：

```bash
pokemon-go-cleanup read-scan "data/scans/2026-07-28/<scan_id>"
pokemon-go-cleanup read-scan "data/scans/2026-07-28/<scan_id>" --debug
```

`--debug` 会把五张 OCR 裁剪、原始 IV 条和标注后的 IV 检测图保存到扫描目录
下已被 Git 忽略的 `debug/`。读取所有完整扫描并写出 CSV：

```bash
pokemon-go-cleanup read-dataset --csv inventory.csv
```

名称、CP 和招式使用 RapidOCR；三项 IV 只使用 OpenCV 颜色及条形几何分析，
不会用 OCR 估算。低置信度或无法识别的值会保留并添加 warning，不要求
`ground_truth.json`。

## 自动扫描一只固定界面的宝可梦

该命令只针对 Huawei Mate 30、1440x3120、当前繁体中文界面、WSL Ubuntu、
Python 3.12，以及可从 WSL 调用的 Windows `adb.exe`。运行前请手动打开一只
宝可梦的详情页顶部。

首先执行绝不发送设备输入的检查：

```bash
pokemon-go-cleanup scan-auto-one --dry-run --debug
```

dry-run 会截取并验证当前 summary 页面、打印全部计划坐标，并写出一组供检查的
`incomplete` 本地扫描；它绝不会调用 ADB tap、swipe 或 BACK。请先检查
`debug/automation/plan.json`、初始 PNG 和页面状态 JSON。

确认无误后，再自动扫描这一只：

```bash
pokemon-go-cleanup scan-auto-one --debug
```

如果 WSL PATH 中找不到 Windows Platform-Tools，请把 `adb.exe` 的 WSL 路径
作为全局选项写在命令前：

```bash
pokemon-go-cleanup --adb-path /mnt/c/Android/platform-tools/adb.exe scan-auto-one --dry-run --debug
```

实时状态机会在每次输入前确认页面，任何状态不符都会停止。完成 `moves.png` 后，
只要页面仍是 `detail_moves` 或 `detail_summary`，就会直接点击始终可见的固定菜单
坐标 `(1244, 2772)`，不再滑回顶部。程序每 500 毫秒检测一次，最多等待 10 秒，
只有 OCR 可靠识别为 `action_menu` 才会继续；若页面始终保持详情状态，最多只会在
重新确认后重试同一坐标一次。菜单项只会点击高置信度 OCR 识别到的
`調查寶可夢` 文本框中心，不存在猜测坐标的后备路径；靠近 `傳送` 的目标会被
拒绝。只有现有三条 IV 检测成功时，才会接受 appraisal 截图。

使用 `--debug` 时，每次操作前后截图、页面检测、目标状态等待时间、稳定差异和
实际坐标保存在：

```text
data/scans/YYYY-MM-DD/<scan_id>/debug/automation/
```

成功时会安全退出评价、调用现有读取器、生成 `recognition.json`，并打印名称、
CP、招式和 IV。失败时保留已有截图，把 manifest 标记为 `incomplete` 并记录
失败步骤。Ctrl+C 会先尝试执行同样的恢复，再以退出码 130 结束。

固定坐标流程已有聚焦测试，summary/moves/IV 页面检测也已用现有本地截图验证。
2026-07-29 的真实手机测试已经完整跑通：三张截图、OCR 菜单定位、评价对话、IV
检测、安全退出、`recognition.json` 和 complete manifest 均成功。每次允许实时
输入前仍建议先运行 dry-run。

## 批量扫描固定 Huawei Mate 30 仓库

请先手动打开仓库第一只宝可梦的详情页顶部。首次提高数量前，先执行两只实机校准：

```bash
pokemon-go-cleanup scan-batch --limit 2 --csv batch-test.csv --debug --delay 2
```

批量命令直接复用已经验证的 `scan-auto-one` 服务，不改菜单或评价流程。每只扫描
完成并识别后，程序立即原子更新 CSV，确认已经退出评价并回到 `detail_summary`，
等待指定 delay，然后执行固定右滑：从 `(260, 1500)` 到 `(1180, 1500)`，持续
600 毫秒。之后每 500 毫秒检测一次，最多等待 15 秒；只有页面为
`detail_summary`，且名称或 CP 至少一项不同，才会扫描下一只。若一直相同，只允许
再横滑一次；异常状态或两次仍相同都会安全停止。

默认 limit 为 20。`--resume` 会先验证已有 CSV 及其中引用的 complete manifest
再继续追加；未传 `--resume` 时绝不会覆盖已有目标文件。每行包括识别字段、扫描目录
绝对路径和本地页面指纹。Ctrl+C 或任意单只失败时，之前已经写入的行都会保留。
`--debug` 会同时保留每只单扫证据和该只之后的切换证据，全部位于被 Git 忽略的
`debug/` 目录。

批量流程不会传送、强化、进化、改名、解锁招式、战斗，也不会访问账户或网络。
达到 limit，或安全确认已经回到第一只的名称/CP 时就停止。

## 校验数据集并添加人工标注

递归校验默认 `data/scans/` 下的全部扫描：

```powershell
pokemon-go-cleanup dataset validate
pokemon-go-cleanup dataset status
```

`validate` 会完整解码三张 PNG、检查三张图片尺寸一致，并用 Pydantic
校验 `manifest.json` 和已有的 `ground_truth.json`。只要存在
`invalid` 扫描，命令就会返回非零退出码。`status` 显示扫描 ID、捕获日期、
截图完整度、manifest 有效性、标注是否存在和整体状态。

自定义数据集根目录时，`--output` 应直接指向包含日期目录的扫描根目录：

```powershell
pokemon-go-cleanup dataset validate --output "D:\Local Captures\宝可梦整理\scans"
```

状态含义：

- `complete`：必需文件齐全，PNG 可解码且尺寸相同，manifest 有效并标记为
  `complete`，已有标注也有效；
- `incomplete`：缺少必需文件，或 manifest 尚未标记为 `complete`；
- `invalid`：JSON、PNG、图片尺寸或已有标注校验失败。

为一个扫描目录创建人工标注：

```powershell
pokemon-go-cleanup annotate "data\scans\2026-07-28\<scan_id>"
```

命令会先打印 `summary.png`、`moves.png`、`appraisal.png` 的完整路径，
再依次询问标注字段。已有有效标注时，直接按 Enter 会保留显示的原值。覆盖
`ground_truth.json` 前必须确认；`--force` 可以明确跳过确认。

未来的非交互工具可以提供 UTF-8 JSON：

```powershell
pokemon-go-cleanup annotate "data\scans\2026-07-28\<scan_id>" --input-json ".\annotation-input.json"
```

JSON 必须包含 `pokemon_name`、`cp`、`hp_current`、`hp_max`、
`weight_kg`、`height_m`、`types`、`fast_move`、
`charged_move_1`、`attack_iv`、`defense_iv`、`hp_iv`、
`favorite`、`shiny`、`shadow`、`purified` 和 `costume`；
`charged_move_2` 与 `notes` 可以为 `null`。IV 必须是 0 到 15 的整数，
HP、CP、体重和身高也会进行数值与范围校验。保存时中文保持为可读 UTF-8。

## 本地数据目录

一次引导式扫描会在唯一的扫描 ID 下保存一组固定文件。每张截图成功后，
`manifest.json` 都会以原子方式更新：

```text
data/
└── scans/
    └── 2026-07-28/
        └── 20260728_143015_123456_a1b2c3d4e5f60718293a4b5c6d7e8f90/
            ├── summary.png
            ├── moves.png
            ├── appraisal.png
            ├── manifest.json
            └── ground_truth.json  # 可选，由 annotate 创建
```

`manifest.json` 记录扫描 ID、各截图时间、设备序列号、可用时的设备型号、
屏幕分辨率、截图文件名、`guided` 工作流模式、应用版本、可选备注、扫描状态，
以及失败时的具体步骤。

如果中途失败，之前成功的截图会保留，manifest 状态变为 `incomplete`；
全部完成后状态为 `complete`。未完成的临时文件会被清理。

原有的单次截图仍会生成一对同名的 PNG 和 JSON sidecar：

```text
data/
└── screenshots/
    └── 2026-07-28/
        ├── 20260728_143015_123456_ABC123_a1b2c3d4.png
        └── 20260728_143015_123456_ABC123_a1b2c3d4.json
```

元数据示例：

```json
{
  "schema_version": "1.0",
  "captured_at": "2026-07-28T14:30:15.123456+09:00",
  "serial_number": "ABC123",
  "resolution": {
    "width": 1080,
    "height": 2400
  },
  "screenshot_path": "C:\\path\\to\\data\\screenshots\\2026-07-28\\capture.png",
  "metadata_path": "C:\\path\\to\\data\\screenshots\\2026-07-28\\capture.json",
  "file_size_bytes": 123456,
  "sha256": "64-lowercase-hex-characters"
}
```

项目支持 Windows 路径和包含 Unicode 字符的目录名。设备序列号写入文件名之前
会被转换为 Windows 安全字符；元数据中仍保留原始序列号。

## 错误说明

预期内的错误会显示清楚的提示，并使用固定的进程退出码：

| 退出码 | 含义 |
| ---: | --- |
| 2 | 未安装 ADB，或找不到 ADB |
| 3 | 没有已连接且可用的设备 |
| 4 | 设备尚未授权 |
| 5 | 存在多台可用设备，需要使用 `--serial` |
| 6 | 截图命令失败，或返回内容不是 PNG |
| 7 | 其他 ADB 命令执行失败 |
| 8 | 找不到指定序列号的设备 |
| 9 | 指定设备处于离线或其他不可用状态 |
| 10 | ADB 返回了无法识别的响应 |
| 11 | 本地截图或元数据保存失败 |
| 12 | 数据集根目录或扫描遍历失败 |
| 13 | 标注输入、数值校验或覆盖保护失败 |
| 14 | 固定截图识别无法继续 |
| 15 | 自动扫描在安全边界或超时处停止 |

引导式扫描失败时会沿用原始错误的退出码，并明确显示失败步骤。之前成功的截图
不会被删除；只要恢复写入成功，`manifest.json` 就会标记为 `incomplete`。

以下排查命令应在 **Windows PowerShell** 中运行：

```powershell
Get-Command adb
adb kill-server
adb start-server
adb devices -l
pokemon-go-cleanup device list
```

如果仍然找不到手机，请更换支持数据传输的 USB 线或 USB 接口，并在 Windows
设备管理器中检查驱动问题。如果设备状态为 `unauthorized`，请解锁手机、
撤销 USB 调试授权、重新连接，然后接受新的授权提示。

## 开发

仓库采用 `src` 布局，要求 Python 3.12 或更高版本，并使用完整类型标注、
Typer、Pydantic、pytest、Ruff 和 mypy。

以下命令应在 **Ubuntu / WSL** 中运行，项目的规范路径为：

```bash
cd /home/zhang/projects/pokemonGo_cleanUp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
pytest
ruff check .
mypy
```

测试会模拟 ADB，并使用命令级 fake runner；数据集测试只在临时目录中即时生成
小型合成 PNG，不需要连接真实手机。CI 会在 Windows 和 Ubuntu 上分别使用
Python 3.12 与 3.13 运行依赖检查、pytest、Ruff 和 mypy。

## 参与贡献与许可证

欢迎提交能够保持本地优先、无需账号这一范围边界的贡献。详情参见
[CONTRIBUTING.md](CONTRIBUTING.md)。

本项目采用 [Apache License 2.0](LICENSE)。
