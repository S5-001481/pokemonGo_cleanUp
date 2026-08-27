# 2026-08-26 自动批量扫描问题与更改总结

本文总结 2026-08-26 的连续调试工作，并包含 2026-08-27 凌晨完成的
“已验证改名页面左滑前 CP 缺失”修复。范围包括 GUI、自动改名、详情页 OCR、
评价流程、暗影/极巨化招式、批量 resume 与切换安全。

## 总体结果

本次修复后，程序能够：

- 在 GUI 中显示本次成功扫描数量和已用时间；
- 为每只 Pokémon 保存真实的逐步骤耗时、OCR 耗时和稳定等待耗时；
- 用 progressive fallback 减少详情页重复 OCR；
- 不再把 Pokémon GO 永久动画区域当成“画面稳定”条件；
- 更快识别评价页左下角的“你好”；
- 更稳健地读取长昵称、分段昵称和带 IV 后缀的昵称；
- 识别暗影和极巨化页面中上移的普通招式，并在 CSV 备注中标记类型；
- 在周年金色背景遮挡 CP 时安全处理 resume 和已验证改名后的左滑前检查。

加入第 15 节的统一 CP fallback 后，最终验证结果为 141 个测试全部通过，Ruff
通过，严格 mypy 检查 19 个源码文件通过，`pip check` 和 `git diff --check`
通过。真实截图用于只读回放；CP 缺失后的完整写行与实际左滑仍需下一次实体
设备批量运行确认。

## 始终保留的安全原则

本次所有更改都遵守以下边界：

- `SummaryIdentity.cp` 仍然是必填 `int`，没有全局改成 Optional；
- `_same_switch_identity` 没有放宽，普通切换仍要求名字、CP 和静态 fingerprint；
- 静态 fingerprint 继续使用 `(100,1550,1340,2300)`，距离阈值仍为 8；
- nickname、CP、HP、fingerprint 和页面状态的最终验证没有被局部稳定或快速
  OCR 替代；
- 只有已经有可信 CP 基准的同一身份验证，才能使用名字/昵称、HP 和 fingerprint
  的 strong fallback；新页面仍必须实际读到 CP；
- 证据不足时保持 safe abort，不猜测、不写不完整 CSV 行、不发送后续输入；
- full-width IV 字符不会被 NFKC 归一化后冒充 half-width 字符通过验证。

## 一、改名与输入法问题

### 1. keyevent 和 `input_text()` 在拼音键盘下仍产生全角字符

现象：逐字符发送 `KEYCODE_1`、`KEYCODE_SLASH`，以及后来一次性调用
`input_text("10/14/4")`，在 Gboard 拼音布局下都可能得到 `１０／１４／４`
或半角数字配全角斜线。真实扫描
`20260826_102152_537806_e166da7687744511bd0c9ef7d8534f99` 中，编辑框显示为
`腕力10／14／4`。

根因：ADB 只把输入交给当前输入法；最终字符宽度由输入法布局决定，Android
keycode 或普通 `input text` 不能保证拼音布局输出半角斜线。

更改：

- 程序改为一次调用现有 `AdbClient.input_text()` 发送完整 ASCII IV 后缀；
- 编辑框 OCR 保留字符宽度，确认前严格拒绝全角数字或斜线；
- 操作要求写入中英文 README：启用 `--rename-with-iv` 前手动切换 Gboard 到
  English；程序不会自动切换或恢复输入法。

结果：错误宽度会在点击键盘“确定”和游戏对话框 `OK` 之前停止。当前解决方案
是“English 键盘操作前提 + fail-closed 宽度验证”，不是自动管理输入法。

### 2. 古月鳥改名流程找不到第二次编辑目标

现象：古月鳥完成第一次恢复默认名字后，第二次打开编辑器之前停止；同时历史
OCR 一度把实际 CP1217 保存为 CP121。

根因：编辑目标曾依赖昵称/铅笔 OCR，动画和长名字会让目标瞬时缺失；旧 CP
fallback 还可能在正常路径已经得到正确 CP 后继续运行，并让差结果覆盖好结果。

更改：

