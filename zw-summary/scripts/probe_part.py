#!/usr/bin/env python3
"""抽样识别多P视频每个分P的内容。

用法:
    python probe.py <BV号> [起始P] [结束P]
例:
    python probe.py BV17Q4y1P7Ym 2 9

对每个 P 只下载开头 90 秒音频，用 whisper base 转写并打印前 400 字，
用来判断「这一 P 到底是什么内容、属于哪一年」——比看分P标题可靠得多。
"""
import json
import os
import subprocess
import sys

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"
VENV_W = os.path.join(_SKILL_DIR, "whisper_venv", "bin", "python3")


def _proxy_from_config():
    """代理优先取 zw_config.json 的 proxy，其次环境变量；为空则不传 --proxy"""
    try:
        with open(os.path.join(_SCRIPTS_DIR, "zw_config.json"), encoding="utf-8") as f:
            p = (json.load(f).get("proxy") or "").strip()
            if p:
                return p
    except Exception:
        pass
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()


PROXY = _proxy_from_config()
WORK = "/tmp/mpvs_probe"
SAMPLE_SECONDS = 90

PROBE = os.path.join(WORK, "_whisper_probe.py")
PROBE_SRC = (
    "import sys, whisper\n"
    "model = whisper.load_model('base')\n"
    "r = model.transcribe(sys.argv[1], language='zh', fp16=False)\n"
    "print(r['text'][:400])\n"
)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    bv = sys.argv[1]
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end = int(sys.argv[3]) if len(sys.argv) > 3 else start

    if not os.path.exists(VENV_W):
        print(f"❌ 未找到 Whisper 运行环境 {VENV_W}\n"
              "   请先按 SKILL.md 的「环境依赖」在技能目录创建 whisper_venv")
        sys.exit(1)

    os.makedirs(WORK, exist_ok=True)
    with open(PROBE, "w") as f:
        f.write(PROBE_SRC)

    for p in range(start, end + 1):
        raw = os.path.join(WORK, f"p{p}.m4a")
        wav = os.path.join(WORK, f"p{p}.wav")
        if not os.path.exists(wav):
            url = f"https://www.bilibili.com/video/{bv}?p={p}"
            cmd = [PY, "-m", "yt_dlp"]
            if PROXY:
                cmd += ["--proxy", PROXY]
            cmd += ["--download-sections", f"*0-{SAMPLE_SECONDS}", "--force-keyframes-at-cuts",
                    "-f", "ba/b", "-o", raw, "--no-playlist", "--quiet", "--no-warnings", url]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"\n===== p{p} =====\nDOWNLOAD FAIL: {r.stderr.strip()[-200:]}")
                continue
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw,
                            "-ac", "1", "-ar", "16000", wav], check=True)
            if os.path.exists(raw):
                os.remove(raw)
        r = subprocess.run([VENV_W, PROBE, wav], capture_output=True, text=True)
        print(f"\n===== p{p} =====")
        print((r.stdout or r.stderr).strip()[:400])
        sys.stdout.flush()


if __name__ == "__main__":
    main()
