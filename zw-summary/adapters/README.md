# adapters/ — 本机改造：ego 取数 + AudioTranscribe 转写

原版 `zw_summary.py` 是 macOS + 有字幕场景写的，在本机（Windows、B站 412、抖音 blob）
开箱跑不通。本目录是替代链路，**不动原脚本**，只补取数和转写两层。

## 一句话架构

```
链接 → ego_fetch.py（浏览器内拿元数据 + 真实直链 + 下媒体/图片）
     → extract_frames.py（抽帧拼图 + 音轨）
     → AudioTranscribe（音轨转文字，带时间戳）
     → 人工/模型按 SKILL.md 的七块结构写 summary.md
```

## 为什么不用原脚本的 urllib

| 平台 | 原路径的问题 | 本机实测 |
|---|---|---|
| B站 | `api.bilibili.com/.../view` 裸请求 | **HTTP 412 风控页**（直连、走代理都是 412），同一接口在 ego 里正常 |
| 抖音 | yt-dlp 要 Fresh cookies；分享页 SSR 需 ttwid | 桌面页已改 RSC 渲染，`_ROUTER_DATA` 不存在；视频是 `blob:`，真实地址只在 `performance` 资源记录里 |
| 小红书 | 无 xsec_token 直接 404 | token 只在搜索页 state 的 `search.feeds[].xsecToken` 里，必须从站内搜索拿 |
| YouTube | 字幕接口 IP 级 429 | 页面内数据正常，字幕 CDN 仍可能 429 |

## 用法

三档输出，按「花多少 token」选（另有 `--search` 从关键词直接起跑，见下）：

```bash
# ① 批量扫描：跑了 N 条只要一行结论，不刷屏
python ego_fetch.py <链接> --quiet
# → SUMMARY: ok=1 fail=0 platform=douyin dur=91s media=2 tags=8 comments=9 out=...
# 失败也会打这一行（err=...），不会静默

# ② 生成索引：扫已落盘目录，一次看 N 条标题/时长/指标
python ego_fetch.py --digest
# → douyin_770715575531249  douyin   91s  like69    第49集 | 你的AI Agent，也需要一个项目经理
#   --- 共 3 条 ---

# ③ 深读单条：正文全文 + 话题 + 评论（纯文本，无 JSON 噪声）
python ego_fetch.py <链接> --full
# → ===== FULL =====  平台/标题/作者/互动/话题/链接 + 正文全文 + 字幕轨道 + 评论（带 ↳ 回复层级）
```

其他参数：

```bash
python ego_fetch.py "https://www.douyin.com/video/<19位ID>" --frames   # 下载+抽帧，默认 @@RESULT@@ JSON
python ego_fetch.py BV1SAWC6YEFg --frames                            # 只有 ID 也行
python ego_fetch.py <链接> --only audio                              # 只取音轨（最小体积，仅转写）
```

### 关键词批量（`--search`：不给链接，只给关键词）

```bash
python ego_fetch.py --search "agent" --platform douyin --limit 5
python ego_fetch.py --search "agent 工作流" --platform xhs --limit 3
python ego_fetch.py "bilibili:agent" --limit 5        # 平台:关键词 简写
```

流程是 `站内搜索 → 取前 N 条 → 逐条落盘 → 直接给索引表`，最后一行：

```
SUMMARY: search=douyin kw=agent found=5 ok=4 fail=1 list=~/videosum/search_douyin_agent/search.json
```

候选列表（id/标题/作者/时长播放）和取到的目录都写进 `search.json`，索引表格式与 `--digest` 完全一致，
所以看完索引后照旧用 `--full` 深读挑中的那几条。

单条取数是**独立子进程**（每条一个干净浏览器会话）：某条失败或超时（300s）只跳过它，不影响其余。

### 两阶段流水线（省 token 的标准跑法）

```
--quiet 跑 20 条   →  141 字节 / 1 行
--digest 看索引    →  判断哪 3 条值得深读
--full 读那 3 条   →  5386 字节，含 20 条评论和全文
```

也可以把第一步换成 `--search "关键词" --limit 20`：一条命令搜完并落盘，输出一张索引表。

实测同一视频的输出体积：`--quiet` 141B ｜ 默认 JSON 2206B ｜ `--full` 5386B ｜ 落盘的 `meta.json` 7308B。
`--full` 比 `meta.json` 更小是因为去掉了媒体 URL 数组——那些字节对理解内容没有价值。

产物落在 `~/videosum/<平台>_<ID>/`：

```
media_audio.m4a   /  media_video.mp4
img/img_01.jpg    图文笔记的图（正文常在图里）
frames/           抽帧 jpg + sheet_*.jpg 拼图 + audio.m4a + manifest.json
meta.json         标题/作者/正文/互动数/话题/评论/图片直链/字幕轨道
```