- 两次打开昵称编辑器都改为在确认 `detail_summary` 后点击固定昵称行中心
  `(720,1460)`；
- 点击后仍必须检测到 `rename_dialog`/`rename_keyboard`，因此固定坐标不是盲点；
- progressive summary OCR 在快速 CP 成功后立即停止，古月鳥真实截图稳定返回
  CP1217。

结果：不再依赖铅笔位置或昵称宽度决定是否点击，同时保留点击后的编辑器状态
安全门。

### 3. 怒鸚哥被通用名字 OCR 读成单字“怒”

现象：恢复默认名字后，通用 summary OCR 选中了高置信度单字 `怒`，内部错误地
构造预期昵称 `怒11/7/8`；实际编辑框已经是正确的 `怒鸚哥11/7/8`，精确验证
因此安全停止。

根因：三套 OCR 变体会把 `怒鸚哥` 拆成 `怒` 和 `鸚哥`，通用 `_best_text()`
只选单个最高置信度候选，不会拼接同行文字。

更改：恢复默认名字后，不再用通用 summary 名字构造预期昵称，而是使用独立宽
ROI `(150,1300,1290,1550)`，把昵称行候选按 x 顺序拼接。空结果会在第二次打开
编辑器及输入 IV 前停止。

结果：真实保存帧重新读取为 `怒鸚哥`，同时精确编辑框文本、最终宽昵称、CP、
HP 和 fingerprint 验证全部保留。

### 4. 哈力栗被宽昵称 OCR 拼成“哈力栗1”

现象：宽 ROI 同时读到昵称 `哈力栗` 和右侧日期标签碎片 `1`，旧逻辑按 x 拼接
后得到 `哈力栗1`，进而构造错误的 `哈力栗114/11/12`。实际编辑框是正确的
`哈力栗14/11/12`，程序在确认前停止。

根因：宽昵称读取只做 y 上界过滤，没有判断候选是否与昵称主体同一行、字体
高度是否相近。

更改：

- 优先用最高大、可信的中文候选作为昵称行 anchor；
- 只保留中心 y 相差不超过 70 px 的候选；
- 候选高度必须至少为 anchor 高度的 40%；
- ROI 仍保持宽范围，没有为避开日期而截断长昵称右侧。

结果：真实失败帧从 `哈力栗1` 修正为 `哈力栗`；分段 IV 后缀和
`古月鳥15/15/13` 仍能完整拼接。

## 二、性能、页面状态与计时

### 5. 单次 summary 检测重复运行所有 OCR 变体

现象：一次详情页检测约需 8.6～9.0 秒。即使快速 CP 或名字已经成功，旧流程
仍继续运行增强 threshold 变体和 HP OCR。

更改后的 progressive fallback 顺序为：

1. 普通 CP 三变体；
2. 普通 Name 三变体；
3. Name+CP 成功立即确认 `detail_summary`；
4. CP 失败且 Name 成功时运行 HSV near-white CP；
5. HSV 仍失败才运行六套昂贵 threshold CP；
6. 最后才 OCR HP，以 Name+HP 证明页面状态；
7. Name 缺失时不运行无意义的 CP/HP 后续 fallback。

最终 `read_scan` 的 CP 提取也采用同样的提前停止原则。真实保存截图的 summary
检测约降到 3.2～3.6 秒，古月鳥 CP1217 的 CP OCR 本身约 1.905 秒。

### 6. 全屏 stability 永远等不到稳定

现象：详情页上半部分的 CP 背景、Pokémon 3D 模型和粒子特效持续动画；对全屏
缩小图做 pixel difference 并等待连续三帧稳定，可能每一步白等到超时。

审计结果：正式 moves、menu、appraisal、rename 和 detail-summary 流程已经大多
使用目标状态轮询。残留的全屏稳定链只存在于未使用的 `_tap()`/`_wait()` 路径。

更改：删除未使用的全屏稳定等待、difference utility、8 秒配置和相关调试链。
生产流程统一以目标状态出现为准；需要 fingerprint 时只比较静态 ROI，永久排除
上半部动画。

