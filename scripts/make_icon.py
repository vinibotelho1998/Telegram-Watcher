from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
ASSETS = Path(__file__).resolve().parent.parent / "assets"

TOP = (59, 169, 244)
BOTTOM = (30, 111, 224)
MARGIN = 40
RADIUS = 216


def gradient() -> Image.Image:
    grad = Image.new("RGB", (S, S))
    draw = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        color = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (S, y)], fill=color)
    return grad


def rounded_mask() -> Image.Image:
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([MARGIN, MARGIN, S - MARGIN, S - MARGIN], radius=RADIUS, fill=255)
    return mask


def plane_points():
    nose = (0.845 * S, 0.250 * S)
    tail = (0.175 * S, 0.500 * S)
    crease = (0.455 * S, 0.560 * S)
    belly = (0.605 * S, 0.760 * S)
    wing = [nose, tail, crease]
    body = [nose, crease, belly]
    return wing, body


def highlight() -> Image.Image:
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    limit = S * 0.55
    for y in range(int(limit)):
        alpha = int(46 * (1 - y / limit))
        draw.line([(0, y), (S, y)], fill=(255, 255, 255, alpha))
    return layer


def build() -> Image.Image:
    mask = rounded_mask()
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(gradient(), (0, 0))
    canvas = Image.alpha_composite(canvas, highlight())

    wing, body = plane_points()
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    offset = [(x, y + 16) for x, y in wing]
    offset2 = [(x, y + 16) for x, y in body]
    sdraw.polygon(offset, fill=(10, 40, 90, 90))
    sdraw.polygon(offset2, fill=(10, 40, 90, 90))
    from PIL import ImageFilter

    shadow = shadow.filter(ImageFilter.GaussianBlur(20))
    canvas = Image.alpha_composite(canvas, shadow)

    plane = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(plane)
    pdraw.polygon(body, fill=(205, 228, 250, 255))
    pdraw.polygon(wing, fill=(255, 255, 255, 255))
    canvas = Image.alpha_composite(canvas, plane)

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    return out


def main() -> None:
    icon = build()
    png = icon.resize((256, 256), Image.LANCZOS)
    png.save(ASSETS / "app_icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS / "final_icon.ico", format="ICO", sizes=sizes)
    print("icones gerados em", ASSETS)


if __name__ == "__main__":
    main()
