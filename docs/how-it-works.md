# How Musical Scraper works

This note covers the implementation choices behind profile scanning, clean format selection,
and resumable downloads. For installation and everyday use, start with the
[README](../README.md).

## Finding the full profile history

The default yt-dlp TikTok user extractor paginates from the newest posts and relies on the
API's `hasMorePrevious` flag. In testing, that flag stopped early on a large profile. The
extractor also applies its own lower cursor bound, which excludes some older posts.

Musical Scraper reads the public profile metadata, including its account creation time and
reported video count, then queries dated cursor windows back through the account history. It
widens the time step across quiet periods and narrows it again when posts are found. Results
are de-duplicated by video ID, sorted by creation time, and cached locally for 24 hours.

TikTok may still omit private, deleted, or region-restricted videos. The reported profile
count is used as a completeness check, not as a guarantee that every post can be retrieved.

## Selecting a clean video stream

The profile API exposes a playback address separately from the watermarked download address.
The downloader selects a video format from the playback stream. It does not remove a watermark
from pixels, crop the frame, or re-encode the video.

The selector runs inside the same yt-dlp extraction that downloads the video. Format IDs can
change between requests, and clean formats may not carry the metadata fields expected by
yt-dlp's text format-filter syntax. The default prefers H.264 for compatibility; users can
choose the best available resolution, which may be H.265.

If an older post's webpage cannot be parsed, the app can request a fresh playback address from
the profile API and fetch that video directly.

## Files and resume behavior

- The archive file records completed video IDs so repeat runs continue with undownloaded posts.
- The listing cache avoids scanning the full account on every run; `--refresh` rebuilds it.
- A CSV manifest stores the full caption, source URL, upload date, selected resolution, and
  download status.
- Original timestamps are applied to downloaded files so filesystem date sorting follows
  upload order.
- Caption filenames are sanitized and shortened to stay below common per-filename byte limits.

The archive and listing cache are local files inside the output folder. Do not publish them if
you want to keep a download history or profile list private.