结果：`timings.json` 中 `stable_wait_seconds` 为 0。此前慢步骤的主要成本被确认
为 OCR，而不是稳定等待。

### 7. 点击“調查寶可夢”后评价入口判断过慢

现象：左下角已经出现“你好”，但宽泛评价对话 OCR 没有利用它，真实一次
`wait_for_appraisal_entry` 花费约 13.39 秒。

更改：增加固定 ROI `(80,2450,420,2615)`。当高置信度候选包含精确 `你好` 时，
立即判定 `appraisal_dialogue`，随后只发送原有的一次 `(1120,1660)` 点击。IV 条
几何检测仍保持更高优先级，普通 action-menu 轮询不会承担这项额外 OCR。

结果：真实首帧回放置信度 0.99997，约 0.50 秒即可确认入口。

### 8. 缺少每只 Pokémon 的逐步骤真实耗时

更改：`--debug` 时，每个扫描目录写入
`debug/automation/timings.json`。内容包括：

- 初始化、summary 验证、moves/menu/appraisal 导航与截图；
- recognition、退出评价、改名 reset/append/confirm/verify；
- 每步真实 duration、`ocr_seconds`、`stable_wait_seconds` 和 outcome；
- 总耗时与 `complete`、`failed`、`interrupted` 或 `dry_run` 结果。

批量模式下每只尝试扫描的 Pokémon 各有一份，失败项也会保留，因而不再需要
根据文件修改时间估算耗时。

## 三、GUI 可观测性

### 9. GUI 没显示本次成功扫描数量

更改：

- 单只扫描只有退出码 0 才把本次成功数设为 1；
- 批量扫描通过轮询原子 CSV 统计已完成数据行；
- `--resume` 会记录启动时已有行数，只显示本次新增数量；
- 使用标准 CSV reader，带引号的换行字段不会造成误计数；
- 显示位置移到“最多扫描数量”右侧空白区域，显示为
  `本次已成功扫描 0 只`，并放大数字。

失败的 partial scan 和 resume 前已有历史行都不会计入本次成功数量。已打开的旧
GUI 需要关闭并重新启动才能加载新布局。

### 10. GUI 缺少本次运行计时器

更改：在成功计数下方增加 `本次已用时间 00:00:00`。单只或批量子进程成功启动
时使用 monotonic clock 开始计时，GUI 现有 100 ms poll 负责刷新；完成、失败或
Ctrl+C 后进行最后一次采样并冻结。新扫描重新归零，设备检查和 dry-run 不改变
上一次正式扫描时长。

该计时器只影响显示，不改变 subprocess 命令、扫描超时或安全验证。

## 四、暗影与极巨化招式页面

### 11. 暗影 Pokémon 的天气/暗影行让普通招式超出原 crop

现象：暗影冰雪龍和果然翁页面中，`暗影獎勵`、`天氣優勢` 等行把普通招式区域
上移，正常 `(100,1100,1340,2320)` crop 无法完整恢复招式。

更改：正常 crop 保持第一路径；只有其失败时才运行上方
`(100,800,1340,1800)` crop，并要求同时出现上方招式标题和高置信度
`暗影獎勵`。modifier 标签不会被当成普通招式。

结果：真实回放恢复：

- 暗影冰雪龍：`冰息` / `遷怒`；
- 暗影果然翁：`躍起` / `遷怒`。

### 12. 极巨化豪力指标看似齐全，但普通招式未结构化保存

现象：扫描目录中可见 CP1123、IV `13/11/12`、HP120/120、体重、身高、格斗
类型、极巨化状态、普通招式和 Max Move 卡片，但原识别 JSON 的两个普通招式为
null。扫描仍是 complete，因为当前 schema 允许 moves 为空，也没有 HP、体重、
身高、类型、极巨状态或 Max Move 的结构化字段。

根因：极巨化页面也把普通招式标题移到 y≈990；暗影 fallback 虽能看到
`極巨招式`，但按设计拒绝缺少 `暗影獎勵` 的页面。

更改：把特殊上移布局分成两个明确分支：

- `暗影獎勵` → 暗影；
- `極巨招式` → 极巨化。

