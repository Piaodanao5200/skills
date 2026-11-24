---
name: zw-summary
display_name: ZW-视频总结
display_name_en: ZW Video Summary
description: 把视频或播客链接整理成一份结构完整的中文总结笔记。当用户给出 B站、YouTube、X/Twitter、抖音的视频链接或视频 ID，或小宇宙播客单集链接与 episode ID，并要求总结、整理、提炼、做笔记时使用。每份笔记包含元数据表、视频简介原文、一句话总览、按时间轴的分段详解、核心观点提炼、金句摘录，以及与公开资料的事实对照。典型用法：总结这个视频（附链接）、把这条视频整理成笔记、BV1xxxx 讲了什么、这期播客帮我提炼要点。多P合集与数小时长音频同样支持。
description_zh: 给一个视频或播客链接，自动产出结构化的中文总结笔记。支持 B站、YouTube、X、抖音、小宇宙，也接受 BV 号、视频 ID、播客 episode ID。每份笔记含元数据、简介原文、一句话总览、时间轴分段详解、核心观点、金句摘录与事实对照；多P合集与数小时长音频同样支持。
description_en: Turn a video or podcast link into a structured Chinese summary note. Works with Bilibili, YouTube, X, Douyin and Xiaoyuzhou, and also accepts BV ids, video ids or podcast episode ids. Each note includes metadata, the original description, a one-paragraph overview, a timeline-based breakdown, key takeaways, quotes, and a fact-check against public sources. Multi-part collections and multi-hour audio are supported.
version: 1.3.0
author: 张伟
icon: icons/logo.png
allowed-tools: Bash, Read, Write, Edit, WebFetch, WebSearch
---

# ZW-视频总结（B站 + YouTube + X/Twitter + 抖音 + 小宇宙）

> **本机改造（Windows，2026-11-16）**：原 `scripts/zw_summary.py` 依赖 urllib 直连 + `whisper_venv`，
> 在本机跑不通（B站 412、抖音 blob、macOS 路径、无 whisper venv）。改用 `adapters/ego_fetch.py`：
> ego 浏览器取元数据与真实直链 → 下载音轨/视频/图片 → `extract_frames.py` 抽帧 → AudioTranscribe 转写
> → 按本文档的七块结构写总结。**优先走这条链路**，详见 `adapters/README.md`。

## 多 agent 共用（先读这一节）

这个技能是给多个 agent 用的，**实体只有一份**，放在共用仓库：

```
D:\collaborative project\skills\zw-summary\        ← 唯一实体（git 仓库，改这里）
```

各 agent 的 skills 目录里只有一个 Junction 指向它，改一次全部生效：

| agent | 挂载点 | 怎么触发 |
|---|---|---|
| WorkBuddy | `~/.workbuddy/skills/zw-summary` | 直接说「总结这个视频」+ 链接，按 description 自动匹配 |
| Codex | `~/.codex/skills/zw-summary` | `/skills` 看列表，`$zw-summary` 显式调用 |
| CodeBuddy | `~/.codebuddy/skills/zw-summary` | 同上 |
| 通用（agents 标准） | `~/.agents/skills/zw-summary` | 各客户端按 agentskills.io 标准扫描 |
| 豆包 | 豆包 workspace 的 `.user_skills` | **2026-11-24 实测可用**：Junction 指向实体，SKILL.md 可读，`bili_api.py` / `ego_fetch.py` 可直接用 python 跑通；豆包不自带注入，靠平台偏好把 agent 引到这里 |

**不要在 agent 目录里改文件**——那是链接，改的是实体；也不要用 `npx skills add` 装这个技能，
它会往每个 agent 目录塞实体副本，一份变四份、无法统一更新。

### 运行前提（缺一个就跑不通）

| 前提 | 检查方式 | 缺失时 |
|---|---|---|
| `ego-browser` CLI 可用 | `ego-browser --version` | 走不了浏览器取数链路，抖音/小红书必失败 |
| Python 3（`yt_dlp` 已装） | `python -c "import yt_dlp"` | YouTube 取数失败，其余平台不受影响 |
| `ffmpeg`（imageio-ffmpeg 自带即可） | `python -c "import imageio_ffmpeg"` | 抽帧/转写失效，元数据仍可读 |
| ego 里该平台已登录 | 打开对应站点看右上角 | 小红书需登录态拿 `xsec_token`；抖音搜索/详情可能拿不到 |

### 取数怎么调（四档输出，按 token 预算选）

