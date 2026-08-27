# 截至 2026-08-27 的自动批量扫描问题与解决方法

本文从 2026-08-26 连续调试总结扩展而来，现在累计记录截至
2026-08-27 的已知问题、根因、解决方法、安全边界和验证证据。范围
包括 GUI、自动改名、详情页 OCR、评价流程、暗影/极巨化招式、批量
resume/wrap/切换安全，以及后续实机暴露的昵称长度、final-wide OCR 和
动画 CP 冲突。

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

截至本次文档同步，最新验证结果为 175 个测试全部通过，Ruff
通过，严格 mypy 检查 33 个源码文件通过，`git diff --check` 通过。
真实截图只用于只读回放；旧的湧躍鴨运行在 CP-only 共识逻辑实现之前就已
结束，因此能证明冲突原因，但不能冒充两张独立 CP997 帧的实机共识证据。

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
- editor 内容仍必须与 expected 完全相等；final-wide 仍必须通过现有完整
  skeleton 比较，没有 fuzzy、prefix 或人工字符替换；
- 改名前 CP 共识只在初始基准与第一次 editor checkpoint 冲突时触发；
  一致路径不增加 CP OCR；
- CP-only 共识帧不重复读取 name/nickname、HP 或 static fingerprint；进入
  该有界无输入连拍前只做一次 `detail_summary` 页面门。

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

## 六、后续实机失败与专用补救

### 16. 飄飄球改名正确，final-wide OCR 却丢了中间数字

现象：实机已把昵称保存为 `飄飄球12/2/5`，但旧 final-wide crop
`(150,1250,1290,1600)` 读成 `飄飄球125`。程序因完整昵称不匹配而 exit 15，
没有写 CSV，也没有左滑。

根因：原 crop 夹带了更多上下文，RapidOCR 把同一昵称行拆分后丢失了中间
token。这是 final evidence 假阴性，不是改名或切换手势失败。

解决：

- 只把专用昵称行 ROI 收紧为 `(150,1300,1290,1550)`；
- 同行 token 继续按 x 顺序拼接；
- 没有修改通用 summary OCR，也没有放宽最终完整比较。

结果：两张历史失败图都能重放为 token `飄飄球12`、`2`、`5`，并得到
完整昵称 skeleton。

### 17. 已改名的首只 Pokémon 回环时未被 wrap detection 识别

现象：batch 第 1 行和第 6 行实际是同一只 CP175 飄飄球，静态 fingerprint
距离为 0，但长昵称的通用 OCR 在两次分别只读到 `"2"` 和 `"5"`。旧 wrap
检查依赖通用名字精确相等，因此漏掉回环，随后还会对已经正确的下一只
泥巴鱼尝试冗余改名。

解决：

- 保留通用 `_same_switch_identity` 不变；
- 只给 verified-rename wrap 增加专用路径：当前宽昵称必须完全等于第 1 行
  保存的 expected nickname，同时 CP、可用 HP 和距离 8 内 fingerprint 匹配；
- 切换流程保留用于 wrap 比较的实际截图和 debug state；
- 后续改名入口已统一为 OCR 确认 `detail_summary` 后的固定昵称行中心，不再
  依赖可被长名截断的动态铅笔坐标。

结果：真实 row1/row6 回放只在 verified-rename wrap 路径上由 false 变为
true；普通 identity comparator 仍然严格。历史 CSV 中已有的重复行不会自动改写。

### 18. 退出评价后用完整 OCR 重新识别页面过慢

现象：点击退出评价后，程序只需知道“已回到详情页”，却重新运行名字、
CP、moves 等完整 OCR。真实中位 `exit_appraisal` 约为 6.302 秒，其中
OCR 约 3.597 秒。

解决：增加轻量 `detail_returned` 状态，只要求现有 IV 条几何已消失，并在固定
关闭/菜单按钮的小 ROI 上同时确认青绿色圆盘与外环对比。正常轮询不运行
任何 OCR；不确定帧仍是 `unknown`，超时才在最后一帧跑旧完整 detector 作诊断。

