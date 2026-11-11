"""Smoke-test every operation against small generated files, plus a few exact checks."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from recast import paths
from recast.engine import REGISTRY, Context, operations_for, run

FFMPEG = paths.ffmpeg()


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("samples")
    s: dict[str, Path] = {}

    img = Image.new("RGBA", (320, 200), (30, 120, 220, 255))
    for x in range(0, 320, 20):
        img.paste((240, 80, 40, 128), (x, 0, x + 10, 200))
    s["png"] = d / "figures.png"
    img.save(s["png"])
    s["jpg"] = d / "landscape.jpg"
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    exif[0x0112] = 6  # rotated 90°: converters must bake this in
    img.convert("RGB").save(s["jpg"], quality=90, exif=exif.tobytes())
    s["heic"] = d / "holiday.heic"
    img.convert("RGB").save(s["heic"], "HEIF")
    s["gif"] = d / "spin.gif"
    frames = [Image.new("P", (64, 64), i * 40) for i in range(4)]
    frames[0].save(s["gif"], save_all=True, append_images=frames[1:], duration=100, loop=0)
    s["svg"] = d / "logo.svg"
    s["svg"].write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
                        '<circle cx="32" cy="32" r="28" fill="#1e78dc"/></svg>')

    import pymupdf
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Chapter {i + 1}", fontsize=24)
        page.insert_text((72, 120), "Body text for the converter test. " * 3, fontsize=11)
        page.insert_image(pymupdf.Rect(72, 200, 272, 325), filename=str(s["png"]))
    s["pdf"] = d / "field-notes.pdf"
    doc.save(s["pdf"])

    if FFMPEG:
        s["mp3"] = d / "mixtape.mp3"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:a", "libmp3lame", str(s["mp3"])], check=True)
        s["mov"] = d / "weekend.mov"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
                        "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(s["mov"])], check=True)

    s["zip"] = d / "little-project.zip"
    with zipfile.ZipFile(s["zip"], "w") as zf:
        zf.writestr("little-project/readme.txt", "hello")
        zf.writestr("little-project/src/main.py", "print('hi')")
    s["srt"] = d / "film.srt"
    s["srt"].write_text("1\n00:00:01,000 --> 00:00:02,500\nHello <i>there</i>\n\n"
                        "2\n00:01:02,040 --> 00:01:04,000\nSecond line\nwraps\n", encoding="utf-8")
    s["csv"] = d / "budget.csv"
    s["csv"].write_text("id,item,cost\n007,Paper,12.50\n042,Ink,3\n", encoding="utf-8")
    s["md"] = d / "notes.md"
    s["md"].write_text("# Notes\n\nSome *text* and a list:\n\n- one\n- two\n", encoding="utf-8")
    return s


def _ops(path: Path):
    return [o for w in ("convert", "tools", "actions") for o in operations_for([path], w)
            if (o.wheel != "actions" or o.id == "any.zip") and not o.asks]  # asks: tested with options below


@pytest.mark.parametrize("kind", ["png", "jpg", "heic", "gif", "svg", "pdf", "mp3", "mov", "zip", "srt", "csv", "md"])
def test_every_operation_produces_output(samples, kind, tmp_path):
    if kind not in samples:
        pytest.skip("FFmpeg not installed")
    # Work on a copy so outputs from different kinds don't collide.
    src = tmp_path / samples[kind].name
    src.write_bytes(samples[kind].read_bytes())
    ops = _ops(src)
    assert ops, f"no operations offered for .{kind}"
    for operation in ops:
        outs = run(operation, [src], Context())
        assert outs, operation.id
        for o in outs:
            assert o.exists(), (operation.id, o)
            if o.is_file():
                assert o.stat().st_size > 0, (operation.id, o)


def test_jpg_conversion_bakes_orientation_and_flattens_alpha(samples, tmp_path):
    src = tmp_path / "landscape.jpg"
    src.write_bytes(samples["jpg"].read_bytes())
    (out,) = run(REGISTRY["image.to.png"], [src], Context())
    assert Image.open(out).size == (200, 320)
    png = tmp_path / "figures.png"
    png.write_bytes(samples["png"].read_bytes())
    (out,) = run(REGISTRY["image.to.jpg"], [png], Context())
    assert Image.open(out).mode == "RGB"


def test_strip_metadata_removes_exif(samples, tmp_path):
    src = tmp_path / "landscape.jpg"
    src.write_bytes(samples["jpg"].read_bytes())
    (out,) = run(REGISTRY["image.strip"], [src], Context())
    assert not Image.open(out).getexif()
    assert out.name == "landscape-clean.jpg"


def test_outputs_never_overwrite(samples, tmp_path):
    src = tmp_path / "figures.png"
    src.write_bytes(samples["png"].read_bytes())
    a = run(REGISTRY["image.to.webp"], [src], Context())[0]
    b = run(REGISTRY["image.to.webp"], [src], Context())[0]
    assert a.name == "figures.webp" and b.name == "figures (1).webp"


def test_pdf_merge_and_split(samples, tmp_path):
    import pymupdf
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(samples["pdf"].read_bytes())
    b.write_bytes(samples["pdf"].read_bytes())
    assert REGISTRY["pdf.merge"] in operations_for([a, b], "tools")
    (merged,) = run(REGISTRY["pdf.merge"], [a, b], Context())
    assert pymupdf.open(merged).page_count == 6
    (folder,) = run(REGISTRY["pdf.split"], [a], Context())
    assert len(list(folder.glob("*.pdf"))) == 3


def test_subtitles_round_trip(samples, tmp_path):
    src = tmp_path / "film.srt"
    src.write_bytes(samples["srt"].read_bytes())
    (vtt,) = run(REGISTRY["subtitle.to.vtt"], [src], Context())
    text = vtt.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT") and "00:01:02.040 --> 00:01:04.000" in text
    (txt,) = run(REGISTRY["subtitle.to.txt"], [src], Context())
    assert "Hello there" in txt.read_text(encoding="utf-8")


def test_csv_to_xlsx_keeps_leading_zero_ids(samples, tmp_path):
    import openpyxl
    src = tmp_path / "budget.csv"
    src.write_bytes(samples["csv"].read_bytes())
    (out,) = run(REGISTRY["sheet.to.xlsx"], [src], Context())
    ws = openpyxl.load_workbook(out).active
    assert ws["A2"].value == "007" and ws["C2"].value == 12.5


def test_extract_avoids_double_nesting(samples, tmp_path):
    src = tmp_path / "little-project.zip"
    src.write_bytes(samples["zip"].read_bytes())
    (out,) = run(REGISTRY["archive.extract"], [src], Context())
    assert out.name == "little-project" and (out / "src" / "main.py").exists()


def test_wheels_offer_sensible_options(samples):
    labels = [o.label for o in operations_for([samples["png"]], "convert")]
    assert "PNG" not in labels and "WEBP" in labels
    assert operations_for([samples["png"], samples["pdf"]], "convert") == []
    assert [o.id for o in operations_for([samples["png"], samples["pdf"]], "actions")] == ["any.zip"]


def test_crop_uses_fractions_and_aspect(samples, tmp_path):
    src = tmp_path / "figures.png"
    src.write_bytes(samples["png"].read_bytes())  # 320 × 200
    (out,) = run(REGISTRY["image.crop"], [src], Context(options={"crop": (0.25, 0.0, 0.75, 1.0)}))
    assert out.name == "figures-cropped.png" and Image.open(out).size == (160, 200)
    (sq,) = run(REGISTRY["image.crop"], [src], Context(options={"crop": (0, 0, 1, 1), "aspect": 1.0}))
    assert Image.open(sq).size == (200, 200)
    (sized,) = run(REGISTRY["image.crop"], [src], Context(options={"crop": (0, 0, 1, 1), "size": (64, 40)}))
    assert Image.open(sized).size == (64, 40)


def test_image_compress_to_hits_target(tmp_path):
    import random
    rnd = random.Random(3)
    src = tmp_path / "noise.jpg"
    img = Image.new("RGB", (900, 600))
    img.putdata([(rnd.randrange(256),) * 3 for _ in range(900 * 600)])
    img.save(src, quality=95)
    (out,) = run(REGISTRY["image.compress_to"], [src], Context(options={"ratio": 0.2}))
    assert out.stat().st_size <= src.stat().st_size * 0.2


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")
def test_trim_and_compress_to_media(samples, tmp_path):
    from recast.engine.media import duration, probe
    mov = tmp_path / "weekend.mov"
    mov.write_bytes(samples["mov"].read_bytes())
    (cut,) = run(REGISTRY["video.trim"], [mov], Context(options={"start": 0.5, "end": 1.5}))
    assert abs(duration(probe(cut)) - 1.0) < 0.15
    mp3 = tmp_path / "mixtape.mp3"
    mp3.write_bytes(samples["mp3"].read_bytes())
    (acut,) = run(REGISTRY["audio.trim"], [mp3], Context(options={"start": 0.2, "end": 1.2}))
    assert abs(duration(probe(acut)) - 1.0) < 0.15
    big = tmp_path / "clip.mp4"  # a realistic bitrate, so half its size is reachable
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-b:v", "4M", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(big)], check=True)
    (small,) = run(REGISTRY["video.compress_to"], [big], Context(options={"ratio": 0.5}))
    assert small.stat().st_size <= big.stat().st_size * 0.5 * 1.1


def test_pdf_compress_to(samples, tmp_path):
    src = tmp_path / "field-notes.pdf"
    src.write_bytes(samples["pdf"].read_bytes())
    (out,) = run(REGISTRY["pdf.compress_to"], [src], Context(options={"ratio": 0.9}))
    assert out.exists() and out.name == "field-notes-compressed.pdf"


def test_tools_order_puts_compress_crop_first(samples):
    labels = [o.label for o in operations_for([samples["jpg"]], "tools")]
    assert labels[:3] == ["Compress", "Compress to size…", "Crop…"]


def test_output_folder_setting(samples, tmp_path):
    from recast import paths as P
    src = tmp_path / "figures.png"
    src.write_bytes(samples["png"].read_bytes())
    P.OUTPUT_DIR = tmp_path / "Converted"
    try:
        (out,) = run(REGISTRY["image.to.webp"], [src], Context())
    finally:
        P.OUTPUT_DIR = None
    assert out.parent == tmp_path / "Converted"


def test_hdr_jxr_tonemaps_without_clipping(tmp_path):
    import imagecodecs
    import numpy as np
    h, w = 16, 256
    hdr = np.zeros((h, w, 4), np.float16)
    ramp = np.linspace(0, 12, w, dtype=np.float32)  # 0 → 960 nits in scRGB units
    hdr[..., 0] = ramp
    hdr[..., 1] = ramp * 0.8
    hdr[..., 2] = ramp * 0.5
    hdr[..., 3] = 1
    src = tmp_path / "Forza 2026-10-28 HDR.jxr"
    src.write_bytes(imagecodecs.jpegxr_encode(hdr, level=1.0))
    assert REGISTRY["image.to.png"] in operations_for([src], "convert")
    (out,) = run(REGISTRY["image.to.png"], [src], Context())
    img = Image.open(out)
    assert img.mode == "RGB" and img.size == (w, h)
    # overall brightness (sum of channels): red, the strongest channel, tops out first and the
    # pixel then whitens, so judge the roll-off on all three together
    row = [sum(img.getpixel((x, 8))) for x in range(w)]
    assert row[0] == 0
    assert all(b >= a for a, b in zip(row, row[1:]))      # brightness never inverts
    assert len(set(row[w // 2:])) > 12                      # highlights stay graded, not one flat white
    # paper white (200 nits = 2.5 in scRGB) lands bright but below the roll-off ceiling
    assert 200 < img.getpixel((int(2.5 / 12 * (w - 1)), 8))[0] < 255


@pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")
def test_discord_fits_limit_and_skips_small_files(samples, tmp_path):
    op_ = REGISTRY["video.discord"]
    assert op_.copy_result and op_ in operations_for([samples["mov"]], "tools")
    big = tmp_path / "clip.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-b:v", "6M", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(big)], check=True)
    assert big.stat().st_size > 1_500_000
    (out,) = run(op_, [big], Context(options={"discord_limit_mb": 1}))
    assert out.name == "clip-discord.mp4" and out.stat().st_size <= 1_000_000
    small = tmp_path / "tiny.mp4"
    small.write_bytes(out.read_bytes())
    assert run(op_, [small], Context(options={"discord_limit_mb": 10})) == [small]
