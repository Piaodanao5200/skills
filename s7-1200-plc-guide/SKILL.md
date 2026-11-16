---
name: s7-1200-plc-guide
description: |
  S7-1200 可编程控制器系统手册的可发现主入口，面向工程实践中的决策、计算、流程与排障。覆盖功率预算与供电选型、用户程序结构决策、数据一致性、通信协议选型与组态、在线诊断排障、存储卡固件与出厂复位维护、V3 到 V4 设备更换兼容等高频独立意图；纯指令语法、订货号与针脚表不在此入口，走检索参考。
metadata:
  cangjie.generated-by: cangjie-tools v2.5.0
  cangjie.variant: single
  cangjie.bundle-id: bundle.s7-1200
  cangjie.capability-count: 13
  cangjie.entrypoint-count: 1
---
# 西门子 S7-1200 可编程控制器系统手册 — 全书能力入口

## 触发与不触发

**适用**：与本书能力域相关的咨询与任务（见下方路由表的意图列）。
**不适用**：
- 逐条指令语法与参数表速查（位逻辑/定时/计数/数学等，走手册检索）
- 订货号、针脚定义、端子排备件套件对照表（走 reference-tables.md 检索）
- 与三菱/AB 等其他品牌 PLC 的对比与迁移
- 经认证的功能安全（Failsafe）与网络安全设计

## 核心原则（常驻速览，概览类问题读到这里即可回答）

1. 先决策再操作：功率预算、程序结构、协议角色都要先定方向，避免返工。
2. 安全前置：独立于 PLC 的急停/机电超控不可省，STOP 不等于安全。
3. 先取证后处置：排障先看 LED 与诊断缓冲区，再按事件到 OB 映射定位。
4. 不可逆操作先告知：固件更新、出厂复位、设备更换前确认边界与回退可能。
5. 数据一致性显式化：CPU 只保证单值原子，多值须用 DIS_AIRT/EN_AIRT 等机制。

## 能力路由（先读本表，按意图加载 1 张能力卡）