结果：保存真实 appraisal/detail/action-menu 组合分别回放为
`unknown` / `detail_returned` / `unknown`；实机中位退出步骤降到约 2.024 秒且
OCR 时间为 0。该状态不携带 Pokémon identity，不参与 batch/rename/wrap 比较。

### 19. 自动流程已识别过三张图，保存后却又完整 OCR 一次

现象：summary、moves 和 appraisal 的页面门已经产生候选、IV 条和 debug 图，
`recognize_scan` 却从磁盘重新解码并 OCR 相同 PNG，增加数秒耗时。

解决：

- `PageDetection` 保留 summary/moves/appraisal 的最终候选、特殊页面备注、IV 条、
  debug 图和对应 PNG 的 SHA-256；
- 文件原子保存后，reader 重新 hash 三张 PNG；只有证据完整且 hash 一致时
  才直接构建同样的 `RecognitionResult`；
- 任一证据缺失、fallback-only move、不可靠 CP、文件缺失或 hash 不一致，
  就回到原 `read_scan()`。

结果：三次 Mate 30 rename-enabled 实机运行仍然生成完整
`recognition.json` 和七张 recognition debug PNG，但 `recognize_scan` 的 OCR 时间为 0。
历史/CLI/guided/manual/replay 仍使用原路径。

### 20. 赫拉克羅斯完整 pretty 昵称超过游戏 12 字符上限

现象：实机识别到 `赫拉克羅斯`、CP1758、IV `15/14/13`。旧程序生成
`赫拉克羅斯15/14/13`，实际长度为 13。游戏
只保留前 12 个字符，editor 变为 `赫拉克羅斯15/14/1`。现有 exact equality 正确拒绝，
因此没有点击键盘确认或游戏 `OK`。

根因：昵称构造没有在输入前处理 Pokémon GO 的 12 字符边界。

解决：把 single 和 batch 共用的昵称构造集中为 `build_iv_nickname()`：

1. 先构造 `pretty = name + attack/defense/hp`；
2. 用 Python `len(pretty)` 直接计算 Unicode 字符数；
3. 长度不超过 12 时保持 pretty；
4. 超过时改用三组固定两位 ASCII 数字 `AADDHH`，例如
   `赫拉克羅斯151413`、`1/11/1 -> 011101`、`0/0/15 -> 000015`；
5. compact 仍超过 12 时在发送文字前 fail closed，不截断名字或 IV，不发明简称。

字符 `・`、`：`、ASCII 数字及其他 Unicode 都交给 Python 字符串长度处理。
editor exact equality、half-width 规则和 final-wide 验证未放宽。后续实机成功把
`赫拉克羅斯151413` 完整输入、验证并保存。

### 21. compact 昵称已验证，batch 后检查仍要求旧斜线后缀

现象：`赫拉克羅斯151413` 已经通过 editor 和 final summary 验证，
`nickname_change.json` 也完整，batch 却 exit 16：
`Verified nickname evidence does not contain the recognized half-width IV suffix.`

根因：`_verified_renamed_identity()` 还用旧规则检查昵称是否以
`attack/defense/hp` 结尾，不认识 compact `AADDHH`。

解决：batch 后检查调用同一个 `build_iv_nickname(default_name, IVs)`，并要求重建的
完整昵称与已验证 expected nickname 完全相等。无效证据仍是
`BatchAutomationError`；identity、switch、wrap、resume 和 CSV schema 未改。

### 22. 一對鼠 final-wide 把“一”读成“-”，且第一版 raw 1.5x 仍失败

现象一：保存图中 expected 为 `一對鼠14/15/15`，当前 sharpen 1.5x 结果是
`-對鼠14/15/15`，而原始彩色 1.5x 可正确读出完整 expected。程序原本只跑 sharpen，
因此 final-wide 假阴性 exit 15。

第一次解决：仅在 final-wide 专用路径增加 progressive fallback：

