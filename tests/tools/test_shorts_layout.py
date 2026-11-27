"""Unit tests for shorts_layout geometry/text helpers (no ffmpeg needed)."""

import re

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


def test_long_cue_splits_into_subcues_that_keep_62px():
    text = "Phản vật chất rất khó giữ vì nó biến mất ngay khi chạm vào vật chất thông thường."
    cues = sl.split_cues([(2.0, 12.0, text)], 62)           # real Be Vietnam Pro metrics
    assert len(cues) == 2
    assert cues[0][0] == 2.0 and cues[-1][1] == 12.0 and cues[0][1] == cues[1][0]   # contiguous, same span
    assert " ".join(c[2] for c in cues) == text
    for _, _, body in cues:
        s, lines = sl.wrap_caption(body, 62, 45)
        assert s == 62 and len(lines) <= 2
    # time proportional to characters
    n0, n1 = len(cues[0][2]), len(cues[1][2])
    assert abs((cues[0][1] - cues[0][0]) / 10 - n0 / (n0 + n1)) < 1e-6


def test_split_prefers_punctuation():
    text = "Khi một hạt mang điện bay trong từ trường, nó không bay thẳng được mà luôn uốn cong."
    parts = sl.split_cue(text, 62)
    assert parts[0].endswith(",")
    assert sl.split_cue("Ngắn thôi.", 62) == ["Ngắn thôi."]


def test_unsplittable_word_shrinks_not_splits():
    huge = "Điệnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"
    assert sl.split_cue(huge, 62) == [huge]
    s, lines = sl.wrap_caption(huge, 62, 45)
    assert s == 45 and len(lines) == 1


def test_floor_tolerance_only_on_shrink_path():
    s, lines = sl.wrap_caption("Phản vật chất rất khó giữ vì nó biến mất ngay khi chạm vào vật chất thông thường.", 62, 45)
    assert s == 45 and len(lines) == 2


# ------------------------------------------------------------ text_fx
CUES = [(1.0, 3.0, "Có 92 hạt trong 1 tấn nhé")]


def _ass(fx, cues=CUES, wt=None, **kw):
    return sl.build_ass(cues, 62, 45, 1300, "#FFFFFF", measure_factory=measure_factory, text_fx=fx,
                        word_times=wt, **kw)


def _events(ass):
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]


def test_none_is_static_old_output():
    ass = _ass("none")
    old = sl.build_ass(CUES, 62, 45, 1300, "#FFFFFF", measure_factory=measure_factory)
    assert ass == old
    assert "\\t(" not in ass and "\\fad" not in ass and "\\p1" not in ass


def test_word_highlight_times_and_gold_numbers():
    words = CUES[0][2].split()
    wt = [[(1.0 + 0.3 * i, 1.0 + 0.3 * (i + 1)) for i in range(len(words))]]
    ev = _events(_ass("pop", wt=wt))
    assert len(ev) == 1 and "\\fad(120,120)" in ev[0]
    gold, white = "&H0042C5F5", "&H00FFFFFF"
    blocks = re.findall(r"\{([^}]*)\}([^\s{\\]+)", ev[0])
    by = {w: b for b, w in blocks if not b.startswith("\\fad")}
    # "Có" (idx 0): gold at 0 ms, white again at 300 ms (the next word starts); pop 100>112>100 over 140 ms
    assert f"\\1c{white}&\\t(0,1,\\1c{gold}&)\\t(300,301,\\1c{white}&)" in by["Có"]
    assert "\\t(0,70,\\fscx112\\fscy112)\\t(70,140,\\fscx100\\fscy100)" in by["Có"]
    # "hạt" (idx 2) is active 600..900 ms
    assert "\\t(600,601,\\1c" in by["hạt"] and "\\t(900,901,\\1c" in by["hạt"]
    # numbers and the unit after a number stay gold: no colour tween back to white
    for w in ("92", "1", "tấn"):
        assert by[w].startswith(f"\\1c{gold}") and "\\t(" in by[w] and f"\\1c{white}" not in by[w]


