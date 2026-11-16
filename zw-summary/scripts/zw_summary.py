#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zw-summary 统一视频总结工具（B站 + YouTube + X + 抖音）

支持：
  - B站 BV 号 / 链接
  - YouTube 链接 / video_id（自动走本机代理）
  - X/Twitter 推文链接
  - 抖音短链（v.douyin.com/xxx）/ 完整链接（iesdouyin.com/share/video/...、www.douyin.com/video/...）/ 19 位数字 ID

流程：字幕优先（B站字幕 / YouTube 字幕 / X 字幕）→ 无字幕降级 Whisper 本地识别
      → AI 双 Pass 清洗纠错 → AI 生成中文总结
      （抖音目前走 Whisper 兜底，不依赖 yt-dlp 抖音解析器）

用法:
  python3 zw_summary.py BV1XHwWzVEmF
  python3 zw_summary.py https://www.youtube.com/watch?v=dQw4w9WgXcQ
  python3 zw_summary.py https://x.com/xxx/status/1234567890
  python3 zw_summary.py https://v.douyin.com/9ydsScO0WE0/

配置文件（zw_config.json，与脚本同目录）:
  {
    "bilibili": {"cookie": {"SESSDATA": "", "bili_jct": "", "buvid3": ""}},
    "ai": {"api_key": "", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash"},
    "whisper": {"model": "base"},
    "error_correction": "dual",
    "proxy": "",
    "output": {"dir": ""}
  }

依赖:
  托管 venv 需装 yt-dlp；whisper 走系统 Python venv（见 whisper_venv）；系统需 ffmpeg
"""

import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 重定向到文件时保持行缓冲，否则进度日志要等进程结束才可见
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ──────────────────────────────────────────────
# 配置加载
# ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "zw_config.json"
AUDIO_DIR = SCRIPT_DIR / "audio"
try:
    AUDIO_DIR.mkdir(exist_ok=True)
except PermissionError:
    pass  # 沙箱 shim 对已存在目录误报 EEXIST，目录已在则忽略

DEFAULT_CONFIG = {
    "bilibili": {"cookie": {"SESSDATA": "", "bili_jct": "", "buvid3": ""}},
    "ai": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
    },
    "whisper": {"model": "base", "parallel_workers": 0},
    "error_correction": "dual",
    "proxy": "",
    "output": {"dir": ""},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in loaded.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


CFG = load_config()


def get_output_dir(cfg=None):
    """总结文件输出目录：优先 config 的 output.dir（支持 ~），未配置则用当前工作目录"""
    raw = str(((cfg or CFG).get("output") or {}).get("dir") or "").strip()
    if raw:
        try:
            path = Path(raw).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        except (PermissionError, OSError):
            pass
    return Path.cwd()


# ──────────────────────────────────────────────
# 平台识别
# ──────────────────────────────────────────────
def detect_platform(arg):
    """从输入判断平台，返回 (platform, id_or_raw)"""
    arg = arg.strip()
    if "BV" in arg or "bilibili" in arg.lower():
        m = re.search(r"BV[A-Za-z0-9]+", arg)
        if m:
            return "bilibili", m.group(0)
    if re.search(r"xiaoyuzhoufm\.com|xyzfm\.link", arg, re.I):
        m = re.search(r"/episode/([0-9a-fA-F]{24})", arg)
        if m:
            return "xyz", m.group(1)
        raise ValueError(f"无法从小宇宙链接解析 episode ID: {arg}")
    # 纯 24 位十六进制 ID 视作小宇宙 episode
    if re.match(r"^[0-9a-f]{24}$", arg):
        return "xyz", arg
    if re.search(r"(?:v\.douyin\.com|iesdouyin\.com|www\.douyin\.com)", arg, re.I):
        # 完整 ID 链接：直接抽取 19 位数字 ID
        m = re.search(r"/video/(\d{15,})", arg)
        if m:
            return "douyin", m.group(1)
        # 短链/分享页：原样保留，后续由 get_douyin_meta 解析
        return "douyin", arg
    if re.search(r"youtu\.?be|youtube\.com", arg, re.I):
        m = re.search(
            r"(?:youtu\.be/|youtube\.com/(?:watch\?.*?v=|shorts/|embed/|live/|v/))"
            r"([A-Za-z0-9_-]{11})",
            arg,
        )
        if m:
            return "youtube", m.group(1)
        raise ValueError(f"无法从 YouTube 链接解析 video_id: {arg}")
    if re.search(r"\b(?:x|twitter)\.com", arg, re.I):
        m = re.search(r"/status/(\d+)", arg)
        if m:
            return "x", m.group(1)
        raise ValueError(f"无法从 X 链接解析 status ID: {arg}")
    # 纯 11 位 id 视作 YouTube
    if re.match(r"^[A-Za-z0-9_-]{11}$", arg):
        return "youtube", arg
    # 抖音纯数字 ID（与 iesdouyin SSR 里的 aweme_id 一致）
    if re.match(r"^\d{15,}$", arg):
        return "douyin", arg
    raise ValueError(f"无法识别视频链接/ID: {arg}")


# ──────────────────────────────────────────────
# 代理检测
# ──────────────────────────────────────────────
def detect_proxy():
    """返回代理字符串（http://host:port）或 None。
    优先级：macOS 系统代理(scutil，Shadowrocket/Clash 设置的权威源) → 环境变量 → 配置 proxy"""
    try:
        out = subprocess.run(
            ["scutil", "--proxy"], capture_output=True, text=True, timeout=5
        ).stdout
        if "HTTPSEnable : 1" in out:
            m = re.search(r"HTTPSPort\s*:\s*(\d+)", out)
            if m:
                return f"http://127.0.0.1:{m.group(1)}"
        if "HTTPEnable : 1" in out:
            m = re.search(r"HTTPPort\s*:\s*(\d+)", out)
            if m:
                return f"http://127.0.0.1:{m.group(1)}"
    except Exception:
        pass
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(var)
        if v:
            return v
    p = CFG.get("proxy", "")
    return p or None


def download_text_url(url, proxy=None):
    """带代理下载文本内容"""
    opener = None
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if opener:
        with opener.open(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ──────────────────────────────────────────────
# B站 API（含 wbi 签名，用于 AI 字幕真实地址）
# ──────────────────────────────────────────────
def make_bili_headers(sessdata=""):
    h = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}
    if sessdata:
        h["Cookie"] = f"SESSDATA={sessdata}"
    return h


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


_WBI_PERM = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
             5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
             24, 55, 40, 61, 26, 17, 0, 1, 57, 22, 25, 34, 46, 52, 11, 44, 54, 36,
             4, 21, 56, 59, 6, 60, 20, 51, 30, 62, 63]


def _wbi_mixin_key(orig):
    return "".join(orig[i] for i in _WBI_PERM)[:32]


def _wbi_sign(params, sessdata=""):
    nav = http_get_json(
        "https://api.bilibili.com/x/web-interface/nav", make_bili_headers(sessdata)
    )
    img = re.search(r"([0-9a-f]+)\.png", nav["data"]["wbi_img"]["img_url"]).group(1)
    sub = re.search(r"([0-9a-f]+)\.png", nav["data"]["wbi_img"]["sub_url"]).group(1)
    mixin = _wbi_mixin_key(img + sub)
    params["wts"] = int(time.time())
    s = "".join(f"{k}={params[k]}" for k in sorted(params))
    params["w_rid"] = hashlib.md5((s + mixin).encode()).hexdigest()
    return params


def get_bilibili_meta(bvid, sessdata=""):
    data = http_get_json(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        make_bili_headers(sessdata),
    )
    if data.get("code") != 0:
        raise RuntimeError(f"获取B站视频信息失败: {data.get('message')}")
    info = data["data"]
    return {
        "platform": "bilibili",
        "id": bvid,
        "title": info.get("title", "未知"),
        "uploader": info.get("owner", {}).get("name", ""),
        "duration": info.get("duration", 0),
        "view": info.get("stat", {}).get("view", 0),
        "desc": info.get("desc", "").strip(),
        "cid": info.get("cid"),
        "url": f"https://www.bilibili.com/video/{bvid}/",
    }


def detect_multipart(bvid, proxy=None):
    """检测 B站多P合集。

    本脚本全程用 yt-dlp 的 noplaylist，多P视频**只会处理第一个分P**。
    这里先把分P情况查出来并显式提醒，避免"只转写了一集却当成整个合集"。
    返回 (分P数, [(序号, 时长秒), ...])；非多P或查询失败返回 (0, [])。
    """
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False}
        if proxy:
            opts["proxy"] = proxy
        opts["http_headers"] = {
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0",
        }
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.bilibili.com/video/{bvid}", download=False)
        entries = info.get("entries") or []
        if len(entries) > 1:
            return len(entries), [(i + 1, e.get("duration") or 0) for i, e in enumerate(entries)]
    except Exception:
        pass
    return 0, []


def _dur_cn(seconds):
    """秒 -> 中文时长，如 3小时26分"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "未知"
    h, m = divmod(s // 60, 60)
    if h:
        return f"{h}小时{m}分" if m else f"{h}小时"
    return f"{m or 1}分"


def content_warnings(meta):
    """本次转写的内容完整性警告（充电专属试看 / 音频被截断），无则返回 []"""
    return [meta[k] for k in ("_paywall_warn", "_short_audio_warn") if meta.get(k)]


def content_warnings_md(meta):
    """把内容完整性警告渲染成 Markdown 引用块（逐行加 >，否则多行警告会掉出引用块）"""
    out = ""
    for w in content_warnings(meta):
        for line in w.split("\n"):
            out += f"> {line}\n"
        out += ">\n"
    return out


def check_bilibili_paywall(bvid, cid, sessdata=""):
    """检测 B站【充电专属 / 付费试看】，避免把试看片段当成完整视频总结。

    充电专属视频（is_upower_exclusive=True）对未充电账号只返回一段试看流：
    playurl 接口的 durl[0].length 会远小于 timelength（实测某期充电专属访谈
    是 600s 试看 vs 6871s 完整视频）。脚本若不检测，会把 10 分钟试看转写出来
    并报"识别完成"，总结直接失真。

    返回 (警告文本 或 "", 可播秒数, 完整秒数)
    """
    try:
        data = http_get_json(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            make_bili_headers(sessdata),
        )
        info = (data or {}).get("data") or {}
        exclusive = bool(info.get("is_upower_exclusive"))
        can_play = bool(info.get("is_upower_play"))
        total = int(info.get("duration") or 0)
        # 多P合集：info.duration 是**所有分P之和**，而本脚本只处理第一个分P（cid 也是第一个），
        # 直接比总时长会误判成"试看片段"。这里换成第一个分P的时长。
        pages = info.get("pages") or []
        if len(pages) > 1:
            total = int(pages[0].get("duration") or total)
        playable = 0
        # fnval=0 走 durl（老接口），一定会带 length（毫秒），是判断"实际可播多长"最可靠的字段；
        # fnval=4048 返回 DASH，没有 durl，只在拿不到 durl 时兜底。
        for _fnval in (0, 4048):
            if playable:
                break
            try:
                params = _wbi_sign(
                    {"bvid": bvid, "cid": cid, "qn": 64 if _fnval else 32, "fnval": _fnval,
                     "fourk": 1 if _fnval else 0},
                    sessdata,
                )
                q = "&".join(f"{k}={urllib.parse.quote(str(params[k]))}" for k in params)
                p = http_get_json(
                    f"https://api.bilibili.com/x/player/wbi/playurl?{q}",
                    make_bili_headers(sessdata),
                )
                pd = (p or {}).get("data") or {}
                durl = pd.get("durl") or []
                if durl:
                    playable = int((durl[0].get("length") or 0) / 1000)
                if not total:
                    total = int((pd.get("timelength") or 0) / 1000)
            except Exception:
                continue

        lines = []
        if exclusive and not can_play:
            lines.append(
                "⚠️  这是【充电专属】视频，当前账号未充电"
                "（is_upower_exclusive=True / is_upower_play=False）"
            )
        if playable and total and playable < total * 0.95 and (total - playable) > 60:
            lines.append(
                f"⚠️  只能取到 {_dur_cn(playable)} 的试看片段，完整视频为 {_dur_cn(total)}"
            )
        if lines:
            lines.append("⚠️  转写结果只覆盖试看片段，**不能当作完整内容总结**。")
            lines.append(
                "💡 建议：优先找同一内容在其他平台的公开完整版（播客 / YouTube），"
                "或改用已充电/大会员账号的 cookie 重跑。"
            )
        return ("\n".join(lines), playable, total)
    except Exception:
        return ("", 0, 0)


def get_bilibili_subtitles(bvid, cid, sessdata=""):
    """优先 wbi 接口（可返回 ai-zh 真实地址），失败回退普通接口"""
    subs = []
    try:
        params = _wbi_sign({"bvid": bvid, "cid": cid}, sessdata)
        q = "&".join(f"{k}={urllib.parse.quote(str(params[k]))}" for k in params)
        data = http_get_json(
            f"https://api.bilibili.com/x/player/wbi/v2?{q}", make_bili_headers(sessdata)
        )
        if data.get("code") == 0:
            subs = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if subs:
                return subs
    except Exception:
        pass
    data = http_get_json(
        f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}",
        make_bili_headers(sessdata),
    )
    if data.get("code") != 0:
        return []
    return data.get("data", {}).get("subtitle", {}).get("subtitles", [])


def bilibili_subtitle_to_text(url):
    if url.startswith("//"):
        url = "https:" + url
    body = http_get_json(url, {"User-Agent": "Mozilla/5.0"}).get("body", [])
    return "\n".join(
        item.get("content", "").strip() for item in body if item.get("content", "").strip()
    )


# ──────────────────────────────────────────────
# YouTube（yt-dlp 元数据 + 字幕）
# ──────────────────────────────────────────────
YT_SUB_LANG_PRIORITY = [
    "zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK",
    "en", "en-US", "en-GB", "en-orig",
]


def get_stream_meta(video_id, url, platform, proxy=None):
    """yt-dlp 提取元数据（YouTube / X 通用）"""
    import yt_dlp
    ydl_opts = {"quiet": True, "noplaylist": True, "skip_download": True}
    if proxy:
        ydl_opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "platform": platform,
        "id": video_id,
        "title": info.get("title", "未知"),
        "uploader": info.get("uploader") or info.get("channel", ""),
        "duration": int(info.get("duration") or 0),
        "view": int(info.get("view_count") or 0),
        "desc": (info.get("description") or "").strip(),
        "cid": None,
        "url": url,
        "_info": info,
    }


