# 抖音链路说明

## 为什么不用 yt-dlp

yt-dlp 的抖音解析器（包括 2026.08.x 最新版）始终报 `Fresh cookies (not necessarily logged in) are needed`：

- 旧 iteminfo API（`web/api/v2/aweme/iteminfo/`）已废弃
- 分享页 SSR（`_ROUTER_DATA` → `video_(id)/page` → `videoInfoRes`）需要有效 ttwid 才能内嵌视频数据
- 浏览器 cookie 不靠谱：Safari 受 macOS TCC 保护，curl/`--cookies-from-browser` 都读不到；Chrome cookie 在没有访问 douyin 痕迹时也不可用

实测：`yt-dlp --cookies-from-browser chrome` 和 `yt-dlp --cookies-from-browser safari` 同样报 Fresh cookies needed。结论：**抖音解析必须自建，不能依赖 yt-dlp**。

## 自建链路（脚本里 `get_douyin_meta` + `download_douyin_audio`）

```
输入链接/ID
  │
├─ /video/<19位ID> → 直接用 ID
├─ ...?modal_id=<19位ID>（桌面端搜索页/用户页弹窗分享链接）→ 正则取 modal_id
├─ v.douyin.com/<short> 或 iesdouyin 分享页 → 短链 302 跟随拿 ID
├─ 纯数字 19 位 → 直接用
  ↓
register_ttwid()
  ├─ POST ttwid.bytedance.com/ttwid/union/register/ (aid=1768)
  └─ 拿 redirect_url（带 ticket）→ GET ixigua 回调地址 → Set-Cookie ttwid (1 年有效)
  ↓
抓 https://www.iesdouyin.com/share/video/<id>/ (带 ttwid cookie, 移动 UA)
  ↓
正则提取 window._ROUTER_DATA = {...}; JSON 解析
  ↓
递归找含 video.play_addr 的条目
  ↓
play_addr.url_list 里第一个 URL 把 /playwm/ 改成 /play/（去水印 CDN）
  ↓
urllib 带 Referer=https://www.douyin.com/ 下载无水印 mp4
  ↓
ffmpeg -i <mp4> -vn -ac 1 -ar 16000 <wav>
  ↓
whisper base 语言=zh 转写 → 文字稿
```

## 关键代码位置

| 函数 | 作用 |
|------|------|
| `resolve_douyin_short(url)` | 短链 302 跟随，提取 19 位 ID；返回 (id, final_url) |
| `register_ttwid()` | 注册 ttwid cookie（不缓存，每次跑现注册；可优化为缓存到 `audio/ttwid.txt`） |
| `get_douyin_meta(raw_arg)` | 综合入口：拿 ID → 注册 ttwid → 抓 SSR → 解析 → 返回 meta dict（含 `play_url`/`play_uri`） |
| `download_douyin_audio(meta, cfg)` | urllib 下载 mp4 → ffmpeg 抽 16k mono wav，返回 wav 路径；下载后删 mp4 |

## 缓存与复用

- 文本缓存走 `scripts/audio/<id>.transcript.txt`（与 B站/YT/X 一致），重跑秒级复用
- mp4 抽完音频即删（避免残留 19MB+ 视频文件）
- wav 走通用 `cleanup_audio_files` 自动清理
- **ttwid 未缓存**：每次跑脚本现注册（约 1-2s）。若高频跑可在 `register_ttwid()` 里加 `audio/ttwid.txt` 缓存（`Max-Age=31536000`，仅在过期前复用）

## 已知坑

0. **长音频（>25 分钟）必须走并行分片**：抖音正片类内容动辄 2-3 小时（如《罗永浩的十字路口》正片 10711 秒），单进程 `base` 约 40 分钟以上。脚本已内置 `transcribe_parallel`（默认 min(核数,10,时长/18min) 片、每片 1 线程、重叠 12 秒后按绝对时间裁切拼接）。**16GB 内存机器的经验值：base 模型 6 片并发是甜点，10 片会把内存打到 swap（实测 load 19、swap 5.8G，反而比 6 片慢）**；`small` 模型每进程约 1.5-2GB RSS，核少/内存小时降到 4 片。
1. **aweme CDN 偶尔返回 403**：play URL 里的签名 token 过期。重试即可（urllib 不带额外重试逻辑，目前需手动重跑）。
2. **Whisper base 错字率较高**：抖音口音、专有名词（中英混用、新造词）识别不准。整理时务必人工校对；纯人工模式（默认）下 WorkBuddy 在对话里校对即可。
3. **视频被删除/私密**：SSR 返回但无 `video.play_addr` → 脚本抛 "未找到视频条目"。短链可能仍在 302 内跳到活页，需进一步容错（当前直接报错）。
4. **播放量/标题为 0/空**：SSR 数据字段名偶尔变动（`statistics.play_count`、`desc` 等），脚本已 `or {}` 兜底，缺失时填 0/空。
5. **ttwid 注册频率**：连续多视频时重复注册不会失败，但增加 2 次 HTTP 请求。生产场景可加本地缓存。

## 扩展建议

- `whisper.model` 调到 `small`：抖音错字明显减少，转写耗时翻倍（约 1 分钟/4 分钟视频 → 2 分钟/4 分钟视频）。中文优先场景建议直接 `small`。
- `error_correction="dual"` 已对抖音生效（whisper 属识别类来源，会过双 Pass 清洗）；无 api_key 时跳过 AI 清洗，由 WorkBuddy 对话人工整理。
- 接入 AI 总结后，输出中文质量与 B站/YT/X 一致；硬伤仍在专有名词（如作者临时发明的术语），仍需人审。