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

## 界面示例
<img
  src="示例图片.png"
  width="900"
  alt="Pokémon GO Cleanup 图形界面示例"
/>


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

## 使用图形界面

桌面启动器通过 Tkinter 调用现有 CLI，并没有另一套扫描逻辑。

1. 连接并解锁手机，开启 USB 调试，把 Pokémon GO 停留在一只宝可梦的详情
   首页。
2. 点击“检查手机”，确认只有一台已授权设备处于可用状态。
3. 第一次正式扫描前先点“检查当前页面（不操作）”。它运行
   `scan-auto-one --dry-run`，不会向手机发送点击、滑动或文字。
4. 点击“扫描一只”执行单只自动扫描；批量扫描前设置总行数上限、切换等待
   时间、CSV 路径和所需选项，再点击“开始批量扫描”。
5. 只有续接所选批量 CSV 时才勾选“续接已有 CSV”。未勾选时，GUI 不会覆盖
   已存在的 CSV。

“最多扫描数量”同一行右侧的空白区域会用大号数字显示“本次已成功扫描”。
单只扫描成功后显示 1；批量扫描每原子写入一条验证成功的 CSV 数据行就会
实时更新。resume 只统计本次新增加的行，不把 CSV 中原有的历史行计算进去。
成功计数正下方显示“本次已用时间”，单只或批量扫描启动时归零，运行中按
`时:分:秒` 更新，并在完成、失败或手动停止后保留最终耗时。

## IV 改名的键盘要求

在图形界面勾选“重置中文名后追加 IV”，或使用 `scan-auto-one`、
`scan-batch` 的 `--rename-with-iv` 参数之前，请先手动把 Gboard 切换到
**English** 布局。
拼音布局会把 ADB 输入的半角 `/` 转成全角 `／`。程序不会自动切换或恢复
键盘布局；如果检测到全角数字或标点，会在确认昵称之前停止，避免保存错误
名称。

## 命令行用法

在项目目录中使用已经安装好的虚拟环境运行：

```bash
# 查看已连接手机及当前分辨率。
.venv/bin/python -m pokemon_go_cleanup device info

# 截取当前屏幕，并保存 PNG 和 JSON 元数据。
.venv/bin/python -m pokemon_go_cleanup capture

# 手动准备并确认详情首页、招式页和 IV 评价页。
.venv/bin/python -m pokemon_go_cleanup scan-one --guided

# 不操作手机，只检查固定布局自动流程的初始页面与计划。
.venv/bin/python -m pokemon_go_cleanup scan-auto-one --dry-run --debug

# 自动扫描一只宝可梦。
.venv/bin/python -m pokemon_go_cleanup scan-auto-one --debug

# 批量扫描，CSV 最多共五行，每次切换前额外等待两秒。
.venv/bin/python -m pokemon_go_cleanup scan-batch \
  --limit 5 --csv inventory.csv --delay 2 --debug

# 续接已有批量 CSV。
.venv/bin/python -m pokemon_go_cleanup scan-batch \
  --limit 20 --csv inventory.csv --delay 2 --debug --resume
```

需要自动改名时，先满足上一节的 English 键盘要求，再为单只或批量命令添加
`--rename-with-iv`。续接批量任务时必须沿用创建该 CSV 时的改名模式。
`--limit` 表示 CSV 的**总行数上限**，不是本次新增行数。

以下识别和数据集命令只读取或写入本地文件：

```bash
# 读取一组完整的校准截图并写入 recognition.json。
.venv/bin/python -m pokemon_go_cleanup read-scan data/scans/YYYY-MM-DD/SCAN_ID --debug

# 读取全部完整扫描，并写入另一份识别结果 CSV。
.venv/bin/python -m pokemon_go_cleanup read-dataset --csv recognized-inventory.csv

# 校验或汇总所有扫描目录。
.venv/bin/python -m pokemon_go_cleanup dataset validate
.venv/bin/python -m pokemon_go_cleanup dataset status

# 创建或校验人工 ground_truth.json 标注。
.venv/bin/python -m pokemon_go_cleanup annotate data/scans/YYYY-MM-DD/SCAN_ID
```