| 用户意图 | 先读 | 补读/备注 |
|---|---|---|
| 算 S7-1200 功率预算；判断要不要加外部 24V 电源；选 CPU 能否带动模块清单；算 5V/24V 电流余量 | references/capabilities/power-budget-sizing.md | references/capabilities/power-wiring-installation.md、references/capabilities/device-replacement.md、references/capabilities/technology-motion-config.md |
| 决定逻辑放 OB/FC/FB/DB 哪种块；多台同类设备复用控制逻辑；排查跨扫描数据丢失；规划线性还是模块化结构；规划调用嵌套层级 | references/capabilities/program-structure-decision.md | references/capabilities/data-types-addressing.md、references/capabilities/realtime-scan-interrupt.md、references/capabilities/data-consistency.md |
| 解决多值读写撕裂/HMI 值对不上；HMI 改的值被覆盖丢失；配过程映像分区 PIP；传大结构选传值还是传引用；PROFINET 一致性数据读写 | references/capabilities/data-consistency.md | references/capabilities/program-structure-decision.md、references/capabilities/realtime-scan-interrupt.md、references/capabilities/communication-config.md |
| 选 PROFINET/PROFIBUS/PtP/Modbus 并组态；PROFINET 数据交换不运行；运行时改 IP/设备名；多 Modbus TCP 连接分配端口；RS485 终端电阻；启用 PUT/GET 访问 | references/capabilities/communication-config.md | references/capabilities/data-consistency.md、references/capabilities/power-wiring-installation.md、references/capabilities/online-troubleshooting.md |
| CPU 亮灯/停机排查；解读诊断缓冲区；时间错误/诊断错误事件响应；PROFINET 从站/模块故障定位；模拟量 32767 歧义判定；用 Trace 捕捉间歇异常 | references/capabilities/online-troubleshooting.md | references/capabilities/program-download-debug.md、references/capabilities/storage-firmware-maintenance.md、references/capabilities/communication-config.md、references/capabilities/realtime-scan-interrupt.md |
| 传送卡/程序卡/固件卡选型与制作；更新 CPU 固件；格式化存储卡；恢复出厂/复位并保留 IP；备份与恢复 CPU；忘记密码恢复访问 | references/capabilities/storage-firmware-maintenance.md | references/capabilities/device-replacement.md、references/capabilities/program-download-debug.md、references/capabilities/online-troubleshooting.md |
| 用 V4.2 CPU 更换 V3.0 CPU；判断能否换型/回退；换型后编译下载失败排查；运动控制 TO 版本升级；DB 预留区处理；传送卡不兼容处理 | references/capabilities/device-replacement.md | references/capabilities/storage-firmware-maintenance.md、references/capabilities/power-budget-sizing.md、references/capabilities/technology-motion-config.md、references/capabilities/program-download-debug.md |
| 评估响应延迟有几个扫描周期；组态循环中断与相移；硬件中断建了不触发；延时中断与定时器；时间错误/队列溢出分析；V3→V4 OB 可中断属性复核 | references/capabilities/realtime-scan-interrupt.md | references/capabilities/program-structure-decision.md、references/capabilities/data-consistency.md、references/capabilities/technology-motion-config.md、references/capabilities/online-troubleshooting.md |
| 下载失败恢复；在线/离线同步与上传；监视/修改/强制调试；RUN 模式下载与风险评估；DB 值快照；时间戳冲突排查 | references/capabilities/program-download-debug.md | references/capabilities/online-troubleshooting.md、references/capabilities/data-consistency.md、references/capabilities/storage-firmware-maintenance.md |
| 编码器漏计/高速计数；按位置触发输出；PID 整定与采样；4-20mA 标定工程单位；配开环/闭环轴；运动控制 ErrorID 排查 | references/capabilities/technology-motion-config.md | references/capabilities/realtime-scan-interrupt.md、references/capabilities/data-types-addressing.md、references/capabilities/power-budget-sizing.md、references/capabilities/device-replacement.md |
| SCL 绝对地址编译报错；读到旧值/0 值；数值溢出或浮点精度；优化 DB 无绝对地址；I/O 地址分配与字节对齐 | references/capabilities/data-types-addressing.md | references/capabilities/program-structure-decision.md、references/capabilities/technology-motion-config.md |
| 控制柜布局与散热间距；接线/接地前安全前提；灯/感性负载抑制；安装后逐条安全核对 | references/capabilities/power-wiring-installation.md | references/capabilities/power-budget-sizing.md、references/capabilities/device-replacement.md |
| 从零规划新设备的控制系统；担心漏安全回路或操作员站；团队要统一分区与符号表规范 | references/capabilities/system-design-workflow.md | references/capabilities/power-budget-sizing.md、references/capabilities/power-wiring-installation.md |

**非能力类查询**：
- 书名/作者/章节/整书概览 → references/overview.md
- 术语解释 → references/glossary.md
- 决策规则速查（不需要原文依据时） → references/cheatsheet.md
- 完整意图与关键词索引（本表未覆盖的意图先查这里） → references/capability-index.md

## 加载规则

- 每次任务先读本文件，再按路由表加载 **1** 张能力卡；任务明确跨域时最多加载 2 张。
- 概览/书名类问题不加载能力卡，用「核心原则」与 overview.md 回答。
- 路由表与 capability-index.md 都无法命中的意图，明确告知超出本书范围，不要硬套。

## 边界与判停

- 用户要求的操作涉及运行中设备且未确认已做安全隔离时，先停下要求断电与急停确认。
- 固件跨大版本、V3 到 V4 更换、恢复出厂等不可逆动作，未获用户明确确认前不执行。
- 问题属于其他品牌或新版固件（V4.4+）特性、本手册 V4.2 未覆盖时，停止并说明范围限制。
