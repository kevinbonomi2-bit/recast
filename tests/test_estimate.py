"""Result names and size estimates shown on the wheel."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from recast import estimate
from recast.engine import REGISTRY


def _photo(path: Path, size=(1200, 900)) -> Path:
    import random
    rnd = random.Random(1)
    img = Image.new("RGB", size)
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    img.save(path, quality=90)
    return path


def test_human_sizes():
    assert estimate.human(999) == "999 B"
    assert estimate.human(410_000) == "410 KB"
    assert estimate.human(1_300_000) == "1.3 MB"
    assert estimate.human(250_000_000) == "250 MB"


def test_convert_preview_names_and_sizes(tmp_path):
    src = _photo(tmp_path / "holiday.jpg")
    info = [estimate.inspect(src)]
    assert (info[0].w, info[0].h) == (1200, 900)
    webp = estimate.preview(REGISTRY["image.to.webp"], info)
    assert (webp.stem, webp.ext, webp.chip) == ("holiday", ".webp", "WEBP")
    assert webp.sub.startswith("≈ ") and "smaller" in webp.meta
    bmp = estimate.preview(REGISTRY["image.to.bmp"], info)
    assert bmp.sub == "≈ 3.2 MB"  # exact: 1200 × 900 × 3 + header
    rot = estimate.preview(REGISTRY["image.rotate_right"], info)
    assert (rot.stem, rot.ext, rot.meta) == ("holiday-rotated", ".jpg", "900 × 1200")


def test_multi_file_preview(tmp_path):
    files = [_photo(tmp_path / f"p{i}.jpg", (300, 200)) for i in range(3)]
    infos = [estimate.inspect(f) for f in files]
    p = estimate.preview(REGISTRY["image.to.png"], infos)
    assert p.stem == "3 images → " and p.ext == ".png"
    idle = estimate.idle_preview(infos)
    assert idle.stem == "3 images"


def test_idle_preview_single(tmp_path):
    src = _photo(tmp_path / "holiday.jpg")
    idle = estimate.idle_preview([estimate.inspect(src)])
    assert (idle.stem, idle.ext) == ("holiday", ".jpg") and "1200 × 900" in idle.meta


def test_every_operation_has_a_preview(tmp_path):
    src = _photo(tmp_path / "holiday.jpg")
    out = estimate.previews(list(REGISTRY.values()), [estimate.inspect(src)])
    assert set(out) == set(REGISTRY)
