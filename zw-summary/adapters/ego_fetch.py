#!/usr/bin/env python3
"""ego_fetch.py — 用 ego 浏览器取「元数据 + 媒体直链」，再下载到本地。

为什么不走 urllib 直连：
  B站 api 对裸请求返回 412 风控页；抖音桌面页是 RSC 渲染、视频走 blob，只能从
  网络请求里拿真实 CDN 地址；小红书新版 stream 用 EF7/EF4 编码键。这些在 ego 浏览器
  里都天然成立（有登录态、页面自己算签名），所以取数一律走浏览器内 evaluate。

用法:
  python ego_fetch.py <url 或 ID> [--out DIR] [--frames] [--only audio|video]
  关键词批量：
    python ego_fetch.py --search "agent" --platform bilibili --limit 5
    python ego_fetch.py "xiaohongshu:agent 工作流" --limit 3   # 平台:关键词 简写
  两阶段取数：
    批量扫描  python ego_fetch.py <url> --quiet        # 只落盘，最后一行 SUMMARY
    生成索引  python ego_fetch.py --digest             # 扫 --out 下已落盘的 meta.json
    深读单条  python ego_fetch.py <url> --full         # 正文全文 + 话题 + 评论

输出:
  默认最后一行 @@RESULT@@ {...}（机器解析用）
  --quiet 只输出 SUMMARY: ok=N ...（失败也会输出，不会静默）
  --full  输出 ===== FULL ===== 文本块（比 JSON 省 30-40% token）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

REFERER = {
    "douyin": "https://www.douyin.com/",
    "xhs": "https://www.xiaohongshu.com/",
    "bilibili": "https://www.bilibili.com/",
    "youtube": "https://www.youtube.com/",
    "x": "https://x.com/",
}

EGO = shutil.which("ego-browser") or r"C:\Users\12647\.local\share\ego\active_version_dir\ego-browser"
FRAMES_PY = r"D:\WorkBuddy\video_understanding\extract_frames.py"


def detect(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", raw):
        return "bilibili", raw
    if "douyin.com" in raw or "iesdouyin.com" in raw:
        m = re.search(r"(?:video/|modal_id=)(\d{15,21})", raw)
        return "douyin", m.group(1) if m else raw
    if "xiaohongshu.com" in raw or "xhslink.com" in raw:
        return "xhs", raw
    if "bilibili.com" in raw:
        m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
        return "bilibili", m.group(1) if m else raw
    if "youtube.com" in raw or "youtu.be" in raw:
        return "youtube", raw
    if "x.com" in raw or "twitter.com" in raw:
        return "x", raw
    if re.fullmatch(r"\d{15,21}", raw):
        return "douyin", raw
    return "unknown", raw


def build_url(platform: str, vid: str) -> str:
    if platform == "douyin" and re.fullmatch(r"\d{15,21}", vid):
        return f"https://www.douyin.com/video/{vid}"
    if platform == "bilibili" and vid.startswith("BV"):
        return f"https://www.bilibili.com/video/{vid}"
    return vid


SCRIPT = r"""
const URL = __URL__;
const WAIT = __WAIT__;
const NEED_FULL = __FULL__;   // 模块级只用于决定是否先滚动加载评论
const task = await taskSpace("fetch media");
let out = { ok: false, error: "not started", audios: [], videos: [], images: [] };
try {
  const page = await task.newPage();
  try { await page.goto(URL, { timeout: 30000 }); } catch (e) { /* 长连接页面 load 不触发属正常 */ }
  await page.waitForTimeout(WAIT);
  // 深读模式：先滚一屏，评论区和完整文案才会渲染；不回滚，避免元素被卸载
  if (NEED_FULL) {
    try { await page.evaluate(() => window.scrollBy(0, 900)); } catch (e) {}
    await page.waitForTimeout(3000);
  }
  out = await page.evaluate(async () => {
    const FULL = __FULL__;   // 页面上下文内的字面量，不能引用 Node 侧变量
    const r = { ok: false, audios: [], videos: [], images: [], title: "", author: "", desc: "",
                when: "", tab: "", duration: 0, stats: {}, subtitles: [], tags: [], comments: [], items: [], errors: [] };
    // X 的纯文字推文没有媒体，ok 判定要允许「有正文即可」；其他平台仍要求有媒体
    let isX = false;
    const host = location.hostname;
    const push = (arr, u) => { if (typeof u === "string" && /^https?:/.test(u)) arr.push(u); };
    const flatten = (s, n) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);

    r.title = (document.querySelector('meta[property="og:title"]')?.content
      || document.querySelector("h1")?.innerText || document.title || "").trim();
    r.desc = (document.querySelector('meta[property="og:description"]')?.content
      || document.querySelector('meta[name="description"]')?.content || "").trim();
    for (const v of document.querySelectorAll("video")) {
      if (v.duration && isFinite(v.duration)) r.duration = Math.round(v.duration);
      push(r.videos, v.currentSrc); push(r.videos, v.src);
    }

    try {
      // ---------- 抖音：blob 播放，真实地址只在网络请求里 ----------
      if (host.includes("douyin")) {
        const res = performance.getEntriesByType("resource").map(e => e.name);
        const uniq = (a) => [...new Set(a)];
        r.audios = uniq(res.filter(u => /media-audio|[a-z]a?mp4a/i.test(u) && /^https?:/.test(u)));
        r.videos = uniq(res.filter(u => /media-video|mime_type=video|\.mp4\?/i.test(u) && /^https?:/.test(u)));
        // 抖音是 RSC 渲染，meta 与 title 的完整度随时序变化，
        // 所以多源取最长者，不依赖单一字段
        const cands = [
          document.title,
          document.querySelector('meta[property="og:title"]')?.content,
          document.querySelector('meta[property="og:description"]')?.content,
          document.querySelector('meta[name="description"]')?.content,
          document.querySelector('[data-e2e="video-desc"]')?.innerText,
          document.querySelector('[data-e2e="detail-video-desc"]')?.innerText,
        ].map(s => flatten(s, 4000).replace(/\s*[-—]\s*抖音\s*$/, ""));
        const full = cands.sort((a, b) => b.length - a.length)[0] || "";
        if (full.length > (r.desc || "").length) r.desc = full;
        // 注意：不要用 document.body.innerText 兜底捞文案。
        // 抖音页面底部有「推荐视频」，其话题标签会被误当成本视频的，
        // 曾把标题污染成「热门：游戏视频推荐…」。宁可缺话题，也不能错。
        const firstLine = (r.desc || "").split(/[。！？\n|]/)[0].trim();
        if (firstLine) r.title = firstLine.slice(0, 60);
        // 作者：user-info 里是「名字粉丝X获赞Y关注Z」，切掉统计部分
        const ui = document.querySelector('[data-e2e="user-info"]');
        if (ui) r.author = flatten(ui.innerText, 80).split(/粉丝|获赞|关注/)[0].trim();
        const num = (sel) => {
          const el = document.querySelector(sel);
          const t = (el?.innerText || "").replace(/[^\d.万亿wWkK]/g, "");
          return t || null;
        };
        r.stats = { like: num('[data-e2e="video-player-digg"], [data-e2e="like-count"]'),
                    collect: num('[data-e2e="video-player-collect"], [data-e2e="collect-count"]') };
        if (FULL) {
          // 评论项是嵌套的：用户评论的 item 里又包着作者回复的 item，
          // 所以不能用 innerText 取最长行（会串到子节点）。
          // 用「N天前·地区」这行做锚点，正文就在它上面一行。
          const all = [...document.querySelectorAll('[data-e2e="comment-item"]')].slice(0, 30);
          r.comments = all.map(el => {
            const lines = (el.innerText || "").split("\n").map(s => s.trim()).filter(Boolean);
            const ti = lines.findIndex(s => /^[\d.]+[天小时分秒]前/.test(s));
            let text = "";
            if (ti > 0) {
              text = lines[ti - 1];
              if (/^(\.{2,}|…+|作者|展开|收起)$/.test(text) && ti > 1) text = lines[ti - 2];
            }
            return {
              author: (lines[0] || "").replace(/\s*作者\s*$/, ""),
              text: flatten(text, 400),
              isAuthor: lines.slice(0, 2).includes("作者"),
              reply: !!(el.parentElement && el.parentElement.closest('[data-e2e="comment-item"]'))
            };
          }).filter(c => c.text && c.text.length > 1);
        }
      }

      // ---------- 小红书 ----------
      if (host.includes("xiaohongshu")) {
        const s = window.__INITIAL_STATE__;
        const m = s?.note?.noteDetailMap || {};
        const k = Object.keys(m)[0];
        const note = k ? (m[k]?.note || {}) : null;
        if (note) {
          r.title = note.title || r.title;
          r.desc = note.desc || r.desc;
          r.author = note.user?.nickname || "";
          r.stats = { like: note.interactInfo?.likedCount, collect: note.interactInfo?.collectedCount, comment: note.interactInfo?.commentCount };
          r.images = (note.imageList || []).map(i => i.urlDefault || i.urlPre || "").filter(Boolean);
          const stream = note.video?.media?.stream || {};
          for (const codec of Object.keys(stream)) {
            const arr = stream[codec];
            if (Array.isArray(arr) && arr[0]) {
              const u = arr[0].masterUrl || (arr[0].backupUrls || [])[0];
              if (u) r.videos.unshift(u);
            }
          }
          if (note.video?.capa?.duration) r.duration = Math.round(note.video.capa.duration);
          if (FULL) {
            r.tags = (note.tagList || []).map(t => t.name).filter(Boolean).slice(0, 20);
            const cl = m[k]?.comments?.list || [];
            r.comments = cl.slice(0, 20).map(c => ({
              author: c.userInfo?.nickname || "",
              text: flatten(c.content, 300),
              like: c.likeCount
            })).filter(c => c.text);
          }
        }
      }

      // ---------- B站 ----------
      if (host.includes("bilibili")) {
        const init = window.__INITIAL_STATE__?.videoData;
        if (init) {
          r.title = init.title || r.title;
          r.desc = init.desc || r.desc;
          r.author = init.owner?.name || "";
          r.duration = init.duration || r.duration;
          r.stats = { view: init.stat?.view, danmaku: init.stat?.danmaku, like: init.stat?.like };
          r.bvid = init.bvid; r.aid = init.aid; r.cid = init.cid;
          const sub = init.subtitle?.list;
          if (Array.isArray(sub) && sub.length) r.subtitles = sub.map(s => ({ lan: s.lan, url: s.subtitle_url }));
          if (FULL) {
            const tg = (init.tags || []).map(t => t.tag_name).filter(Boolean);
            if (init.tname) tg.unshift(init.tname);
            r.tags = tg.slice(0, 20);
            // 评论不在页面初始数据里，走接口（带 cookie 才通）
            if (init.aid) {
              try {
                const resp = await fetch(
                  `https://api.bilibili.com/x/v2/reply?type=1&oid=${init.aid}&sort=2&ps=20&nohot=0`,
                  { credentials: "include" });
                const j = await resp.json();
                const reps = (j?.data?.replies) || [];
                r.comments = reps.slice(0, 20).map(c => ({
                  author: c.member?.uname || "",
                  text: flatten(c.content?.message, 300),
                  like: c.like
                })).filter(c => c.text);
              } catch (e) { r.errors.push("bili comment: " + String(e).slice(0, 120)); }
            }
          }
        }
        const dash = window.__playinfo__?.data?.dash;
        if (dash) {
          const a = (dash.audio || []).slice().sort((x, y) => (y.bandwidth || 0) - (x.bandwidth || 0))[0];
          if (a) { push(r.audios, a.baseUrl || a.base_url); (a.backupUrl || []).forEach(u => push(r.audios, u)); }
          const vids = (dash.video || []).filter(v => (v.id || 0) <= 64).sort((x, y) => (y.bandwidth || 0) - (x.bandwidth || 0))[0]
                     || (dash.video || []).slice().sort((x, y) => (x.bandwidth || 0) - (y.bandwidth || 0))[0];
          if (vids) { push(r.videos, vids.baseUrl || vids.base_url); (vids.backupUrl || []).forEach(u => push(r.videos, u)); }
        }
      }

      // ---------- YouTube ----------
      if (host.includes("youtube")) {
        const pr = window.ytInitialPlayerResponse;
        const vd = pr?.videoDetails;
        if (vd) {
          r.title = vd.title || r.title;
          r.author = vd.author || "";
          const fullDesc = vd.shortDescription || "";
          r.desc = FULL ? fullDesc : fullDesc.slice(0, 1200);
          r.duration = Number(vd.lengthSeconds) || r.duration;
          r.stats = { view: vd.viewCount };
        }
        const fmts = pr?.streamingData?.adaptiveFormats || [];
        const au = fmts.filter(f => (f.mimeType || "").startsWith("audio")).sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
        if (au?.url) r.audios.push(au.url);
        const vi = fmts.filter(f => (f.mimeType || "").startsWith("video") && f.url).sort((a, b) => (a.bitrate || 0) - (b.bitrate || 0))[0];
        if (vi?.url) r.videos.push(vi.url);
        const tracks = pr?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
        if (tracks.length) r.subtitles = tracks.map(t => ({ lan: t.languageCode, url: t.baseUrl, kind: t.kind }));
      }

      // ---------- X ----------
      // 注意：纯文字推文没有任何媒体，ok 判定不能只看媒体数（见下方 isTextOnly）。
      if (host.includes("x.com") || host.includes("twitter.com")) {
        isX = true;
        const arts = [...document.querySelectorAll('article[data-testid="tweet"]')];

        // 图片 URL：保留页面给的 format（原图可能是 png，写死 jpg 会 404），只把尺寸提到 orig
        const imgUrl = (im) => {
          const u = im.src || "";
          if (!/[?&]format=/.test(u)) return u.split("?")[0] + "?format=jpg&name=900x900";
          const base = u.replace(/[?&]name=[^&]+/, "").replace(/&$/, "");
          return base + (base.includes("?") ? "&" : "?") + "name=orig";
        };

        // 主页 / 回复页 / 媒体页都按「按账号巡」处理；搜索不可用时这是唯一的批量入口
        const path = location.pathname.replace(/\/+$/, "");
        const pm = path.match(/^\/([A-Za-z0-9_]{1,20})(?:\/(with_replies|media|highlights))?$/);
        const isProfile = !!pm && !/^\/(home|explore|search|i|settings|notifications|messages|compose)$/.test(path);
        if (isProfile && arts.length) {
          const take = FULL ? 20 : 6;
          const handle = pm[1];
          r.tab = pm[2] || "posts";
          r.author = "@" + handle;
          r.title = (document.querySelector('[data-testid="UserName"]')?.innerText || handle).split("\n")[0];
          r.desc = flatten(document.querySelector('[data-testid="UserDescription"]')?.innerText, 400);
          r.items = arts.slice(0, take).map(a => {
            const link = a.querySelector('a[href*="/status/"]')?.getAttribute("href") || "";
            const m = link.match(/\/status\/(\d+)/);
            const num = (sel) => {
              const el = a.querySelector(sel);
              if (!el) return "";
              const mm = (el.innerText || "").match(/[0-9][0-9.,]*[万亿KkMm]?/);
              return mm ? mm[0].replace(/,/g, "") : "";
            };
            // 每条推文自带的图（媒体页主要靠这个），最多 4 张
            const pics = [...a.querySelectorAll('img[src*="pbs.twimg.com/media"]')].slice(0, 4).map(imgUrl);
            if (r.images.length < 12) pics.forEach(u => push(r.images, u));
            return {
              id: m ? m[1] : "",
              url: link ? "https://x.com" + link.split("?")[0] : "",
              text: flatten(a.querySelector('[data-testid="tweetText"]')?.innerText, FULL ? 600 : 200),
              when: a.querySelector("time")?.getAttribute("datetime") || "",
              reply: num('[data-testid="reply"]'),
              repost: num('[data-testid="retweet"]'),
              like: num('[data-testid="like"]'),
              view: num('[data-testid="analytics"]') || num('a[href$="/analytics"]'),
              images: pics,
            };
          }).filter(x => x.id && x.text);
        }

        const tw = arts[0];
        if (tw && !isProfile) {
          const text = tw.querySelector('[data-testid="tweetText"]')?.innerText || "";
          r.desc = text;
          r.title = flatten(text, 120);
          // User-Name 是「显示名 \n @handle \n · \n 时间」
          const nameBlock = (tw.querySelector('[data-testid="User-Name"]')?.innerText || "")
            .split("\n").map(s => s.trim()).filter(Boolean);
          r.author = (nameBlock[0] || "") + (nameBlock[1] && nameBlock[1].startsWith("@") ? " " + nameBlock[1] : "");
          r.when = tw.querySelector("time")?.getAttribute("datetime") || "";
          const pick = (sel) => {
            const el = tw.querySelector(sel);
            if (!el) return "";
            // retweet 按钮 innerText 可能同时含转推数与引用数（曾拼出 70667065），
            // 浏览数还带「查看 / View」后缀，统一只取第一个数字段
            const s = el.innerText || el.getAttribute("aria-label") || "";
            const m = s.match(/[0-9][0-9.,]*[万亿KkMm]?/);
            return m ? m[0].replace(/,/g, "").replace(/\.$/, "") : "";
          };
          r.stats = {
            reply: pick('[data-testid="reply"]'),
            repost: pick('[data-testid="retweet"]'),
            like: pick('[data-testid="like"]'),
            view: pick('[data-testid="analytics"]') || pick('a[href$="/analytics"]'),
          };
          for (const im of tw.querySelectorAll('img[src*="pbs.twimg.com/media"]')) push(r.images, imgUrl(im));
          // 视频：<video> 常是 blob:，真实地址（含 m3u8）在网络请求里
          const v = tw.querySelector("video");
          if (v) { push(r.videos, v.currentSrc); push(r.videos, v.src); }
          [...(tw.querySelectorAll("video source") || [])].forEach(s => push(r.videos, s.src));
          if (!r.videos.length) {
            const res = performance.getEntriesByType("resource").map(e => e.name);
            res.filter(u => /video\.twimg\.com\/.*\.(mp4|m3u8)/.test(u)).slice(0, 4).forEach(u => push(r.videos, u));
          }
          if (!r.duration && v && v.duration && isFinite(v.duration)) r.duration = Math.round(v.duration);
        }
        // --full：主贴下面的回复（arts[1..]；主页模式下 arts 都是推文，不是回复）
        if (FULL && !isProfile && arts.length > 1) {
          r.comments = arts.slice(1, 21).map(a => {
            const nb = (a.querySelector('[data-testid="User-Name"]')?.innerText || "")
              .split("\n").map(s => s.trim()).filter(Boolean);
            return {
              author: nb[1] || nb[0] || "",
              text: flatten(a.querySelector('[data-testid="tweetText"]')?.innerText, 400),
              when: a.querySelector("time")?.getAttribute("datetime") || "",
              like: (a.querySelector('[data-testid="like"]')?.innerText || "").trim(),
            };
          }).filter(c => c.text);
        }
      }
    } catch (e) { r.errors.push(String(e)); }

    // 通用兜底：正文里以 # 开头的话题（各平台格式一致）
    if (!r.tags || !r.tags.length) {
      const found = [];
      const re = /#([^\s#＃]{1,20})/g;
      let m2;
      while ((m2 = re.exec(r.desc || "")) !== null) found.push(m2[1]);
      r.tags = [...new Set(found)].slice(0, 20);
    }

    const dedupe = (a) => [...new Set(a.filter(Boolean))];
    r.audios = dedupe(r.audios); r.videos = dedupe(r.videos);
    r.ok = r.audios.length + r.videos.length + r.images.length > 0
        || (isX && (!!(r.desc || r.title) || r.items.length > 0));
    return r;
  });
} catch (e) { out = { ok: false, error: String((e && e.stack) || e), audios: [], videos: [], images: [] }; }
console.log("@@DATA@@" + JSON.stringify(out));
try { await task.finish({ keep: [] }); } catch (e) {}
"""


SEARCH_URL = {
    "douyin": "https://www.douyin.com/search/{q}?type=video",
    "bilibili": "https://search.bilibili.com/all?keyword={q}",
    "xhs": "https://www.xiaohongshu.com/search_result?keyword={q}&source=web_explore_feed",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "x": "https://x.com/search?q={q}&f=top",
}

SEARCH_SCRIPT = r"""
const URL = __URL__;
const PLAT = __PLAT__;
const LIMIT = __LIMIT__;
const task = await taskSpace("search");
let out = { ok: false, platform: PLAT, items: [], error: "not started" };
try {
  const page = await task.newPage();
  try { await page.goto(URL, { timeout: 40000 }); } catch (e) { /* 长连接页面 load 不触发属正常 */ }
  await page.waitForTimeout(6000);
  // 搜索结果首屏常只有 4-6 条，滚动两次才能拿到足够候选
  for (let i = 0; i < 2; i++) {
    try { await page.evaluate(() => window.scrollBy(0, 1200)); } catch (e) {}
    await page.waitForTimeout(1800);
  }
  out = await page.evaluate(() => {
    const P = __PLAT__;
    const LIM = __LIMIT__;
    const r = { ok: false, items: [], error: "" };
    const flat = (s) => String(s || "").replace(/\s+/g, " ").trim();
    // 小红书卡片把「作者 + 时间」挤在一个节点里，作者名要切掉时间尾巴
    const cleanAuthor = (s) => flat(s).replace(/\s*(\d+\s*(分钟|小时|天)前|昨天.*|前天.*|刚刚|\d{2}-\d{2}).*$/, "").trim();
    const abs = (h) => { try { return new URL(h, location.origin).href; } catch (e) { return h; } };
    let items = [];

    if (P === "douyin") {
      // 搜索卡片整段 innerText 是「时长 / 播放量 / 标题+简介 / @作者 / 时间」固定顺序，
      // 没有稳定的 title 属性或 data-e2e，只能按位置解析。
      for (const a of document.querySelectorAll('a[href*="/video/"]')) {
        const m = (a.getAttribute("href") || "").match(/\/video\/(\d{15,21})/);
        if (!m) continue;
        const lines = (a.innerText || "").split("\n").map(s => s.trim()).filter(Boolean);
        if (!lines.length) continue;
        let i = 0, dur = "", play = "";
        if (/^\d{1,2}:\d{2}/.test(lines[0])) dur = lines[i++];
        if (/^[\d.]+(万|亿|w|W)?$/.test(lines[i] || "")) play = lines[i++];
        items.push({
          id: m[1],
          url: "https://www.douyin.com/video/" + m[1],
          title: flat(lines[i] || ""),
          author: (lines.slice(i + 1).find(s => s.startsWith("@")) || "").slice(1),
          extra: [dur, play].filter(Boolean).join(" "),
        });
      }
    } else if (P === "bilibili") {
      for (const a of document.querySelectorAll('a[href*="/video/BV"]')) {
        const m = (a.getAttribute("href") || "").match(/BV[0-9A-Za-z]{10}/);
        if (!m) continue;
        const card = a.closest(".bili-video-card") || a.parentElement;
        items.push({
          id: m[0],
          url: "https://www.bilibili.com/video/" + m[0],
          title: flat(card?.querySelector(".bili-video-card__info--tit")?.innerText
                   || a.getAttribute("title") || a.innerText),
          author: flat(card?.querySelector(".bili-video-card__info--author")?.innerText || ""),
          extra: flat(card?.querySelector(".bili-video-card__stats")?.innerText || ""),
        });
      }
    } else if (P === "xhs") {
      // 必须保留整条 href 里的 xsec_token，否则详情页 404
      for (const a of document.querySelectorAll('a[href*="/explore/"], a[href*="/search_result/"]')) {
        const href = a.getAttribute("href") || "";
        const m = href.match(/\/(?:explore|search_result)\/([0-9a-f]{24})/);
        if (!m) continue;
        const card = a.closest("section.note-item, .note-item, li") || a;
        items.push({
          id: m[1],
          url: abs(href),
          title: flat(card.querySelector(".title, .note-title")?.innerText || a.innerText),
          author: cleanAuthor(card.querySelector(".author, .name")?.innerText || ""),
          extra: flat(card.querySelector(".like-wrapper, .count")?.innerText || ""),
        });
      }
    } else if (P === "youtube") {
      // ytInitialData 是嵌套 object，递归找 videoRenderer
      const walk = (o, acc) => {
        if (!o || typeof o !== "object" || acc.length > 60) return acc;
        if (o.videoRenderer && o.videoRenderer.videoId) acc.push(o.videoRenderer);
        for (const k in o) { const v = o[k]; if (v && typeof v === "object") walk(v, acc); }
        return acc;
      };
      for (const v of walk(window.ytInitialData, [])) {
        items.push({
          id: v.videoId,
          url: "https://www.youtube.com/watch?v=" + v.videoId,
          title: flat(v.title?.runs?.[0]?.text || v.title?.simpleText),
          author: flat(v.ownerText?.runs?.[0]?.text || ""),
          extra: flat(v.lengthText?.simpleText || ""),
        });
      }
    } else if (P === "x") {
      for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
        const links = [...art.querySelectorAll('a[href*="/status/"]')];
        const a = links[links.length - 1];
        const m = (a?.getAttribute("href") || "").match(/\/status\/(\d+)/);
        if (!m) continue;
        items.push({
          id: m[1],
          url: abs(a.getAttribute("href")),
          title: flat(art.querySelector('[data-testid="tweetText"]')?.innerText),
          author: flat(art.querySelector('[data-testid="User-Name"]')?.innerText || "").split(" ")[0],
          extra: "",
        });
      }
    }

    const seen = new Set();
    r.items = items.filter(x => {
      if (!x.id || seen.has(x.id) || !x.title) return false;
      if (x.title.length < 3) return false;
      // B站搜索页右侧「热搜」卡片也是 /video/ 链接，但没有作者和播放数据，
      // 曾把一条电竞热门当成第 1 条结果。缺这两个字段的直接丢。
      if (P === "bilibili" && !x.author && !x.extra) return false;
      seen.add(x.id);
      x.title = x.title.slice(0, 90);
      return true;
    }).slice(0, LIM);
    r.ok = r.items.length > 0;
    if (!r.ok) {
      // 失败原因要能区分「没登录」和「站点自己报错」，否则下次还会盲猜
      const body = (document.body.innerText || "").slice(0, 1200);
      if (/出错了|重新加载|Something went wrong/i.test(body)) {
        r.error = "站点返回错误页（X 实测：多关键词、桌面/移动端、热门/最新全部报错，点重试无效，属账号侧搜索权限受限，非参数问题）→ 改用用户主页时间线，或直接喂单条链接";
      } else if (/扫码登录|登录后|Sign in|Log in/i.test(body)) {
        r.error = "页面要求登录（ego 里该平台登录态可能已失效）";
      } else {
        r.error = "搜索结果为空（页面结构可能已变，或该关键词无结果）";
      }
    }
    return r;
  });
} catch (e) { out = { ok: false, platform: PLAT, items: [], error: String((e && e.stack) || e) }; }
console.log("@@DATA@@" + JSON.stringify(out));
try { await task.finish({ keep: [] }); } catch (e) {}
"""


def run_ego(url: str, wait_ms: int, timeout: int = 240, full: bool = False) -> dict:
    script = (SCRIPT.replace("__URL__", json.dumps(url))
                    .replace("__WAIT__", str(wait_ms))
                    .replace("__FULL__", "true" if full else "false"))
    proc = subprocess.run([EGO, "nodejs"], input=script, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    # ego-browser 把 console.log 写到 stderr，stdout 常为空，两边都要看
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    for line in blob.splitlines():
        idx = line.find("@@DATA@@")
        if idx >= 0:
            try:
                return json.loads(line[idx + len("@@DATA@@"):])
            except json.JSONDecodeError:
                continue
    return {"ok": False, "error": "ego 无数据返回", "raw": blob.strip()[-500:],
            "audios": [], "videos": [], "images": []}


def run_search(platform: str, keyword: str, limit: int, timeout: int = 200) -> dict:
    url = SEARCH_URL[platform].format(q=urllib.parse.quote(keyword))
    script = (SEARCH_SCRIPT.replace("__URL__", json.dumps(url))
                           .replace("__PLAT__", json.dumps(platform))
                           .replace("__LIMIT__", str(limit)))
    proc = subprocess.run([EGO, "nodejs"], input=script, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
    for line in blob.splitlines():
        idx = line.find("@@DATA@@")
        if idx >= 0:
            try:
                return json.loads(line[idx + len("@@DATA@@"):])
            except json.JSONDecodeError:
                continue
    return {"ok": False, "items": [], "error": "ego 无数据返回", "raw": blob.strip()[-400:]}


def download(url: str, dest: Path, platform: str) -> tuple[bool, str]:
    headers = {"User-Agent": UA, "Referer": REFERER.get(platform, "https://www.google.com/"), "Accept": "*/*"}
    if platform == "bilibili":
        headers["Origin"] = "https://www.bilibili.com"
        headers["Range"] = "bytes=0-"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f, 1024 * 256)
        size = dest.stat().st_size
        if size < 30_000:
            return False, f"文件过小 {size}B，疑似错误页"
        return True, f"{size / 1024 / 1024:.1f}MB"
    except Exception as e:
        return False, str(e)


def say(msg: str, quiet: bool = False) -> None:
    """进度行：--quiet 时全部抑制，只留最后一行 SUMMARY。"""
    if not quiet:
        print(msg, flush=True)


def emit_full(result: dict) -> None:
    """深读模式：纯文本块输出。比 JSON dump 省 30-40% token（无键名与标点）。"""
    st = result.get("stats") or {}
    lines = ["===== FULL =====",
             f"平台: {result.get('platform')}   时长: {result.get('duration') or '-'}s",
             f"标题: {flatten1(result.get('title')) or '-'}",
             f"作者: {flatten1(result.get('author')) or '-'}"]
    if st:
        pair = " / ".join(f"{k} {v}" for k, v in st.items() if v not in (None, ""))
        if pair:
            lines.append("互动: " + pair)
    tags = result.get("tags") or []
    if tags:
        lines.append("话题: " + " ".join("#" + str(t) for t in tags))
    lines.append(f"链接: {result.get('url')}")
    lines.append(f"目录: {result.get('out_dir')}")

    posts = result.get("posts") or []
    if posts:
        lines.append(f"--- 该账号最近 {len(posts)} 条 ---")
        for p in posts:
            stat = " ".join(f"{k[:4]}{v}" for k, v in
                            (("reply", p.get("reply")), ("like", p.get("like")), ("view", p.get("view"))) if v)
            lines.append(f"[{(p.get('when') or '')[:10]}] {flatten1(p.get('text'))[:150]}")
            if stat:
                lines.append(f"    {stat}  {p.get('url')}")
        lines.append("")

    lines.append("--- 正文全文 ---")
    lines.append((result.get("desc") or "(无)").strip())

    subs = result.get("subtitles") or []
    if subs:
        lines.append(f"--- 字幕轨道 {len(subs)} 条 ---")
        for s in subs[:8]:
            lines.append(f"  {s.get('lan', '?')} kind={s.get('kind', '-')} url={str(s.get('url', ''))[:90]}")

    cm = result.get("comments") or []
    lines.append(f"--- 评论 {len(cm)} 条 ---")
    if cm:
        for c in cm[:15]:
            # 回复层缩进，作者本人发言打标
            indent = "  ↳ " if c.get("reply") else ""
            tag = "[作者]" if c.get("isAuthor") else ""
            like = f" [赞{c['like']}]" if c.get("like") else ""
            lines.append(f"{indent}{c.get('author', '')}{tag}{like}: {c.get('text', '')}")
    else:
        lines.append("(未取到：该平台评论需滚动加载或另走接口)")

    errs = result.get("errors") or []
    if errs:
        lines.append("--- 采集告警 ---")
        lines.extend("  " + str(e)[:160] for e in errs[:5])
    lines.append("===============")
    print("\n".join(lines), flush=True)


def flatten1(s) -> str:
    return " ".join(str(s or "").split())


def digest_rows(metas) -> list[dict]:
    """把 meta.json 压成索引行（标题/时长/一个主指标）。"""
    rows = []
    for meta in metas:
        try:
            d = json.loads(Path(meta).read_text(encoding="utf-8"))
        except Exception:
            continue
        title = flatten1(d.get("title"))[:42] or "(无标题)"
        st = d.get("stats") or {}
        metric = "-"
        for k in ("like", "view", "collect"):
            if st.get(k):
                metric = f"{k}{st[k]}"
                break
        dur = d.get("duration") or 0
        rows.append({"dir": Path(meta).parent.name, "platform": d.get("platform", "?"),
                     "dur": f"{dur}s" if dur else "-", "metric": metric, "title": title})
    return rows


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("[index] 没有可索引的 meta.json", flush=True)
        return
    wd = max(len(r["dir"]) for r in rows)
    for r in rows:
        print(f'{r["dir"]:<{wd}}  {r["platform"]:<8} {r["dur"]:>6}  {r["metric"]:<11} {r["title"]}', flush=True)
    print(f"--- 共 {len(rows)} 条 ---", flush=True)


def emit_digest(root: Path) -> int:
    """索引模式：扫落盘目录生成紧凑表，用于判断哪几条值得深读。"""
    metas = sorted(root.glob("*/meta.json"))
    if not metas:
        print(f"[digest] {root} 下没有 meta.json", flush=True)
        return 1
    print_table(digest_rows(metas))
    return 0


def do_search(args) -> int:
    """关键词 → 站内搜索 → 逐条取数 → 索引表。搜到的 url 也写进 search.json。"""
    target = (args.target or "").strip()
    platform, keyword = args.platform, target
    head, _, tail = target.partition(":")
    if tail and head in SEARCH_URL:          # 支持 "bilibili:agent"
        platform, keyword = head, tail.strip()
    if platform not in SEARCH_URL:
        print(f"[!] 不支持的平台 {platform!r}，可选: {', '.join(SEARCH_URL)}", flush=True)
        return 2
    if not keyword:
        print('[!] --search 需要关键词，例如: --search "agent" --platform bilibili', flush=True)
        return 2
    if args.full:
        print("[i] --search 只产出索引；深读请对单条链接再跑 --full", flush=True)

    print(f"[1/3] 搜索 {platform} 关键词={keyword!r} 目标 {args.limit} 条", flush=True)
    res = run_search(platform, keyword, args.limit)
    items = res.get("items") or []
    if not items:
        print("[!] 搜索无结果: " + str(res.get("error") or res.get("raw") or "")[:300], flush=True)
        print(f"SUMMARY: search={platform} kw={keyword} found=0 ok=0 fail=0", flush=True)
        return 1
    print(f"    候选 {len(items)} 条:", flush=True)
    for i, it in enumerate(items, 1):
        who = f' @{it["author"]}' if it.get("author") else ""
        print(f'    {i}. {it.get("title", "")[:46]}{who}  {it.get("extra", "")}', flush=True)

    # 复用本脚本的单条链路（独立进程 = 每条一个干净浏览器会话，单条失败不影响其余）
    dirs, okn = [], 0
    for i, it in enumerate(items, 1):
        print(f"[2/3] ({i}/{len(items)}) {it['id']}", flush=True)
        cmd = [sys.executable, str(Path(__file__).resolve()), it["url"], "--out", args.out, "--quiet"]
        if args.no_download:
            cmd.append("--no-download")
        if args.only != "both":
            cmd += ["--only", args.only]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            print("    [!] 单条超时 300s，跳过", flush=True)
            continue
        line = next((l for l in (p.stdout or "").splitlines() if l.startswith("SUMMARY:")), "")
        print("    " + (line or (p.stderr or "").strip()[-200:] or "无输出"), flush=True)
        if "ok=1" in line:
            okn += 1
            m = re.search(r"out=(.+)$", line)
            if m:
                dirs.append(Path(m.group(1).strip()))

    print("[3/3] 索引", flush=True)
    rows = digest_rows([d / "meta.json" for d in dirs])
    print_table(rows)
    slug = re.sub(r"[^0-9A-Za-z]+", "_", keyword).strip("_")[:16] or "kw"
    idx = Path(args.out) / f"search_{platform}_{slug}"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "search.json").write_text(json.dumps(
        {"platform": platform, "keyword": keyword, "items": items, "fetched": [str(d) for d in dirs]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY: search={platform} kw={keyword} found={len(items)} ok={okn} "
          f"fail={len(items) - okn} list={idx / 'search.json'}", flush=True)
    return 0 if okn else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="链接或 ID；--digest 模式可省略")
    ap.add_argument("--out", default=str(Path.home() / "videosum"))
    ap.add_argument("--frames", action="store_true", help="下载后调用 extract_frames.py 抽帧+音轨")
    ap.add_argument("--only", choices=["audio", "video", "both"], default="both")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="批量模式：抑制全部进度行，只保留一行 SUMMARY（失败也会打）")
    ap.add_argument("--full", action="store_true",
                    help="深读模式：正文全文 + 话题 + 评论，纯文本输出")
    ap.add_argument("--digest", action="store_true",
                    help="索引模式：扫 --out 下的 meta.json 输出紧凑表，不启动浏览器")
    ap.add_argument("--search", action="store_true",
                    help="关键词模式：站内搜索取前 N 条并逐条落盘，直接产出索引")
    ap.add_argument("--platform", default="douyin", choices=list(SEARCH_URL),
                    help="--search 用哪个平台（默认 douyin）")
    ap.add_argument("--limit", type=int, default=5, help="--search 取前 N 条（默认 5）")
    args = ap.parse_args()

    if args.digest:
        return emit_digest(Path(args.out))
    if args.search:
        return do_search(args)
    if not args.target:
        ap.error("需要 target，或改用 --digest / --search")

    q = args.quiet
    platform, vid = detect(args.target)
    url = build_url(platform, vid)
    wait = 9000 if platform == "douyin" else (7000 if platform == "x" else 4500)
    say(f"[1/4] 平台={platform} 等待={wait}ms 目标={url}", q)

    data = run_ego(url, wait, full=args.full)
    if not data.get("ok"):
        err = data.get("error") or data.get("raw") or "未取到媒体地址"
        say("[!] 浏览器取数失败: " + str(err)[:400], q)
        if q:
            print(f"SUMMARY: ok=0 fail=1 platform={platform} err={str(err)[:120]}", flush=True)
        else:
            print("@@RESULT@@" + json.dumps({"ok": False, "platform": platform, "id": vid,
                                             "error": str(err)[:300]}, ensure_ascii=False))
        return 1

    # X 的 vid 是整条 URL，直接清洗会得到又长又难读的目录名，改用 handle + 状态 ID
    if platform == "x":
        mx = re.search(r"(?:x|twitter)\.com/([^/]+)/status(?:es)?/(\d+)", str(vid))
        if mx:
            stem = f"x_{mx.group(1)}_{mx.group(2)[:14]}"
        else:
            mh = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", str(vid))
            stem = f"x_{mh.group(1)}" if mh else f"x_{re.sub(r'[^0-9A-Za-z]', '', str(vid))[:24]}"
    else:
        stem = f"{platform}_{re.sub(r'[^0-9A-Za-z]', '', str(vid))[:24]}"
    out = Path(args.out) / stem
    out.mkdir(parents=True, exist_ok=True)
    say(f"[2/4] 音频候选 {len(data.get('audios', []))} / 视频候选 {len(data.get('videos', []))} / 图片 {len(data.get('images', []))}", q)

    media = {"audio": None, "video": None}
    if not args.no_download:
        jobs = []
        if args.only in ("audio", "both") and data.get("audios"):
            jobs.append(("audio", data["audios"], ".m4a"))
        if args.only in ("video", "both") and data.get("videos"):
            jobs.append(("video", data["videos"], ".mp4"))
        # 只有视频流时，视频本身就是音轨来源
        if not jobs and data.get("videos"):
            jobs.append(("video", data["videos"], ".mp4"))

        for kind, urls, ext in jobs:
            for i, u in enumerate(urls[:3]):
                dest = out / f"media_{kind}{ext}"
                ok, msg = download(u, dest, platform)
                say(f"    {kind}候选#{i + 1}: {msg}", q)
                if ok:
                    media[kind] = str(dest)
                    break

    result = {
        "ok": True, "platform": platform, "id": vid, "url": url,
        "title": data.get("title"), "author": data.get("author"), "desc": data.get("desc"),
        "when": data.get("when") or "", "tab": data.get("tab") or "",
        "duration": data.get("duration"), "stats": data.get("stats"),
        "tags": data.get("tags", []), "comments": data.get("comments", []),
        "images": data.get("images", [])[:12], "subtitles": data.get("subtitles", []),
        "media_audio": media["audio"], "media_video": media["video"],
        "out_dir": str(out), "errors": data.get("errors", []),
    }
    if data.get("items"):
        result["posts"] = data["items"]
        (out / "posts.json").write_text(
            json.dumps({"author": data.get("author"), "bio": data.get("desc"),
                        "posts": data["items"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 图文笔记：正文常在图里，图片必须落盘才能读
    img_dir = out / "img"
    if not args.no_download and data.get("images"):
        img_dir.mkdir(exist_ok=True)
        saved = []
        for i, u in enumerate(data["images"][:9]):
            dest = img_dir / f"img_{i + 1:02d}.jpg"
            ok, _ = download(u, dest, platform)
            if ok:
                saved.append(str(dest))
        say(f"    图片落盘 {len(saved)}/{min(len(data['images']), 9)}", q)
        result["image_files"] = saved

    if args.frames:
        source = media["video"] or media["audio"]
        if source:
            say("[3/4] 抽帧 + 音轨", q)
            r = subprocess.run([sys.executable, FRAMES_PY, source, "--interval", "4", "--out", str(out / "frames")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            say("    " + (r.stdout or r.stderr or "").strip()[-260:], q)
            result["frames_dir"] = str(out / "frames")
        else:
            say("[3/4] 无媒体文件，跳过抽帧", q)
    else:
        say("[3/4] 跳过抽帧", q)

    (out / "meta.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if q:
        got = sum(1 for k in ("media_audio", "media_video") if result.get(k))
        extra = f" posts={len(result['posts'])}" if result.get("posts") else ""
        print(f"SUMMARY: ok=1 fail=0 platform={platform} dur={result.get('duration') or '-'}s "
              f"media={got} tags={len(result['tags'])} comments={len(result['comments'])}{extra} out={out}", flush=True)
    elif args.full:
        emit_full(result)
    else:
        say("[4/4] 完成", q)
        print("@@RESULT@@" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