```bash
python adapters/ego_fetch.py <链接> --quiet     # 批量：一行 SUMMARY
python adapters/ego_fetch.py <链接>             # 单条：@@RESULT@@ JSON
python adapters/ego_fetch.py --digest           # 索引：扫已落盘目录出表
python adapters/ego_fetch.py <链接> --full      # 深读：正文全文 + 话题 + 评论
python adapters/ego_fetch.py --search "关键词" --platform douyin --limit 5   # 关键词直跑
```

产物落在 `~/videosum/<平台>_<ID>/`（Windows 即 `C:\Users\<用户名>\videosum\`），含
`meta.json`、`media_audio.m4a`、`media_video.mp4`、`img/`、`frames/`。
`--search` 的平台：抖音 / B站 / 小红书 / YouTube 可用，**X 的搜索页稳定报错，只能喂单条链接**。

### 给非 WorkBuddy agent 的两条额外提醒

1. 原文里出现的 `WorkBuddy` 只是「整理者」角色的代称，**换成你自己即可**——纯人工模式下由当前 agent 读文字稿写总结。
2. SKILL.md 里 `~/.workbuddy/skills/zw-summary/` 这种路径，在你那边请改用**实体路径**
   `D:\collaborative project\skills\zw-summary\`，或你自己 skills 目录下那条 Junction 的路径。

## 批量情报场景（每天 20 条/平台，2026-11-24 新增）

适用：不要单条总结，而是每天批量拉 20 条，快速判断「今天业内有什么新做法」。核心是**把「看」和「记」分开**：转写稿只落盘，主会话只收结论。

四步：

1. **检索层（便宜）**：`--search "关键词" --limit 20 --quiet`，产物落 `~/videosum`。只读 `--digest` 出来的一行一条索引（标题 / 时长 / 播放量 / 日期），**不要读原始 `search.json`**。20 条索引约 800–1300 token，直接读 JSON 会涨到 8000–15000。
2. **粗筛**：按标题与时长剔掉同名无关内容（游戏「记忆库」、Trados 翻译记忆库、电子琴记忆库这类），剩下的进细看队列。宁可少几条，不许拿无关的凑数。
3. **细看层（贵，必须隔离）**：按每批 5 条派子 agent或批处理跑转写。**转写稿、字幕、关键帧一律只留在 `~/videosum/<平台>_<ID>/`，不回传主会话**。子 agent 只回「结论段 + 关键数据 + 3–5 条要点」，每条 ≤300 字。
4. **汇总**：主会话只拿各批结论拼摘要，20 条的整体上下文成本压在 1 万 token 量级。

**为什么必须隔离**：单条 10 分钟视频转写约 2000 字 / 3000 token，20 条就是 5–6 万 token，是裁剪后检索的 40–70 倍；而且转写稿一旦进上下文，后面每一轮都会重背一遍。

**进度与断点**：`search.json` 里维护 `done` / `pending` 两列。当天跑不完就写「剩余 N 条待续，明日续跑」，绝不把未读的当已读。

**并行注意**：`ego_fetch.py` 不能多进程并行（会抢 page label，见 pitfall-20261118-9545），细看队列要串行或按平台错开时间；`bili_api.py` 走纯 API，可并行。

## 使用方法

用户提供 B站（BV号/链接）、YouTube（链接/11位 video_id）、X/Twitter（推文视频链接，`x.com/用户/status/数字ID`）、抖音（`v.douyin.com/xxx` 短链、`iesdouyin.com/share/video/xxx`、`www.douyin.com/video/xxx` 完整链接、19位纯数字 ID）或 小宇宙（`xiaoyuzhoufm.com/episode/<24位ID>`、`xyzfm.link` 分享链、24位纯十六进制 episode ID）后，执行：

1. 运行脚本（务必设置长超时 + 放行网络，见下方"注意事项"）。必须用 **已安装 yt_dlp 的 Python** 运行，技能目录默认为 `~/.workbuddy/skills/zw-summary/`：
   `<python> ~/.workbuddy/skills/zw-summary/scripts/zw_summary.py <BV号 或 YouTube链接 或 X链接 或 抖音链接 或 小宇宙链接>`
2. 脚本自动：识别平台 → 取元数据 → 字幕优先/Whisper 兜底 → （有 api_key 则 AI 中文总结，否则跳过）→ 保存 Markdown
3. **纯人工模式（默认，api_key 为空）**：脚本只负责取字幕/转写，结果落在 `scripts/audio/{vid}.transcript.txt` 缓存（供复用），**不写进输出文档**；文档只留占位（`<!-- ZW_SUMMARY_STUB -->` + `⚠️ AI 自动总结未执行`）。随后 WorkBuddy **读缓存的文字稿人工整理**，用 `Write` 覆盖写入同一输出文件（保留原文件名），交付前先 `Read` 原文件确认结构。
   - **硬规则（2026-09-24 用户要求）：转录稿/文字稿一律不放进输出文档**，文档里只放总结正文。用户原话：「转录稿不要放在Obsidian文档的最后，这个你存着就可以了，要复用的时候调用，不要放到文档里头。」
4. 用 `present_files` 交付总结文件，并在回复中给出总结要点概览

## 环境依赖

| 依赖 | 用途 | 缺失后果 |
|------|------|----------|
| Python 3（需装 `yt_dlp`） | 跑主脚本、YouTube/X 元数据与字幕 | 脚本无法运行 |
| `ffmpeg` / `ffprobe` | Whisper 兜底时抽 16k 单声道 wav、长音频分片与拼接 | Whisper 兜底失效 |
| `whisper_venv/`（技能目录下） | 本地语音识别（子进程调用，支持多进程分片） | 仅影响无字幕内容的 Whisper 兜底；字幕路径不受影响 |

`whisper_venv` 体积约 500MB（含 PyTorch），**不在发布包内**，需在目标机自建，位置固定为 `<技能目录>/whisper_venv`：

```bash
cd ~/.workbuddy/skills/zw-summary
python3.9 -m venv whisper_venv
./whisper_venv/bin/pip install openai-whisper
```

首次转写会自动下载 Whisper 模型权重（`base` 约 140MB）。若不建 venv，B站/YouTube/X 有字幕的内容仍可正常总结；抖音、小宇宙与无字幕视频会失败。三个脚本（`zw_summary.py` / `probe_part.py` / `transcribe_part.py`）都会在缺 venv 时给出明确报错。

## 播客类视频：先取同源播客的 shownotes 章节时间轴（2026-09-28 新增）

B站/YouTube 上大量"视频播客"是音频播客的同步分发的**视频版**（例：`十字路口`、`硅谷101`、`张小珺` 等）。这类视频人工整理前，**先花一次搜索把同源播客的章节时间轴拿到手**，用它当文档骨架，比按转录稿自己猜分段准得多，而且章节标题本身就是创作者的原生结构（用户回看时能直接对照定位）。

做法：

1. 用视频标题里的**播客名 + 嘉宾名**做一次 WebSearch。命中的通常是该播客在小宇宙（`xiaoyuzhoufm.com/podcast/<id>`）或公众号的节目页。
2. 节目页/intro 里通常有完整章节清单，形如 `00:39 快问快答` / `02:20 AI 正热，却离开了 VC` / `01:06:01 离开 GGV：8 位数 Carry 的取舍`——直接抄进文档的 `### 时间轴 小标题`，逐节填内容。
3. 章节标题里常已包含嘉宾的关键信息（人名、机构名、金额），可顺带当核验线索。
4. 若搜不到同源播客页（纯 UP 主自制视频），退回按内容逻辑自行分段。

