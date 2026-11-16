#!/usr/bin/env python3
"""多P视频：指定分P整段转写（分片并行 Whisper）。

用法:
    python transcribe_part.py <BV号> <P1> [P2] [P3] ...
例:
    python transcribe_part.py BV17Q4y1P7Ym 6 7 8 9

产物写到 /tmp/mpvs_out/p{N}.txt（一整行文本），已有的会跳过（可复跑）。
想直接落进 zw-summary 复用缓存，加 --cache 参数，缓存名形如
    {BV号}_p{N}.transcript.txt
若想带年份/主题后缀（推荐，便于人工查找），转写完成后手工改名即可。

设计要点：
- 全部走「yt-dlp 下整段音频 → ffmpeg 抽 16k mono wav → 按 18 分钟切片
  → whisper_venv 并行（每片 OMP_NUM_THREADS=1）→ 拼接」。
- 每片多切 12 秒做重叠，避免切点丢字。
- 下载失败自动重试 3 次。
注意：本脚本不会删除 /tmp/mpvs_out，清理请自行在流程最末尾做，不要和转写
写在同一条 shell 命令里（前面的步骤失败时 rm 仍会执行）。
"""
import glob
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"
VENV_W = os.path.join(_SKILL_DIR, "whisper_venv", "bin", "python3")
CACHE = os.path.join(_SCRIPTS_DIR, "audio")


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
WORK = "/tmp/mpvs_out"
SEG = 1080      # 每片 18 分钟
OVERLAP = 12    # 相邻片重叠秒数
MAXW = 6        # 并行片数；16GB 内存建议不超过 6


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    to_cache = "--cache" in sys.argv
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    bv, parts = args[0], [int(x) for x in args[1:]]

    if not os.path.exists(VENV_W):
        print(f"❌ 未找到 Whisper 运行环境 {VENV_W}\n"
              "   请先按 SKILL.md 的「环境依赖」在技能目录创建 whisper_venv")
        sys.exit(1)
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    wp = os.path.join(WORK, "_whisper_run.py")
    with open(wp, "w") as f:
        f.write(
            "import sys, whisper, os\n"
            "os.environ['OMP_NUM_THREADS']='1'\n"
            "model = whisper.load_model('base')\n"
            "r = model.transcribe(sys.argv[1], language='zh', fp16=False)\n"
            "open(sys.argv[2],'w').write(r['text'])\n"
        )

    for p in parts:
        out_txt = os.path.join(WORK, f"p{p}.txt")
        if os.path.exists(out_txt):
            print(f"p{p}: cached ({len(open(out_txt, encoding='utf-8').read())} chars)")
            continue
        d = os.path.join(WORK, f"part{p}")
        os.makedirs(d, exist_ok=True)
        wav = os.path.join(d, "full.wav")
        print(f"p{p}: downloading ...", flush=True)
        ok = False
        for attempt in range(3):
            cmd = [PY, "-m", "yt_dlp"]
            if PROXY:
                cmd += ["--proxy", PROXY]
            cmd += ["-f", "ba/b", "-o", os.path.join(d, "a.%(ext)s"),
                    "--no-playlist", "--quiet", "--no-warnings",
                    f"https://www.bilibili.com/video/{bv}?p={p}"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and glob.glob(os.path.join(d, "a.*")):
                ok = True
                break
            print(f"p{p}: retry {attempt + 1}", flush=True)
        if not ok:
            print(f"p{p}: DOWNLOAD FAILED", flush=True)
            continue

        src = glob.glob(os.path.join(d, "a.*"))[0]
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                        "-ac", "1", "-ar", "16000", wav], check=True)
        os.remove(src)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", wav],
            capture_output=True, text=True).stdout.strip())
        n = max(1, math.ceil(dur / SEG))
        print(f"p{p}: {dur/60:.1f} min -> {n} shards", flush=True)
        for i in range(n):
            subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-ss", str(i * SEG), "-t", str(SEG + OVERLAP),
                            "-i", wav, "-ac", "1", "-ar", "16000",
                            os.path.join(d, f"s{i:02d}.wav")], check=True)
        tasks = [(os.path.join(d, f"s{i:02d}.wav"), os.path.join(d, f"s{i:02d}.txt"))
                 for i in range(n)]
        with ThreadPoolExecutor(max_workers=min(MAXW, n)) as ex:
            list(ex.map(lambda t: subprocess.run([VENV_W, wp, t[0], t[1]],
                                                 capture_output=True), tasks))
        text = "".join(open(t[1], encoding="utf-8").read() for t in tasks)
        open(out_txt, "w", encoding="utf-8").write(text)
        print(f"p{p}: DONE {len(text)} chars -> {out_txt}", flush=True)

        if to_cache:
            dest = os.path.join(CACHE, f"{bv}_p{p}.transcript.txt")
            open(dest, "w", encoding="utf-8").write(text)
            print(f"p{p}: cached -> {dest}", flush=True)

        shutil.rmtree(d, ignore_errors=True)

    print("ALL DONE")


if __name__ == "__main__":
    main()
