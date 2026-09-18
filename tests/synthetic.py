"""Синтетические «фото комплектующих» для тестов, пока нет реальных фото.

Каждая «деталь» — плата/корпус своего цвета с элементами (чипы, разъёмы, провода)
на светлом фоне стола. perturb() имитирует повторную съёмку того же типа детали:
другая яркость, небольшой сдвиг и масштаб, шум, поворот, JPEG-сжатие.
"""

import io

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

SIZE = (640, 480)
BACKGROUND = (228, 226, 222)


def make_part(seed, size=SIZE):
    rng = np.random.RandomState(seed)
    img = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(img)
    w, h = size

    body = tuple(int(c) for c in rng.randint(20, 235, 3))
    bw, bh = int(w * rng.uniform(0.35, 0.8)), int(h * rng.uniform(0.3, 0.75))
    x0, y0 = int(rng.uniform(0.05, 0.95) * (w - bw)), int(rng.uniform(0.05, 0.95) * (h - bh))
    draw.rectangle([x0, y0, x0 + bw, y0 + bh], fill=body)

    for _ in range(rng.randint(3, 9)):
        color = tuple(int(c) for c in rng.randint(0, 255, 3))
        ew, eh = int(bw * rng.uniform(0.08, 0.35)), int(bh * rng.uniform(0.08, 0.35))
        ex, ey = x0 + int(rng.uniform(0, bw - ew)), y0 + int(rng.uniform(0, bh - eh))
        if rng.rand() < 0.5:
            draw.rectangle([ex, ey, ex + ew, ey + eh], fill=color)
        else:
            draw.ellipse([ex, ey, ex + ew, ey + eh], fill=color)

    for _ in range(rng.randint(0, 3)):  # провода
        color = tuple(int(c) for c in rng.randint(0, 255, 3))
        points = [(int(rng.uniform(0, w)), int(rng.uniform(0, h))) for _ in range(4)]
        draw.line(points, fill=color, width=int(rng.randint(4, 14)))
    return img


def perturb(img, seed, rotate=0, quality=85):
    rng = np.random.RandomState(10_000 + seed)
    w, h = img.size
    # сдвиг и масштаб до ±4 %
    dx, dy = rng.uniform(-0.04, 0.04, 2)
    scale = rng.uniform(0.96, 1.04)
    cw, ch = w / scale, h / scale
    left, top = (w - cw) / 2 + dx * w, (h - ch) / 2 + dy * h
    out = img.transform(img.size, Image.EXTENT, (left, top, left + cw, top + ch), Image.BILINEAR, fillcolor=BACKGROUND)
    out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.88, 1.12))
    arr = np.asarray(out, dtype=np.float32) + rng.normal(0, 4, (h, w, 3))
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if rotate:
        out = out.rotate(rotate, expand=True)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def save_jpeg(img, path, quality=90, exif=None):
    kwargs = {"quality": quality}
    if exif is not None:
        kwargs["exif"] = exif.tobytes()
    img.save(str(path), "JPEG", **kwargs)
