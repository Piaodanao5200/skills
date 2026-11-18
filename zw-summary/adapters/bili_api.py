#!/usr/bin/env python3
"""B站 API 直取适配器 —— 不开浏览器，直接调 api.bilibili.com 拿结构化 JSON。

对照 ego_fetch.py（浏览器装人路线）：本适配器走 Web API + wbi 签名，更快、字段更全、
不怕页面改版。产物格式与 ego_fetch 对齐（meta.json / search.json），zw-summary 链路通用。

用法:
  python bili_api.py --search "关键词" --limit 5        站内搜索取前 N 条并逐条落盘
  python bili_api.py BV1RG33zPEof                        单条视频详情落盘
  python bili_api.py --search "kw" --limit 5 --quiet     批量静默（只留 SUMMARY）
  python bili_api.py --digest                            扫 --out 下的 meta.json 输出索引表

依据: SocialSisterYi/bilibili-API-collect (wbi 签名) · 未登录可查公开数据 · 密钥每日更替(已做缓存)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

OUT_ROOT = Path(r"C:\Users\12647\videosum")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")
# wbi 混淆表（bilibili-API-collect 权威版，勿改）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
STRIP_CHARS = str.maketrans("", "", "!'()*")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.headers["Referer"] = "https://www.bilibili.com/"
    return s


def load_cache() -> dict:
    p = OUT_ROOT / ".bili_api_cache.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(c: dict) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / ".bili_api_cache.json").write_text(
        json.dumps(c, ensure_ascii=False), encoding="utf-8")


def get_session_state(s: requests.Session) -> dict:
    """buvid3 + wbi 密钥，每日缓存（buvid 缓存长期有效，密钥按天刷新）。"""
    c = load_cache()
    today = time.strftime("%Y-%m-%d")
    buvid = c.get("buvid3")
    if not buvid:
        s.get("https://www.bilibili.com/", timeout=10)
        buvid = s.cookies.get("buvid3") or ""
        c["buvid3"] = buvid
    else:
        s.cookies.set("buvid3", buvid, domain=".bilibili.com")
    if c.get("wbi_date") == today and c.get("img_key") and c.get("sub_key"):
        return {"buvid3": buvid, "img_key": c["img_key"], "sub_key": c["sub_key"]}
    r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()
    wbi = r["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    c.update({"img_key": img_key, "sub_key": sub_key, "wbi_date": today})
    save_cache(c)
    return {"buvid3": buvid, "img_key": img_key, "sub_key": sub_key}


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def wbi_sign(params: dict, mk: str) -> dict:
    """wbi 签名：加 wts → 按 key 排序 → 过滤 !'()* → 大写百分号编码 → md5(query+mk)"""
    p = {k: str(v).translate(STRIP_CHARS) for k, v in params.items()}
    p["wts"] = str(int(time.time()))
    p = dict(sorted(p.items()))
    q = "&".join(f"{k}={quote(v, safe='')}" for k, v in p.items())
    p["w_rid"] = hashlib.md5((q + mk).encode()).hexdigest()
    return p


def clean_title(t: str) -> str:
    return re.sub(r"</?em[^>]*>", "", t or "").strip()


def api_get(s: requests.Session, url: str, params: dict = None) -> dict:
    r = s.get(url, params=params, timeout=15)
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"API code={j.get('code')}: {j.get('message', '')[:80]}")
    return j.get("data") or {}


def secs(v) -> int:
    if isinstance(v, int):
        return v
    parts = [int(x) for x in str(v).split(":")]
    return sum(x * 60 ** i for i, x in enumerate(reversed(parts)))


def search_videos(s: requests.Session, kw: str, limit: int, st: dict) -> list:
    signed = wbi_sign({"keyword": kw, "search_type": "video", "page": 1},
                      mixin_key(st["img_key"], st["sub_key"]))
    d = api_get(s, "https://api.bilibili.com/x/web-interface/search/type", signed)
    items = []
    for v in (d.get("result") or [])[:limit]:
        items.append({
            "id": v.get("bvid"),
            "url": v.get("arcurl") or f"https://www.bilibili.com/video/{v.get('bvid')}",
            "title": clean_title(v.get("title")),
            "author": v.get("author"),
            "extra": f"{v.get('play', 0)} {v.get('video_review', 0)} "
                     f"{datetime.fromtimestamp(v.get('pubdate', 0)).strftime('%m:%d')}",
        })
    return items


