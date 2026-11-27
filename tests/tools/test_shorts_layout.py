"""Unit tests for shorts_layout geometry/text helpers (no ffmpeg needed)."""

import pytest

from tools.video import shorts_layout as sl


def measure_factory(size):
    return lambda t: len(t) * size * 0.55          # fake monospace-ish metrics


def test_tokenize_marks_accent_phrase_and_diacritics():
    toks = sl.tokenize_title("Cần cả cỗ máy 1 tấn để giữ", ["Cỗ máy 1 tấn"])
    assert [k >= 0 for _, k in toks] == [False, False, True, True, True, True, False, False]


def test_accent_phrase_not_split_across_lines():
    fit = sl.fit_title("Cần cả cỗ máy 1 tấn chỉ để giữ 92 hạt phản vật chất", ["cỗ máy 1 tấn"],
                       size=76, measure_factory=measure_factory)
    for line in fit["lines"]:
        flags = [a for _, a in line]
        assert flags == sorted(flags) or flags == sorted(flags, reverse=True)  # contiguous
    joined = [" ".join(w for w, a in ln if a) for ln in fit["lines"]]
    assert "cỗ máy 1 tấn" in joined


def test_title_wraps_within_width_and_three_lines():
    fit = sl.fit_title("một hai ba bốn năm sáu bảy tám chín mười", None, size=76, measure_factory=measure_factory)
    assert len(fit["lines"]) <= 3
    for ln in fit["lines"]:
        assert measure_factory(fit["size"])(" ".join(w for w, _ in ln)) <= sl.TEXT_W


def test_title_shrinks_then_stops_at_min():
    long = " ".join(["từ"] * 36)
    fit = sl.fit_title(long, None, size=76, min_size=56, measure_factory=measure_factory)
    assert fit["size"] < 76
    huge = " ".join(["từkhóa"] * 80)
    fit = sl.fit_title(huge, None, size=76, min_size=56, measure_factory=measure_factory)
    assert fit["size"] == 56 and fit["truncated"] and len(fit["lines"]) == 3
    assert fit["lines"][-1][-1][0].endswith("…")


def test_real_font_renders_vietnamese_title(tmp_path):
    fit = sl.fit_title("Cần cả cỗ máy 1 tấn chỉ để giữ 92 hạt phản vật chất", ["cỗ máy 1 tấn"])
    assert fit["size"] >= 56 and len(fit["lines"]) <= 3
    h, block = sl.render_title_png(fit, tmp_path / "t.png")
    assert (tmp_path / "t.png").stat().st_size > 1000 and h > block


@pytest.mark.parametrize("lines,size", [(1, 76), (2, 70), (3, 76), (3, 56)])
def test_layout_zones_do_not_overlap_and_stay_safe(lines, size):
    lay = sl.compute_layout(lines, size, 62)
    for box_h in (500, 608, lay["box_max_h"]):
        f = sl.finish_layout(lay, box_h)
        t, b, c = f["title"], f["box"], f["caption"]
        assert t["y"] >= sl.TITLE_TOP
        top_gap, bot_gap = t["y"] - sl.TITLE_TOP, sl.CAPTION_BOTTOM_LIMIT - (c["y"] + lay["caption_h"])
        assert abs(top_gap - bot_gap) <= 1  # stack centred in 180..1600
        assert t["y"] + t["h"] + sl.GAP <= b["y"]
        assert b["y"] + b["h"] + sl.GAP <= c["y"]
        assert c["y"] + c["h"] <= sl.CAPTION_BOTTOM_LIMIT
        for z in (t, c):
            assert z["x"] >= sl.SAFE_X0 and z["x"] + z["w"] <= sl.SAFE_X1
        assert b["w"] == sl.OUT_W and b["h"] <= sl.BOX_MAX_H


def test_box_cap_shrinks_when_captions_would_cross_limit():
    lay = sl.compute_layout(3, 76, 62, box_max_h=1200)
    assert lay["box_max_h"] < 1200
    f = sl.finish_layout(lay, lay["box_max_h"])
    assert f["caption"]["y"] + f["caption"]["h"] <= sl.CAPTION_BOTTOM_LIMIT


def test_box_height_is_max_need_capped_even():
    assert sl.box_height([608, 650, 628], 760) == 650
    assert sl.box_height([608, 900], 760) == 760
    assert sl.box_height([], 760) == 606


def test_shot_window_wide_vs_narrow():
    wide = sl.shot_window([0.0, 1.0], 1920, 1080, 0.02, 1.25, 760)
    assert wide["zoom"] == 1.0 and wide["win_w"] == 1920 and wide["content_h"] == 608
    narrow = sl.shot_window([0.4, 0.6], 1920, 1080, 0.02, 1.25, 760)
    assert narrow["zoom"] == 1.25 and narrow["content_h"] == 760
    # never cuts the bbox
    assert narrow["win_x"] <= 0.4 * 1920 and narrow["win_x"] + narrow["win_w"] >= 0.6 * 1920
    edge = sl.shot_window([0.0, 0.5], 1920, 1080, 0.02, 1.25, 760)
    assert edge["win_x"] == 0


def test_shot_window_tall_source_crops_vertically():
    w = sl.shot_window(None, 1080, 1080, 0.02, 1.0, 760)
    assert w["vertical_crop"] and w["content_h"] <= 760


def test_srt_and_ass(tmp_path):
    cues = sl.parse_srt("1\n00:00:00,000 --> 00:00:01,760\nHãy quay lại lúc vũ trụ mới hình\nthành.\n\n2\n00:00:02,300 --> 00:00:02,720\n<i>Khi đó,</i>\n")
    assert len(cues) == 2 and cues[0][2] == "Hãy quay lại lúc vũ trụ mới hình thành." and cues[1][2] == "Khi đó,"
    ass = sl.build_ass(cues, 62, 45, 1300, "#F5C542", measure_factory=measure_factory)
    assert "PlayResY: 1920" in ass and ",62," in ass and "Dialogue: 0,0:00:00.00,0:00:01.76" in ass
    assert "&H0042C5F5" in ass and "\\N" in ass.split("Dialogue")[1]
    assert f",{sl.SAFE_X0},{sl.OUT_W - sl.SAFE_X1},1300," in ass


def test_caption_max_two_lines_or_shrinks():
    s, lines = sl.wrap_caption("a " * 34, 62, 45, measure_factory=measure_factory)
    assert len(lines) <= 2 and 45 <= s <= 62


def test_long_caption_two_lines_with_real_font():
    text = "Phản vật chất rất khó giữ vì nó biến mất ngay khi chạm vào vật chất thông thường."
    s, lines = sl.wrap_caption(text, 62, 45)          # real Be Vietnam Pro metrics
    assert len(lines) == 2 and s == 45                # 2579 px at 62 cannot fit 2x960: shrinks to the floor
    f = sl._font(s)
    widths = [f.getlength(ln) for ln in lines]
    assert max(widths) <= sl.TEXT_W * sl.CAPTION_FLOOR_TOLERANCE
    # a shorter cue keeps 62 px and uses balanced lines
    s2, l2 = sl.wrap_caption("Phản vật chất rất khó giữ vì nó biến mất ngay.", 62, 45)
    assert s2 == 62 and len(l2) == 2
