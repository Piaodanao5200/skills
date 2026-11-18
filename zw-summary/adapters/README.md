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

## bili_api.py — B站 API 直取（2026-11-18 新增，B站首选）

B站**不开浏览器**：直接调 `api.bilibili.com` + wbi 签名，秒级返回，产物格式与 ego_fetch 对齐。
上面表格里的「412 风控」已破解：裸 urllib 没有 cookie 才会 412；带上 buvid3 + UA + Referer 就通。

```bash
python bili_api.py --search "关键词" --limit 5 --quiet   # 搜索+逐条详情落盘
python bili_api.py BV1RG33zPEof                          # 单条详情
python bili_api.py --digest                              # 索引表
```

- 字段：标题/简介/作者/发布时间/时长/七项统计(view·danmaku·reply·favorite·coin·share·like)/标签/20条评论
- wbi 密钥每日更替，自动缓存于 `videosum/.bili_api_cache.json`（buvid3 长期缓存）
- 依据 SocialSisterYi/bilibili-API-collect；评论走 `/x/v2/reply/main`（旧 `/x/v2/reply` 已返回空）
- 未登录可查公开数据；单条评论仅 3 条热评时是 guest 限额，登录 cookie 可解锁完整楼层

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

# X 没有可用搜索，按账号巡（主页 = 最近 6 条；--full = 20 条）
python ego_fetch.py "https://x.com/AnthropicAI" --quiet        # → posts=6
python ego_fetch.py "https://x.com/AnthropicAI" --full         # 正文放宽到 600 字 + 20 条
python ego_fetch.py "https://x.com/AnthropicAI/media" --quiet  # 媒体页：顺带收图并落盘，tab=media
python ego_fetch.py "https://x.com/AnthropicAI/with_replies"   # 回复页，tab=with_replies
python ego_fetch.py "https://x.com/OpenAI/status/2122750124543926515" --full   # 单条 + 主贴下回复
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

### X 没有站内搜索：用 `--urls` 走站外补位

X 的 `--search` 在本机账号上不可用（见「已知坑」）。要按**主题**搜集而不是按账号巡，走这条：

```bash
# 1. 站外搜：Google/Bing/任意搜索 跑 `site:x.com "关键词"`
# 2. 把结果整段复制进 txt（不用整理，脚本自己抠链接）
# 3. 一条命令全取完
python ego_fetch.py --urls links.txt --out ~/videosum/x-topic
```

`--urls` 会从任意文本里认出 x/抖音/B站/YouTube/小红书 链接，自动补协议、去重
（搜索结果常写成裸域名 `x.com/dhh/status/123`，也认）。之后照旧 `--digest` 看索引、`--full` 深读。

实测：5 条混合链接（4 条 X + 1 条 B站）全部 `ok=1`，索引表直接可用。

**代价要知情**：站外搜索的覆盖面取决于搜索引擎对 X 的收录，拿不到「全部讨论」，只能拿到被外部索引到的热门帖子。
要做穷尽式主题抓取，X 这条路目前不通。

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

## 平台覆盖（2026-11-18 复测）

| 平台 | 元数据 | 音轨 | 画面 | 评论（`--full`） | 搜索（`--search`） | 备注 |
|---|---|---|---|---|---|---|
| 抖音 | ✅ | ✅ 0.6MB/90s | ✅ 23 帧 | ✅ 9 条含作者回复 | ✅ 38 条候选 | 需等 9s 起播；搜索卡片无 title 属性，按时长/播放量/标题/作者的行序解析 |
| 小红书 | ✅ | 视频笔记 ✅ | 图文 ✅ 5 图 | ✅ 读 `__INITIAL_STATE__.comments` | ✅ | 必须带 `xsec_token`，搜索拿到的 href 整条保留 |
| B站 | ✅ 含播放/弹幕 | ✅ DASH 音轨 | ⚠️ 视频流需 Range，首条常 567 | ✅ 20 条带赞数 | ✅ | 评论走 `x/v2/reply`，浏览器内 fetch 不受 412 影响 |
| YouTube | ⚠️ | ⚠️ | ⚠️ | ✖ 未实现 | ✅ 读 `ytInitialData` 递归找 `videoRenderer` | 字幕 CDN 429 未解；下载走 `yt_fetch.py` |
| X | ✅ 含回复/转推/赞/浏览 | ✖ 未接（HLS） | ✅ 图片按原格式落盘 | ✅ 主贴下 20 条回复 | ✖ **账号侧搜索受限** | 搜索不可用 → 按账号巡：`/handle`、`/media`、`/with_replies`，落 `posts.json`（`tab` 标明当前是哪个页） |


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
- **X 搜索页返回「出错了。请尝试重新加载。」**：`f=top` / `f=live` / 引号短语 / 桌面 / 移动端全部一样，
  点三次「重试」也不恢复，且页面**一个 GraphQL 请求都没发出去**（`performance` 里 0 条 `/i/api/graphql/`），
  说明是账号侧搜索权限受限，不是参数或网络问题。替代路径：用户主页时间线（批量）、探索页趋势（单条）。
