# pokemonGo_cleanUp

[English](README.md) | 简体中文

`pokemonGo_cleanUp` 是一个本地优先的 Windows 命令行工具。它通过 ADB
截取 Android 或 HarmonyOS 设备的当前屏幕，并将 PNG 截图保存在本地。
每张截图旁边会生成一个 UTF-8 JSON 文件，记录设备序列号、分辨率、截取时间、
文件路径、文件大小和 SHA-256 摘要。
引导式扫描还可以把同一台设备上的详情顶部、招式和评价截图归入同一个扫描 ID，
并用一份持续更新的 manifest 记录整个过程。

Python 包名是 `pokemon_go_cleanup`，安装后的命令是
`pokemon-go-cleanup`。

## 功能范围与隐私

当前版本支持：

- 检测 ADB 是否可用，并列出已连接设备的状态；
- 连接运行兼容 ADB 的 HarmonyOS 系统的 Huawei Mate 30；
- 读取设备当前分辨率；
- 通过 `adb exec-out screencap -p` 截取当前屏幕；
- 引导用户手动准备三个页面状态，但不会向手机发送 tap 或 swipe 命令；
- 将同一次扫描的三个截图和 manifest 保存在同一目录；
- 将截图和元数据保存在本地计算机。

当前版本不包含 OCR，不自动操作游戏，也不发送界面点击或滑动命令。它不访问
Pokémon GO 账号或私有 API，不分析网络流量，也不读取登录凭据。截图可能包含
个人信息，因此 `data/screenshots/` 和 `data/scans/` 均已被 Git 忽略。
分享文件前请先检查截图内容。

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
            └── manifest.json
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
pytest
ruff check .
mypy
```

测试会模拟 ADB，并使用命令级 fake runner 覆盖引导式集成流程，不需要连接
真实手机。

## 参与贡献与许可证

欢迎提交能够保持本地优先、无需账号这一范围边界的贡献。详情参见
[CONTRIBUTING.md](CONTRIBUTING.md)。

本项目采用 [Apache License 2.0](LICENSE)。