实测收益（BV1bChR6nEhw，112 分钟对谈）：章节时间轴一次搜到，文档 19 个小节全部对齐创作者原结构，且避免了"按转录稿猜段落边界"的返工。

## 文档结构定式（人工整理输出）

多次交付要保持一致，按下面七块写（实测于 BV1bChR6nEhw，44KB / 约 9000 字）：

1. **元数据表**——平台 / 视频ID / 标题 / 作者 / 嘉宾（姓名+身份+原机构）/ 时长 / 发布时间 / 播放量 / 转录来源。
2. **视频简介原文**——用引用块照抄（简介是创作者自己的话，别改写）。
3. **一句话总览**——一段话把整场的论证链说清，让人不看正文也知道这期在论证什么。
4. **分段内容详解**——按 shownotes 时间轴分 `### 时间轴 小标题`，逐节写。**保留具体数字、案例名、原话**，宁细勿略；转录不清的人名/术语当场标"（转录不清）"。
5. **核心观点提炼**——编号 8～12 条，每条要能脱离视频独立成立（这是用户最常直接引用的部分）。
6. **金句摘录**——说话人原话，中文全角标点。
7. **与公开资料对照** + 文末一行（整理日期 / 转录来源与字数）。**全文不出现转录稿正文**（硬规则）。

## 脚本输出说明

终端输出：
- 平台/ID/代理检测结果
- 视频元数据（标题、作者、播放量、时长）
- ⚠️ 内容完整性警告（B站充电专属/付费试看、音频被截断；无则不打）
- 转录来源（`bilibili_subtitle` / `youtube_subtitle` / `youtube_autosub` / `whisper`）
- 完整 AI 总结正文
- 最后一行 `📄 总结已保存: /path/to/xxx.md`

