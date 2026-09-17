"""Contact sheets: frames tiled into one labeled image that fits a profile's edge."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from .sources import Source, frame_at, mmss


def _font(size: int):
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def label_text(i: int, t: float, labels: str, extra: str | None = None) -> str:
    if labels == "timestamp" or labels == "mm_ss":
        s = f"{i + 1}  {mmss(t)}"
    elif labels == "index" or labels == "image_n":
        s = f"{i + 1}"
    elif labels == "seconds":
        s = f"{i + 1}  t={t:.2f}s"
    else:
        s = ""
    if extra:
        s = f"{s}  {extra}" if s else extra
    return s


def build(frames: list, times: list[float], cols: int = 3, labels: str = "timestamp", long_edge: int = 1568,
          extras: list[str] | None = None, gap: int = 6) -> Image.Image:
    """frames: RGB arrays or PIL images (same aspect). Labels are drawn as a bar under each cell."""
    ims = [f if isinstance(f, Image.Image) else Image.fromarray(f) for f in frames]
    n = len(ims)
    cols = max(1, min(cols, n))
    rows = (n + cols - 1) // cols
    aspect = ims[0].width / ims[0].height
    # cell width so the whole sheet's long edge == long_edge
    if aspect >= 1:
        cw = (long_edge - gap * (cols + 1)) // cols
        ch = int(cw / aspect)
    else:
        ch = (long_edge - gap * (rows + 1)) // rows
        cw = int(ch * aspect)
    bar = max(18, cw // 16) if labels != "none" else 0
    W = cols * cw + gap * (cols + 1)
    H = rows * (ch + bar) + gap * (rows + 1)
    sheet = Image.new("RGB", (W, H), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    font = _font(max(12, bar - 6))
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        x = gap + c * (cw + gap); y = gap + r * (ch + bar + gap)
        sheet.paste(im.resize((cw, ch), Image.LANCZOS), (x, y))
        if bar:
            draw.rectangle([x, y + ch, x + cw, y + ch + bar], fill=(40, 40, 46))
            draw.text((x + 6, y + ch + 2), label_text(i, times[i], labels, extras[i] if extras else None), fill=(240, 240, 240), font=font)
    return sheet


def from_source(src: Source, times: list[float], cols: int = 3, labels: str = "timestamp", long_edge: int = 1568,
                extras: list[str] | None = None) -> Image.Image:
    # decode at a size that is not wasted: at most 2x the cell size
    cell_edge = max(256, long_edge // max(1, min(cols, len(times))) * 2)
    frames = [frame_at(src, t, cell_edge) for t in times]
    return build(frames, times, cols, labels, long_edge, extras)
