#!/usr/bin/env python3
"""
Musical Scraper - bulk-download a TikTok profile's videos, watermark-free, oldest first,
named after each video's caption.

Filenames are:  <caption> #<username>.mp4
Add your own hashtags with --tag, e.g. --tag MyArchive

Why it works the way it does (learned by probing TikTok's real responses):
  * TikTok serves two video URLs per post. `download_addr` is the watermarked one
    (yt-dlp calls it format_id "download"); every other format is `play_addr`,
    which has no watermark. So "remove the watermark" is really "never ask for
    the watermarked file" - nothing is cropped, blurred or re-encoded.
  * Format IDs embed the bitrate (h264_720p_2250114) and CHANGE between requests,
    so you cannot list formats in one call and fetch by ID in the next. Selection
    has to happen inside a single extraction - hence the Python selector callable.
  * Clean formats have an EMPTY format_note, and yt-dlp's -f filters silently drop
    entries whose field is missing, so `-f "b[format_note!*=watermark]"` matches
    nothing at all. Another reason selection lives in Python here.
  * Listing a profile with full metadata costs ~7.7s per video, but a flat listing
    costs ~0.07s per video and still includes caption + timestamp. We enumerate
    flat, then download.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError:
    sys.exit("yt-dlp is not installed.  Run:  pip install -U yt-dlp")

ARCHIVE_NAME = ".musicalscraper-archive.txt"

# TikTok serves a captcha page to some TLS/HTTP fingerprints and the real page to
# others, and which ones pass drifts over time (chrome-110/124/131 were being
# challenged while 116/120/133 sailed through). So try a list and remember which
# one worked rather than pinning a single browser or curl_cffi version.
IMPERSONATE_TARGETS = (
    ("chrome133a", "chrome-133"),
    ("chrome120", "chrome-120"),
    ("chrome116", "chrome-116"),
    ("safari18_0", "safari-18.0"),
    ("edge101", "edge-101"),
    ("firefox133", "firefox-133"),
)
_WORKING_TARGET = {"pair": None}
REHYDRATION_MARKER = "__UNIVERSAL_DATA_FOR_REHYDRATION__"
LISTING_CACHE = ".musicalscraper-listing.json"

# Pre-1.0 builds wrote their state under different filenames. Renaming them on
# first run means an existing archive keeps working instead of re-downloading.
_LEGACY_STATE = {
    ".valoria-archive.txt": ARCHIVE_NAME,
    ".valoria-listing.json": LISTING_CACHE,
}


def migrate_legacy_state(out_dir):
    for old, new in _LEGACY_STATE.items():
        old_path, new_path = os.path.join(out_dir, old), os.path.join(out_dir, new)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try:
                os.replace(old_path, new_path)
            except OSError:
                pass

# ----------------------------------------------------------------------------
# Filename construction
# ----------------------------------------------------------------------------

# Characters illegal on Windows/macOS, mapped to visually similar safe ones so the
# caption stays readable instead of turning into a row of underscores.
_ILLEGAL = {
    "/": "-", "\\": "-", ":": "-", "|": "-",
    '"': "'", "<": "(", ">": ")", "?": "", "*": "",
}
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def clean_text(text: str) -> str:
    """Make an arbitrary TikTok caption safe to use as a filename component."""
    if not text:
        return ""
    # Newlines/tabs become spaces - deleting them would fuse the words either side.
    text = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in text)
    # Drop zero-width and directionality marks (incl. the U+202E RTL-override trick
    # that can disguise a file's real extension). Keep ZWJ so emoji stay intact.
    text = "".join(
        ch for ch in text
        if ch == "‍" or unicodedata.category(ch) != "Cf"
    )
    text = "".join(_ILLEGAL.get(ch, ch) for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    # Windows rejects names that end in a dot or space.
    text = text.strip(". ")
    if text.upper().split(".")[0] in _WIN_RESERVED:
        text = f"_{text}"
    return text


def truncate_bytes(text: str, max_bytes: int) -> str:
    """Cut to a byte budget without splitting a character.

    Filesystems cap a *component* at 255 bytes, not 255 characters - and an emoji
    costs 4 bytes. TikTok captions run to 2200 chars, so this fires often.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # The ellipsis has to fit INSIDE the budget, not be bolted on after it.
    ell = "…"
    ell_bytes = len(ell.encode("utf-8"))
    if max_bytes <= ell_bytes:
        return ""
    cut = encoded[:max_bytes - ell_bytes]
    while cut:
        try:
            out = cut.decode("utf-8")
            break
        except UnicodeDecodeError:
            cut = cut[:-1]
    else:
        return ""
    # Don't leave a dangling joiner/combining mark at the cut point.
    while out and (out[-1] == "‍" or unicodedata.combining(out[-1])):
        out = out[:-1]
    return out.rstrip() + ell