def fetch_detail(s: requests.Session, bvid: str, st: dict, quiet: bool) -> dict:
    out_dir = OUT_ROOT / f"bilibili_{bvid}"
    meta_path = out_dir / "meta.json"
    if not quiet:
        print(f"[2/2] {bvid}")
    v = api_get(s, "https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
    aid = v.get("aid")
    stat = v.get("stat") or {}
    tags, comments = [], []
    try:
        tags = [t.get("tag_name") for t in
                api_get(s, "https://api.bilibili.com/x/tag/archive/tags", {"bvid": bvid}) or []]
    except Exception as e:
        if not quiet:
            print(f"  [tags] {e}")
    try:
        rep = api_get(s, "https://api.bilibili.com/x/v2/reply/main",
                      {"type": 1, "oid": aid, "mode": 3})
        comments = [{"user": (r.get("member") or {}).get("uname"),
                     "like": (r.get("like") or 0),
                     "text": ((r.get("content") or {}).get("message") or "")[:200]}
                    for r in (rep.get("replies") or [])[:20]]
    except Exception as e:
        if not quiet:
            print(f"  [comments] {e}")
    meta = {
        "ok": True, "platform": "bilibili", "engine": "webapi",
        "id": bvid, "url": f"https://www.bilibili.com/video/{bvid}",
        "title": v.get("title"), "author": (v.get("owner") or {}).get("name"),
        "desc": (v.get("desc") or "")[:500],
        "when": datetime.fromtimestamp(v.get("pubdate", 0)).strftime("%Y-%m-%d %H:%M"),
        "duration": secs(v.get("duration", 0)),
        "stats": {"view": stat.get("view"), "danmaku": stat.get("danmaku"),
                  "reply": stat.get("reply"), "favorite": stat.get("favorite"),
                  "coin": stat.get("coin"), "share": stat.get("share"),
                  "like": stat.get("like")},
        "tags": tags, "comments": comments,
        "images": [v.get("pic")] if v.get("pic") else [],
        "subtitles": [], "media_audio": None, "media_video": None,
        "out_dir": str(out_dir), "errors": [],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def digest(out: Path) -> str:
    rows = []
    for mp in sorted(out.glob("*/meta.json")):
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not m.get("ok"):
            continue
        st = m.get("stats") or {}
        rows.append(f"{m.get('platform','?')}_{m.get('id','?')}  {m.get('duration','-')}s  "
                    f"view{st.get('view','-')} like{st.get('like','-')}  {m.get('title','')}")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="B站 API 直取（无需浏览器）")
    ap.add_argument("target", nargs="?", help="BV号（或省略，配合 --search）")
    ap.add_argument("--search", action="store_true", help="关键词搜索模式")
    ap.add_argument("--platform", default="bilibili")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default=str(OUT_ROOT))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--digest", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)

    if args.digest:
        print(digest(out))
        return

    s = make_session()
    st = get_session_state(s)
    ok = fail = 0

    if args.search:
        kw = args.target or ""
        if not kw:
            sys.exit("用法: bili_api.py --search \"关键词\"")
        if not args.quiet:
            print(f"[1/2] 搜索 bilibili 关键词='{kw}' 目标 {args.limit} 条")
        items = search_videos(s, kw, args.limit, st)
        if not items:
            print("[!] 搜索无结果")
        search_dir = out / f"search_bilibili_{kw}"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "search.json").write_text(
            json.dumps({"platform": "bilibili", "engine": "webapi", "keyword": kw,
                        "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        for i, it in enumerate(items, 1):
            if not args.quiet:
                print(f"    {i}. {it['title']} @{it['author']}")
            try:
                m = fetch_detail(s, it["id"], st, args.quiet)
                ok += 1
                print(f"    SUMMARY: ok=1 id={it['id']} view={m['stats']['view']} "
                      f"comments={len(m['comments'])} out={m['out_dir']}" if args.quiet
                      else f"    [{i}/{len(items)}] ok view={m['stats']['view']}")
            except Exception as e:
                fail += 1
                print(f"    SUMMARY: ok=0 fail=1 id={it['id']} err={e}" if args.quiet
                      else f"    [{i}/{len(items)}] fail: {e}")
            time.sleep(1.2)
        print(f"--- 共 {len(items)} 条 (ok={ok} fail={fail}) ---")
        print(f"SUMMARY: search=bilibili kw={kw} found={len(items)} ok={ok} fail={fail} "
              f"list={search_dir / 'search.json'}")
    else:
        bvid = args.target or ""
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            sys.exit("用法: bili_api.py BV1xxxxxxxxxx 或 --search \"关键词\"")
        try:
            m = fetch_detail(s, bvid, st, args.quiet)
            print(f"SUMMARY: ok=1 id={bvid} view={m['stats']['view']} out={m['out_dir']}")
        except Exception as e:
            print(f"SUMMARY: ok=0 fail=1 id={bvid} err={e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