## 配置文件

- 位置：`scripts/zw_config.json`（**需自行创建**，可从同目录 `zw_config.example.json` 复制；文件不存在时脚本使用内置默认值，不会报错）
- `bilibili.cookie`：B站登录态（SESSDATA 等）。缺失时 B站字幕接口可能失败并降级 Whisper。**凭据只留在本机，不要提交或打包**
- `ai`：AI 后端（默认 DeepSeek，api_key/base_url/model 可替换）。**api_key 留空 = 纯人工模式**：脚本跳过 AI 调用、不报错，只保存文字稿，由 WorkBuddy 人工总结。需要恢复自动总结时填回 api_key 即可（脚本逻辑不变）。
- `whisper.model`：Whisper 模型（`base` 默认，`small` 更准但更慢）
- `whisper.parallel_workers`：长音频并行分片数（可选）。不填或填 0 时自动取 `min(CPU 核数, 内存上限, 10, 音频时长/18分钟)`；16GB 内存机器建议固定为 6。
- `error_correction`：`dual`（默认，双 Pass 纠错）/ `single`（关闭清洗，省一次调用）。注意：即便 `dual`，也**只对识别类来源清洗**（Whisper 转写、自动字幕、B站AI字幕）；手动/官方字幕（X 官方字幕、YouTube 创作者字幕）质量高，自动跳过清洗直接总结。
- `proxy`：代理地址。**留空则自动检测**（环境变量 → macOS 系统代理 scutil → 此字段）。`probe_part.py` / `transcribe_part.py` 也读这个字段，为空则不传 `--proxy`。
- `output.dir`：总结 Markdown 的输出目录（支持 `~`）。**留空则写入当前工作目录**。需要固定归档到笔记库时在此指定，例如 `~/Obsidian/视频总结`。

## 架构说明

### 多平台统一数据流

```
输入链接 → detect_platform 识别平台
   ├─ B站:      wbi 签名拿字幕 → 匹配失败 → yt-dlp 下音频(直连) → Whisper(zh)
   ├─ YouTube:  yt-dlp 元数据 + 字幕(中/英) → 无字幕 → yt-dlp 下音频(代理) → Whisper(auto)
   ├─ X/Twitter: yt-dlp 元数据 + 字幕(HLS 流，en/zh) → 无字幕 → yt-dlp 下音频(代理) → Whisper(auto)
   ├─ 抖音:     短链 302 解析 ID → 注册 ttwid → 抓 iesdouyin SSR → 拼 aweme CDN 视频 URL
   │            → 下载 mp4 → ffmpeg 抽 16k wav → Whisper(zh)  [始终兜底，无字幕可用]
   └─ 小宇宙:   抓 SSR 页面 __NEXT_DATA__ 取 enclosure 音频直链
                → urllib 流式下 m4a → ffmpeg 抽 16k wav → Whisper(zh)  [无字幕路径]
          ↓
   文字稿（字幕 / Whisper 转写；长音频自动多进程分片后按绝对时间拼接）
          ↓
   ┌─ 有 api_key：AI 清洗纠错 → AI 中文总结 → 保存 Markdown（写入 output.dir）
   └─ 无 api_key（默认）：保存文字稿到 scripts/audio/ → WorkBuddy 人工通读总结 → 覆盖写入同一文件
```

### 关键设计

- **B站充电专属/付费试看检测（2026-09-27 新增）**：B站「充电专属」视频（`is_upower_exclusive=true`）对未充电账号只返回一段**试看流**——实测 BV1MaYK6HEyV 完整 6871 秒、只能拿到 600 秒（10 分钟）。不检测就会把试看片段转写完并报"识别完成"，总结直接失真。脚本现在两道防线：
  1. **转写前预检**（B站，`check_bilibili_paywall`）：查 `x/web-interface/view` 的 upower 字段 + `playurl`（先 `fnval=0` 拿 durl，兜底 `fnval=4048`）比对 `durl[0].length` 与 `timelength`。多P视频用第一个分P的时长比对，避免把"只处理第一个分P"误判成试看。
  2. **转写后校验**（全平台，`probe_duration`）：音频实际时长 < 标称时长 × 0.9 且差 > 90 秒 → 判定疑似试看/截断。
  报警会同时出现在终端、占位文档的引用块、以及 AI 总结正文最前面（`content_warnings` / `content_warnings_md`）。**看到报警时不要用这段转写做总结**，去找同一内容在其他平台的公开完整版（播客/YouTube）或换已充电账号。
