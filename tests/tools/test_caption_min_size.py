from lib.caption_size import (
    clamp_ass_font_size,
    clamp_caption_px,
    min_caption_px,
)
from tools.video.video_compose import VideoCompose


def test_min_px_1080p_and_vertical():
    assert min_caption_px(1920, 1080) == 45
    assert min_caption_px(1080, 1920) == 45
    assert min_caption_px(3840, 2160) == 91


def test_small_sizes_clamped_large_pass_through():
    assert clamp_caption_px(30, 1920, 1080) == 45
    assert clamp_caption_px(40, 1920, 1080) == 45
    assert clamp_caption_px(48, 1920, 1080) == 48
    assert clamp_caption_px(90, 1920, 1080) == 90
    assert clamp_caption_px("bad", 1920, 1080) == 45


def test_ass_units_are_relative_to_playresy():
    # 45 px of 1080 -> 12 units of 288
    assert clamp_ass_font_size(5, 1920, 1080) == 12
    assert clamp_ass_font_size(22, 1920, 1080) == 22


def test_force_style_uses_clamp():
    assert "FontSize=12," in VideoCompose._build_subtitle_style({"font_size": 3}, 1920, 1080)
    assert "FontSize=28," in VideoCompose._build_subtitle_style({"font_size": 28}, 1920, 1080)