def vtt_to_text(vtt):
    """把 VTT 字幕解析为纯文本"""
    out = []
    for line in vtt.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("WEBVTT") or line.upper().startswith("NOTE"):
            continue
        if "-->" in line or re.match(r"^\d{2}:\d{2}:\d{2}", line):
            continue
        if re.match(r"^[0-9a-f-]{8,}$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)  # 去 <c> 等标签
        line = html.unescape(line)
        line = line.strip()
        if line and not re.match(r"^\[.*\]$", line):  # 去 [音乐]/[掌声] 等
            out.append(line)
    return "\n".join(out)


def youtube_subtitle_to_text(raw):
    """解析 YouTube 字幕（自动识别 JSON3 / VTT 两种格式）。
    JSON3: events[].segs[].utf8 逐段拼接（空格/换行是独立 segment）"""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            lines = []
            for event in data.get("events", []):
                seg_text = "".join(
                    (seg.get("utf8") or "") for seg in event.get("segs", [])
                ).strip()
                if seg_text and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", seg_text):
                    lines.append(seg_text)
            if lines:
                return "\n".join(lines)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return vtt_to_text(raw)


def get_youtube_subtitle(meta, proxy=None):
    """优先手动字幕，其次自动字幕；语言中文优先、英文兜底。返回 (text, source)"""
    info = meta.get("_info") or {}
    for pool, src in (
        (info.get("subtitles") or {}, "youtube_subtitle"),
        (info.get("automatic_captions") or {}, "youtube_autosub"),
    ):
        for lang in YT_SUB_LANG_PRIORITY:
            entries = pool.get(lang) or []
            if not entries:
                continue
            url = entries[0].get("url")
            if not url:
                continue
            try:
                raw = download_text_url(url, proxy)
                text = youtube_subtitle_to_text(raw)
                if len(text) > 100:
                    print(f"✅ YouTube 字幕可用（{lang}，{len(text)} 字）")
                    return text, src
            except Exception as e:
                print(f"⚠️  YouTube 字幕 {lang} 下载失败（{e}）")
    return "", "none"


def get_x_subtitle(meta, proxy=None):
    """X/Twitter 字幕（HLS 字幕流）：用 yt-dlp 下载合并为 vtt 再解析。返回 (text, source)"""
    vid = meta["id"]
    tmpdir = tempfile.mkdtemp(prefix="xsub_")
    try:
        import yt_dlp
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "subtitleslangs": ["en", "zh", "zh-Hans", "zh-CN", "zh-Hant"],
            "subtitlesformat": "vtt",
            "outtmpl": tmpdir + "/%(id)s.%(ext)s",
            "proxy": proxy,
            "quiet": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([meta["url"]])
        vtt_files = glob.glob(tmpdir + "/*.vtt")
        if vtt_files:
            text = vtt_to_text(Path(vtt_files[0]).read_text(encoding="utf-8"))
            if len(text) > 100:
                print(f"✅ X 字幕可用（{len(text)} 字）")
                return text, "x_subtitle"
            print(f"⚠️  X 字幕内容过短（{len(text)} 字），切换到 Whisper")
    except Exception as e:
        print(f"⚠️  X 字幕下载失败（{e}），切换到 Whisper")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return "", "none"


# ──────────────────────────────────────────────
# 抖音（短链 302 → ttwid 注册 → iesdouyin SSR 解析 → aweme CDN 拉视频）
# ──────────────────────────────────────────────
_DOUYIN_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_DOUYIN_MOBILE_UA = _DOUYIN_UA


def resolve_douyin_short(url):
    """抖音短链 302 跟随，提取真实视频 ID（19 位数字）。返回 (id, final_url)"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _DOUYIN_MOBILE_UA,
        "Accept": "*/*",
    })
    final = ""
    try:
        # 不自动跟随重定向，方便抓 Location
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=30) as resp:
            final = resp.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc:
            final = loc
    if not final:
        # 兜底：直接跟随 302
        with urllib.request.urlopen(req, timeout=30) as resp:
            final = resp.geturl()
    m = re.search(r"/video/(\d{15,})", final)
    if not m:
        raise ValueError(f"无法从短链解析视频ID: {final}")
    return m.group(1), final


def register_ttwid():
    """向 ttwid.bytedance.com 注册 ttwid cookie（1 年有效）。返回解码后的 ttwid 值。
    链路：POST register/ → 拿 redirect_url（带 ticket）→ GET 回调地址 → Set-Cookie ttwid"""
    body = json.dumps({
        "region": "cn", "aid": 1768, "needFid": False,
        "service": "www.ixigua.com",
        "migrate_info": {"ticket": "", "source": "node"},
        "cbUrlProtocol": "https", "union": True,
    }).encode()
    req = urllib.request.Request(
        "https://ttwid.bytedance.com/ttwid/union/register/",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    redirect = data.get("redirect_url", "")
    if not redirect:
        raise RuntimeError("ttwid 注册失败：未返回 redirect_url")
    req2 = urllib.request.Request(redirect, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    })
    with urllib.request.urlopen(req2, timeout=30) as resp2:
        for c in resp2.headers.get_all("Set-Cookie") or []:
            m = re.match(r"ttwid=([^;]+)", c)
            if m:
                return urllib.parse.unquote(m.group(1))
    raise RuntimeError("ttwid 注册回调未返回 ttwid cookie")


def get_douyin_meta(raw_arg):
    """抖音元数据：短链解析 → 注册 ttwid → 抓 iesdouyin 分享页 SSR → 提取 play_addr。
    返回 meta dict（含 play_url、play_uri 字段供下载使用）"""
    # 1. 拿 19 位视频 ID
    vid = None
    if raw_arg.startswith("http"):
        m = re.search(r"/video/(\d{15,})", raw_arg)
        if m:
            vid = m.group(1)
        else:
            # 桌面端分享链接：...?modal_id=<19位ID> （如 douyin.com/root/search/xxx?modal_id=xxx）
            m2 = re.search(r"[?&]modal_id=(\d{15,})", raw_arg)
            if m2:
                vid = m2.group(1)
            else:
                vid, _ = resolve_douyin_short(raw_arg)
    elif re.match(r"^\d{15,}$", raw_arg):
        vid = raw_arg
    if not vid:
        raise ValueError(f"无法从抖音输入解析视频ID: {raw_arg}")

    # 2. 注册 ttwid 并抓分享页
    ttwid = register_ttwid()
    share_url = f"https://www.iesdouyin.com/share/video/{vid}/"
    req = urllib.request.Request(share_url, headers={
        "User-Agent": _DOUYIN_MOBILE_UA,
        "Cookie": f"ttwid={ttwid}",
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("抖音分享页未返回 _ROUTER_DATA（ttwid 可能失效，重试）")
    data = json.loads(m.group(1))

    # 3. 递归找含 video.play_addr 的条目
    def find_video(obj, depth=0):
        if depth > 12 or obj is None:
            return None
        if isinstance(obj, dict):
            v = obj.get("video")
            if isinstance(v, dict) and ("play_addr" in v or "playAddr" in v):
                return obj
            for vv in obj.values():
                r = find_video(vv, depth + 1)
                if r:
                    return r
        elif isinstance(obj, list):
            for vv in obj:
                r = find_video(vv, depth + 1)
                if r:
                    return r
        return None

    item = find_video(data)
    if not item:
        raise RuntimeError("抖音分享页 SSR 中未找到视频条目（视频可能已被删除/私密）")
    video = item.get("video", {}) or {}
    pa = video.get("play_addr", {}) or video.get("playAddr", {}) or {}
    url_list = list(pa.get("url_list") or [])
    play_uri = pa.get("uri", "")
    # 把 playwm 替换成 play（去水印 CDN）
    play_url = ""
    for u in url_list:
        if "playwm" in u:
            play_url = u.replace("/playwm/", "/play/")
            break
    if not play_url and url_list:
        play_url = url_list[0]
    if not play_url and play_uri:
        play_url = f"https://aweme.snssdk.com/aweme/v1/play/?line=0&ratio=720p&video_id={play_uri}"

    stats = item.get("statistics") or {}
    return {
        "platform": "douyin",
        "id": vid,
        "title": (item.get("desc") or "抖音视频").strip() or "抖音视频",
        "uploader": (item.get("author") or {}).get("nickname", ""),
        "duration": int(video.get("duration", 0) or 0),
        "view": int(stats.get("play_count", 0) or 0),
        "desc": (item.get("desc") or "").strip(),
        "cid": None,
        "url": f"https://www.douyin.com/video/{vid}",
        "play_url": play_url,
        "play_uri": play_uri,
    }


def download_douyin_audio(meta, cfg):
    """下载抖音视频 mp4 → ffmpeg 抽 16k mono wav，返回 wav 路径。
    文本缓存（.transcript/.subtitle/.clean）保留复用；音频 wav 走 cleanup_audio_files 自动删。"""
    vid = meta["id"]
    out_wav = AUDIO_DIR / f"{vid}.wav"
    if out_wav.exists() and out_wav.stat().st_size > 1024:
        print(f"📂 复用抖音音频: {out_wav}")
        return str(out_wav)
    play_url = meta.get("play_url") or (
        f"https://aweme.snssdk.com/aweme/v1/play/?line=0&ratio=720p&video_id={meta.get('play_uri','')}"
    )
    out_mp4 = AUDIO_DIR / f"{vid}.mp4"
    print("⬇️  下载抖音视频...")
    req = urllib.request.Request(play_url, headers={
        "User-Agent": _DOUYIN_MOBILE_UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    out_mp4.write_bytes(data)
    print(f"  ✅ 视频下载完成（{len(data) / 1024 / 1024:.1f} MB）")
    print("🎞  ffmpeg 抽 16k mono wav...")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_mp4),
             "-vn", "-ac", "1", "-ar", "16000", str(out_wav)],
            check=True,
        )
    finally:
        try:
            out_mp4.unlink()
        except OSError:
            pass
    return str(out_wav)


# ──────────────────────────────────────────────
# 小宇宙（音频直链，无字幕可依赖 → 直接 Whisper）
# ──────────────────────────────────────────────
_XYZ_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_XYZ_REFERER = "https://www.xiaoyuzhoufm.com/"
_XYZ_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def get_xyz_meta(raw_arg):
    """小宇宙单集：抓 SSR 页面 __NEXT_DATA__，取标题/时长/音频直链。
    官方转写接口（/v1/episode-transcript）需登录态 access token（实测 401），故不做字幕路径。"""
    m = re.search(r"/episode/([0-9a-fA-F]{24})", raw_arg)
    eid = m.group(1) if m else raw_arg.strip()
    url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
    print("🔎 抓取小宇宙单集页面...")
    req = urllib.request.Request(url, headers={"User-Agent": _XYZ_UA, "Referer": _XYZ_REFERER})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    m = _XYZ_NEXT_DATA.search(html)
    if not m:
        raise RuntimeError("小宇宙页面解析失败：未找到 __NEXT_DATA__")
    page = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
    ep = page.get("episode") or {}
    if not ep:
        raise RuntimeError("小宇宙页面解析失败：无 episode 数据（单集可能已下架）")

    audio_url = ((ep.get("enclosure") or {}).get("url") or "").strip()
    if not audio_url:
        raise RuntimeError("未取到小宇宙音频直链（可能是付费/私密单集）")

    desc = re.sub(r"<[^>]+>", " ", ep.get("description") or "")
    desc = re.sub(r"[ \t]+", " ", desc).strip()
    title = ep.get("title") or f"小宇宙单集 {eid}"
    podcast = (ep.get("podcast") or {}).get("title", "")
    return {
        "id": eid,
        "platform": "xyz",
        "title": title,
        "url": url,
        "audio_url": audio_url,
        "duration": int(ep.get("duration") or 0),
        "uploader": podcast,
        "view": int(ep.get("playCount") or 0),
        "desc": desc[:600] + ("…（完整 shownotes 见原页面）" if len(desc) > 600 else ""),
        "pubdate": (ep.get("pubDate") or "")[:10],
    }


def download_xyz_audio(meta, cfg):
    """小宇宙音频是 CDN 直链 m4a，urllib 流式下载即可（无需 yt-dlp）"""
    vid = meta["id"]
    cached = find_audio_cache(vid)
    if cached:
        print(f"📂 复用已有音频: {cached}")
        return cached
    url = meta.get("audio_url")
    if not url:
        raise RuntimeError("无音频直链")
    out = AUDIO_DIR / f"{vid}.m4a"
    print("⬇️  下载小宇宙音频中（可能上百 MB，请稍候）...")
    req = urllib.request.Request(url, headers={"User-Agent": _XYZ_UA, "Referer": _XYZ_REFERER})
    last_mb = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(out, "wb") as f:
        total = int(resp.headers.get("content-length") or 0)
        while True:
            buf = resp.read(1024 * 256)
            if not buf:
                break
            f.write(buf)
            done_mb = f.tell() // (1024 * 1024)
            if done_mb >= last_mb + 8:
                last_mb = done_mb
                print(f"   … 已下载 {done_mb} MB"
                      + (f" / {total // (1024 * 1024)} MB" if total else ""), flush=True)
    if out.stat().st_size < 1024 * 100:
        raise RuntimeError("下载文件过小，疑似音频直链失效")
    print(f"✅ 音频下载完成（{out.stat().st_size // (1024 * 1024)} MB）")
    return str(out)


# ──────────────────────────────────────────────
# 字幕校验
# ──────────────────────────────────────────────
def validate_subtitle(candidate, title):
    cn_title = set(re.sub(r"[^\u4e00-\u9fff]", " ", title).split())
    cn_sub = set(re.sub(r"[^\u4e00-\u9fff]", " ", candidate[:500]).split())
    cn_overlap = cn_title & cn_sub
    en_title = set(w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", title))
    en_sub = set(w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", candidate[:500]))
    en_overlap = en_title & en_sub
    return len(candidate) > 100 and (len(cn_overlap) >= 1 or len(en_overlap) >= 1)


# ──────────────────────────────────────────────
# 音频下载 + Whisper
# ──────────────────────────────────────────────
COOKIE_FILE = AUDIO_DIR / "bili_cookies.txt"

# 音频处理完成后即清理，仅保留文本缓存（subtitle/transcript/clean）以备复用
AUDIO_EXTS = (".m4a", ".wav", ".mp3", ".aac", ".opus", ".webm")


def cleanup_audio_files(vid):
    """删除指定视频的音频文件；文本缓存（字幕/转写/清洗）保留复用"""
    removed = []
    for f in glob.glob(str(AUDIO_DIR / f"{vid}.*")):
        if f.lower().endswith(AUDIO_EXTS):
            try:
                os.remove(f)
                removed.append(Path(f).name)
            except OSError:
                pass
    if removed:
        print(f"🧹 音频文件已清理: {', '.join(removed)}")


def generate_cookie_file(cfg):
    cookie = (cfg.get("bilibili") or {}).get("cookie", {})
    sessdata = cookie.get("SESSDATA", "")
    if not sessdata:
        return False
    expiry = int(time.time()) + 86400 * 365
    lines = [
        "# Netscape HTTP Cookie File",
        ".bilibili.com\tTRUE\t/\tTRUE\t" + str(expiry) + "\tSESSDATA\t" + sessdata,
        ".bilibili.com\tTRUE\t/\tTRUE\t" + str(expiry) + "\tbili_jct\t" + cookie.get("bili_jct", ""),
        ".bilibili.com\tTRUE\t/\tFALSE\t" + str(expiry) + "\tbuvid3\t" + cookie.get("buvid3", ""),
    ]
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


def get_cookie_file(cfg):
    if COOKIE_FILE.exists():
        return str(COOKIE_FILE)
    if generate_cookie_file(cfg):
        return str(COOKIE_FILE)
    return None


def find_audio_cache(vid):
    for f in glob.glob(str(AUDIO_DIR / f"{vid}.*")):
        if not f.endswith((".txt", ".vtt", ".srt", ".json")):
            return f
    return None


def download_audio(vid, url, cfg, proxy=None):
    """yt-dlp 下载音频；已有缓存直接复用。proxy 仅 YouTube 需要"""
    import yt_dlp

    cached = find_audio_cache(vid)
    if cached:
        print(f"📂 复用已有音频: {cached}")
        return cached

    print("⬇️  下载音频中...")
    if "x.com" in url or "twitter.com" in url:
        referer = "https://x.com/"
    elif "bilibili" in url:
        referer = "https://www.bilibili.com"
    else:
        referer = "https://www.youtube.com/"
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(AUDIO_DIR / f"{vid}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer,
        },
    }
    if proxy:
        ydl_opts["proxy"] = proxy
        print("🛰  使用代理下载")
    cookie_path = get_cookie_file(cfg)
    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    cached = find_audio_cache(vid)
    if not cached:
        raise RuntimeError("音频下载失败，未找到输出文件")
    return cached


_WHISPER_VENV = SCRIPT_DIR.parent / "whisper_venv/bin/python3"


def _require_whisper_venv():
    """本地 Whisper 环境不在发布包内，缺了要给可操作的报错而不是晦涩的子进程失败"""
    if not _WHISPER_VENV.exists():
        raise RuntimeError(
            f"未找到 Whisper 运行环境 {_WHISPER_VENV}。请先在技能目录执行 "
            "python3.9 -m venv whisper_venv && ./whisper_venv/bin/pip install openai-whisper"
            "（详见 SKILL.md 环境依赖）"
        )

# 分片转写：每个子进程输出 [{s:start秒, t:text}] 供跨片拼接时裁掉重叠区
_CHUNK_CODE = (
    "import sys, json, whisper\n"
    "wav, model_name, lang, out_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
    "model = whisper.load_model(model_name)\n"
    "kwargs = {'verbose': False}\n"
    "if lang != 'auto':\n"
    "    kwargs['language'] = lang\n"
    "r = model.transcribe(wav, **kwargs)\n"
    "json.dump([{'s': round(s['start'], 1), 't': s['text'].strip()} for s in r.get('segments', [])],\n"
    "          open(out_json, 'w'), ensure_ascii=False)\n"
)

_PARALLEL_MIN_SECONDS = 1500   # 音频短于 25 分钟不做分片
_CHUNK_TARGET_SECONDS = 1080   # 每片目标时长（约 18 分钟）
_CHUNK_OVERLAP = 12            # 相邻片重叠秒数，避免切在词中间丢字


def probe_duration(path):
    """ffprobe 读音频时长（秒）；失败返回 0"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def merge_chunk_segments(chunks):
    """chunks: [(nominal_begin, nominal_end, extract_start, segments), ...]
    按绝对时间裁掉各片重叠区，再按顺序拼接成完整文字稿"""
    parts = []
    for nominal_begin, nominal_end, extract_start, segs in chunks:
        for s in segs:
            abs_t = extract_start + float(s.get("s", 0))
            if nominal_begin - 0.5 <= abs_t < nominal_end:
                t = (s.get("t") or "").strip()
                if t:
                    parts.append(t)
    out = ""
    for p in parts:
        if out and (out[-1].isascii() and out[-1].isalnum()) and (p[:1].isascii() and p[:1].isalnum()):
            out += " " + p
        else:
            out += p
    return out


def transcribe_parallel(audio_path, model_name, language, workers):
    """把长音频切成 workers 片并行 Whisper 转写，再裁重叠拼接。
    每进程限 1 线程（OMP_NUM_THREADS=1），靠多进程吃满多核；内存占用 = workers × 模型"""
    duration = probe_duration(audio_path)
    if duration <= 0:
        raise RuntimeError("无法读取音频时长")
    workers = max(2, int(workers))
    span = duration / workers
    tmpd = Path(tempfile.mkdtemp(prefix="zw_chunks_"))
    procs = []
    try:
        for i in range(workers):
            nominal_begin = i * span
            nominal_end = duration if i == workers - 1 else (i + 1) * span
            extract_start = max(0.0, nominal_begin - (_CHUNK_OVERLAP if i else 0))
            extract_end = min(duration, nominal_end + (_CHUNK_OVERLAP if i < workers - 1 else 0))
            wav = tmpd / f"c{i:02d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{extract_start:.3f}",
                 "-t", f"{extract_end - extract_start:.3f}", "-i", str(audio_path),
                 "-ar", "16000", "-ac", "1", str(wav)],
                check=True, timeout=1800,
            )
            script = tmpd / f"asr{i:02d}.py"
            script.write_text(_CHUNK_CODE, encoding="utf-8")
            out_json = tmpd / f"c{i:02d}.json"
            env = dict(os.environ, OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
            log = open(tmpd / f"c{i:02d}.log", "w")
            p = subprocess.Popen(
                [str(_WHISPER_VENV), str(script), str(wav), model_name, language, str(out_json)],
                env=env, stdout=log, stderr=log,
            )
            procs.append((i, nominal_begin, nominal_end, extract_start, p, out_json, log))

        chunks = []
        failed = []
        for i, nb, ne, es, p, out_json, log in procs:
            rc = p.wait()
            log.close()
            if rc != 0 or not Path(out_json).exists():
                failed.append(i)
                continue
            segs = json.loads(Path(out_json).read_text(encoding="utf-8"))
            chunks.append((nb, ne, es, segs))
            print(f"   ✔ 分片 {i + 1}/{workers} 完成（{len(segs)} 段）")
        if failed:
            raise RuntimeError(f"分片 {failed} 转写失败，改用单进程模式")
        chunks.sort(key=lambda c: c[0])
        return merge_chunk_segments(chunks)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _whisper_workers(workers_cfg, duration, model_name="base"):
    """决定并行度：配置优先；默认取 min(核数, 内存上限, 10, 音频时长/每片目标)。
    内存上限按模型估算，避免多进程把内存打到 swap 反而变慢（16GB + base 实测 10 片会换页）"""
    try:
        n = int(workers_cfg or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    cores = os.cpu_count() or 4
    try:
        total_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
    except (OSError, ValueError, AttributeError):
        total_gb = 16.0
    per_worker_gb = 2.5 if model_name in ("small", "medium") else 1.5
    mem_cap = max(2, int(total_gb * 0.6 / per_worker_gb))
    by_len = max(1, int(-(-duration // _CHUNK_TARGET_SECONDS)))  # 向上取整
    return max(1, min(cores, mem_cap, 10, by_len))


def transcribe_with_whisper(audio_path, model_name="base", language="auto"):
    """Whisper 本地识别（子进程调用系统 Python venv）。
    长音频自动切分片并行转写（多进程吃满多核），短音频单进程。"""
    _require_whisper_venv()
    duration = probe_duration(audio_path)
    workers = _whisper_workers((CFG.get("whisper") or {}).get("parallel_workers"), duration, model_name)
    if duration > _PARALLEL_MIN_SECONDS and workers > 1:
        print(f"⚡ 长音频并行转写: {duration / 60:.0f} 分钟 → {workers} 片并行"
              f"（{model_name}，每片约 {duration / workers / 60:.0f} 分钟）")
        try:
            text = transcribe_parallel(audio_path, model_name, language, workers)
            if len(text) > 50:
                return text
            print("⚠️  并行转写结果过短，回退单进程模式")
        except Exception as e:
            print(f"⚠️  并行转写失败（{e}），回退单进程模式")

    code = (
        "import sys, whisper\n"
        "audio_path = sys.argv[1]\n"
        "model_name = sys.argv[2]\n"
        "lang = sys.argv[3] if len(sys.argv) > 3 else 'auto'\n"
        "model = whisper.load_model(model_name)\n"
        "print('WHISPER_READY', flush=True)\n"
        "kwargs = {}\n"
        "if lang != 'auto':\n"
        "    kwargs['language'] = lang\n"
        "result = model.transcribe(audio_path, **kwargs)\n"
        "print(result['text'])\n"
    )
    script_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        script_file.write(code)
        script_file.close()
        print(f"🔊 加载 Whisper {model_name} 模型（language={language}）...")
        proc = subprocess.run(
            [str(_WHISPER_VENV), script_file.name, audio_path, model_name, language],
            capture_output=True, text=True, timeout=7200,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Whisper 子进程失败: {proc.stderr.strip()}")
        lines = [l for l in proc.stdout.strip().split("\n") if l != "WHISPER_READY"]
        return "\n".join(lines)
    finally:
        try:
            os.unlink(script_file.name)
        except OSError:
            pass


# ──────────────────────────────────────────────
# 文字稿获取（字幕优先 → Whisper 兜底）
# ──────────────────────────────────────────────
_SUBTITLE_SOURCE = {"bilibili": "bilibili_subtitle", "x": "x_subtitle", "youtube": "youtube_subtitle"}
# 只有识别类来源才需要 AI 清洗纠错；手动/官方字幕质量高，跳过清洗（省时省 token，也避免 AI 改错原文）
_NEED_CLEAN_SOURCES = {"whisper", "youtube_autosub", "bilibili_subtitle"}


def get_transcript(meta, cfg, proxy=None):
    vid = meta["id"]
    platform = meta["platform"]

    # 缓存复用（跨平台按 id 区分）
    sub_cache = AUDIO_DIR / f"{vid}.subtitle.txt"
    if sub_cache.exists():
        cached = sub_cache.read_text(encoding="utf-8")
        if len(cached) > 100 and validate_subtitle(cached, meta["title"]):
            print(f"📂 复用已缓存字幕: {sub_cache}")
            return cached, _SUBTITLE_SOURCE.get(platform, "subtitle")

    # 1. 字幕优先
    text = ""
    source = "none"
    if platform == "bilibili":
        subs = get_bilibili_subtitles(vid, meta.get("cid"), (cfg.get("bilibili") or {}).get("cookie", {}).get("SESSDATA", ""))
        if subs:
            try:
                text = bilibili_subtitle_to_text(subs[0]["subtitle_url"])
                source = "bilibili_subtitle"
            except Exception as e:
                print(f"⚠️  字幕下载失败（{e}），切换到 Whisper")
        if text and not validate_subtitle(text, meta["title"]):
            print("⚠️  B站字幕与标题不匹配，切换到 Whisper")
            text = ""
    elif platform == "x":
        text, source = get_x_subtitle(meta, proxy)
    elif platform == "xyz":
        # 小宇宙无公开字幕接口，直接走 Whisper
        text, source = "", "none"
    else:
        text, source = get_youtube_subtitle(meta, proxy)

    if text and source != "none":
        try:
            sub_cache.write_text(text, encoding="utf-8")
        except OSError:
            pass
        return text, source

    # 2. Whisper 兜底
    whisper_model = (cfg.get("whisper") or {}).get("model", "base")
    # B站/抖音默认中文；YouTube/X 自动检测（中英文都支持）
    lang = "zh" if platform in ("bilibili", "douyin", "xyz") else "auto"
    return get_whisper_transcript(meta, cfg, whisper_model, lang, proxy)


def get_whisper_transcript(meta, cfg, whisper_model, language, proxy=None):
    vid = meta["id"]
    cache = AUDIO_DIR / f"{vid}.transcript.txt"
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        if len(text) > 50:
            print(f"📂 复用已识别文字稿: {cache}")
            return text, "whisper"

    print("🎙  使用 Whisper 本地语音识别...")
    try:
        if meta.get("platform") == "douyin":
            audio_path = download_douyin_audio(meta, cfg)
        elif meta.get("platform") == "xyz":
            audio_path = download_xyz_audio(meta, cfg)
        else:
            audio_path = download_audio(vid, meta["url"], cfg, proxy)

        # 时长校验：实际音频明显短于标称时长 = 试看片段 / 付费墙 / 下载被截断。
        # 必须显式报警，否则"只转写了前 10 分钟"会被当成完整总结交付。
        real_dur = probe_duration(audio_path)
        expect_dur = float(meta.get("duration") or 0)
        if real_dur > 60 and expect_dur > 0 and real_dur < expect_dur * 0.9 and (expect_dur - real_dur) > 90:
            warn = (
                f"⚠️  音频实际时长 {_dur_cn(real_dur)}，视频标称时长 {_dur_cn(expect_dur)}"
                f"（只拿到前 {real_dur / 60:.0f} 分钟），疑似试看/付费片段或下载被截断。"
            )
            print(warn)
            meta["_short_audio_warn"] = warn

        transcript = transcribe_with_whisper(audio_path, whisper_model, language)
        try:
            cache.write_text(transcript, encoding="utf-8")
        except OSError:
            pass
        print(f"✅ Whisper 识别完成（{len(transcript)} 字）")
        cleanup_audio_files(vid)
        return transcript, "whisper"
    except Exception as e:
        print(f"❌ 语音识别失败: {e}")
        return "", "none"


# ──────────────────────────────────────────────
# AI 调用与总结（输出统一中文）
# ──────────────────────────────────────────────
def _call_ai(messages, cfg, max_tokens):
    api_key = cfg["ai"]["api_key"]
    base_url = cfg["ai"]["base_url"].rstrip("/")
    model = cfg["ai"]["model"]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    url = f"{base_url}/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    last_err = None
    for attempt in range(4):
        wait = (2 ** attempt) * 5
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"].strip()
            if content:
                return content
            print(f"⚠️  AI 返回空内容，{wait}s 后重试 ({attempt+1}/4)...")
            time.sleep(wait)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                print(f"⚠️  AI 接口返回 {e.code}，{wait}s 后重试 ({attempt+1}/4)...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            last_err = e
            print(f"⚠️  AI 调用异常({e})，{wait}s 后重试 ({attempt+1}/4)...")
            time.sleep(wait)
    raise last_err or RuntimeError("AI 多次返回空内容，已放弃")


def _split_into_chunks(text, max_chars=12000, overlap=300):
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            cut = -1
            for i in range(end, max(start + max_chars // 2, start + 1), -1):
                if text[i] in "。！？\n.!?":
                    cut = i + 1
                    break
            if cut > start:
                end = cut
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


_CLEAN_SYS = (
    "你是一名严谨的字幕/语音识别纠错助手。下面是一段视频转写文本（来自字幕或 Whisper 语音识别），"
    "可能含有同音/近音错别字、英文术语误听、人名/公司名/产品名混淆等识别错误。"
    "请仅做最小必要修正：结合上下文语义，纠正明显的人名、机构名、产品名、英文术语、数字单位等误听；"
    "保持原文的句子结构、分段、标点和所有直接引述不变；不要增删内容、不要改写风格、不要翻译、不要概括。"
    "仅在结合上下文高置信度时修改，不确定则保留原词，严禁臆造。"
    "直接输出纠错后的文本，不要任何解释或客套语。"
)

_SUMMARY_SYS = (
    "你是专业的视频内容分析师，擅长将视频原文整理成详细、有价值的中文总结报告。"
    "无论视频转写原文是什么语言（中文或英文），最终总结一律使用简体中文输出。"
    "【语言要求】全文用通顺的简体中文撰写：英文原话一律翻译成中文，严禁中英混杂、严禁照抄英文长句、"
    "严禁用引号标注大段英文原文。仅保留必要的专有名词与术语原文——如人名、机构名、公司名、产品名、"
    "品牌名、项目/论文名、专业术语（例如 Fei-Fei Li、ImageNet、Stanford HAI、Sora、AlphaGo、"
    "Transformer 等），以及精确数字和单位。"
    "总结时要忠于原文，内容翔实、全面、准确，不遗漏重要信息、不添油加醋。"
    "注意：待总结的原文来自视频语音识别或字幕，可能存在同音/近音错别字、英文术语误听、"
    "人名/公司名/产品名混淆等问题。请先结合上下文语义自动纠正明显的识别错误"
    "（尤其人名、机构名、产品名、英文术语、数字），再据此总结。"
    "纠正原则：仅在结合上下文高置信度时纠正，不确定则保留原词，严禁臆造或替换成无关内容。"
    "直接输出总结正文，不要复述指令、不要以“好的/收到/当然”等客套语开头。"
)

_SUMMARY_REQ = """请根据以上内容，生成一份详尽、完整的中文视频总结，要求如下：

1. **核心主题**
   用2-3句话概括视频主旨。

2. **内容详解**（按视频讲述顺序完整展开，覆盖视频涉及的所有主要话题，不设数量上限）
   每个话题：
   - 写清楚讲了什么、怎么说的、关键论据与例子
   - **用中文准确转述原文的关键信息、观点、数据**，仅保留必要的专有名词/术语原文
   - 补充必要的背景或说明

3. **重要细节 / 干货**
   完整列举原文中提到的具体方法、步骤、数字、案例、技巧、人名、机构、作品等，尽量不遗漏。

4. **关键结论**
   总结最终观点或行动建议。

注意：总结要详尽、完整、准确，**不要精简、不要过度概括、不要压缩信息**。篇幅尽量充分，宁可长一些、信息密度高一些，也要让读者读完后能完整还原视频的全部信息。
全文一律使用简体中文，英文原话翻译成中文，不要中英混杂、不要照抄英文长句、不要用引号标注英文原文。"""


def clean_transcript(text, meta, cfg):
    if cfg.get("error_correction", "dual") != "dual" or not text:
        return text
    vid = meta["id"]
    cc = AUDIO_DIR / f"{vid}.clean.txt"
    if cc.exists():
        cached = cc.read_text(encoding="utf-8")
        if len(cached) > 50:
            print("📂 复用已清洗文字稿")
            return cached

    clean_chunk = 9000
    if len(text) <= clean_chunk:
        cleaned = _call_ai(
            [{"role": "system", "content": _CLEAN_SYS},
             {"role": "user", "content": "待纠错文本：\n" + text}],
            cfg, 48000,
        )
    else:
        chunks = _split_into_chunks(text, max_chars=clean_chunk, overlap=0)
        print(f"🧹 双 Pass：清洗分 {len(chunks)} 块...")
        parts = []
        for i, ch in enumerate(chunks, 1):
            c = _call_ai(
                [{"role": "system", "content": _CLEAN_SYS},
                 {"role": "user", "content": f"这是第 {i}/{len(chunks)} 段，待纠错文本：\n{ch}"}],
                cfg, 48000,
            )
            parts.append(c)
            print(f"  ✅ 清洗 {i}/{len(chunks)} 完成")
        cleaned = "\n".join(parts)

    try:
        (AUDIO_DIR / f"{vid}.clean.txt").write_text(cleaned, encoding="utf-8")
    except OSError:
        pass
    return cleaned


def summarize_with_ai(transcript, meta, cfg, source="whisper"):
    title = meta.get("title", "")
    uploader = meta.get("uploader", "")
    desc = meta.get("desc", "")
    duration = meta.get("duration", 0)
    m, s = divmod(duration, 60)

    header = f"视频标题：{title}\n作者：{uploader}\n时长：{m}分{s}秒"
    if desc:
        header += f"\n简介：{desc[:200]}"

    # 仅识别类来源（whisper/自动字幕/B站AI字幕）需要清洗纠错；手动/官方字幕质量高，跳过清洗
    if (
        transcript
        and cfg.get("error_correction", "dual") == "dual"
        and source in _NEED_CLEAN_SOURCES
    ):
        transcript = clean_transcript(transcript, meta, cfg)

    if not transcript:
        user_msg = f"{header}\n\n（无字幕，请根据标题和简介总结）\n\n{_SUMMARY_REQ}"
        return _call_ai(
            [{"role": "system", "content": _SUMMARY_SYS},
             {"role": "user", "content": user_msg}],
            cfg, 10000,
        )

    if len(transcript) <= 14000:
        user_msg = f"{header}\n\n视频转写内容（原文）：\n{transcript}\n\n{_SUMMARY_REQ}"
        return _call_ai(
            [{"role": "system", "content": _SUMMARY_SYS},
             {"role": "user", "content": user_msg}],
            cfg, 10000,
        )

    chunks = _split_into_chunks(transcript, max_chars=12000, overlap=300)
    print(f"📚 长文本分 {len(chunks)} 块总结中...")
    parts = []
    for i, ch in enumerate(chunks, 1):
        prompt = (
            f"{header}\n\n这是视频转写的第 {i}/{len(chunks)} 段原文，请详细整理本段内容：\n"
            f"- 写清楚本段讲了什么、怎么说的、有哪些具体论点\n"
            f"- **完整保留本段的关键信息、观点、数据、案例、例子**，不要遗漏\n"
            f"- 用中文准确转述，仅保留必要的专有名词/术语原文\n"
            f"- 不要写总体结论\n\n"
            f"本段原文：\n{ch}"
        )
        part = _call_ai(
            [{"role": "system", "content": "你是视频内容分析师，擅长详细整理段落内容。"
              "本段原文来自语音识别或字幕，可能有识别错误；整理前请先结合上下文纠正本段明显的人名、"
              "产品名、机构名、英文术语等误听，再据此整理；不确定则保留原词，不要臆造。"
              "无论原文是中文还是英文，一律用简体中文输出：英文原话翻译成中文，"
              "仅保留必要的专有名词/术语原文，严禁照抄英文长句。"
              "要求详细、完整，不要精简、不要压缩信息。"
              "直接输出正文，不要复述指令或以客套语开头。"},
             {"role": "user", "content": prompt}],
            cfg, 12000,
        )
        parts.append(f"【第{i}段要点】\n{part}")
        print(f"  ✅ 第 {i}/{len(chunks)} 块完成")

    combined = "\n\n".join(parts)
    integrate = (
        f"{header}\n\n以下是该视频各段落的要点摘录，请整合为一份完整、连贯的中文总结，"
        f"必须包含「核心主题 / 内容详解 / 重要细节 / 关键结论」四个部分。"
        f"直接输出总结正文，不要任何客套语、前言或说明。\n\n"
        f"{combined}\n\n{_SUMMARY_REQ}"
    )
    print("🔗 整合各块要点为完整总结...")
    summary = _call_ai(
        [{"role": "system", "content": _SUMMARY_SYS},
         {"role": "user", "content": integrate}],
        cfg, 48000,
    )
    for _ in range(2):
        tail = summary.rstrip()
        if "关键结论" in tail and tail[-1] in "。！？”)”":
            break
        print("  ↳ 总结疑似被截断，自动续写补全...")
        cont = _call_ai(
            [{"role": "system", "content": _SUMMARY_SYS},
             {"role": "user", "content":
                f"以下是已写出的总结前半部分：\n\n{summary}\n\n请直接从中断处继续写下去，"
                f"补全剩余内容，重点是完成「关键结论」部分。不要重复已有内容，直接续写。"}],
            cfg, 24000,
        )
        summary = summary.rstrip() + "\n" + cont
    return summary


# ──────────────────────────────────────────────
# 输出保存
# ──────────────────────────────────────────────
PLATFORM_LABEL = {"bilibili": "B站", "youtube": "YouTube", "x": "X/Twitter",
                  "douyin": "抖音", "xyz": "小宇宙"}


def save_summary_file(meta, source, summary):
    m, s = divmod(meta.get("duration", 0), 60)
    header = f"""## 视频总结报告

| 项目 | 内容 |
|------|------|
| **平台** | {PLATFORM_LABEL.get(meta.get('platform'), meta.get('platform'))} |
| **视频ID** | {meta.get('id')} |
| **标题** | {meta.get('title')} |
| **作者** | {meta.get('uploader')} |
| **时长** | {m:02d}:{s:02d} |
| **播放量** | {meta.get('view', 0):,} |
| **来源** | {source} |

"""
    desc = meta.get("desc", "")
    if desc:
        header += f"> {desc}\n\n---\n\n"
    else:
        header += "---\n\n"

    content = header + summary

    out_dir = get_output_dir()
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", meta.get("title", "视频"))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{safe_title}.md"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)
    except (PermissionError, OSError):
        out_file = Path.cwd() / f"{meta['id']}_视频总结.md"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)
    return str(out_file)


# ──────────────────────────────────────────────
# 主程序
# ──────────────────────────────────────────────
# 纯人工模式的"占位稿"标记：只有带此标记（或旧的「原始文字稿」标记）的文件才允许被覆盖，
# 已人工/AI 整理好的正式总结绝不覆盖。用 HTML 注释，Obsidian 渲染时不可见。
STUB_MARK = "<!-- ZW_SUMMARY_STUB -->"


def main():
    args = sys.argv[1:]
    raw = next((a for a in args if a.strip()), None)
    if not raw:
        print("\n🎬 zw-summary 视频总结工具（B站 + YouTube + X + 抖音 + 小宇宙）")
        print("用法: python3 zw_summary.py <BV号/B站链接/YouTube链接/X链接/抖音短链或完整链接/19位数字ID/小宇宙episode链接或24位ID>")
        raw = input("请输入链接或ID: ").strip()
        if not raw:
            return

    platform, vid = detect_platform(raw)
    proxy = detect_proxy()
    if proxy:
        print(f"\n🛰  检测到代理: {proxy}")

    # 元数据
    if platform == "douyin":
        meta = get_douyin_meta(raw)
    elif platform == "xyz":
        meta = get_xyz_meta(raw)
    elif platform == "bilibili":
        sessdata = (CFG.get("bilibili") or {}).get("cookie", {}).get("SESSDATA", "")
        meta = get_bilibili_meta(vid, sessdata)
    else:
        url = (
            f"https://www.youtube.com/watch?v={vid}"
            if platform == "youtube"
            else f"https://x.com/i/status/{vid}"
        )
        meta = get_stream_meta(vid, url, platform, proxy)

    print(f"\n📡 平台: {platform} | ID: {meta['id']}")
    print(f"🎬 {meta['title']}")

    # 充电专属 / 付费试看检测（B站）：充电专属视频对未充电账号只返回试看片段，
    # 不检测就会把试看当成完整视频总结掉。必须在转写前就告诉人。
    if platform == "bilibili":
        sessdata = (CFG.get("bilibili") or {}).get("cookie", {}).get("SESSDATA", "")
        paywall_msg, _pay_sec, _pay_total = check_bilibili_paywall(
            vid, meta.get("cid"), sessdata
        )
        if paywall_msg:
            print()
            print(paywall_msg)
            meta["_paywall_warn"] = paywall_msg

    # 多P合集检测：本脚本只处理第一个分P，必须显式提醒，别让"只转写了一集"被当成整个合集
    if platform == "bilibili":
        n_parts, parts_info = detect_multipart(vid, proxy)
        if n_parts > 1:
            total = sum(d for _, d in parts_info)
            print(f"\n⚠️  多P合集：共 {n_parts} 个分P，总时长约 {_dur_cn(total)}")
            print("⚠️  本脚本一次只处理【第一个分P】（yt-dlp noplaylist），其余分P不会转写、不会出现在总结里。")
            parts_txt = "、".join(f"P{i}({_dur_cn(d)})" for i, d in parts_info)
            print(f"    分P清单：{parts_txt}")
            print("    需要其他分P：① 给链接加 ?p=N 重跑；② 或多P批量转写")
            print(f"       python3 {SCRIPT_DIR}/transcribe_part.py {meta['id']} <P1> [P2] ...")
            print("    转录稿一律只进 scripts/audio/ 缓存，不写进 Obsidian 文档。")

    # 文字稿
    transcript, source = get_transcript(meta, CFG, proxy)

    # AI 总结：无 api_key 则跳过（纯人工模式，由 WorkBuddy 对话人工总结），不再报错
    api_key = (CFG.get("ai") or {}).get("api_key", "")
    ai_failed = False
    if not api_key:
        print("\n📝 未配置 AI 接口（api_key 为空），跳过自动总结，保存文字稿由人工总结")
        ai_failed = True
    else:
        print("\n🤖 生成总结中...")
        try:
            summary = summarize_with_ai(transcript, meta, CFG, source)
            # 试看/截断时，把警告顶到总结正文最前面，避免文档本身被当成完整内容
            _ws = content_warnings_md(meta)
            if _ws:
                summary = _ws + "\n" + summary
        except Exception as e:
            print(f"❌ AI总结失败: {e}")
            ai_failed = True

    # 纯人工模式：文档里只写"待人工总结"占位，**文字稿不进文档**（只留 scripts/audio/ 缓存供复用）
    if ai_failed:
        cache_path = AUDIO_DIR / f"{vid}.transcript.txt"
        if transcript:
            print(f"📝 文字稿已就绪（{len(transcript)} 字，来源：{source}），存于缓存，不写入文档：\n   {cache_path}")
        else:
            print("📝 未取到文字稿（字幕与音频都失败）")
        warn_block = content_warnings_md(meta)
        if warn_block:
            warn_block += "> 在拿到完整内容之前，**不要**把这段文字稿当作完整内容来总结。\n\n"
        fallback = (
            f"{STUB_MARK}\n"
            + warn_block
            + "⚠️ **AI 自动总结未执行**（未配置 AI 接口 api_key，或接口调用失败）。\n\n"
            f"原始文字稿**不写入本文档**，已存于缓存：`{cache_path}`"
            + (f"（{len(transcript)} 字，来源：{source}）。" if transcript else "（无可用文字稿）。")
            + "\n\n交由 WorkBuddy 对话人工整理后，用总结正文覆盖本文件。\n"
        )
        summary = fallback

    # 文字稿模式（跳过/失败）：不覆盖已有正式总结（人工或 AI 版），只提示
    if ai_failed:
        out_dir = get_output_dir()
        safe_title = re.sub(r'[<>:"/\\|?*]', "_", meta.get("title", "视频"))
        existing = out_dir / f"{safe_title}.md"
        try:
            if existing.exists():
                head = existing.read_text(encoding="utf-8", errors="ignore")[:2000]
                # 兼容旧格式（"原始文字稿"）：只有占位稿才允许覆盖，正式总结一律保留
                if "ZW_SUMMARY_STUB" not in head and "原始文字稿" not in head:
                    print(f"📄 已有总结文件，保留不覆盖: {existing}")
                    output_path = str(existing)
                    cleanup_audio_files(vid)
                    sep = "=" * 60
                    print(f"\n{sep}")
                    print(f"📹 {meta['title']}")
                    print(f"👤 {meta['uploader']}  |  👁 {meta['view']:,}  |  📝 来源: {source}")
                    print(sep)
                    for _w in content_warnings(meta):
                        print(_w)
                    print("（文字稿已就绪，未覆盖已有总结）")
                    print(sep)
                    print(f"\n📄 总结已保存: {output_path}")
                    return
        except OSError:
            pass
        output_path = save_summary_file(meta, source, summary)
    else:
        output_path = save_summary_file(meta, source, summary)

    # 兜底清理：无论走字幕还是 Whisper 路径，确保本视频音频不残留（文本缓存保留）
    cleanup_audio_files(vid)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"📹 {meta['title']}")
    print(f"👤 {meta['uploader']}  |  👁 {meta['view']:,}  |  📝 来源: {source}")
    for _w in content_warnings(meta):
        print(_w)
    print(sep)
    print(summary)
    print(sep)
    print(f"\n📄 总结已保存: {output_path}")


if __name__ == "__main__":
    main()