- **字幕语言策略**：YouTube/X 手动字幕优先，其次自动字幕；语言按 `zh-Hans/zh-CN/zh/zh-Hant/...` 中文优先、`en` 英文兜底。英文视频用英文字幕，AI 总结时统一译成中文。
- **X 字幕特殊性**：X 的字幕是 HLS m3u8 流（不是直接 vtt 文件），用 yt-dlp `writesubtitles` 下载合并成 vtt 后，`vtt_to_text` 去掉 `<X-word-ms>` 逐字标签与时间戳。X 平台无自动字幕，无手动字幕时直接降级 Whisper。
- **抖音链路特殊性**（详见 `references/douyin.md`）：yt-dlp 抖音解析器（包括 2026 最新版）始终报"Fresh cookies needed"，且 Safari/Chrome cookie 受 TCC 保护无法被脚本读取。故抖音绕过 yt-dlp，自建链路：
  1. 短链 302 跟随拿 19 位 aweme_id（也支持桌面分享链接里的 `?modal_id=<19位ID>`）
  2. `ttwid.bytedance.com/ttwid/union/register/` 注册新 ttwid（POST → 拿 redirect_url → GET 回调地址抓 Set-Cookie）
  3. 带 ttwid 抓 `iesdouyin.com/share/video/<id>/` SSR `window._ROUTER_DATA`
  4. 递归找含 `video.play_addr` 的条目，把 `url_list` 里的 `/playwm/` 替换成 `/play/`（去水印 CDN）拿到无水印 mp4
  5. urllib 下载 mp4 → ffmpeg 抽 16k mono wav → Whisper(zh)
- **Whisper 语言自适应**：B站/抖音/小宇宙强制 `zh`（基本都是中文）；YouTube/X 用 `auto` 自动检测（英文视频识别英文、中文视频识别中文）。
- **小宇宙链路特殊性**（2026-09-21 新增）：小宇宙官方转写接口 `api.xiaoyuzhoufm.com/v1/episode-transcript/<eid>` 需登录态 access token（无 token 直接 `401 Authorization Required`），web 页面 NEXT_DATA 里的 `transcript` 字段只返回 `mediaId` 不含正文，**故没有可用字幕路径，一律 Whisper**。可用信息全部来自 SSR：抓 `https://www.xiaoyuzhoufm.com/episode/<eid>` → 正则取 `<script id="__NEXT_DATA__">` → `props.pageProps.episode`，拿 `title` / `duration`(秒) / `pubDate` / `playCount` / `podcast.title`(作者) / `enclosure.url`(音频直链，形如 `https://media.xyzcdn.net/.../<hash>.m4a`)。音频是 CDN 直链，**不走 yt-dlp**，直接 urllib 流式下载（2 小时单集约 120MB），带 `Referer: https://www.xiaoyuzhoufm.com/` 即可。默认中文 → Whisper 强制 `zh`。付费/私密单集取不到 `enclosure` → 直接报错。
- **长音频并行分片**：音频 >25 分钟时自动切 N 片（每片约 18 分钟、相邻片重叠 12 秒），每片一个进程并限 1 线程（`OMP_NUM_THREADS=1`），转写完按绝对时间裁掉重叠区再拼接。分片失败会自动回退单进程模式。
- **代理**：`detect_proxy()` 自动读取本机 Shadowrocket/Clash 等系统代理（HTTP/HTTPS 端口）。**YouTube/X 下载走代理，B站/抖音/小宇宙直连不走代理**。
- **Whisper 运行**：通过独立 venv（`<技能目录>/whisper_venv`）子进程执行，规避托管运行时沙箱代码签名对 PyTorch 的限制。venv 不存在时三个脚本都会给出明确报错，提示按"环境依赖"创建。

## 多P / 超长视频（补充流程）

主脚本全程用 yt-dlp 的 `noplaylist`，**多P合集只会处理第一个分P**——不提醒的话，很容易把「9 个 P 里的第 1 个」当成整个合集总结掉。脚本现在会自动检测并打印分P清单（`detect_multipart`），但**多P的完整处理仍需人工按下面流程走**。

判定信号：打印出的转写字数与视频总时长明显不匹配（例：标题写 9 集、总 790 分钟，却只转写出 42 分钟）。

1. **先探明分P**：脚本已自动打印分P清单。要更细地看内容，跑
   `<python> ~/.workbuddy/skills/zw-summary/scripts/probe_part.py <BV号> <起始P> <结束P>`
   它只下每个 P 开头 90 秒、Whisper 转写后打印前 400 字，用来判断「这一 P 到底是什么、属于哪一年」。
   **不要只看分P标题**——合集里各 P 标题常常完全相同，且顺序可能与简介描述的年份顺序相反。
