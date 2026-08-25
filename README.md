<div align="center">

<img src="assets/icon.png" width="120" alt="Musical Scraper">

# Musical Scraper

**Download a whole TikTok profile — no watermark, oldest first, named by caption.**

</div>

Point it at a username and it downloads that account's videos as clean MP4s, named after
each video's own caption:

```
#myx #lipsync #musicallyfan #lorengray.mp4
```

It reaches the *entire* back catalogue, including 2015-era musical.ly posts that yt-dlp
alone cannot see (see [below](#it-reaches-the-whole-account)).

## Install

Grab the build for your system from the [Releases page](../../releases), unzip, and run.

| | file | notes |
|---|---|---|
| **macOS** | `MusicalScraper-macOS.zip` | Drag `Musical Scraper.app` to Applications |
| **Windows** | `MusicalScraper-Windows.zip` | Single `MusicalScraper.exe`, no install needed |

### First launch: both systems will warn you

The apps aren't signed with a paid Apple/Microsoft developer certificate, so both systems show
a scary warning the first time. This is normal for independent software.

**macOS** — *"Apple could not verify ... is free of malware"*:

1. Right-click (or Control-click) the app → **Open** → **Open**.
2. If that's refused: **System Settings → Privacy & Security**, scroll down, **Open Anyway**.
3. Or in Terminal: `xattr -dr com.apple.quarantine "/Applications/Musical Scraper.app"`

**Windows** — *"Windows protected your PC"*: click **More info** → **Run anyway**.

## Using it

1. Type a TikTok username (`lorengray` — the `@` is optional).
2. Pick where to save. Defaults to `Downloads/Musical Scraper/<username>`.
3. Click **Preview** to see exactly what filenames you'd get, downloading nothing.
4. Click **Download**.

The first scan of a large profile takes a minute or two — it reads the account's entire
history. That list is cached for 24h, so later runs start immediately.

Click **Download** again later and it picks up where it left off: already-downloaded videos are
skipped, so *"Next 25 oldest"* walks forward through the backlog each time.

Handy details:

- **Files are stamped with their original upload date**, so sorting by *Date Modified* gives
  true chronological order.
- **`manifest.csv`** in the output folder holds the *full, untruncated* caption for every
  video — useful when the filename had to be shortened.
- **Extra hashtag** appends your own tag to every filename, e.g. `MyArchive` gives
  `caption #MyArchive #username.mp4`.
- **Number the files** adds `0001 - `, `0002 - ` prefixes in upload order.

## It reaches the whole account

Point stock yt-dlp at `@lorengray` and it reports **1,523 videos, oldest 2018-02-13**. That
profile actually has **3,364 videos going back to 2015-06-27** — two days after the account was
created. yt-dlp silently returns 45% of the account and stops dead at a 2018 wall.

Two separate causes, both in yt-dlp's user extractor:

1. It walks backwards from *now* and stops the moment the API's `hasMorePrevious` flag goes
   false. That flag goes false at Feb 2018 on this account even though far more exists.
2. It hard-refuses to look back past **2016-09-01** (`if cursor < 1472706000000: return`), so
   2015 and most of 2016 are unreachable by design.

The API's cursor is really just *"give me the posts immediately before this instant"*, and it
honours any timestamp you hand it. So Musical Scraper ignores the flag and sweeps the cursor
backwards across the account's whole lifetime, widening the step over quiet stretches and
resetting it whenever posts turn up. On the same profile:

| | videos found | oldest reached | time |
|---|---|---|---|
| yt-dlp built-in (`--shallow`) | 1,523 | 2018-02-13 | 474s |
| Musical Scraper | **3,320** / 3,364 | **2015-06-27** | **90s** |

More complete *and* faster. The shortfall is posts that are private, deleted or region-locked;
the app reports it rather than hiding it.

## How the watermark removal works

It doesn't remove anything — it never asks for the watermarked file in the first place.

TikTok serves **two** video URLs per post:

- `downloadAddr` — the watermarked copy (what the in-app *Save video* button gives you)
- `playAddr` — the clean master, no watermark

Musical Scraper only ever requests `playAddr`. Nothing is cropped, blurred or re-encoded, so
there's no quality loss and no smudge where a logo used to be. Verified frame-by-frame on both
a 2025 post and a 2015 musical.ly-era post.

## Command line

The same program runs headless. On macOS the shipped binary doubles as a CLI:

```bash
"/Applications/Musical Scraper.app/Contents/MacOS/Musical Scraper" @lorengray --limit 25
```

From source (any platform):

```bash
./setup.sh && ./musical-scraper @lorengray --dry-run
```

| Flag | What it does |
|---|---|
| `--dry-run` | Show filenames, download nothing |
| `--limit N` / `--skip N` | Take N videos / skip the first N oldest (applied to what's left, so repeat runs walk forward) |
| `--since` / `--until` | Date filter, `YYYY-MM-DD`. `--until 2018-08-01` = musical.ly era only |
| `--tag NAME` | Extra hashtag appended to every filename; repeatable |
| `--tag-user NAME` | Use this name for the `#username` tag instead of the TikTok handle |
| `--no-user-tag` | Don't append the `#username` tag at all |
| `--number` | Prefix `0001 - `, `0002 - ` in upload order |
| `--newest-first` | Reverse the order |
| `--codec best` | Highest resolution (often 1080p **H.265**) instead of the default H.264 |
| `--jobs N` | Parallel downloads (default 3) |
| `--strip-original-hashtags` | Drop the caption's own `#tags` from the filename |
| `--shallow` | Use yt-dlp's built-in listing — **misses older videos**, see above |
| `--refresh` | Re-scan instead of using the cached list |
| `--cookies-from-browser chrome` | For private / region-locked / rate-limited profiles |
| `--urls-file f.txt` | Download specific video URLs instead of a whole profile |

## Building it yourself

PyInstaller **cannot cross-compile** — the Windows `.exe` must be built on Windows and the
macOS `.app` on macOS. That's what `.github/workflows/release.yml` is for: push a `v*` tag and
GitHub Actions builds both on real runners and attaches them to a release.

Locally, for your own platform:

```bash
./build.sh
```

## Notes from building this

TikTok quirks the code works around, each found by probing real responses:

1. **Format IDs are not stable.** They embed the bitrate (`h264_720p_2250114`) and change
   between requests, so "list formats, then fetch by ID" fails with *Requested format is not
   available*. Selection has to happen inside a single extraction.
2. **Clean formats have an empty `format_note`.** yt-dlp's `-f` filters silently drop entries
   whose field is missing, so the obvious `-f "b[format_note!*=watermark]"` matches **nothing**.
   Format selection is done in Python instead.
3. **`vcodec` is `h264`/`h265`**, not `avc1`/`hev1` — so `vcodec^=avc` matches nothing either.
4. **Legacy musical.ly post IDs decode to 1970.** Modern IDs pack the creation time into the
   high 32 bits (~7.6e18); 2015-era IDs (~4.3e16) use a different layout, so the derived date is
   sanity-checked and discarded when implausible. Real dates come from the API.
5. **TikTok captchas some TLS fingerprints and not others** — `chrome-110`, `124` and `131`
   were challenged while `116`, `120` and `133` sailed through, and which ones pass drifts over
   time. So the app tries a list of browser profiles and remembers whichever works, rather than
   pinning one browser or one `curl_cffi` version.
6. **About 1 in 20 of the oldest posts won't parse** from their video page. Those fall back to a
   freshly-signed `playAddr` fetched straight from the API.

Two deliberate choices:

- **H.264 by default.** yt-dlp's own "best" picks 1080p H.265, which many browsers refuse to
  play and many upload pipelines reject. Tick *Prefer highest resolution* to override. (Very old
  posts are often H.265-only at 540p or lower — 2015 phone video is just small; nothing is being
  downscaled.)
- **The video list is cached for 24h**, so topping up a profile doesn't re-pay the scan.

Filenames are byte-budgeted (not character-budgeted) to 200 bytes, because filesystems cap a
name at 255 **bytes** and one emoji costs four of them. Captions run to 2,200 characters, so
truncation is common; the full text always survives in `manifest.csv`.

## Troubleshooting

**"TikTok challenged every browser profile we tried"** — you've been rate-limited, usually after
many requests in a short time. Wait a few minutes. If it persists, log into TikTok in your
browser and use `--cookies-from-browser chrome`.

**No videos found** — the profile may be private or region-locked; try the browser-cookies
option.

**Fewer videos than the profile claims** — some are private, deleted or region-locked.

**Some downloads fail** — a `failed.txt` is written; retry with `--urls-file <that file>`. Most
failures are transient rate limiting: lower `--jobs`.

**Photo/slideshow posts are skipped** — they have no video stream.

**Interrupted?** Just run it again. Finished videos are recorded in
`.musicalscraper-archive.txt`.

## Please be decent about it

These videos belong to the people who made them, and TikTok's terms don't contemplate bulk
downloading or re-uploading elsewhere. Use this for archiving your own account, for content you
have permission to use, or where fair use applies — and credit creators if you repost. What you
do with it is on you.

## Licence

MIT — see [LICENSE](LICENSE).