两个分支都只恢复普通招式，过滤 modifier 和 Max Move 标签。识别 warnings/CSV
备注写入 `类型：暗影` 或 `类型：极巨化`，不改变 CSV 表头。

结果：真实极巨化豪力回放恢复 `踢倒` / `地獄翻滾`；普通熔蟻獸仍走快速正常
路径，不会被误标特殊类型。

## 五、周年金色背景、resume 与左滑安全门

### 13. 合法 detail_summary 因 CP 缺失在 resume 比较前退出

现象：`resume_current.png` 是合法索財靈详情页，名字 `索財靈` 和
`73/73HP` 都正确，但周年金色背景让 CP458 OCR 只得到 `O`、`000` 等不可靠
候选。PageDetector 通过 Name+HP 接受页面，旧 `summary_identity()` 却在比较当前
名字和 CSV 最后一行之前强制要求 CP，导致 exit 16。

更改分为两部分。

CP OCR：

- 普通 CP 失败后增加 HSV near-white mask，利用高 value、低 saturation 压制
  金色背景；
- 候选必须位于 CP 水平带；优先要求 CP prefix；
- 无 prefix 的纯数字必须满足高置信度和完整 CP 标签的宽/高几何；
- HSV 失败后才运行 threshold 变体；
- 低置信度 `000` 和背景周年数字 `10` 保持拒绝。

Resume：

- 新增仅用于 resume 的 `ResumeObservation`：可靠名字、可选 CP/HP、静态
  fingerprint、宽昵称；
- 当前名字明确不同于最后一行时，无需 CP、无需预先左滑，直接从当前 Pokémon
  开始；
- 名字相同时，最多做两次 500 ms CP-only 重试，所有帧必须保持静态 fingerprint；
- CP 仍缺失时，只允许名字/宽昵称、HP 和 fingerprint 的强 conjunction；
- verified rename 行还必须匹配已保存预期昵称；
- 强 fallback 只允许一次左滑，不允许重试；证据不足仍安全退出。

普通 `_same_switch_identity`、duplicate、rename、wrap 和 append 规则均未改变。

### 14. 改名已保存，但左滑前又遇到金色 CP 遮挡

现象：扫描 `20260826_233228_919189_a8e5656b21534ee4bba19857387be16d`
已完成识别和改名：

- 原名：`索財靈`；
- CP：458；
- IV：`10/15/15`；
- 最终昵称：`索財靈10/15/15`；
- manifest：`complete`；
- 第 1 行已原子写入 `0826.csv`。

等待配置的 batch delay 后，`before_switch.png` 切换到周年金色动画帧。名字和
`73/73HP` 正确，CP OCR 却只得到 `O` 和低置信度 `0`。旧普通
`_switch_to_next()` 在发送左滑前调用严格 `summary_identity()`，因此停止；没有
`switch_attempt_1_action.json`，证明没有发送左滑。

更改：只为“已经完整验证并持久化的改名页面”增加独立 pre-switch 分支：

1. 先做两次 500 ms CP-only 重试；
2. 每个重试帧同时与初始帧和已保存 baseline 比较现有静态 fingerprint 阈值；
3. CP 恢复后回到原有严格 name+CP+fingerprint 比较；
4. CP 始终缺失时，在最新帧重新读取宽昵称和 HP；
5. 必须同时匹配已保存 expected nickname、`renamed_summary.png` 的精确 HP 和
   距离不超过 8 的 fingerprint；
6. 强验证只授权第一次左滑，不授权第二次加强滑动。

真实金色帧只读回放得到：昵称 `索財靈10/15/15`、当前/基准 HP 都为
`73/73HP`、fingerprint 距离 0，因此满足新专用分支。普通未改名页面 CP 缺失、
昵称错误、HP 不同/缺失或静态页面改变时，测试均确认零输入退出。
其中“普通未改名页面一律退出”的范围已被下一节的统一强验证安全扩展所取代。

### 15. 毛崖蟹改名成功后在写 CSV 前因 CP 缺失退出

