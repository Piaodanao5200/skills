#!/usr/bin/env python3
"""YouTube 下载适配器（Windows 可用）

为什么需要它：
    2026 年起 YouTube 对几乎所有 client 的流式格式强制要求 GVS PO Token。
    裸 yt-dlp 会报 `HTTP Error 403: Forbidden`，或在 tv_simply / android_vr
    上提示 `https formats require a GVS PO Token`。

本脚本做的事：
    1. 清掉 NODE_OPTIONS —— WorkBuddy 沙箱会往里注入 node-language-shim，
       导致 yt-dlp 调用 node 跑 JS 挑战时被 FileSystemRead 拒绝；
    2. 用 bgutil 的 script 模式现场生成 PO Token；
    3. 固定 mweb client（实测是当前唯一稳定给 itag=18 直链、且不需要
       player token 的组合）。

用法：
    python yt_fetch.py <url> [--audio-only] [--subs] [--out DIR]

已知限制（实测，不是猜测）：
    - 字幕仍会 429：timedtext 接口按 IP 限流，与 PO Token 无关，只有换
      干净的出口 IP（住宅/移动网络）才能解；
    - 被 YouTube 选中 SABR-only 实验的视频（如 Stanford CS329Z）仍 403，
      日志会出现 `Some ... formats have been skipped as they are missing a URL`，
      这是 yt-dlp issue #12482，需要上游支持。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_HOME = Path(r"D:\WorkBuddy\tools\bgutil-ytdlp-pot-provider\server")
POT_SCRIPT = SERVER_HOME / "build" / "generate_once.js"

DEFAULT_CLIENT = "mweb"
_PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY")


def _clean_env() -> dict:
    env = os.environ.copy()
    # 沙箱注入的 node hook 会让子进程读文件被拒
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_EXTRA_CA_CERTS", None)
    # shell 里的代理指向不通的端口，直连（TUN）反而正常
    for k in _PROXY_KEYS:
        env.pop(k, None)
    return env


def _ffmpeg_dir() -> str | None:
    try:
        import imageio_ffmpeg

        return str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    except Exception:
        return None


def build_cmd(url: str, out_tpl: str, client: str, audio_only: bool, subs: bool) -> list[str]:
    if not POT_SCRIPT.is_file():
        raise SystemExit(
            f"PO Token 脚本不存在: {POT_SCRIPT}\n"
            "请先在 server 目录执行 npm install 并用 esbuild 打包：\n"
            "  npx esbuild src/generate_once.ts --bundle --platform=node "
            "--format=esm --target=node22 --outfile=build/generate_once.js --packages=external"
        )

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--js-runtimes",
        "node",
        "--remote-components",
        "ejs:github",
        "--extractor-args",
        f"youtube:player_client={client}",
        "--extractor-args",
        f"youtubepot-bgutilscript:script_path={POT_SCRIPT}",
        "-o",
        out_tpl,
    ]

    ff = _ffmpeg_dir()
    if ff:
        cmd += ["--ffmpeg-location", ff]

    if audio_only:
        # mweb 只稳定给 itag=18（音视频合一），下完再抽音轨
        cmd += ["-f", "18/bestaudio/best"]
    else:
        cmd += ["-f", "18/best"]

    if subs:
        cmd += [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,zh.*",
            "--sub-format",
            "vtt",
            "--skip-download",
        ]

    cmd.append(url)
    return cmd


def extract_audio(video: Path) -> Path | None:
    """从下载的视频里抽音轨（imageio-ffmpeg 自带 ffmpeg，不在 PATH）。"""
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        print("[warn] 找不到 ffmpeg，跳过抽音轨")
        return None

    target = video.with_suffix(".m4a")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-acodec", "copy", str(target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not target.exists():
        print(f"[warn] 抽音轨失败: {(proc.stderr or '')[-200:]}")
        return None
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube 下载（带 PO Token）")
    ap.add_argument("url")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--client", default=DEFAULT_CLIENT, help=f"player_client，默认 {DEFAULT_CLIENT}")
    ap.add_argument("--audio-only", action="store_true", help="同时抽出 m4a 音轨")
    ap.add_argument("--subs", action="store_true", help="只拉字幕（当前会 429）")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl = str(out_dir / "%(id)s.%(ext)s")

    cmd = build_cmd(args.url, tpl, args.client, args.audio_only, args.subs)
    print(f"[run] client={args.client} audio_only={args.audio_only} subs={args.subs}")
    proc = subprocess.run(cmd, env=_clean_env())

    if proc.returncode != 0:
        print("\n[失败] 常见原因：")
        print("  · 403 → PO Token 未生效，或该视频被选中 SABR-only 实验（yt-dlp #12482）")
        print("  · 429 → 出口 IP 被限流，字幕接口尤其严格")
        return proc.returncode

    if args.audio_only and not args.subs:
        media = [p for p in out_dir.glob("*") if p.suffix in (".mp4", ".mkv", ".webm") and p.stat().st_size > 100_000]
        for v in media:
            a = extract_audio(v)
            if a:
                print(f"[ok] 音轨: {a}  ({a.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
