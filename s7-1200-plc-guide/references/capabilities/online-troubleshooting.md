# 在线诊断与故障排查

## R — 原文 (Reading)

> "CPU 提供了一个诊断缓冲区，其中包含的每个条目对应一个诊断事件……此日志最多可提供 50 个最近发生的事件。日志填满后，新事件将替换日志中最早的事件。掉电时，将保存事件。仅永久存储诊断缓冲区中 10 个最新的事件。"
>
> — Siemens AG，《S7-1200 可编程控制器系统手册》V4.2，ch5.1.6 p123；ch15.10 p1497；ch15.1 p1483–1486

## I — 方法论骨架 (Interpretation)

这是现场排障的主干流程，核心是"先取证、再定位、后处置、终验证"，而不是一上来就改程序。第一步"转到在线"是所有在线运维的前置入口（项目树选 CPU→转到在线→选 PG/PC 接口→橙色在线指示）。取证从两个并列现场开始：①CPU 状态 LED（RUN/STOP/ERROR/MAINT 三灯组合判据）快速判断大类状态；②诊断缓冲区——容量有限（最多 50 条环形、仅 10 条永久、掉电保存、MRES 不清除），是排障"第一现场"，先看最新条目。定位靠"事件→OB/指令"的映射：时间错误（fault_id、RE_TRIGR 十倍上限、同周期二次超时必 STOP）、诊断错误中断（IOstate/LADDR，无 OB 则静默忽略）、拔插模块（分布式 I/O 保持 RUN、中央机架一律 STOP）、无关联 OB 事件（记缓冲区保持 RUN，用 GET_ERROR_ID 查）都有固定响应，先对照再动手。更细的定位用诊断指令集按粒度递进：DeviceStates/ModuleStates 看整体（STATE 位解析定位从站/模块）→Get_Name/IP 定位→GET_DIAG/RD_SINFO 读细节；模拟量返回 32767 的歧义用 AINFO 字节（16#0001 短路/0006 断线/0007 超上限）判类型；SM1231 断路建诊断错误 OB 调 RALRM(F_ID=LADDR)解析。用户程序向诊断缓冲区写报警用 Gen_UsrMsg（TextListID/TextID/Mode/AssocValues）。处置后必须验证：LED 恢复、诊断缓冲区无新条目。两个高频陷阱：诊断缓冲区会被重复安全事件刷屏冲掉关键线索；日时钟未设置则时间戳不可信。强制值会随项目/存储卡传播，STOP 下启用输出前必须确认安全并配急停。

## A1 — 应用示例 (Application Example)

### 示例 1: DeviceStates 定位 PROFINET 从站故障（原书例题 c10）
- **类型与来源**: 原书 worked_example，ch9.7.7 p525–530。
- **问题/输入**: 16 个 PROFINET 从站（et200s_1…16），MODE 2 返回 0x0300_0000。
- **方法论的使用**: 按 MODE/STATE 位解析——MODE2 返回 0x0300_0000 表示设备 1 有故障；MODE4 返回 0xFDFF_0100 表示设备 12 不存在。
- **结论**: 由位列表把故障定位到具体从站。
- **结果**: 手册给出三种场景完整 STATE 值及位含义表。

### 示例 2: 时间错误 fault_id 判读（worked_example，依据 f10）
- **类型与来源**: 合成演练，手册 ch5.1.3.6 p102–103。
- **问题/输入**: 诊断缓冲区 fault_id=16#02，csg_OBnr 指向某 OB。
- **方法论的使用**: 判为"OB 无法启动（前中断未完）"，定位该 OB 并优化。
- **结论**: 从症状直接落到具体处置动作。
- **结果**: 与手册 fault_id 判据一致。

### 示例 3: 模拟量 32767 歧义判定（worked_example，依据 f66）
- **类型与来源**: 合成演练，手册 ch15.16 p1524–1527。
- **问题/输入**: SM1231 模拟量返回 32767，不知断路还是溢出。
- **方法论的使用**: 建诊断错误 OB→调 RALRM(F_ID=LADDR)→解析 AINFO 字节 32/33（16#0006 断线、16#0007 超上限）。
- **结论**: 把歧义值定位到具体错误类型。
- **结果**: 与手册 AINFO 判据一致。

## A2 — 触发场景 (Future Trigger) ★

### 用户会在什么情境下需要这个能力?
1. CPU 亮红灯/黄灯/STOP，想知道发生了什么、怎么恢复。
2. 诊断缓冲区里一堆条目，不知道先看哪条、怎么解读。
3. 时间错误/诊断错误/拔插模块事件，想知道 CPU 为什么 STOP 或为什么没 STOP。
4. PROFINET 从站/模块报故障，要定位到具体设备或插槽。
5. 模拟量值异常（32767），分不清断线、短路还是超量程。
6. 设备间歇性异常，想用跟踪（Trace）捕捉波形；或要确认物理拓扑是否符合组态。