1. 先跑原 sharpen 1.5x；
2. 使用原 geometry filter 和同行 token joining；
3. 完整匹配 expected 就立即成功，不跑第二次 OCR；
4. 不匹配才对同一 ROI 运行 raw color，并使用完全相同的几何过滤和拼接；
5. raw 结果仍必须通过原 final skeleton 完整比较。

现象二：后续 `一對鼠15/14/12` 截图在 sharpen 和 raw 1.5x 都读成
`-對鼠15/14/12`；raw 2.0x 才完整匹配。

最终解决：只把 final-only raw fallback 的 scale 从 1.5x 改为 2.0x。原 sharpen
1.5x 仍是首路径，首次成功时不增加 OCR。两张一對鼠保存图都能回放成功。
没有 `"-" -> "一"` 替换、fuzzy/prefix 匹配、置信度候选优先，也没有让通用
summary OCR 单独放行。Editor 精确校验未改。

### 23. 湧躍鴨的不同 CP checkpoint 被动画泡泡遮住不同数字

现象：扫描 `20260827_213607_150274_116b76f50f9f40e49bce607ab66a2a3f`
完成了精确昵称 `湧躍鴨12/15/15`，manifest 和 nickname evidence 都完整。画面上
真实 CP 是 997，但动画泡泡在不同帧遮住不同数字：

- 初始 `summary.png` OCR 为 CP67；
- 第一次 editor 前 checkpoint 为 CP97；
- 第二次 editor 前 checkpoint 为 CP997；
- final renamed summary OCR 为 CP99。

原有 batch rename transition 严格要求 before/after CP 相等，因此正确地拒绝 67 与 99，
没有写该 CSV 行，也没有 switch。既有“CP 缺失时沿用已知基准”分支不适用：
这里每帧都返回了合法但不同的正整数，而且初始基准 CP67 本身就错。

解决：在第一次点击昵称 editor 之前增加条件式 CP 共识：

1. 复用本来就要捕获的 `detail_summary` pre-state；
2. 初始 recognition CP 与 checkpoint CP 都存在且相等时，立即继续，不增加
   CP-only OCR；
3. 两者冲突时保持当前 detail summary 不动，不点击 editor；
4. 以 500 ms 间隔最多捕获 6 个 CP-only 帧；
5. 只接受保留可靠 `CP` 前缀的完整正 CP，同一数值至少出现 2 次才成为
   trusted baseline；
6. 成功时更新本次内存 `RecognitionResult` 并原子重写 `recognition.json`；
7. 无共识时在 editor tap 前 fail closed。

按最后的用户校正，CP-only 连拍期间不每帧重复验证 name/nickname、HP 或 static
fingerprint，也不重跑完整 page detector。进入连拍前的那一帧已经通过
`detail_summary` 页面门，且连拍期间程序不发送任何输入。

自动测试用故障模式 `baseline 67 -> checkpoint 97 -> CP-only 997 / 无结果 /
997` 确认 CP997 成为基准；三个不重复数值会在点击 editor 前停止。旧实机运行
没有捕获新增的 CP-only burst，因此只能证明问题，不能被当成两张独立
CP997 帧的 saved-real 共识证据。

### 24. 来悲茶的 IV 卡整体上移，攻击条被固定顶边界截断

现象：扫描 `20260827_224510_336106_4d6062a9fed44d7eb678fa76bcea62f2`
在 `wait_for_appraisal_bars` 等待 30 秒后 exit 15。保存的
`appraisal_bars_wait_01.png` 到 `_09.png` 都清楚显示三星徽章和攻击/防御/HP
三条橙色 IV 条，所以这不是评价页没有打开，也不是单次推进点击没生效。

日志过程：

- 评价入口的固定“你好” ROI OCR 没有命中，前 4 帧是 `unknown`；
- action menu 连续 4 帧消失后，现有有界 fallback 推断为
  `appraisal_dialogue`，并发送了唯一一次 `(1120,1660)` 推进点击；
- 点击后第一张图就已经显示 IV 卡，但后续 9 个采样全部被分类为
  `appraisal_dialogue`，OCR 反复读到 `防禦`、`HP` 和 `69/69 HP`。