def test_subtle_has_no_scale_and_pop_has():
    wt = [[(1.0 + 0.3 * i, 1.0 + 0.3 * (i + 1)) for i in range(7)]]
    assert "\\fscx" not in _ass("subtle", wt=wt) and "\\fad(120,120)" in _ass("subtle", wt=wt)
    assert "\\fscx112" in _ass("pop", wt=wt)


def test_gold_flags_numbers_and_units():
    f = sl.gold_flags("Chỉ ≈100 người, 1 tấn và 50 triệu kWh 12% năm 2023 rồi".split())
    assert f == [False, True, False, True, True, False, True, True, True, True, False, True, False] or \
        [w for w, g in zip("Chỉ ≈100 người, 1 tấn và 50 triệu kWh 12% năm 2023 rồi".split(), f) if g] == \
        ["≈100", "1", "tấn", "50", "triệu", "kWh", "12%", "năm", "2023"]
    assert sl.is_number_token("92") and sl.is_number_token("%") and not sl.is_number_token("hai")


def test_proportional_times_by_characters():
    t = sl.proportional_times(["a", "bbb"], 0.0, 4.0)
    assert t == [(0.0, 1.0), (1.0, 4.0)]


def test_align_words_difflib_mapping_and_gaps():
    cues = [(0.0, 3.0, "Nó sẽ rơi xuống hay rơi lên"), (3.0, 5.0, "Đo được 92 hạt")]
    asr = [{"word": w, "start": s, "end": e} for w, s, e in [
        ("Nó", 0.1, 0.4), ("sẽ", 0.4, 0.7), ("rơi", 0.8, 1.2), ("xuống", 1.2, 1.7), ("hay", 1.8, 2.0),
        ("rơi", 2.1, 2.4), ("lên", 2.4, 2.9), ("Đo", 3.2, 3.5), ("được", 3.5, 3.9),
        ("chín", 4.0, 4.2), ("mươi", 4.2, 4.4), ("hai", 4.4, 4.6), ("hạt", 4.6, 4.9)]]
    times, st = sl.align_words(cues, asr)
    assert times[0][2] == (0.8, 1.2) and times[0][6] == (2.4, 2.9)       # equal words copied
    assert times[1][3] == (4.6, 4.9)                                       # after the replaced block
    assert 4.0 <= times[1][2][0] and times[1][2][1] <= 4.6                 # "92" spans the spoken digits
    assert st["matched"] > 0.8
    assert all(a < b for c in times for a, b in c)


def test_resolve_falls_back_to_proportional_when_asr_unrelated():
    cues = [(0.0, 2.0, "một hai ba bốn")]
    asr = [{"word": "xyz", "start": 0.0, "end": 1.0}]
    times, rep = sl.resolve_word_times(cues, asr, "whisper")
    assert rep["source"] == "proportional" and times[0][0][0] == 0.0 and abs(times[0][-1][1] - 2.0) < 1e-6
    times, rep = sl.resolve_word_times(cues, None, "whisper")
    assert rep["source"] == "proportional"
    times, rep = sl.resolve_word_times(cues, [{"word": w, "start": i * .5, "end": i * .5 + .4}
                                              for i, w in enumerate("một hai ba bốn".split())], "words_json")
    assert rep["source"] == "words_json"


def test_title_bars_only_in_pop():
    bars = [{"x0": 100, "x1": 400, "y": 380}]
    pop = _ass("pop", title_bars=bars, duration=10)
    assert "\\p1" in pop and "\\t(0,400,0.5,\\fscx100)" in pop and "m 0 0 l 300 0" in pop
    assert "\\p1" not in _ass("subtle", title_bars=bars, duration=10)
    assert "\\p1" not in _ass("none", title_bars=bars, duration=10)


def test_pop_reserves_width_for_scaled_word():
    mf = lambda size: (lambda t: len(t) * size * 0.55)
    text = "một hai ba bốn năm sáu bảy tám chín mười mười một mười hai mười ba"
    ass = sl.build_ass([(0, 4, text)], 62, 45, 1300, measure_factory=mf, text_fx="pop")
    for ev in _events(ass):
        body = re.sub(r"\{[^}]*\}", "", ev.split(",,", 1)[1])
        m = mf(62)
        for line in body.split("\\N"):
            assert m(line) + 0.12 * max(m(w) for w in line.split()) <= sl.TEXT_W + 1