- **X 图片 URL 不能写死 `format=jpg`**：原图可能是 png，`?format=jpg&name=orig` 直接 404。
  `?format=jpg&name=900x900` 可用（服务端会转），但最稳是保留页面给的 format、只把 `name` 提到 `orig`。
- **X 的 `ok` 判定不能只看媒体数**：纯文字推文没有任何媒体，第一版因此把能读到的推文判成失败
  （报「未取到媒体地址」）。现在 X 分支额外允许「有正文或 items 非空」即算成功。
- **X 互动数会串位**：转推按钮的 `innerText` 可能同时含转推数与引用数（曾拼出 `70667065`），
  浏览数还带「查看 / View」后缀。统一正则只取第一个数字段。
- **X 主页模式的 `arts[1..]` 是下一条推文，不是回复**：`--full` 读评论必须排除主页，否则把相邻推文当评论。
- **X 图片落盘统一存成 `.jpg`**，但内容是原始格式（可能是 png）。读图前判文件头，别信扩展名。
- **X 引用推有嵌套的两段 `tweetText`**：外层是引用者的评论，内层是被引原文。目前只取 `arts[0]` 的第一段，
  被引内容不单独出字段——需要时用 `--full` 看正文里的拼接。
- **X 的全局 `video` 循环会扫到右栏推荐位**：曾让纯文字推文显示 `dur=37s` 并带上别人的视频地址。
  X 分支里现在先把 `duration` 与 `videos` 清空重来，只认主贴内部的 `video`。
- **B站合集/多P 视频的 `duration` 是总时长**：搜到「全748集」这类课程会显示 105912s（29 小时），
  不是单集时长。别拿它当视频长度判断，建议看 `--full` 里的实际首集信息。
- 重装或升级 zw-summary 会覆盖整目录，本目录需另行备份。

## x_api.py — X 取数（2026-11-18 新增，ego 捕获通道）

**为什么不直连 HTTP**：X 有 TLS 指纹 + x-client-transaction-id 反爬，requests/httpx/twikit
在 Python 层一律被拦（实测 500/404）。所以走 **ego 真 Chrome 页面内截获**：挂 fetch+XHR 钩子，
截获平台发给搜索/时间线的原始 JSON——真实指纹，风控暴露面与真人浏览一致。

```bash
# 两步流水（python 不拉起浏览器，避免沙箱拦截）
python x_api.py --gen --search "关键词" --mode live > D:/WorkBuddy/ego/x-capture-run.mjs
ego-browser nodejs < D:/WorkBuddy/ego/x-capture-run.mjs > D:/WorkBuddy/ego/x-capture-out.json
python x_api.py --parse D:/WorkBuddy/ego/x-capture-out.json --search "关键词" --limit 10
# 用户时间线同理：--user handle → 解析 --user handle
```

**状态（2026-11-18 实测）**：
- ✅ 用户时间线：全通（UserByScreenName + UserOriginalsTimeline，8 条推文含 like/rt/reply/view 全字段）
- ❌ 搜索：X 服务端对 SearchTimeline 返回 404——**连页面自己的请求都 404**（主页/时间线全 200），
  属环境/IP 级限制，换网络环境后再试；twikit 库直连同样被拦
- cookie 提取：`D:/WorkBuddy/ego/cookie-x.mjs`（CDP 拿 auth_token/ct0/twid，登出后重跑）
- **坑**：addScriptToEvaluateOnNewDocument 注册会跨运行残留，钩子变量必须版本化（__xCap_v4），
  否则旧脚本先建变量、新钩子被 guard 跳过；XHR 设了 responseType='json' 时 responseText 抛异常，须用 response