根因：现有 IV 几何检测只在 x=171..665、y=2150..2700 内统计每行的非近白
像素，并保留高度 20–40 px 的活动区间。放大真实图后确认，IV 卡整体比现有
校准位置更高，完整三条实际是：

```text
attack:  2136..2166 -> 31 px -> endpoint 635 -> IV14
defense: 2269..2298 -> 30 px -> endpoint 537 -> IV11
HP:      2400..2431 -> 32 px -> endpoint 635 -> IV14
```

攻击条的完整高度是 31 px，但固定 `BAR_TOP=2150` 截掉了其上方 14 px，搜索区中
只剩 2150..2166 的 17 px。`_intervals()` 因低于 20 px 下限丢弃它，后面虽然完整
读到防御和 HP，`_bar_sequence()` 仍只有两条。该卡片下方另有 2492..2540 的
49 px 红色背景区，但它不是 HP 条，现有高度和间距规则正确地排除了它。

用户确认 HP IV 是 14；将同一算法的顶边界仅向上扩展后，保存图可完整找到
2136/2269/2400 三个起点，间距为 133/131 px，三个 endpoint 量化为
`14/11/14`。正式流程本次仍返回 `bars=None`，没有保存错误 IV。

解决：保留当前 y=2150 几何规则为首路径，只在它失败时把顶部向上扩展到
y=2100 再试一次。fallback 仍使用原 20–40 px 条高、125–150 px 间距、橙/红
endpoint 和 IV 量化，而且必须只存在一个合法三条序列。没有放宽颜色/尺寸/间距，
也没有用 `防禦`/`HP` OCR 猜 IV。Debug state 记录 `standard` 或
`upward_fallback`。

验证：全部 9 张来悲茶 IV wait 帧都稳定返回 `14/11/14`；同一运行中的
action menu、点击前对话和 4 张 entry 帧全部仍返回 `bars=None`。一张标准位置
真实评价图继续走首路径，没有调用 upward fallback。原失败的 incomplete manifest
不会自动重写，需新实机运行验证完整流程。

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
- 赫拉克羅斯旧的 13 字符失败不会自动重做；修复后的新运行才会使用
  `赫拉克羅斯151413`；
- 新 nickname compact format 没有改 CSV 或 `nickname_change.json` 历史 schema；
- 一對鼠 final-wide raw 2.0x 只影响后续验证，不会回写旧 incomplete manifest；
- 湧躍鴨旧 `recognition.json` 的 CP67 不会离线自动改成 997；只有新实机运行中
  实际捕获到两帧一致 CP 才会重建 trusted baseline；
- 最新索財靈第 1 行已经安全保存，可以使用相同 `--rename-with-iv` 模式和
  `--resume` 继续；
- 旧版合法 13 列非改名 batch CSV 仍只能用非改名模式 resume；启用改名的新
  CSV 使用 16 列。

## 仍待确认或尚未扩展的范围

- 统一 CP-missing 已知身份分支已通过毛崖蟹/索財靈真实截图回放和自动测试，但
  还需一次实体设备多 Pokémon 批量运行确认完整写行与实际左滑结果；
- 改名前 CP-only 共识已通过自动测试，但需要一次新的实机冲突运行，才能确认
  真实动画帧中能定期捕获两次完整相同 CP；
- 来悲茶 IV 顶部 ROI 裁切假阴性已完成 upward-only fallback 和 saved-real
  正负回归；仍需新实机运行确认可在首张 IV wait 帧继续完整扫描；
- 固定昵称行中心 `(720,1460)` 有状态安全门和自动测试，但文档仍保留实体设备
  再确认项；
- Gboard English 仍是人工前置条件，程序不会自动切换输入法；
- 当前自动化仍只支持已校准的 Huawei Mate 30、1440×3120、繁体中文布局；
- schema 尚未结构化保存 HP、体重、身高、属性、极巨化状态或 Max Move；
- 项目不会传送 Pokémon、强化、进化、解锁招式或访问账号/私有 API。