2. **抽样识别完再决定转哪些 P**（成本极低，必做）。
3. **分P整段转写**：
   `<python> ~/.workbuddy/skills/zw-summary/scripts/transcribe_part.py <BV号> <P1> [P2] ...`
   （yt-dlp 按 `?p=N` 下整段音频 → ffmpeg 抽 16k mono wav → 按 18 分钟切片 → `whisper_venv` 并行，每片 `OMP_NUM_THREADS=1`。实测 8.7 小时音频 / 4 个 P、6 片并行 ≈ 12 分钟出稿。加 `--cache` 可直接写进 `scripts/audio/`。）
4. **转写稿存放**：多P用 `scripts/audio/{BV号}_p{N}_{年份或主题}.transcript.txt`（单P仍用 `{BV号}.transcript.txt`）。**一个视频只出一个 md**，多部分用 `# 第一部分 / # 第二部分` 分节。
5. **每个 P 都要标清「年份 + 场合 + 时间」的头部对照表**（长合集最容易张冠李戴），事实照旧联网核验。

### 多P处理的两个坑（踩过）

- **别把「清理」和「转写」写在同一条 shell 命令里**：`cd /tmp/x && python … <换行> rm -rf /tmp/x` 这种写法，前面 python 报错时后面的 `rm -rf` 照样执行，中间产物全丢。清理要写进脚本最后一步，或用 `&&` 串接。
- **缓存文件不要在同一个路径上「先合并后拆分」**：拆分循环里写 p6 时会把同名的合并文件覆盖，紧接着的 `os.remove(src)` 再把它删掉。要先算好最终文件名，逐个直接写。

## 注意事项

- **⚠️ 先看有没有"试看/付费"报警（2026-09-27 用户实测踩坑）**：B站上大量播客类长访谈是**充电专属**，脚本只能拿到约 10 分钟试看。脚本会自动报警，但**人工也要自查**：转写字数与视频时长明显不匹配（例：114 分钟视频只出 3300 字）就是信号。此时正确做法是**换源**——同一场对谈通常在播客平台（小宇宙等）有公开完整版，用那边的链接重跑，而不是拿试看片段硬总结。手动核验命令：
  ```bash
  python3 -c "import json,urllib.request as u;d=json.load(u.urlopen(u.Request('https://api.bilibili.com/x/web-interface/view?bvid=BV号',headers={'User-Agent':'Mozilla/5.0'})))['data'];print('exclusive',d.get('is_upower_exclusive'),'can_play',d.get('is_upower_play'),'duration',d.get('duration'))"
  ```