接着两步交给模型：
1. `AudioTranscribe(media_audio.m4a)` → 带时间戳的文稿
2. `ViewImage(frames/sheet_001.jpg)` → 画面内容（口播人、UI 截图、字幕条）

## 平台覆盖（2026-11-16 实测）

| 平台 | 元数据 | 音轨 | 画面 | 评论（`--full`） | 搜索（`--search`） | 备注 |
|---|---|---|---|---|---|---|
| 抖音 | ✅ | ✅ 0.6MB/90s | ✅ 23 帧 | ✅ 9 条含作者回复 | ✅ 38 条候选 | 需等 9s 起播；搜索卡片无 title 属性，按时长/播放量/标题/作者的行序解析 |
| 小红书 | ✅ | 视频笔记 ✅ | 图文 ✅ 5 图 | ✅ 读 `__INITIAL_STATE__.comments` | ✅ | 必须带 `xsec_token`，搜索拿到的 href 整条保留 |
| B站 | ✅ 含播放/弹幕 | ✅ DASH 音轨 | ⚠️ 视频流需 Range，首条常 567 | ✅ 20 条带赞数 | ✅ | 评论走 `x/v2/reply`，浏览器内 fetch 不受 412 影响 |
| YouTube | ⚠️ | ⚠️ | ⚠️ | ✖ 未实现 | ✅ 读 `ytInitialData` 递归找 `videoRenderer` | 字幕 CDN 429 未解；下载走 `yt_fetch.py` |
| X | ✅ 文案 | ⚠️ | ⚠️ | ✖ 未实现 | ✖ **站点报错页** | 搜索页稳定返回「出错了」，点重试也无效（2026-11-16 实测），只能喂单条链接 |


## 已知坑

- **ego 的 `console.log` 走 stderr**，stdout 基本为空，解析结果要两个流都看。
- **`page.evaluate` 里读不到 Node 侧变量**。曾把 `const FULL = __FULL__` 写在模块顶层，页面里报 `FULL is not defined`，整个评论区静默失败。正确做法：把占位符放在 evaluate 回调内部，替换后成为页面里的字面量。
- **抖音评论项是嵌套的**：用户评论的 `[data-e2e="comment-item"]` 里又包着作者回复的同类节点。用 `innerText` 取最长行会把回复算到评论者头上（归因错误）。正解是用「N天前·地区」这行做锚点，正文在它上一行；层级靠 `parentElement.closest('[data-e2e="comment-item"]')` 判断。
- **抖音互动数**：`like` 和 `collect` 常取到同一个值（都是 `video-player-digg` 的回退），别当两个独立指标看。
- **抖音话题标签常取不到**：完整文案依赖 `[data-e2e="video-desc"]` 是否渲染，时机不稳；正文退化成 196 字的 `og:description` 时，`#话题` 就没有了。**不要用 `document.body.innerText` 兜底捞文案**——页面底部「推荐视频」的话题会被误抓，曾把标题污染成「热门：游戏视频推荐…」。宁可缺标签，不能错。
- **B站视频流**：第一条 baseUrl 常返回 `HTTP 567`，脚本会自动试下一条；只做转写时用 `--only audio` 更快。
- **小红书**：`explore/<id>` 不带 token 时页面 404 但 HTTP 200，要判断 `__INITIAL_STATE__.note.noteDetailMap` 是否为空。
- **B站搜索页右侧「热搜」卡片也是 `/video/` 链接**：没有作者、没有播放数据，`innerText` 只有个「BLG」。
  第一版没过滤，搜 "agent" 的第 1 条结果变成了「GEN vs BLG」。现在 B站候选缺 author 且缺 extra 就直接丢。
- **抖音搜索卡片没有 title 属性、没有 `data-e2e`**：整段 `innerText` 是「时长 / 播放量 / 标题+简介 / @作者 / 时间」
  的固定顺序，只能按位置解析（先吃掉 `\d{1,2}:\d{2}`，再吃纯数字播放量，下一行才是标题）。用 `img[alt]` 或
  `a.innerText` 当标题会全空，导致「搜索有 38 条候选却一条都不合格」。
- **X 搜索页返回「出错了。请尝试重新加载。」**：`f=top` 和 `f=live` 都一样，点三次「重试」也不恢复，
  是站点侧对搜索接口的限制，不是代码问题。错误信息里已写明，别反复重跑浪费时间。
- **B站合集/多P 视频的 `duration` 是总时长**：搜到「全748集」这类课程会显示 105912s（29 小时），
  不是单集时长。别拿它当视频长度判断，建议看 `--full` 里的实际首集信息。
- 重装或升级 zw-summary 会覆盖整目录，本目录需另行备份。
