"""Tests for the thumbnail_compose publisher tool."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont

from tools.publishers.thumbnail_compose import BUNDLED_FONT, ThumbnailCompose
from tools.tool_registry import ToolRegistry

HEADLINE = "Vì sao pin chai? Vì sao cháy?"
PALETTE = {"text": "#F3EEE2", "accent": "#5BC6A1", "scrim": "#0F1A2E"}


def _bg(path, size=(1600, 900)):
    img = Image.new("RGB", size, (60, 90, 140))
    ImageDraw.Draw(img).ellipse((size[0] // 2, 100, size[0] - 100, size[1] - 100), fill=(226, 107, 90))
    img.save(path)
    return str(path)


@pytest.mark.parametrize("layout", ["left", "right", "bottom", "center"])
def test_compose_dimensions_and_size(tmp_path, layout):
    out = tmp_path / "t.png"
    r = ThumbnailCompose().execute({
        "background_path": _bg(tmp_path / "bg.png"), "headline": HEADLINE,
        "accent_words": ["chai?", "cháy?"], "palette": PALETTE, "layout": layout,
        "output_path": str(out),
    })
    assert r.success, r.error
    d = r.data
    assert (d["width"], d["height"]) == (1280, 720)
    assert Path(d["path"]).stat().st_size == d["bytes"] < 2 * 1024 * 1024
    with Image.open(d["path"]) as im:
        assert im.size == (1280, 720)
    assert len(d["lines"]) <= 3


def test_custom_size_and_missing_background(tmp_path):
    r = ThumbnailCompose().execute({
        "background_path": _bg(tmp_path / "bg.png", (400, 400)), "headline": "Xin chào",
        "output_path": str(tmp_path / "s.png"), "width": 640, "height": 360,
    })
    assert r.success and Image.open(r.data["path"]).size == (640, 360)
    r = ThumbnailCompose().execute({
        "background_path": str(tmp_path / "nope.png"), "headline": "x", "output_path": str(tmp_path / "o.png"),
    })
    assert not r.success


def test_vietnamese_glyphs_render():
    assert BUNDLED_FONT.is_file()
    assert (BUNDLED_FONT.parent / "OFL.txt").is_file()
    font = ImageFont.truetype(str(BUNDLED_FONT), 80)
    for ch in set(HEADLINE) - {" "}:
        mask = font.getmask(ch)
        assert mask.getbbox() is not None, f"missing glyph {ch!r}"
    # .notdef box differs from real glyphs: 'ạ' must not equal the fallback for an unmapped char
    assert font.getmask("ạ").getbbox() != font.getmask("\U0001F9EA").getbbox()


def test_large_png_falls_back_to_jpeg(tmp_path):
    import numpy as np

    rng = np.random.default_rng(0)
    noise = Image.fromarray(rng.integers(0, 255, (720, 1280, 3), dtype="uint8"))
    noise.save(tmp_path / "noise.png")
    r = ThumbnailCompose().execute({
        "background_path": str(tmp_path / "noise.png"), "headline": HEADLINE,
        "output_path": str(tmp_path / "n.png"),
    })
    assert r.success
    assert r.data["path"].endswith(".jpg") and r.data["bytes"] < 2 * 1024 * 1024


def test_registry_discovers_tool():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("thumbnail_compose") is not None