运行 `.venv/bin/python -m pokemon_go_cleanup --help`，或在任一子命令后添加
`--help`，可以查看完整参数。`--data-dir`、`--adb-path`、
`--log-format text` 等根选项必须写在子命令之前。

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
            ├── recognition.json   # 自动扫描或 read-scan 的输出
            ├── renamed_summary.png # IV 改名验证成功后生成
            ├── nickname_change.json # 精确的改名验证证据
            ├── ground_truth.json  # 可选，由 annotate 创建
            └── debug/             # 可选，已被 Git 忽略
                └── automation/
                    └── timings.json # 使用 --debug 时的逐步骤单调时钟耗时
```

单次截图的 PNG 与同名 JSON sidecar 位于：

```text
data/screenshots/YYYY-MM-DD/
```

`manifest.json` 记录扫描进度与设备元数据；
`recognition.json` 保存机器识别值和 warning；
`ground_truth.json` 独立保存经过校验的人工输入值。

启用 `--debug` 后，每次自动扫描都会写入
`debug/automation/timings.json`。其中记录导航、截图、识别和各个改名子步骤
基于单调时钟测得的真实耗时、总耗时，以及最终的 `complete`、`failed`、
`interrupted` 或 `dry_run` 结果。每个步骤和文件顶层还会记录 `ocr_seconds`
与 `stable_wait_seconds`；后者固定为 0，因为正式自动流程只轮询目标页面状态，
已经没有任何全屏稳定等待。批量模式下，每只宝可梦自己的扫描目录各有
一份，失败的那一只也会保留，因此不必再通过文件修改时间估算耗时。

批量扫描只有在单只扫描完成且身份、改名检查全部通过后，才会原子写入一行
CSV。启用改名的新 CSV 当前共有 16 列，其中包括 `nickname_before`、
`nickname_after` 和 `rename_status`。现有 `warnings` 列同时作为备注字段：
明确识别到暗影或极巨化招式布局时，会写入 `类型：暗影` 或 `类型：极巨化`，
不会改变 CSV 表头。使用 `--resume` 时，程序会先验证表头、
历史扫描目录、manifest、已保存截图、重复 scan ID 和当前页面位置。预检查会
先比较可靠名字：名字明确不同时无需 CP 且不会预先左滑；同名但 CP 缺失时会
做两次 CP-only 重试，仍失败只能使用名字、HP 和静态 fingerprint 全部一致的
强兜底。凡是批量流程拿当前页面与一个已知身份做验证，CP 暂时读不到时都使用
同一条 fail-closed 规则：普通页面必须同时匹配通用名字和宽昵称，已验证改名
页面必须匹配已保存的预期昵称；两者还必须匹配完全一致的 HP 和现有静态
fingerprint 阈值。该规则覆盖改名后写 CSV 前验证、历史行恢复、duplicate/resume
基准、左滑前检查、左滑等待和第二次左滑确认。同一张已保存截图已经完成的
recognition 也可以提供它读到的 CP。这个分支不会为另一只或全新的宝可梦猜 CP；
新身份仍必须实际读到 CP。通过强兜底的左滑前页面只允许发送一次左滑。

## 自动扫描的安全与失败行为

自动输入仅支持已校准的 Huawei Mate 30 固定布局。程序会在每个动作前检查
页面状态，通过 OCR 确认菜单目标，固定点击昵称行中心 `(720,1460)`，并确认
昵称编辑框确实打开；之后还会验证 IV 条，并确认左滑后确实到达另一只
宝可梦。程序不会传送宝可梦、强化、进化、解锁招式，也不会访问账号凭据或
私有 API。

如果某一步无法验证，已完成的截图会保留，manifest 会标记为 `incomplete`。
退出码 `15` 表示单只自动扫描安全停止；退出码 `16` 表示批量层拒绝了页面、
CSV、resume 或身份验证证据。批量项目失败时不会写入不完整 CSV 行，也不会
在失败后继续左滑。按 Ctrl+C 停止时，已经完成的行和文件仍会保留。

## 安全、隐私与功能边界
仓库不包含任何 Pokémon 图片、图标或截图。Pokémon 是其权利人的商标；本项目是
独立项目，与其权利人没有隶属或背书关系。

## 许可证
本项目采用 [Apache License 2.0](LICENSE)。
