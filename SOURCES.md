# 来源清单

每个 skill 的出处、版本、审阅结论。更新时按这里回到原始仓库比对。

| skill | 来源仓库 | 路径 | 提交 SHA | 装入日期 | 审阅 |
|---|---|---|---|---|---|
| `find-skills` | `vercel-labs/skills` | `skills/find-skills/SKILL.md` | `a41bdd0` | 2026-11-16 | 纯指令，无脚本 |

## 审阅记录

### find-skills

- **来源**：官方 `vercel-labs/skills`，非第三方 fork。skills.sh 排名第一，190 万+ 安装量。
- **内容**：单文件 `SKILL.md`，5472 字节，无 `scripts/`、无 `node_modules`、无网络请求代码。全部是指令文字，由 agent 自己执行。
- **依赖**：`npx skills` CLI。本机已验证可用，版本 1.8.1，`find` 在无 TTY 环境下正常返回。
- **风险**：skill 指导 agent 用 `npx skills add <pkg> -g -y` 安装第三方 skill，`-y` 跳过确认。**真正危险的是它要装的包，不是这个 skill 本身。** 装任何包之前必须自己看过源码。

## 已知冲突

`npx skills add -g` 会把 skill 装进各 agent 的 skills 目录（实体副本），**绕过本仓库的 Junction 机制**。同一 skill 可能在 4 个 agent 目录里各存一份，`check.ps1` 也认不出它们属于本仓库。

因此约定：

| 操作 | 用什么 |
|---|---|
| 搜索 skill | `npx skills find <关键词>` （只读，安全） |
| 安装 | 把 `SKILL.md` 拉进 `skills/<名称>/`，再跑 `挂载.cmd` |
| 不要用 | `npx skills add`、`npx skills update` |
