# pokemonGo_cleanUp

[English](README.md) | 简体中文

## 目标：
用ADB截取安卓设备上的Pokémon GO每只宝可梦的详情页面与IV条，帮助整理现有宝可梦的CP，HP，技能，IV等。

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
```
此后直接双击 Windows 桌面上的 `Pokémon GO Cleanup.bat` 启动图形界面。

## 只扫描 IV 并命名

在手机上打开当前宝可梦的详情页顶部，然后点击 GUI 的
**扫描 IV 并命名**。程序直接打开评价页读取三项 IV，再恢复游戏默认中文名并追加
IV，例如 `超梦⑭⑭⑮`。攻击、防御、HP 各用一个圈号数字，0 使用 `⓪`。
完整名字最多 12 个字符，超长时停止，不截断名字或改回普通数字。

此按钮每次只处理当前一只，不扫描技能、不切换下一只，也不保存 CSV、截图、
识别 JSON 或调试文件。它不使用批量数量、CSV、续接或保存调试设置。
进度和计时会更新，可以使用“停止”；识别或命名校验失败时会停止并在日志中说明原因。

Ubuntu / WSL 命令行入口：`pokemon-go-cleanup rename-iv-one`。

### 圈号输入设置

普通 `adb input text` 无法输入圈号数字。手机需要先安装并启用
[ADB Keyboard](https://github.com/senzhk/ADBKeyBoard)；只切换英文键盘不够。
缺少组件时，程序会在扫描和修改昵称之前停止并提示。此依赖同样适用于完整扫描的
“追加 IV”选项；项目不会自动安装或启用输入法。

安装并在手机设置中启用后，保留你平时使用的键盘即可。程序发送 IV 时临时切换到
ADB Keyboard，通过 UTF-8/Base64 发送圈号，随后恢复原输入法；失败或停止也会尝试
恢复。恢复后仍要通过编辑框和最终名字的精确校验，普通数字不会当作圈号验收。
2026-09-07 已用 `向日種子⑮⑭⑩` 完成真机验证：编辑框、最终昵称和 Gboard
恢复均通过，没有新增扫描文件或改动 CSV。圈号验证会检查三个圆圈及圈内数字，
避免普通 OCR 把圈号误读成普通数字。

## 界面示例

![Pokémon GO Cleanup 图形界面](docs/images/interface-example.png)


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
