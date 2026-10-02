#!/usr/bin/env python3
"""Render the 1280x640 GitHub social preview using only Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets" / "social-preview.png"
W, H = 1280, 640


def font(size, bold=False):
    candidates = (
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
         "/System/Library/Fonts/Supplemental/Arial.ttf"),
        ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def blend(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main():
    image = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(image, "RGBA")

    stops = [(0.0, (232, 50, 61)), (0.52, (244, 90, 38)), (1.0, (255, 173, 39))]
    for x in range(W):
        t = x / (W - 1)
        left, right = (stops[0], stops[1]) if t <= stops[1][0] else (stops[1], stops[2])
        local = (t - left[0]) / (right[0] - left[0])
        draw.line((x, 0, x, H), fill=blend(left[1], right[1], local))

    # Soft decorative shapes keep the gradient from feeling like a flat banner.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((855, -185, 1375, 335), fill=(255, 237, 154, 36))
    gd.ellipse((930, 365, 1375, 810), fill=(125, 25, 37, 24))
    image = Image.alpha_composite(image, glow)
    draw = ImageDraw.Draw(image, "RGBA")

    # Wordmark tile and music note.
    draw.rounded_rectangle((72, 72, 144, 144), radius=22, fill="#c83b32")
    draw.line((113, 91, 113, 123), fill="white", width=6)
    draw.line((113, 92, 130, 87), fill="white", width=6)
    draw.line((130, 87, 130, 115), fill="white", width=6)
    draw.ellipse((96, 117, 115, 132), fill="white")
    draw.ellipse((114, 108, 133, 123), fill="white")

    draw.text((72, 178), "TIKTOK PROFILE ARCHIVER", font=font(19, True), fill="#fff5e8",
              stroke_width=0)
    draw.text((67, 230), "Musical Scraper", font=font(61, True), fill="white")
    draw.text((73, 319), "Your archive, in order.", font=font(29), fill="#fff8eb")

    # Feature pills.
    for x, width, text in ((73, 214, "CLEAN VIDEO FILES"), (300, 176, "OLDEST FIRST")):
        draw.rounded_rectangle((x, 397, x + width, 443), radius=23,
                               fill="#b9402c")
        draw.ellipse((x + 17, 415, x + 29, 427), fill="#fff0bf")
        draw.text((x + 41, 408), text, font=font(14, True), fill="white")

    # Archive card with three timeline rows.
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((735, 98, 1172, 482), radius=30, fill=(80, 27, 17, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    image = Image.alpha_composite(image, shadow)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((722, 79, 1159, 463), radius=28, fill="#fff8ef")
    draw.rounded_rectangle((748, 105, 808, 165), radius=18, fill="#f15b2b")
    draw.line((780, 119, 780, 145), fill="white", width=4)
    draw.line((780, 119, 795, 115), fill="white", width=4)
    draw.line((795, 115, 795, 137), fill="white", width=4)
    draw.ellipse((766, 139, 782, 151), fill="white")
    draw.ellipse((781, 132, 797, 144), fill="white")
    draw.text((827, 113), "Profile archive", font=font(20, True), fill="#45251b")
    draw.text((827, 141), "3,320 videos · oldest first", font=font(14), fill="#a28572")
    draw.line((748, 186, 1133, 186), fill="#eddfd1", width=2)

    rows = [
        (204, "summer memories #lipsync", "JUN 27, 2015"),
        (276, "back when we were young", "JUL 03, 2015"),
        (348, "everyday things #throwback", "JUL 08, 2015"),
    ]
    for y, caption, date in rows:
        draw.rounded_rectangle((746, y, 1135, y + 58), radius=13, fill="white")
        draw.ellipse((761, y + 14, 789, y + 42), fill="#fff0d9")
        draw.polygon(((772, y + 20), (772, y + 37), (783, y + 28)), fill="#f04b30")
        draw.text((803, y + 8), caption, font=font(14, True), fill="#503324")
        draw.text((803, y + 32), date, font=font(11, True), fill="#b2947e")
        draw.line((1102, y + 26, 1111, y + 35), fill="#58a66a", width=3)
        draw.line((1111, y + 35, 1125, y + 18), fill="#58a66a", width=3)

    draw.rounded_rectangle((1009, 416, 1192, 469), radius=26, fill="white")
    draw.ellipse((1024, 431, 1042, 449), fill="#52aa63")
    draw.line((1029, 440, 1033, 444), fill="white", width=2)
    draw.line((1033, 444, 1039, 436), fill="white", width=2)
    draw.text((1050, 433), "NO WATERMARK", font=font(13, True), fill="#4d3024")

    image.convert("RGB").save(DEST, optimize=True)
    print(f"Wrote {DEST} ({W}×{H})")


if __name__ == "__main__":
    main()