def build_filename(caption, username, tag_user=None, ext="mp4",
                   max_bytes=200, strip_original_tags=False,
                   number=None, upload_dt=None, video_id=None,
                   extra_tags=(), user_tag=True):
    """caption + trailing #hashtags + extension, within the byte budget."""
    caption = caption or ""
    if strip_original_tags:
        caption = re.sub(r"#[^\s#]+", " ", caption)
    caption = clean_text(caption)

    handle = clean_text(tag_user or username) or "unknown"
    tags = [t for t in (clean_text(str(x)).lstrip("#") for x in extra_tags) if t]
    if user_tag:
        tags.append(handle)

    # Don't say a tag twice if the caption already carries it.
    for tag in tags:
        pattern = r"(?<!\w)#" + re.escape(tag) + r"(?!\w)"
        if re.search(pattern, caption, re.I):
            caption = re.sub(r"\s+", " ",
                             re.sub(pattern, "", caption, flags=re.I)).strip()

    suffix = (" " + " ".join(f"#{t}" for t in tags)) if tags else ""

    prefix = f"{number:04d} - " if number is not None else ""
    if not caption:
        # Captionless posts still need a stable, unique name: date if we know it,
        # otherwise the post id (legacy musical.ly ids carry no decodable date).
        stamp = (upload_dt.strftime("%Y-%m-%d") if upload_dt
                 else (str(video_id) if video_id else "no-caption"))
        caption = f"{handle} {stamp}"

    budget = max_bytes - len((prefix + suffix + "." + ext).encode("utf-8"))
    if budget < 8:
        budget = 8
    caption = truncate_bytes(caption, budget)
    return f"{prefix}{caption}{suffix}.{ext}"


def dedupe(path_dir: str, name: str, taken: set) -> str:
    """Two posts can share a caption; keep both."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate.lower() in taken or os.path.exists(os.path.join(path_dir, candidate)):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    taken.add(candidate.lower())
    return candidate


# ----------------------------------------------------------------------------
# yt-dlp plumbing
# ----------------------------------------------------------------------------

class QuietLogger:
    def __init__(self):
        self.errors = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.errors.append(msg)


def make_format_selector(prefer_h264=True, max_height=None):
    """Pick the best watermark-free progressive format.

    Defaults to H.264 because H.265/bytevc1 (which is what yt-dlp would otherwise
    choose, since it's the 1080p rung) refuses to play in a lot of browsers and
    chokes plenty of upload pipelines. Use --codec best to override.
    """
    def selector(ctx):
        candidates = []
        for f in ctx.get("formats") or []:
            if f.get("format_id") == "download":
                continue                                    # the watermarked one
            if "watermark" in (f.get("format_note") or "").lower():
                continue
            if f.get("vcodec") in (None, "none"):
                continue                                    # audio-only / images
            if max_height and (f.get("height") or 0) > max_height:
                continue
            candidates.append(f)
        if not candidates:
            return

        def rank(f):
            vcodec = (f.get("vcodec") or "").lower()
            is_h264 = vcodec.startswith("h264") or vcodec.startswith("avc")
            return (
                (1 if is_h264 else 0) if prefer_h264 else 0,
                f.get("height") or 0,
                f.get("tbr") or 0,
                f.get("filesize") or f.get("filesize_approx") or 0,
            )
        yield max(candidates, key=rank)

    return selector


def handle_from_target(target: str) -> str:
    """Get the bare @handle from a username, @username or profile URL."""
    m = re.search(r"@([\w.\-]+)", target or "")
    return m.group(1) if m else (target or "").strip("/").split("/")[-1].lstrip("@")


def load_listing_cache(path, max_age_hours):
    """Listing a big profile takes minutes; don't re-pay that on every run."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if (time.time() - data.get("fetched_at", 0)) > max_age_hours * 3600:
        return None
    return data if data.get("videos") else None


def save_listing_cache(path, username, videos, sec_uid=""):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "username": username,
                       "sec_uid": sec_uid, "videos": videos}, fh)
    except OSError:
        pass


def ts_from_id(video_id: str):
    """TikTok packs the creation time into the high 32 bits of the post ID.

    Only true for modern snowflake IDs. Legacy musical.ly IDs (~4.3e16, vs ~7.6e18
    today) use a different layout and decode to 1970, so the result is sanity-checked
    and discarded if it isn't a plausible date.
    """
    try:
        ts = int(video_id) >> 32
    except (TypeError, ValueError):
        return None
    # TikTok/musical.ly content only exists from ~2014 onward.
    if 1388534400 < ts < time.time() + 86400:
        return ts
    return None


