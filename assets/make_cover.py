"""Generate the social preview card (1280x640, GitHub's og:image size).

Design notes
------------
The one idea that has to land while someone scrolls: **four files, four zero
exit codes, one of them real.**  So the card is a before/after ledger.  Left
column is what MarkItDown reports; right column is what actually came out.
The whole card is one glance: three red zeroes, one green.

Same warm ink-on-paper palette as the other repos.  No stock imagery, no
gradient mush, no corporate blue.

Run: python assets/make_cover.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent / "cover.png"

W, H = 1280, 640

INK = (26, 24, 22)
PAPER = (250, 248, 244)
MUTED = (138, 130, 120)
RULE = (223, 217, 208)
AMBER = (196, 132, 42)
WRONG = (176, 68, 52)
RIGHT = (86, 118, 92)

MONO = "C:/Windows/Fonts/consola.ttf"
MONO_B = "C:/Windows/Fonts/consolab.ttf"
SANS_B = "C:/Windows/Fonts/segoeuib.ttf"
SANS = "C:/Windows/Fonts/segoeui.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def main() -> None:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # Paper grain: near-invisible dots beat a flat fill.
    for y in range(0, H, 3):
        for x in range(0, W, 3):
            if (x * 7 + y * 13) % 97 == 0:
                d.point((x, y), fill=(244, 241, 236))

    # --- header ------------------------------------------------------------
    d.text((64, 46), "markitdown-doctor", font=font(SANS_B, 48), fill=INK)
    d.text(
        (66, 108),
        "MarkItDown works. Getting it to work is the hard part.",
        font=font(SANS, 23),
        fill=MUTED,
    )
    d.rectangle([64, 148, 132, 153], fill=AMBER)

    # --- the ledger --------------------------------------------------------
    top = 190
    row_h = 62
    left = 64
    right = W - 64

    # column headers
    d.text((left, top - 30), "INPUT", font=font(SANS_B, 16), fill=MUTED)
    d.text((left + 470, top - 30), "EXIT", font=font(SANS_B, 16), fill=MUTED)
    d.text((left + 600, top - 30), "WHAT YOU ACTUALLY GET", font=font(SANS_B, 16), fill=MUTED)

    rows = [
        ("report.pdf", "0", "the text  ·  correct", RIGHT),
        ("scanned.pdf", "0", "nothing  ·  empty string", WRONG),
        ("photo.png", "0", "nothing  ·  empty string", WRONG),
        ("archive.bin", "0", "mojibake  ·  raw bytes", WRONG),
    ]

    mono = font(MONO, 21)
    # Segoe UI Bold for the exit codes: Consolas renders 0 with a slash, which
    # reads as a theta at this size and makes the column look like a typo.
    code_font = font(SANS_B, 27)

    y = top
    for name, code, verdict, colour in rows:
        d.line([left, y, right, y], fill=RULE, width=1)
        d.text((left, y + 16), name, font=mono, fill=INK)
        d.text((left + 470, y + 13), code, font=code_font, fill=colour)
        d.text((left + 600, y + 17), verdict, font=mono, fill=colour)
        y += row_h
    d.line([left, y, right, y], fill=RULE, width=1)

    # --- the punchline -----------------------------------------------------
    d.text(
        (left, y + 26),
        "All four exited 0. Three of them were useless.",
        font=font(SANS_B, 27),
        fill=INK,
    )
    d.text(
        (left, y + 66),
        "mdc turns exit code 0 into a verdict you can branch on.",
        font=font(SANS, 20),
        fill=MUTED,
    )

    # --- footer ------------------------------------------------------------
    d.line([64, 556, W - 64, 556], fill=RULE, width=1)
    d.text(
        (64, 576),
        "60 tests  ·  byte-identical to markitdown  ·  zero changes to core",
        font=font(SANS_B, 20),
        fill=INK,
    )
    d.text(
        (W - 64 - 348, 580),
        "github.com/linmoxi33/markitdown-doctor",
        font=font(MONO, 16),
        fill=MUTED,
    )

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {W}x{H})")


if __name__ == "__main__":
    main()
