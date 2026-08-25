# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Musical Scraper.

Builds a .app bundle on macOS and a single-file .exe on Windows. PyInstaller
cannot cross-compile, so each platform's artifact must be built on that platform
(see .github/workflows/release.yml).
"""
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# yt-dlp loads extractors dynamically and curl_cffi ships native libraries;
# neither is discoverable by static analysis, so collect them wholesale.
for pkg in ("yt_dlp", "curl_cffi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

IS_MAC = sys.platform == "darwin"
ICON = "assets/icon.icns" if IS_MAC else "assets/icon.ico"

a = Analysis(
    ["musical_scraper_gui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas + [("assets/icon.png", "assets")],
    hiddenimports=hiddenimports + ["musical_scraper"],
    excludes=["matplotlib", "numpy", "pandas", "scipy", "pytest", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
              name="Musical Scraper", console=False, icon=ICON)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   name="Musical Scraper")
    app = BUNDLE(
        coll,
        name="Musical Scraper.app",
        icon=ICON,
        bundle_identifier="io.github.musicalscraper",
        info_plist={
            "CFBundleName": "Musical Scraper",
            "CFBundleDisplayName": "Musical Scraper",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "Downloads only content you have the right to use.",
        },
    )
else:
    # One file on Windows: a single .exe is far easier to hand round a community.
    # Passing binaries+datas into EXE (and leaving exclude_binaries False) IS what
    # selects onefile mode - there is no `onefile=` argument.
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
              name="MusicalScraper", console=False, icon=ICON, upx=False)
