# 数据一致性保障

## R — 原文 (Reading)

> "CPU 为所有基本数据类型……保持数据一致性。值的读/写操作无法中断。如果用户程序共享存储器中在程序循环 OB 和中断 OB 之间生成的多个值，用户程序还必须确保在修改或读取这些值时保持一致性。由于通信会周期性地中断用户程序，因而不能保证 HMI 能够同时更新 CPU 中的多个值。"
>
> — Siemens AG，《S7-1200 可编程控制器系统手册》V4.2，ch7.4 p213；ch9.5.5 p484–485

## I — 方法论骨架 (Interpretation)

"CPU 保证数据一致性"这句话极易被误读——它只保证**单个基本数据类型**的读/写不可中断（原子性），不保证**多个相关值**同时正确。当循环 OB 与中断 OB 共享若干相关变量，或 HMI 经通信周期性打断程序去更新画面时，同一组值可能来自不同扫描周期，出现"半新半旧"的撕裂数据。保障方法有三层：①过程映像分区（PIP）——把 I/O 分配给 PIP0–4 或自动更新，绑定特定 OB 在事件时刷新；关键陷阱是"分配了 PIP 但未绑 OB"等价于"无 PIP"，该 I/O 永不刷新。②参数传递——向 FB 传复杂结构（如 20 元素 STRUCT）时用 IN/OUT 走引用，且块与 DB 的"优化访问"设置必须一致，否则退化为副本复制，运行中 HMI 改的值会被 FB 结束时的副本回写覆盖（f29 症状）。③临界区保护——用 DIS_AIRT 延迟中断、读写多个值、EN_AIRT 恢复，保证一组值在同一扫描周期内逻辑一致；DIS_AIRT 可多次累加，须等量 EN_AIRT 且 RET_VAL=0 才真正恢复，EN_AIRT 必须在同一 OB 或其调用链内执行。通信缓冲区同样要避免在循环 OB 与中断 OB 同时读写，必须配合 DIS_AIRT/EN_AIRT。对 PROFINET 一致性数据用 DPRD_DAT/DPWR_DAT，且须从过程映像自动更新中移除该区域。SCL 的 PEEK/POKE 索引寻址、READ_DBL/WRIT_DBL 在装载/工作存储器间搬运，也要注意优化 DB 无固定地址的约束。

## A1 — 应用示例 (Application Example)

### 示例 1: 三值一致性（worked_example，依据 f30 的 walkthrough）
- **类型与来源**: 合成演练——手册 ch7.4 p213 给出三段式，未给完整项目。
- **问题/输入**: 循环 OB 与中断 OB 共享 3 个相关值（如设定值、反馈值、模式字），要求同一周期一致。
- **方法论的使用**: 在循环 OB 中用 `DIS_AIRT → 读写 3 值 → EN_AIRT` 包住临界区，RET_VAL=0 确认恢复中断。
- **结论**: 3 个值在同一扫描周期内逻辑一致，不被中断撕裂。
- **结果**: 与手册三段式一致；延迟中断会增大 OB 响应等待，是已知代价。

### 示例 2: PIP 未绑 OB 陷阱（worked_example，依据 f04）
- **类型与来源**: 合成演练，手册 ch5.1.2 p91–92。
- **问题/输入**: 某模拟量 AI 分配给 PIP1，希望只在循环中断 OB10 更新，但忘记把 PIP1 绑到 OB10。
- **方法论的使用**: 按 5 步流程：设 PIP1 → 绑 OB10 → 若未绑则等价"无 PIP"，该 AI 永不刷新。
- **结论**: 必须绑定 OB 才会更新，否则静默失效。
- **结果**: 与手册"分配 PIP 未绑 OB 等价无"的陷阱一致。

### 示例 3: 优化不匹配丢数据（worked_example，依据 f29）
- **类型与来源**: 合成演练，手册 ch7.3.6 p212。
- **问题/输入**: HMI 在 FB 运行期改 STRUCT 某值，而 DB 与 FB 的优化设置不同。
- **方法论的使用**: 识别为副本回写覆盖——该值在 FB 结束时被副本写回而丢失；最佳解是匹配优化设置。
- **结论**: 优化设置一致时走引用，HMI 修改不会丢。
- **结果**: 与手册根因分析一致。

## A2 — 触发场景 (Future Trigger) ★

### 用户会在什么情境下需要这个能力?
1. HMI 画面上几个相关的值"对不上"、来自不同时刻。
2. 循环 OB 与中断 OB 共享变量，担心读写撕裂。
3. HMI 在运行中改的值被程序覆盖"丢失了"。
4. 配置了过程映像分区（PIP）但某个 I/O 始终不刷新。
5. 要在 FB 间传大结构，纠结传值还是传引用、优化设置怎么配。
6. 用 DPRD_DAT/DPWR_DAT 做 PROFINET 一致性数据，不知道要不要移出自动更新。