class TikTokAPI:
    """Thin wrapper over yt-dlp's TikTok request machinery.

    Reuses yt-dlp's signed-query builder, device id, impersonation and cookie jar -
    calling the item_list endpoint by hand gets you statusCode 10201.
    """

    def __init__(self, cookiefile=None, cookies_from_browser=None):
        self._cookiefile = cookiefile
        self._cookies_from_browser = cookies_from_browser
        self._build(_WORKING_TARGET["pair"] or IMPERSONATE_TARGETS[0])

    def _build(self, pair):
        """(Re)create the yt-dlp session pinned to one impersonation fingerprint."""
        from yt_dlp.extractor.tiktok import TikTokUserIE
        opts = {"quiet": True, "no_warnings": True}
        if self._cookies_from_browser:
            opts["cookiesfrombrowser"] = (self._cookies_from_browser,)
        if self._cookiefile:
            opts["cookiefile"] = self._cookiefile
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str(pair[1])
        except Exception:
            pass                       # older yt-dlp: fall back to its own default
        self.ydl = YoutubeDL(opts)
        self.ie = TikTokUserIE()
        self.ie.set_downloader(self.ydl)
        self.ie.initialize()
        self.target = pair

    def _fetch_html(self, url, marker):
        """Fetch a page, trying each fingerprint until one isn't challenged."""
        from curl_cffi import requests as creq
        order = [self.target] + [p for p in IMPERSONATE_TARGETS if p != self.target]
        problem = "no response"
        for pair in order:
            try:
                resp = creq.get(url, impersonate=pair[0], timeout=45)
            except Exception as exc:
                problem = str(exc)[:80]
                continue
            if marker in resp.text:
                if pair != self.target:
                    self._build(pair)   # keep the API calls on the same fingerprint
                _WORKING_TARGET["pair"] = pair
                return resp.text
            problem = f"HTTP {resp.status_code}, {len(resp.text)} bytes (challenge page)"
        raise RuntimeError(
            "TikTok challenged every browser profile we tried "
            f"({problem}). Wait a few minutes, or use the browser-cookies option.")

    def profile_meta(self, handle):
        """secUid, account creation time and the profile's own video count."""
        page = self._fetch_html(f"https://www.tiktok.com/@{handle}", REHYDRATION_MARKER)
        m = re.search(REHYDRATION_MARKER + r'[^>]*>(.*?)</script>', page, re.S)
        if not m:
            raise RuntimeError("could not parse profile page")
        info = (json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
                .get("webapp.user-detail", {}).get("userInfo", {}))
        user = info.get("user") or {}
        if not user.get("secUid"):
            raise RuntimeError(f"profile @{handle} not found or not public")
        return {
            "sec_uid": user["secUid"],
            "handle": user.get("uniqueId") or handle,
            "created": int(user.get("createTime") or 0),
            "video_count": int((info.get("stats") or {}).get("videoCount") or 0),
        }

    def page(self, sec_uid, cursor, count=15):
        query = self.ie._build_web_query(sec_uid, int(cursor))
        query["count"] = str(count)
        return self.ie._download_json(
            self.ie._API_BASE_URL, "listing", note=False, query=query) or {}


def _record(item):
    author = (item.get("author") or {}).get("uniqueId") or ""
    return {
        "id": str(item.get("id") or ""),
        "url": f"https://www.tiktok.com/@{author}/video/{item.get('id')}",
        "caption": item.get("desc") or "",
        "timestamp": int(item.get("createTime") or 0) or None,
        "duration": (item.get("video") or {}).get("duration"),
        "uploader": author,
        # play_addr is the watermark-free URL; kept as a rescue path for old posts
        # whose video page no longer parses. Signed and short-lived, so it gets
        # re-fetched rather than trusted from cache.
        "play_addr": (item.get("video") or {}).get("playAddr") or "",
    }


DAY_MS = 86_400_000