- **必须长超时 + 放行网络**：Whisper + AI 总结整体可能超过 Bash 默认超时（约 120s）。建议 `timeout=600000`（10 分钟）+ `dangerouslyDisableSandbox=true`（放行 AI 接口与 YouTube 代理网络）。
- **长音频自动并行分片（2026-09-19 新增）**：音频 >25 分钟时自动切成 N 片（每片约 18 分钟、相邻片重叠 12 秒），每片一个进程并限 1 线程（`OMP_NUM_THREADS=1`），靠多进程吃满多核，转写完按绝对时间裁掉重叠区再拼接。实测 2 小时 58 分抖音正片：单进程需 40 分钟以上，6 片并行 10 分钟出稿。**并行度默认 = min(CPU 核数, 内存上限, 10, 音频时长/18分钟)**，可用 `whisper.parallel_workers` 固定。**注意内存**：16GB 机器上 10 个 base 进程会把 memory 打到 swap（实测 load 19、换页 5.8G，反而更慢），长音频建议 6 片；`small` 模型更吃内存，核数少或内存 <32GB 时降到 4 片。分片失败会自动回退单进程模式。
- **抖音桌面分享链接**：`https://www.douyin.com/...?modal_id=<19位ID>`（搜索页/用户页弹窗分享出来的链接）已支持直接解析 modal_id，不必再手工提取 ID。
- **进度日志**：主脚本已启用行缓冲（`sys.stdout.reconfigure(line_buffering=True)`），重定向到文件时也能实时看到进度，不会像早期版本那样"看着像卡死"。
- **后台进程生命周期**：在 WorkBuddy 的 Bash 工具里用 `nohup ... &` 起的子进程会随工具调用结束被杀，长任务必须让同一个命令"前台等待"（`... & ... wait`）并以 `run_in_background=true` 托底，否则进程静默消失、只留下空日志。
- **缓存复用（音频用完即清）**：字幕 `.subtitle.txt`、Whisper `.transcript.txt`、双 Pass `.clean.txt` 缓存在 `scripts/audio/`，重跑秒级复用——AI 总结失败时重跑即可复用文本缓存重新总结，无需重新下载音频。**音频文件（.m4a/.wav 等）转录完成后自动删除，不保留**（`cleanup_audio_files`，主流程结束还有兜底清理）。Whisper 失败时音频暂留以便重试复用，重试成功后即清理；确认不再需要可手动删 `scripts/audio/` 下的音频。转写更新后删对应 `.clean.txt` 强制重新清洗。
- **文字稿模式不覆盖已有总结**：无 api_key（纯人工模式）时，若目标文件已存在且**不含占位标记**（`<!-- ZW_SUMMARY_STUB -->`，兼容旧格式的「原始文字稿」字样），即已是人工/AI 总结版，脚本**保留不覆盖**，只打印提示。总结版文件重跑脚本安全；只有占位稿会被刷新。
- **转录稿位置**：`scripts/audio/` 下的 `{vid}.transcript.txt` / `_subtitle.txt` / `.clean.txt` 是复用缓存，重跑秒级复用；**但不要把它的内容复制进输出文档**（用户 2026-09-24 硬性要求）。
- **转录来源为 `none`**：说明字幕与音频都获取失败，需提示用户（YouTube 可能是代理未开/视频受限/需登录；抖音可能是视频已被删除/私密、aweme CDN URL 失效，或网络抖动——重试即可。小宇宙可能是单集付费/私密/已下架，或 `media.xyzcdn.net` 直链失效）。
- **YouTube 限制**：年龄限制/会员视频需 cookie（当前未配置，可用 yt-dlp `--cookies-from-browser` 提取后放 `audio/cookies.txt` 并改脚本）。公开视频无需 cookie。
- **X/Twitter 限制**：X 无自动字幕，无手动字幕的视频只能靠 Whisper 转写（更慢）。X 被墙，必须走代理。X 视频音频是 HLS 流，Whisper 兜底时需 ffmpeg 合并（已具备）。极长视频（如 2 小时播客）字幕可达数万字，AI 分块清洗+总结耗时较长。
- **抖音限制**：详见 `references/douyin.md`。要点：ttwid 每次现注册（未缓存，可优化）；aweme CDN 偶尔会返回 `403`（签名 URL 过期）→ 重试；Whisper `base` 模型对抖音口音错字较多，整理时人工校对。
- **代理检测**：若 YouTube 下载失败且检测不到代理，检查代理软件是否开启、或手动在 `zw_config.json` 的 `proxy` 填 `http://127.0.0.1:<端口>`。
- **小宇宙限制**：无字幕路径，2 小时播客必须跑 Whisper（16GB 机器自动取 6 片并行，约 10-15 分钟出稿）；官方转写接口要 access token，未做登录集成；付费/私密单集无法获取音频。同一个人物密集、术语密集的科技播客，`base` 模型对人名/产品名错字较多，整理前务必按"专名校正"流程联网核对。
- **Whisper 默认 `base` 模型**：英文专有名词识别明显优于 `tiny`，个别术语仍可能听岔（如 MuonClip→"Meal Clip"），交付前需人工校正；需更准可把 `whisper.model` 调 `small`。
- **长文字稿必须切片后再读**：Whisper/字幕文字稿通常是**一整行无换行**的文本，`Read` 对单行超过 2000 字符会截断，直接读会丢内容。人工整理前先用 python 按每 ~1600 字符切成 `/tmp/*.txt` 分片（单行必然 <2000，不会截断），然后**多个 Read 并行读——一条消息里一次读 7 片左右**（32k 字约 21 片，3 轮读完，比逐片串行读快得多）。整理完成后删除 `/tmp` 切片，`scripts/audio/*.transcript.txt` / `.subtitle.txt` 缓存保留（可复用）。与 api_key 无关，纯人工模式与自动模式都适用。
- **专名校正（人工整理前必做）**：Whisper `base` 对中文语境里的产品或机构名错得厉害，实测同一场直播里 DeepSeek Harness→"Depthite Harness"、CANN 社区→"看社区"、Tauri→"Todis"、Claude Code→"Crowcode/Closer"、Agent→"AZENT"。整理前先用 WebSearch 核对**产品名、版本号、星标数、发布日期**等硬事实，按核对结果校正正文；无法确证的名称（个别模型名、开源项目名）一律标注"转录不清"，**禁止凭印象推测补写**。整理完毕后建议在文末加一段"与公开资料对照"，列出核实到的关键事实与出处，方便用户判断可信度。
  - **实测高频错拼对照表（2026-09-28 新增，先查表再搜网）**：`base` 模型对 AI 圈的英文专名有一套稳定的错法，整理前先按此表做一轮全局替换，能省掉大半搜索。左为转录稿原样、右为正确写法：
    | 转录稿 | 正确 | 转录稿 | 正确 |
    |--------|------|--------|------|
    | Mills / MITWIN / Mario's | Muse（Meta） | Growboat / Growboth | Grok Bot（xAI） |
    | C-Dance / C-Dan / Seedance 变体 | Seedance（字节） | K-3 | Kimi K3（月之暗面） |
    | 绝字 / Javelike | Jev / Jev-like（TypeSafe） | H3 + "海螺/明明" | MiniMax H3 |
    | 龙虾 / OpenClown | OpenClaw | 素和路纳 | GPT-6 Sol 与 Luna |
    | Obs5.5 / OBIO 5.5 / OpZ5.5 | Claude Opus 5.5 | 简硬 | 剪映 |
    | ClubFair | Cloudflare | KonEx | Claude Code |
    | 海亮 | 海量 | 两万一 | 两千亿 |
    | 死给我 / 死给 | Skill | AZN / AZENT | Agent |
    用法：先做全局替换 → 再对**数量级、日期、归属**这类不能靠猜的硬事实联网核验 → 剩下无法确证的一律标"转录不清"。**替换只在正文落笔时做，不要改写 `scripts/audio/` 缓存里的转录稿**（缓存要保持原样以便复核）。