def test_render_title_lines_spans(tmp_path):
    fit = sl.fit_title("Cái hộp không có thành giữ hạt phản vật chất", ["hạt phản vật chất"])
    tl = sl.render_title_lines(fit, tmp_path)
    assert len(tl["lines"]) == len(fit["lines"]) and all(l["path"].exists() for l in tl["lines"])
    spans = [s for l in tl["lines"] for s in l["spans"]]
    assert len(spans) == 1 and 60 <= spans[0][0] < spans[0][1] <= 1020


# ------------------------------------------------------------ resegment
def _stream(spec, step=0.3, gap=0.02):
    """spec: words, optional (word, gap_before) tuples -> [(word, start, end)]"""
    out, t = [], 0.0
    for item in spec:
        w, g = item if isinstance(item, tuple) else (item, gap)
        t += g
        out.append((w, round(t, 3), round(t + step, 3)))
        t += step
    return out


def _texts(cues):
    return [c[2] for c in cues]


def test_reseg_does_not_split_compound_word():
    words = _stream("Vì vậy, người ta dùng nguyên tử phản hydro. Nguyên tử này trung hòa về điện nên điện trường không đẩy được nó.".split())
    cues = sl.resegment_cues(words, 62, measure_factory=lambda s: (lambda t: len(t) * s * 0.55), max_w=700)
    for a, b in zip(_texts(cues), _texts(cues)[1:]):
        assert not (a.lower().endswith("nguyên") and b.lower().startswith("tử"))
    assert any("nguyên tử" in t.lower() for t in _texts(cues))


def test_reseg_prefers_sentence_end_over_pause_and_plain():
    spec = ["Một", "hai", "ba.", "Bốn", "năm", "sáu", ("bảy", 0.3), "tám", "chín."]
    cues = sl.resegment_cues(_stream(spec), 62, measure_factory=lambda s: (lambda t: len(t) * s * 0.55), max_w=960)
    assert _texts(cues)[0] == "Một hai ba." or _texts(cues)[0].endswith(".")
    for t in _texts(cues):
        assert t.count(".") <= 1 and (t.endswith(".") or t == _texts(cues)[-1])


def test_reseg_duration_bounds_and_two_lines():
    mf = lambda s: (lambda t: len(t) * s * 0.55)
    words = _stream(["từ%d" % i for i in range(60)], step=0.35, gap=0.12)
    cues = sl.resegment_cues(words, 62, measure_factory=mf, max_w=900)
    assert sum(len(c[2].split()) for c in cues) == 60
    for t0, t1, text, wt in cues[:-1]:
        assert sl.RESEG_MIN_DUR <= t1 - t0 <= sl.RESEG_MAX_DUR + 0.2
        assert len(sl.wrap_balanced(text.split(), 900, mf(62))) <= 2
        assert len(wt) == len(text.split())


def test_reseg_closes_tiny_gaps_only():
    spec = ["a", "b.", ("c", 0.1), "d.", ("e", 0.6), "f."]
    cues = sl.resegment_cues(_stream(spec, step=0.5), 62, measure_factory=lambda s: (lambda t: len(t) * 2), max_w=10)
    for a, b in zip(cues, cues[1:]):
        gap = b[0] - a[1]
        assert gap == 0 or gap >= sl.RESEG_FILL_GAP


def test_resegment_false_keeps_srt_cues():
    cues = [(0.0, 1.0, "một hai"), (1.0, 2.0, "ba bốn")]
    ass = sl.build_ass(cues, 62, 45, 1300, measure_factory=measure_factory)      # the resegment=False path
    ev = _events(ass)
    assert len(ev) == 2 and "một hai" in ev[0] and "ba bốn" in ev[1]
    assert "resegment" in sl.ShortsLayout.input_schema["properties"]
    assert sl.ShortsLayout.input_schema["properties"]["resegment"]["default"] is True