def deep_enumerate(handle, api, log=None, status=None, should_stop=None,
                   max_step_days=120):
    """Sweep the whole profile back to the account's creation date.

    yt-dlp walks back from 'now' and stops when the API's `hasMorePrevious` flag
    goes false - which happens long before the account actually ends (for a
    3,364-video profile it quit at 1,523, hiding everything before Feb 2018). It
    also hard-refuses to look back past 2016-09-01.

    The cursor is really just "give me the posts immediately before this instant",
    and it honours any timestamp you hand it. So instead of trusting the flag, we
    sweep the cursor backwards across the account's entire lifetime, widening the
    step over quiet stretches and resetting it whenever posts turn up.
    """
    log = log or (lambda m: None)
    status = status or (lambda m: None)
    should_stop = should_stop or (lambda: False)

    meta = api.profile_meta(handle)
    sec_uid, created = meta["sec_uid"], meta["created"]
    expected = meta["video_count"]
    floor_ms = (created - 7 * 86400) * 1000 if created else 1_400_000_000_000

    seen = {}
    cursor = int(time.time() * 1000)
    step = 7 * DAY_MS
    max_step = max_step_days * DAY_MS
    pages = 0
    last_report = 0.0

    if created:
        log(f"  profile reports {expected} videos; account created "
            f"{datetime.fromtimestamp(created, timezone.utc):%Y-%m-%d}")
    else:
        log(f"  profile reports {expected} videos")

    while cursor > floor_ms:
        if should_stop():
            break
        try:
            resp = api.page(sec_uid, cursor)
        except Exception:
            cursor -= step                      # transient failure: keep sweeping
            step = min(step * 2, max_step)
            continue
        pages += 1
        items = [i for i in (resp.get("itemList") or []) if i.get("id")]
        oldest = None
        fresh = 0
        for it in items:
            ct = int(it.get("createTime") or 0)
            if ct:
                oldest = ct if oldest is None else min(oldest, ct)
            vid = str(it["id"])
            if vid not in seen:
                seen[vid] = _record(it)
                fresh += 1

        if oldest:
            nxt = oldest * 1000 - 1000          # just before this page's oldest post
            cursor = nxt if nxt < cursor else cursor - step
            step = 7 * DAY_MS if fresh else min(step * 2, max_step)
        else:
            cursor -= step                      # quiet window: widen and keep going
            step = min(step * 2, max_step)

        if time.time() - last_report > 1:
            last_report = time.time()
            at = datetime.fromtimestamp(max(cursor, 0) / 1000, timezone.utc)
            status(f"{len(seen)} videos found - scanned back to {at:%b %Y}")

        if expected and len(seen) >= expected:
            break

    log(f"  {len(seen)} videos found in {pages} requests")
    return meta, sorted(seen.values(),
                        key=lambda v: (v.get("timestamp") or 0, v["id"]))


def enumerate_profile(target, cookies_from_browser=None, cookiefile=None,
                      limit_pages=None, verbose=False):
    """Flat-list every post on a profile. Fast: metadata only, no per-video hit."""
    if target.startswith("@"):
        url = f"https://www.tiktok.com/{target}"
    elif target.startswith("http"):
        url = target
    else:
        url = f"https://www.tiktok.com/@{target}"

    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "logger": QuietLogger(),
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if limit_pages:
        opts["playlistend"] = limit_pages

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise SystemExit(f"Could not read profile: {url}")

    videos = []
    for e in info.get("entries") or []:
        if not e:
            continue
        vid = str(e.get("id") or "")
        ts = e.get("timestamp") or ts_from_id(vid)
        videos.append({
            "id": vid,
            "url": e.get("url") or f"https://www.tiktok.com/@{info.get('uploader','')}/video/{vid}",
            "caption": e.get("description") or e.get("title") or "",
            "timestamp": ts,
            "duration": e.get("duration"),
            "uploader": e.get("uploader") or info.get("uploader") or "",
        })
    return info, videos


def refresh_play_addr(api, sec_uid, video_id, timestamp):
    """Re-query the API around a post's date to get an unexpired playAddr."""
    if not (api and sec_uid and timestamp):
        return ""
    try:
        resp = api.page(sec_uid, int(timestamp) * 1000 + 60_000)
    except Exception:
        return ""
    for item in resp.get("itemList") or []:
        if str(item.get("id")) == str(video_id):
            return (item.get("video") or {}).get("playAddr") or ""
    return ""