2026-08-27 10:47 的扫描
`20260827_104456_757116_2a0406bc2bc0430aa724e4c5591635bf` 已识别毛崖蟹
CP999、IV `11/11/15`，完成并持久化昵称 `毛崖蟹11/11/15`，manifest 也已是
`complete`。但最终 `renamed_summary.png` 只能通过精确昵称加 `96/96HP`
确认 `detail_summary`，CP OCR 为空；batch 在写 CSV 前仍调用严格
`summary_identity()`，因此 exit 16；这一只没有写入批量 CSV，也没有发送左滑。
旧的 verified-rename fallback 位于写行后的左滑前阶段，无法覆盖这里。

更改：所有“当前页面对比可信已知身份”的 CP 验证统一接入强分支：

- 同一张 `summary.png` 已完成的 recognition 可以提供它已经读到的 CP；
- 改名转换、历史改名行恢复、CSV 基准、duplicate/resume、普通或改名页面左滑
  前、左滑等待及第二次左滑确认，都可在 CP 缺失时验证强 conjunction；
- 普通页面必须同时匹配通用名字和宽昵称；改名页面必须匹配已保存 expected
  nickname；两者还必须匹配精确 HP 和距离不超过 8 的静态 fingerprint；
- 只有这些条件全部满足时才沿用可信基准 CP；不同或全新的 Pokémon 仍必须实际
  读到 CP，绝不会继承上一只的数值；
- live pre-switch 仍先做两次 CP-only 重试；强分支只授权一次左滑，不授权加强
  重试。

毛崖蟹原始/最终截图只读回放现返回改名前 CP999、改名后沿用 CP999、HP
`96/96HP` 和精确昵称 `毛崖蟹11/11/15`，通过写 CSV 前转换验证。

## 主要代码与文档位置

- GUI 计数与计时：[gui.py](../src/pokemon_go_cleanup/gui.py)
- 页面状态、评价问候与昵称行处理：[automation.py](../src/pokemon_go_cleanup/automation.py)
- progressive CP、特殊招式布局与类型备注：[recognition.py](../src/pokemon_go_cleanup/recognition.py)
- CSV、resume、switch、fingerprint 与 verified-rename fallback：
  [batch.py](../src/pokemon_go_cleanup/batch.py)
- 自动流程测试：[test_automation.py](../tests/test_automation.py)
- 批量安全测试：[test_batch.py](../tests/test_batch.py)
- OCR/招式测试：[test_recognition.py](../tests/test_recognition.py)
- GUI 测试：[test_gui.py](../tests/test_gui.py)
- 当前操作说明：[README.zh-CN.md](../README.zh-CN.md)
- 批量流程详解：[batch-scan.md](walkthroughs/batch-scan.md)
- 自动单只流程详解：[automatic-one-scan.md](walkthroughs/automatic-one-scan.md)

## 历史数据与兼容性

- 历史 `recognition.json`、CSV 和 incomplete manifest 不会自动重写；
- 新暗影/极巨化备注只出现在修复后的新识别结果；
- 之前错误保存为 CP121 的古月鳥历史记录不会被自动改成 1217；
- 哈力栗、怒鸚哥等失败目录继续保留为诊断证据，不会补写成成功扫描；
- 最新索財靈第 1 行已经安全保存，可以使用相同 `--rename-with-iv` 模式和
  `--resume` 继续；
- 旧版合法 13 列非改名 batch CSV 仍只能用非改名模式 resume；启用改名的新
  CSV 使用 16 列。

## 仍待确认或尚未扩展的范围

- 统一 CP-missing 已知身份分支已通过毛崖蟹/索財靈真实截图回放和自动测试，但
  还需一次实体设备多 Pokémon 批量运行确认完整写行与实际左滑结果；
- 固定昵称行中心 `(720,1460)` 有状态安全门和自动测试，但文档仍保留实体设备
  再确认项；
- Gboard English 仍是人工前置条件，程序不会自动切换输入法；
- 当前自动化仍只支持已校准的 Huawei Mate 30、1440×3120、繁体中文布局；
- schema 尚未结构化保存 HP、体重、身高、属性、极巨化状态或 Max Move；
- 项目不会传送 Pokémon、强化、进化、解锁招式或访问账号/私有 API。
