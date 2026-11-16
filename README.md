# skills —— 多 agent 共用的 skill 仓库

一份实体，挂到多个 agent。改一次，所有 agent 同时生效。

```
D:\collaborative project\skills\          ← 实体（唯一一份，进 git）
├─ my-skill-a\
│  ├─ SKILL.md                            必需，含 name + description
│  └─ scripts\                            可选，被 SKILL.md 引用
└─ my-skill-b\
```

各 agent 目录里只有一个 **Junction**，指向这里：

```
C:\Users\12647\.workbuddy\skills\my-skill-a   →  D:\collaborative project\skills\my-skill-a
```

## 已收录

| skill | 用途 | 运行前提 | 备注 |
|---|---|---|---|
| `find-skills` | 搜技能市场 | 无 | — |
| `zw-summary` | 视频/播客 → 结构化中文总结（B站/YouTube/X/抖音/小宇宙） | `ego-browser` CLI + Python(yt-dlp) + ffmpeg | 取数链路改造成本机版，见 `zw-summary/adapters/README.md`；**X 的搜索页不可用，只能喂单条链接** |

新增 skill 后在这一行表里补一行——别人挂进来第一件事就是看这张表。

## 加一个 skill（4 步）

1. 把 skill 目录复制进来，形如 `skills\<目录名>\SKILL.md`
2. **目录名必须与 SKILL.md 里 `name` 字段完全一致**（链接名就是 skill 名，不一致的会被 mount 跳过）
3. 确认 frontmatter 有 `name` 和 `description`（缺 description，agent 不知道何时调用）
4. 双击 `挂载.cmd`，然后重开 agent 会话

## 三个脚本

| 双击 | 实际脚本 | 作用 |
|---|---|---|
| `挂载.cmd` | `mount.ps1` | 扫描本目录，给每个已安装的 agent 补齐链接。已存在的跳过；指向别处的报冲突（`-Force` 才重建）；**不合规的 skill 跳过不挂** |
| `卸载.cmd` | `unmount.ps1` | 只删链接，**不动实体文件**。不属于本仓库的链接（如 ego-browser）一律跳过 |
| `校验.cmd` | `check.ps1` | 查不合规、查断链、查漏挂。有错误时退出码 1 |

`.ps1` 用英文名是因为 `.cmd` 里不能出现中文——`chcp 65001` 会改变 cmd 解析后续行的代码页，中文路径会变成乱码找不到文件。`.cmd` 名字本身不受影响。

参数（命令行直接跑时）：

```powershell
.\mount.ps1 -DryRun            # 只打印计划，不动文件
.\mount.ps1 -Only WorkBuddy    # 只挂一个 agent
.\mount.ps1 -Force             # 目标已存在且指向别处时强制重建
.\unmount.ps1 -Skill my-skill-a  # 只卸一个
.\check.ps1                    # 全量检查
```

双击 `.cmd` 与命令行跑 `.ps1` 等价。`.cmd` 只是带了 `-ExecutionPolicy Bypass`，因为本机执行策略为默认 Restricted，双击 `.ps1` 会被拦。

## 合规判定（mount 与 check 共用）

| 检查 | 不合规的后果 |
|---|---|
| 有 `SKILL.md` | mount 跳过，不进任何 agent |
| frontmatter 用 `---` 包裹 | 同上 |
| 有 `name`，且等于目录名 | 同上 |
| 有 `description` | check 报错，mount 仍挂（不致命，只影响 agent 选择） |

## 为什么用 Junction 不用 Symlink

| | Junction | SymbolicLink |
|---|---|---|
| 管理员权限 | **不需要** | 需要，除非开开发者模式 |
| 本机当前状态 | 可用 | 不可用（`AllowDevelopmentWithoutDevLicense` 未设置） |
| 只能指向目录 | 是 | 否 |

`~\.workbuddy\skills\ego-browser` 本来就是 Junction，已验证 WorkBuddy 能正常加载。

## 安全

**挂上就等于给每个 agent 任意代码执行权限。** skill 里的 `scripts/` 会被 agent 直接运行。从网上抄来的 skill，放进来之前必须打开 SKILL.md 和脚本看一遍，尤其注意：

- `curl ... | bash`、`iwr ... | iex` 这类下载即执行
- 读 `~/.ssh`、`.env`、浏览器 cookie、系统凭据的代码
- 向外发送请求，把本地内容带出去
- `rm -rf`、`Remove-Item -Recurse` 作用于宽泛路径

这条没有工具能替你兜住，只能人看。

## 已知限制

| 限制 | 说明 |
|---|---|
| Cursor 不通用 | Cursor 用 `.cursor/rules/*.mdc`，格式与 `SKILL.md` 完全不同，需单独转换 |
| 并发写依赖 | skill 带 `node_modules/` 时，多个 agent 同时调用同一实体目录可能撞写。一般只读执行无碍 |
| 需重启会话 | 挂载后当前会话看不到新 skill，要重开 |
| 格式前提 | Claude Code / Codex / CodeBuddy / WorkBuddy 均遵循 Agent Skills 规范（`SKILL.md` + YAML frontmatter），可共用 |
