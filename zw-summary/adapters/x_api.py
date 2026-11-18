#!/usr/bin/env python3
"""X (Twitter) 结构化取数 —— ego 真 Chrome 页面内截获平台自己的 API 响应（两步流水）。

为什么不直连 HTTP：X 有 TLS 指纹 + x-client-transaction-id 反爬，requests/httpx/twikit
在 Python 层一律被拦（2026-11-18 实测 500/404）。真 Chrome 传输层与真人浏览同构，
从旁截获的响应就是平台给搜索页的原始 JSON —— 结构化直出且风控暴露面最低。

两步用法（python 不拉起浏览器，由 agent/人跑第一步）:
  # 第 1 步：生成捕获脚本并跑（URL 按 --search/--user/--tweet 生成）
  python x_api.py --gen --search "关键词" --mode live > D:/WorkBuddy/ego/x-capture-run.mjs
  ego-browser nodejs < D:/WorkBuddy/ego/x-capture-run.mjs > D:/WorkBuddy/ego/x-capture-out.json
  # 第 2 步：解析落盘
  python x_api.py --parse D:/WorkBuddy/ego/x-capture-out.json --search "关键词" --limit 10

产物: C:/Users/12647/videosum/search_x_<关键词>/search.json（与 ego_fetch 对齐）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

OUT_ROOT = Path(r"C:\Users\12647\videosum")
CAPTURE_TEMPLATE = Path(r"D:\WorkBuddy\ego\x-capture.mjs")


def build_url(args) -> str:
    if args.search:
        q = quote(args.search)
        return f"https://x.com/search?q={q}&f=live" if args.mode == "live" else f"https://x.com/search?q={q}"
    if args.user:
        return f"https://x.com/{args.user}"
    return f"https://x.com/i/web/status/{args.tweet}"


def gen(args) -> None:
    tpl = CAPTURE_TEMPLATE.read_text(encoding="utf-8")
    print(tpl.replace("__TARGET_URL__", build_url(args)))


def walk_tweets(obj, out: list) -> None:
    """递归挖响应里的推文节点（SearchTimeline/UserTweets 嵌套结构不定）"""
    if isinstance(obj, dict):
        if "tweet_results" in obj:
            res = obj["tweet_results"].get("result", {})
            if res.get("__typename") == "TweetWithVisibilityResults":
                res = res.get("tweet", {})
            legacy = res.get("legacy") or {}
            user = ((res.get("core") or {}).get("user_results") or {}).get("result", {})
            uscreen = ((user.get("legacy") or {}).get("screen_name")
                       or (user.get("core") or {}).get("screen_name") or "?")
            out.append({
                "id": res.get("rest_id") or legacy.get("id_str") or "",
                "url": f"https://x.com/{uscreen}/status/{res.get('rest_id', '')}",
                "title": (legacy.get("full_text") or "")[:60],
                "text": legacy.get("full_text") or "",
                "author": uscreen,
                "created_at": legacy.get("created_at", ""),
                "stats": {
                    "like": legacy.get("favorite_count", 0),
                    "retweet": legacy.get("retweet_count", 0),
                    "reply": legacy.get("reply_count", 0),
                    "view": ((res.get("views") or {}).get("count") or 0),
                },
            })
        for v in obj.values():
            walk_tweets(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_tweets(v, out)


def parse(args) -> None:
    raw = Path(args.parse).read_text(encoding="utf-8", errors="ignore")
    start, end = raw.find("["), raw.rfind("]")
    if start < 0:
        sys.exit(f"捕获输出里没有 JSON 数组: {raw[:200]}")
    captures = json.loads(raw[start:end + 1])
    items: list = []
    for c in captures:
        walk_tweets(c.get("data", {}), items)
    seen, res = set(), []
    for it in items:
        if it["id"] and it["id"] not in seen:
            seen.add(it["id"])
            res.append(it)
    args.limit = min(args.limit, 50)  # X 取数硬上限：防风控触发，宁缺勿多
    res = res[: args.limit]

    if args.search:
        d = OUT_ROOT / f"search_x_{args.search}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "search.json").write_text(
            json.dumps({"platform": "x", "engine": "ego-capture", "keyword": args.search,
                        "items": res}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        for i, it in enumerate(res, 1):
            if not args.quiet:
                print(f"  {i}. @{it['author']}  ❤{it['stats']['like']}  {it['title']}")
        print(f"--- 共 {len(res)} 条 (截获 {len(captures)} 个API响应) ---")
        print(f"SUMMARY: search=x kw={args.search} found={len(res)} list={d / 'search.json'}")
    else:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        print(f"SUMMARY: found={len(res)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="X 取数（ego 捕获通道，两步流水）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--gen", action="store_true", help="生成捕获脚本（打印到 stdout）")
    g.add_argument("--parse", help="解析 ego-browser 的捕获输出文件")
    ap.add_argument("--search", help="搜索关键词（--gen/--parse 都要传）")
    ap.add_argument("--user", help="用户 handle")
    ap.add_argument("--tweet", help="推文 ID")
    ap.add_argument("--limit", type=int, default=10, help="最多取几条；上限 50（防风控，用户 2026-11-19 定）")
    ap.add_argument("--mode", default="live", choices=["live", "top"])
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.gen:
        gen(args)
    elif args.parse:
        parse(args)
    else:
        sys.exit("用法: --gen 生成脚本 / --parse 解析输出，两步流水见文件头注释")


if __name__ == "__main__":
    main()