def direct_download(url, dest, referer="https://www.tiktok.com/"):
    """Fetch a playAddr URL straight from the CDN.

    Used only when yt-dlp can't parse the video page - which happens for roughly
    1 in 20 of the oldest musical.ly-era posts.
    """
    from curl_cffi import requests as creq
    pair = _WORKING_TARGET["pair"] or IMPERSONATE_TARGETS[0]
    with creq.Session(impersonate=pair[0]) as sess:
        resp = sess.get(url, headers={"Referer": referer}, timeout=90, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(f"CDN returned HTTP {resp.status_code}")
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(65536):
                fh.write(chunk)
    if os.path.getsize(dest) < 2048:
        os.unlink(dest)
        raise RuntimeError("CDN returned an empty file")
    return dest


def download_one(video, out_dir, name_resolver, opts_base, sleep=0.0):
    """Download a single post to a temp name, then rename to the caption name.

    `name_resolver(video, info)` is called AFTER extraction, so posts whose caption
    wasn't in the listing (e.g. --urls-file input, which is also the retry path)
    still get named from their real caption rather than a placeholder.

    Downloading to '<id>.%(ext)s' first keeps yt-dlp's output-template parser away
    from the caption entirely - a caption containing '%(' or a path separator
    would otherwise corrupt the path.
    """
    tmp_tmpl = os.path.join(out_dir, ".part", f"{video['id']}.%(ext)s")
    logger = QuietLogger()
    opts = dict(opts_base)
    opts["outtmpl"] = tmp_tmpl
    opts["logger"] = logger

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video["url"], download=True)

    if not info:
        raise RuntimeError(logger.errors[-1] if logger.errors else "extraction returned nothing")

    downloads = info.get("requested_downloads") or []
    if not downloads:
        raise RuntimeError("no downloadable video (photo/slideshow post?)")
    src = downloads[0].get("filepath")
    if not src or not os.path.exists(src):
        raise RuntimeError("download produced no file")

    filename = name_resolver(video, info)
    ext = os.path.splitext(src)[1].lstrip(".") or "mp4"
    if not filename.lower().endswith("." + ext.lower()):
        filename = os.path.splitext(filename)[0] + "." + ext

    dest = os.path.join(out_dir, filename)
    os.replace(src, dest)

    # Stamp the original upload time so Finder's "Date Modified" sort matches the
    # order you need to re-upload in.
    ts = video.get("timestamp")
    if ts:
        try:
            os.utime(dest, (ts, ts))
        except OSError:
            pass

    if sleep:
        time.sleep(sleep)
    return dest, downloads[0], info


# ----------------------------------------------------------------------------
# Job engine - shared by the CLI and the GUI
# ----------------------------------------------------------------------------

DEFAULTS = {
    "out": None, "tag_user": None, "limit": None, "skip": 0, "since": None,
    "until": None, "newest_first": False, "number": False, "codec": "h264",
    "max_height": None, "max_bytes": 200, "strip_original_hashtags": False,
    "jobs": 3, "sleep": 0.5, "dry_run": False, "shallow": False,
    "refresh": False, "cache_hours": 24.0, "no_archive": False,
    "cookies_from_browser": None, "cookies": None, "urls_file": None,
    "verbose": False, "tag": (), "user_tag": True,
}


class JobError(Exception):
    """Something the user needs to fix (bad handle, private profile, ...)."""


class JobConfig:
    """One run's settings. The CLI and the GUI each build one of these."""

    def __init__(self, target, **kw):
        self.target = target
        for key, default in DEFAULTS.items():
            setattr(self, key, kw.pop(key, default))
        if kw:
            raise TypeError(f"unknown option(s): {', '.join(sorted(kw))}")

    @classmethod
    def from_args(cls, args):
        return cls(args.target,
                   **{k: getattr(args, k) for k in DEFAULTS if hasattr(args, k)})


