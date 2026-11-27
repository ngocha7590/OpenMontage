"""Unit tests for content-aware reframe decisions (synthetic numpy frames)."""

import numpy as np

from tools.video import _content_reframe as cr

H, W = 270, 480
SRC_W, CROP_W = 1920, 606  # 16:9 source, 9:16 window at full height


def frame(x0: float, x1: float, thin: bool = False) -> np.ndarray:
    f = np.zeros((H, W), dtype=np.uint8)
    a, b = int(x0 * W), int(x1 * W)
    if thin:  # 1px-high text-like lines of low-ish contrast
        f[100:101, a:b] = 120
        f[140:141, a:b] = 120
    else:
        f[100:160, a:b] = 220
    return f


def test_bbox_detects_extent_and_empty():
    bb = cr.content_bbox_x(frame(0.4, 0.6))
    assert abs(bb[0] - 0.4) < 0.02 and abs(bb[1] - 0.6) < 0.02
    assert cr.content_bbox_x(np.zeros((H, W), dtype=np.uint8)) is None


def test_thin_text_counts_as_content():
    bb = cr.content_bbox_x(frame(0.2, 0.8, thin=True))
    assert bb is not None and bb[0] < 0.25 and bb[1] > 0.75


def test_light_background_supported():
    f = 255 - frame(0.4, 0.6)
    bb = cr.content_bbox_x(f)
    assert abs(bb[0] - 0.4) < 0.02


def test_narrow_content_crops_centred_on_content():
    samples = [cr.content_bbox_x(frame(0.6, 0.75)) for _ in range(6)]
    d = cr.decide_shot(samples, [i * 0.5 for i in range(6)], SRC_W, CROP_W)
    assert d["strategy"] == "crop"
    centre = 0.675 * SRC_W
    assert abs((d["crop_x"] + CROP_W / 2) - centre) < 25
    assert 0 <= d["crop_x"] <= SRC_W - CROP_W
    assert "keyframes" not in d


def test_crop_clamped_to_frame():
    samples = [cr.content_bbox_x(frame(0.0, 0.1)) for _ in range(4)]
    d = cr.decide_shot(samples, [0, 1, 2, 3], SRC_W, CROP_W)
    assert d["strategy"] == "crop" and d["crop_x"] == 0


def test_wide_content_falls_back_to_fit_blur():
    samples = [cr.content_bbox_x(frame(0.1, 0.9)) for _ in range(6)]
    d = cr.decide_shot(samples, [i * 0.5 for i in range(6)], SRC_W, CROP_W)
    assert d["strategy"] == "fit_blur"


def test_empty_shot_is_fit_blur():
    d = cr.decide_shot([None, None], [0, 1], SRC_W, CROP_W)
    assert d["strategy"] == "fit_blur"


def test_margin_pushes_borderline_content_to_fit_blur():
    # content 0.30 of width = 576 px; fits 606 only without margin
    samples = [cr.content_bbox_x(frame(0.35, 0.65)) for _ in range(4)]
    assert cr.decide_shot(samples, [0, 1, 2, 3], SRC_W, CROP_W, margin=0.0)["strategy"] == "crop"
    assert cr.decide_shot(samples, [0, 1, 2, 3], SRC_W, CROP_W, margin=0.025)["strategy"] == "fit_blur"


def test_moving_narrow_content_pans():
    xs = [0.10, 0.20, 0.30, 0.45, 0.60, 0.75]
    samples = [cr.content_bbox_x(frame(x, x + 0.1)) for x in xs]
    d = cr.decide_shot(samples, [i * 0.5 for i in range(6)], SRC_W, CROP_W)
    assert d["strategy"] == "crop" and len(d["keyframes"]) == 6
    assert d["keyframes"][-1][1] > d["keyframes"][0][1]


def test_analyse_shots_splits_narrow_run_from_wide():
    fps = 4.0
    frames = np.stack([frame(0.4, 0.55)] * 16 + [frame(0.05, 0.95)] * 16)
    out = cr.analyse_shots(frames, fps, [(0.0, None)], 8.0, SRC_W, CROP_W)
    assert [s["strategy"] for s in out] == ["crop", "fit_blur"]
    assert abs(out[0]["end"] - 4.0) < 0.3


def test_filter_graph_covers_all_shots():
    ds = [
        {"strategy": "fit_blur", "start": 0.0, "end": 2.0},
        {"strategy": "crop", "start": 2.0, "end": 5.0, "crop_x": 600},
    ]
    g = cr.build_filter_graph(ds, 606, 1080, 1080, 1920, 1.0, 0.5)
    assert "concat=n=2" in g and "crop=606:1080:600:0" in g and "overlay" in g
    assert "trim=start=2.0" in g and "end=2.0" in g