### 语言信号 (用户的话里出现这些就应激活)
- "CPU 报警 / 红灯 / 停机" / "CPU fault / red LED / STOP / error light"
- "诊断缓冲区怎么看" / "diagnostic buffer / read diagnostic log"
- "故障定位 / 排障" / "troubleshoot / locate fault / root cause"
- "时间错误 / 看门狗超时" / "time error / watchdog timeout"
- "模块/从站报故障" / "module fault / device states / module states"
- "跟踪 / 拓扑比较" / "trace / topology comparison"

### 与相邻能力的区分（阶段 3 定稿）
- 与 `program-download-debug` 的区别：那边是"改程序/下载/同步/强制"的调试操作，本能力是"定位故障"的取证与判读；先诊断定位，再决定是否下载修改。
- 与 `realtime-scan-interrupt` 的区别：那边提供时间错误/中断的判据原理，本能力把它们编排成从 LED 到处置的完整排障链。
- 与 `communication-config` 的区别：那边组态连接，本能力处理"通信建不起来"的现场排查（设备名、从站状态）。
- 与 `storage-firmware-maintenance` 的区别：那边处理卡/固件类故障恢复，本能力处理运行期诊断事件；插卡类故障两者衔接。

## E — 可执行步骤 (Execution)

**必需输入**: CPU 当前状态（LED 组合）；诊断缓冲区条目（含 fault_id/事件类别/时间戳）；故障现象（哪个设备/模块/值）；是否涉及强制/分布式 I/O。缺 LED 状态或诊断条目时先取证。
**交付物与完成标准**: 排障报告——症状→判据→定位结论→处置→验证闭环，含失败处理。

1. **转到在线并取证**
   - 完成标准：项目树选 CPU→转到在线→选 PG/PC 接口→橙色在线指示；记录 LED 三灯组合；打开诊断缓冲区读最新条目。
   - 判停条件：连不上 → 先查接口/连接（与 communication-config 衔接）。

2. **判读状态与事件**
   - 完成标准：按 LED 判据判大类；按事件→OB 映射判断 CPU 应有行为（保持 RUN 还是 STOP、有无 OB）；无 OB 事件用 GET_ERROR_ID 查原因。

3. **定位根因**
   - 完成标准：按粒度递进选诊断指令（DeviceStates/ModuleStates→Get_Name/IP→GET_DIAG/RD_SINFO）；歧义值用 AINFO 判类型；必要时用 Trace 捕捉间歇异常、拓扑比较核对物理连接。

4. **处置**
   - 完成标准：按根因给出具体动作（优化 OB/换模块/重分配设备名/提供驱动器信号等）；涉及强制值时先停止强制（ce24）。
   - 判停条件：需修改程序或下载 → 转 `program-download-debug`，并先确认 STOP/输出安全（ce25/ce42）。

5. **验证**
   - 完成标准：LED 恢复正常、诊断缓冲区无新事件、故障现象消失；确认日时钟已设置（时间戳可信，ce44）、安全事件未刷屏（ce43）。

## B — 边界 (Boundary) ★

### 不要在以下情况使用此能力
- 只是要下载/上传/同步程序——用 `program-download-debug`。
- 只是要更新固件/换存储卡——用 `storage-firmware-maintenance`。
- 要理解时间错误/中断的底层机制原理——用 `realtime-scan-interrupt`。

### 作者在书中警告的失败模式
- **ce24**：忘记强制值随项目/存储卡持续生效并传播——必须显式停止强制。
- **ce25/ce26**：误把 RUN 下载当无风险小改、以为删除逻辑立即失效。
- **ce39**：把 8x 扩展指令错误码当无害提示（CPU 仍 RUN）导致带病运行。
- **ce43/ce44**：安全事件刷屏冲掉关键线索；日时钟未设置致时间戳不可信。
- **ce42**：带分布式 I/O 或强制点时 STOP 下无法启用输出。

### 作者的盲点 / 时代局限
- 手册锁定 V4.2（2016）；新型号诊断缓冲区容量/事件码须查新版。
- 诊断缓冲区只保留最近 50 条/10 条永久，历史久远的故障可能已被覆盖。
- 厂商视角：只讲西门子诊断体系；功能安全与网络安全的诊断缺席。

### 容易混淆的邻近能力
- `program-download-debug`、`realtime-scan-interrupt`、`communication-config`、`storage-firmware-maintenance`。

## 相关能力（阶段 3 填充）

- composes-with: `program-download-debug`, `storage-firmware-maintenance`, `communication-config`
- 对照: `realtime-scan-interrupt`（提供时间错误/中断判据）