def run_job(cfg, log=None, status=None, progress=None, should_stop=None):
    """Enumerate, name, and download. Returns a summary dict.

    All output goes through callbacks so the GUI and the terminal can render it
    differently without the logic being written twice.
    """
    log = log or (lambda m: None)
    status = status or (lambda m: None)
    progress = progress or (lambda done, total: None)
    should_stop = should_stop or (lambda: False)

    handle = handle_from_target(cfg.target)
    out_dir = cfg.out or os.path.join("downloads", handle or "tiktok")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, ".part"), exist_ok=True)
    migrate_legacy_state(out_dir)
    cache_path = os.path.join(out_dir, LISTING_CACHE)

    # ---- enumerate -----------------------------------------------------------
    sec_uid = ""
    if cfg.urls_file:
        with open(cfg.urls_file) as fh:
            urls = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        username = handle
        videos = []
        for url in urls:
            m = re.search(r"/video/(\d+)", url)
            vid = m.group(1) if m else url.rsplit("/", 1)[-1]
            videos.append({"id": vid, "url": url, "caption": "",
                           "timestamp": ts_from_id(vid), "duration": None,
                           "uploader": username})
        log(f"Loaded {len(videos)} URLs from {cfg.urls_file}")
    else:
        cached = None if cfg.refresh else load_listing_cache(cache_path, cfg.cache_hours)
        if cached:
            videos = cached["videos"]
            username = cached.get("username") or handle
            sec_uid = cached.get("sec_uid", "")
            age_h = (time.time() - cached["fetched_at"]) / 3600
            log(f"Using the saved video list: {len(videos)} videos "
                f"({age_h:.1f}h old).")
        else:
            t0 = time.time()
            if cfg.shallow:
                log(f"Listing posts for @{handle} (shallow) ...")
                info, videos = enumerate_profile(
                    cfg.target, cookies_from_browser=cfg.cookies_from_browser,
                    cookiefile=cfg.cookies, verbose=cfg.verbose)
                username = ((videos[0]["uploader"] if videos else "")
                            or info.get("uploader") or handle)
            else:
                log(f"Scanning @{handle}'s full history (this takes a minute or two)...")
                try:
                    api = TikTokAPI(cookiefile=cfg.cookies,
                                    cookies_from_browser=cfg.cookies_from_browser)
                    meta, videos = deep_enumerate(handle, api, log=log, status=status,
                                                  should_stop=should_stop)
                except RuntimeError as exc:
                    raise JobError(str(exc))
                username, sec_uid = meta["handle"], meta["sec_uid"]
                shortfall = meta["video_count"] - len(videos)
                if meta["video_count"] and shortfall > max(5, meta["video_count"] * 0.05):
                    log(f"  note: the profile lists {meta['video_count']} videos but "
                        f"{len(videos)} could be read - {shortfall} are likely private, "
                        f"deleted, or region-locked")
            if should_stop():
                return {"cancelled": True, "downloaded": 0, "failed": 0,
                        "planned": [], "out_dir": out_dir}
            log(f"Found {len(videos)} videos in {time.time() - t0:.0f}s")
            save_listing_cache(cache_path, username, videos, sec_uid)

    if not videos:
        raise JobError(
            "No videos found. If the profile is private or region-locked, try the "
            "'Use browser cookies' option after logging into TikTok in that browser.")

    # ---- order: oldest first -------------------------------------------------
    videos.sort(key=lambda v: (v.get("timestamp") or ts_from_id(v["id"]) or 0,
                               int(v["id"] or 0)))
    if cfg.newest_first:
        videos.reverse()
    if cfg.since:
        videos = [v for v in videos if (v.get("timestamp") or 0) >= cfg.since.timestamp()]
    if cfg.until:
        videos = [v for v in videos if (v.get("timestamp") or 0) < cfg.until.timestamp()]

    # ---- archive (so repeat runs only fetch what's new) ----------------------
    archive_path = os.path.join(out_dir, ARCHIVE_NAME)
    done = set()
    if not cfg.no_archive and os.path.exists(archive_path):
        with open(archive_path) as fh:
            done = {ln.strip() for ln in fh if ln.strip()}
    pending = [v for v in videos if v["id"] not in done]
    if done:
        log(f"Already downloaded: {len(done)}. Remaining: {len(pending)}.")

    # --skip/--limit apply to what's LEFT, not the whole profile - so re-running
    # walks forward through the backlog instead of re-picking the same videos.
    if cfg.skip:
        pending = pending[cfg.skip:]
    if cfg.limit:
        pending = pending[:cfg.limit]

    # ---- plan filenames ------------------------------------------------------
    taken = set()
    for i, v in enumerate(pending, start=1):
        dt = (datetime.fromtimestamp(v["timestamp"], timezone.utc)
              if v.get("timestamp") else None)
        v["upload_date"] = dt.strftime("%Y-%m-%d") if dt else ""
        name = build_filename(
            v["caption"], username, tag_user=cfg.tag_user, max_bytes=cfg.max_bytes,
            strip_original_tags=cfg.strip_original_hashtags,
            number=i if cfg.number else None, upload_dt=dt, video_id=v["id"],
            extra_tags=cfg.tag, user_tag=cfg.user_tag)
        v["_num"] = i
        v["filename"] = dedupe(out_dir, name, taken)

    summary = {"planned": pending, "out_dir": os.path.abspath(out_dir),
               "downloaded": 0, "failed": 0, "cancelled": False,
               "manifest": os.path.join(out_dir, "manifest.csv")}

    if cfg.dry_run:
        log(f"\nPreview - {len(pending)} video(s), oldest first:\n")
        for i, v in enumerate(pending, 1):
            log(f"{i:>4}. [{v['upload_date'] or '????-??-??'}]  {v['filename']}")
        log(f"\nThey would be saved to: {summary['out_dir']}")
        return summary

    if not pending:
        log("Nothing new to download - this profile is fully backed up.")
        return summary

    # ---- download ------------------------------------------------------------
    opts_base = {
        "format": make_format_selector(prefer_h264=(cfg.codec == "h264"),
                                       max_height=cfg.max_height),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "retries": 5, "fragment_retries": 5, "socket_timeout": 30,
        "concurrent_fragment_downloads": 1,
    }
    if cfg.cookies_from_browser:
        opts_base["cookiesfrombrowser"] = (cfg.cookies_from_browser,)
    if cfg.cookies:
        opts_base["cookiefile"] = cfg.cookies

    lock = threading.Lock()
    failures = []
    counter = {"n": 0}
    total = len(pending)

    def resolve_name(v, info):
        """Late-bind the filename when the listing had no caption for this post."""
        if v.get("caption"):
            return v["filename"]
        caption = info.get("description") or info.get("title") or ""
        # yt-dlp invents "TikTok video #123" for captionless posts; that's a
        # placeholder, not a caption - fall back to the dated name instead.
        if re.match(r"^TikTok video #\d+$", caption.strip()):
            caption = ""
        v["caption"] = caption
        if not v.get("timestamp") and info.get("timestamp"):
            v["timestamp"] = int(info["timestamp"])
            v["upload_date"] = datetime.fromtimestamp(
                v["timestamp"], timezone.utc).strftime("%Y-%m-%d")
        dt = (datetime.fromtimestamp(v["timestamp"], timezone.utc)
              if v.get("timestamp") else None)
        name = build_filename(
            v["caption"], info.get("uploader") or username, tag_user=cfg.tag_user,
            max_bytes=cfg.max_bytes, strip_original_tags=cfg.strip_original_hashtags,
            number=v.get("_num") if cfg.number else None, upload_dt=dt,
            video_id=v["id"], extra_tags=cfg.tag, user_tag=cfg.user_tag)
        if name == v.get("filename"):
            return v["filename"]         # unchanged; don't collide with our own reservation
        with lock:                       # `taken` is shared across threads
            taken.discard((v.get("filename") or "").lower())
            v["filename"] = dedupe(out_dir, name, taken)
        return v["filename"]

    api_holder = {"api": None}

    def get_api():
        with lock:
            if api_holder["api"] is None:
                api_holder["api"] = TikTokAPI(
                    cookiefile=cfg.cookies,
                    cookies_from_browser=cfg.cookies_from_browser)
        return api_holder["api"]

    def rescue(v):
        """Old posts sometimes won't parse; grab a fresh playAddr and fetch it."""
        nonlocal sec_uid
        if not sec_uid:
            # --urls-file input has no secUid; look it up once, on demand.
            try:
                sec_uid = get_api().profile_meta(
                    v.get("uploader") or handle)["sec_uid"]
            except Exception:
                pass
        url = (refresh_play_addr(get_api(), sec_uid, v["id"], v.get("timestamp"))
               or v.get("play_addr"))
        if not url:
            raise RuntimeError("no playAddr available")
        tmp = os.path.join(out_dir, ".part", f"{v['id']}.mp4")
        direct_download(url, tmp)
        dest = os.path.join(out_dir, os.path.splitext(v["filename"])[0] + ".mp4")
        os.replace(tmp, dest)
        ts = v.get("timestamp")
        if ts:
            try:
                os.utime(dest, (ts, ts))
            except OSError:
                pass
        return dest

    def work(item):
        idx, v = item
        if should_stop():
            v["status"] = "skipped"
            return v
        try:
            dest, fmt, info = download_one(v, out_dir, resolve_name, opts_base,
                                           sleep=cfg.sleep)
            v["format_id"] = fmt.get("format_id")
            w, h = fmt.get("width"), fmt.get("height")
            v["resolution"] = f"{w}x{h}" if w and h else ""
        except (DownloadError, ExtractorError, RuntimeError, OSError) as exc:
            try:
                dest = rescue(v)
                v["format_id"], v["resolution"] = "playAddr", ""
            except Exception as exc2:
                v["status"] = "failed"
                v["error"] = f"{exc} | rescue: {exc2}"[:300]
                with lock:
                    counter["n"] += 1
                    failures.append(v)
                    log(f"[{counter['n']:>4}/{total}] FAILED {v['id']}: "
                        f"{v['error'][:104]}")
                    progress(counter["n"], total)
                return v
        v["status"] = "ok"
        v["path"] = dest
        with lock:
            counter["n"] += 1
            tag = " (rescued)" if v.get("format_id") == "playAddr" else ""
            log(f"[{counter['n']:>4}/{total}] {os.path.basename(dest)[:80]}{tag}")
            progress(counter["n"], total)
            with open(archive_path, "a") as fh:
                fh.write(v["id"] + "\n")
        return v

    log(f"\nDownloading {total} video(s) to {summary['out_dir']}\n")
    started = time.time()
    progress(0, total)
    with ThreadPoolExecutor(max_workers=max(1, cfg.jobs)) as pool:
        results = list(pool.map(work, enumerate(pending, 1)))

    # ---- manifest ------------------------------------------------------------
    ok = [r for r in results if r.get("status") == "ok"]
    manifest = summary["manifest"]
    write_header = not os.path.exists(manifest)
    with open(manifest, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["upload_order", "upload_date_utc", "video_id", "filename",
                             "full_caption", "url", "duration_s", "resolution", "status"])
        for i, r in enumerate(results, 1):
            writer.writerow([i, r.get("upload_date", ""), r["id"], r.get("filename", ""),
                             (r.get("caption") or "").replace("\n", " "), r["url"],
                             r.get("duration", ""), r.get("resolution", ""),
                             r.get("status", "")])

    part_dir = os.path.join(out_dir, ".part")
    if os.path.isdir(part_dir) and not os.listdir(part_dir):
        os.rmdir(part_dir)

    summary["downloaded"] = len(ok)
    summary["failed"] = len(failures)
    summary["cancelled"] = should_stop()
    log(f"\nDone: {len(ok)} downloaded, {len(failures)} failed, "
        f"in {time.time() - started:.0f}s")
    if failures:
        retry = os.path.join(out_dir, "failed.txt")
        with open(retry, "w") as fh:
            for v in failures:
                fh.write(v["url"] + "\n")
        summary["failed_file"] = retry
        log(f"Failed downloads listed in: {retry}")
    return summary


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"bad date: {s} (use YYYY-MM-DD)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="musical-scraper",
        description="Musical Scraper - download a TikTok profile's videos "
                    "watermark-free, oldest first, named after their captions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  musical-scraper @lorengray --dry-run
  musical-scraper @lorengray --limit 25
  musical-scraper @lorengray --until 2018-08-01 --number
  musical-scraper @lorengray --tag MyArchive --jobs 4