- **嘉宾身份核验用「姓名 + 机构」一次搜准（2026-09-28 新增）**：访谈/播客类内容，第一个人物必须先立住。用「姓名 + 原机构 + 现机构」直接查百科/机构官网/创投库，一次拿到出生年、学历、任职时间线、代表案例，比从转录稿里拼人名和履历可靠得多（转录稿里同一个人常出现多种写法）。实测 BV1bChR6nEhw：转录稿把人名写成"余鸿/余鸻/余雄"，一次搜索即确认为**于红**（浙大计算机学士 + 浙大管院硕士、2013 入 GGV、2021.3 入美团龙珠任合伙人、2025.3 创立诸小葛，产品兔咚咚 SEL），并据此校正了全文错名。
- **"与公开资料对照"用 ✅ / ⚠️ 两级标注（2026-09-28 定式）**：表格固定两列「视频中的说法 / 核实结果」，每条前缀 ✅（核实属实，附来源口径）或 ⚠️（有出入、口径混杂、或查无公开出处）。核实不了的**不要删、也不要替讲话人圆场**，原样记录并标 ⚠️ 说明缺什么；归属不明的（如"她引用的某位合伙人"未点名）直接写"视频中未点名，转录不清，无法确证"。用户对"不要自己编"极敏感，宁可标存疑。
- **归属类断言也要核验（2026-09-21 新增）**：UP 主口播里的"某某公司发布了某某产品／某某模型"经常说错。实测 BV1gjh66kEbx 把第三方（ConvAI Innovations）的开源复现 Laya 说成 TypeSafe 官方出品。凡是"谁做的、谁发布的、什么时间发布"这类归属断言，一律联网核验后再写进正文，错的要在"与公开资料对照"里明确指出（结论表里单列一行"视频说法 / 核实结果"最清楚）。
- **倍率数字要核验对照对象**：口播常把官方的"对比前沿大模型的最高倍率"记成"对比某个具体模型"（如把 193.6× 说成"对比 Claude 3.5"）。倍率对、对照对象错的情况很常见——宁可写官方原口径，不要替 UP 主补一个具体对照模型。
- **视频简介里的推广链接**会被当作"原文"混入转写，注意区分演讲者原话与简介内容。
- **推理模型兼容性**：使用 DeepSeek V4 等推理模型时，脚本已放大各阶段 `max_tokens`（清洗 48k、块提取 8k、整合 32k、续写 16k）。切回非推理模型（如 `deepseek-chat`）可调回。若 AI 持续返回空内容，检查 `zw_config.json` 的 `model` 与 `max_tokens`。
- **总结文件路径**：以脚本最后一行 `📄 总结已保存: ...` 为准（写入 `output.dir`；未配置时为当前工作目录）。
- **运行环境差异**：B站字幕接口依赖登录态 cookie，未配置 cookie 时会降级到 Whisper，需本机已建 `whisper_venv`。首次使用建议先用一个有字幕的 B站视频验证链路。