### 语言信号 (用户的话里出现这些就应激活)
- "数据不一致 / 值对不上 / 撕裂" / "data inconsistency / torn data / values mismatch"
- "HMI 改的值丢了 / 被覆盖" / "HMI write lost / overwritten"
- "DIP/DIS_AIRT / EN_AIRT 配对" / "DIS_AIRT EN_AIRT pairing / interrupt lock"
- "过程映像分区 / PIP 不刷新" / "process image partition / PIP not updating"
- "传值还是传引用 / 优化访问" / "pass by value / reference / optimized block access"

### 与相邻能力的区分（阶段 3 定稿）
- 与 `program-structure-decision` 的区别：那边定"状态存在哪个块"，本能力解决"多值读写不撕裂"；优化 DB 不匹配丢数据归本能力（composes-with）。
- 与 `realtime-scan-interrupt` 的区别：那边提供 DIS_AIRT/EN_AIRT 的底层中断配对规则，本能力把它用于保护数据临界区（composes-with）。
- 与 `data-types-addressing` 的区别：那边保证单个变量类型/地址正确，本能力保证多个变量的复合一致性。
- 与 `communication-config` 的区别：那边组态通信连接，本能力处理通信缓冲区与一致性数据的读写时序（composes-with）。

## E — 可执行步骤 (Execution)

**必需输入**: 共享变量的清单与读写方（循环 OB/中断 OB/HMI）；是否跨通信；DB 与块的优化设置；I/O 更新方式需求。信息不全先问"哪些值必须同时正确"。
**交付物与完成标准**: 一致性方案表——每行列出"共享值组 → 保护机制（PIP/引用/临界区/一致性指令）→ 配对/设置要求",含失败处理。

1. **识别共享与撕裂风险** — 完成标准：列出所有跨 OB/跨通信的共享值组，标注哪些必须同周期一致。
2. **选保护机制**
   - 完成标准：I/O 刷新方式 → PIP（且必须绑 OB）；大结构传参 → IN/OUT 引用 + 优化设置一致；多值临界区 → DIS_AIRT/EN_AIRT；PROFINET 一致性数据 → DPRD_DAT/DPWR_DAT 并移出自动更新。
3. **实现并核对配对**
   - 完成标准：DIS_AIRT 与 EN_AIRT 等量、同 OB 调用链内、RET_VAL=0 才恢复；判停条件：EN_AIRT 放错 OB 或 RET_VAL≠0 → 中断仍禁用，回到步骤 2 重做。
4. **验证一致性** — 完成标准：模拟中断/HMI 打断，确认一组值来自同一周期；HMI 修改不再被副本回写覆盖。
5. **核对副作用** — 完成标准：记录延迟中断增加的等待时间；确认通信缓冲区未在两 OB 同时读写。

## B — 边界 (Boundary) ★

### 不要在以下情况使用此能力
- 只处理单个变量类型/地址——用 `data-types-addressing`。
- 只设计代码块结构——用 `program-structure-decision`。
- 要组态通信连接本身——用 `communication-config`。

### 作者在书中警告的失败模式
- **ce32**：把"基本数据类型一致"误推为"多个值一致"——相关变量分散读写导致撕裂。
- **ce33**：DIS_AIRT/EN_AIRT 不成对、EN_AIRT 放错 OB、RET_VAL≠0 却以为已恢复。
- **ce37**：对优化 DB/运动控制 DB 误用 ATTR_DB（返回 80B2 或 DB_LENGTH=0）。
- **ce38**：把 WRIT_DBL 当普通变量在循环中高频调用，擦写闪存缩短寿命。
- **f04 陷阱**：PIP 分配未绑 OB 等价"无"，I/O 永不刷新。

### 作者的盲点 / 时代局限
- 手册锁定 V4.2（2016）；一致性数据字节数上限（S7-1200 1/2/4 字节、PROFINET 1024 字节）须按实际固件核对。
- 假设读者理解中断优先级与优化 DB 概念；"移出过程映像自动更新"是易漏步骤。
- 厂商视角：只讲 S7 内部机制，不对比其他 PLC 的一致性模型。

### 容易混淆的邻近能力
- `data-types-addressing`、`program-structure-decision`、`realtime-scan-interrupt`、`communication-config`、`storage-firmware-maintenance`（备份一致性）。

## 相关能力（阶段 3 填充）

- composes-with: `program-structure-decision`, `realtime-scan-interrupt`, `data-types-addressing`, `communication-config`