""")
    p.add_argument("target", help="@username, username, or full profile URL")
    p.add_argument("-o", "--out", help="output directory (default: ./downloads/<username>)")
    p.add_argument("--tag", action="append", default=[],
                   help="extra hashtag to append to every filename; repeatable "
                        "(e.g. --tag MyArchive)")
    p.add_argument("--tag-user", help="use this name for the username hashtag instead "
                                      "of the TikTok handle")
    p.add_argument("--no-user-tag", dest="user_tag", action="store_false", default=True,
                   help="don't append the #username hashtag")
    p.add_argument("--limit", type=int, help="download at most N videos")
    p.add_argument("--skip", type=int, default=0, help="skip the first N (oldest) videos")
    p.add_argument("--since", type=parse_date, help="only posts on/after YYYY-MM-DD")
    p.add_argument("--until", type=parse_date, help="only posts before YYYY-MM-DD")
    p.add_argument("--newest-first", action="store_true",
                   help="reverse the default oldest-first order")
    p.add_argument("--number", action="store_true",
                   help="prefix filenames with 0001, 0002, ... in upload order")
    p.add_argument("--codec", choices=("h264", "best"), default="h264",
                   help="h264 = maximum compatibility (default); best = highest "
                        "resolution, may be H.265")
    p.add_argument("--max-height", type=int, help="cap resolution, e.g. 1080")
    p.add_argument("--max-bytes", type=int, default=200,
                   help="filename byte budget (default 200; filesystem limit is 255)")
    p.add_argument("--strip-original-hashtags", action="store_true",
                   help="drop the caption's own #hashtags from the filename")
    p.add_argument("--jobs", type=int, default=3, help="parallel downloads (default 3)")
    p.add_argument("--sleep", type=float, default=0.5,
                   help="seconds to pause after each download")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be downloaded, and the exact filenames")
    p.add_argument("--shallow", action="store_true",
                   help="use yt-dlp's built-in listing; faster, but it stops early "
                        "and misses older videos")
    p.add_argument("--refresh", action="store_true",
                   help="re-list the profile instead of using the cached listing")
    p.add_argument("--cache-hours", type=float, default=24.0,
                   help="how long a cached profile listing stays usable (default 24)")
    p.add_argument("--no-archive", action="store_true",
                   help="re-download videos already recorded as done")
    p.add_argument("--cookies-from-browser",
                   help="chrome | firefox | safari | edge - for private/blocked profiles")
    p.add_argument("--cookies", help="path to a cookies.txt file")
    p.add_argument("--urls-file",
                   help="read video URLs from a file instead of listing a profile")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    tty = sys.stdout.isatty()

    def status(msg):                      # transient one-line progress
        if tty:                           # piped output would fill with padding
            sys.stdout.write("\r  " + msg.ljust(70))
            sys.stdout.flush()

    def log(msg):
        if tty:
            sys.stdout.write("\r" + " " * 74 + "\r")
        print(msg, flush=True)

    try:
        summary = run_job(JobConfig.from_args(args), log=log, status=status)
    except JobError as exc:
        sys.exit(str(exc))
    if summary.get("manifest") and not args.dry_run and summary["downloaded"]:
        print(f"Manifest (full captions for the upload form): {summary['manifest']}")
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\nInterrupted. Re-run the same command to resume - "
                 "already-downloaded videos are skipped.")
