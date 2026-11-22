"""storyboard_frames / storyboard_animate (product-ad, PLAN-03 §4.5).
Selectors are faked — no provider calls."""

import json
from pathlib import Path

import pytest

from schemas.artifacts import validate_artifact
from tools.base_tool import ToolResult, ToolStatus
from tools.storyboard.storyboard_animate import StoryboardAnimate, motion_prompt
from tools.storyboard.storyboard_frames import StoryboardFrames, frame_prompt
from tools.tool_registry import registry


class FakeImageSelector:
    def __init__(self, fail_providers=(), fail_scenes=()):
        self.calls = []
        self.fail_providers = set(fail_providers)
        self.fail_scenes = set(fail_scenes)

    def get_status(self):
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        self.calls.append(inputs)
        allowed = inputs.get("allowed_providers") or ["geminiweb"]
        if allowed[0] in self.fail_providers or any(s in inputs["output_path"] for s in self.fail_scenes):
            return ToolResult(success=False, error=f"{allowed[0]} down")
        Path(inputs["output_path"]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return ToolResult(success=True, data={"selected_tool": f"gateway_{allowed[0]}_image",
                                              "selected_provider": allowed[0]}, model="m1", cost_usd=0.01)


class FakeVideoSelector:
    def __init__(self, fail_times=0, tool="gateway_muse_video", cost=0.0):
        self.calls = []
        self.fail_times = fail_times
        self.tool = tool
        self.cost = cost

    def get_status(self):
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        self.calls.append(inputs)
        if self.fail_times:
            self.fail_times -= 1
            return ToolResult(success=False, error="provider hiccup", cost_usd=self.cost)
        Path(inputs["output_path"]).write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return ToolResult(success=True, data={"selected_tool": self.tool, "selected_provider": "muse"},
                          model="muse-video", cost_usd=self.cost)


def _plan():
    return {
        "version": "1.0",
        "scenes": [
            {"id": "hero", "type": "generated", "description": "Pink clear case on a glass plinth",
             "start_seconds": 0, "end_seconds": 4.2, "shot_intent": "Reveal the silhouette",
             "shot_language": {"shot_size": "close_up", "lens_mm": 85, "camera_movement": "slow_push_in",
                               "lighting_key": "rim_lit"}},
            {"id": "open_lid", "type": "generated", "description": "Lid opens to show the earbuds",
             "start_seconds": 4.2, "end_seconds": 9, "shot_language": {"camera_movement": "static"}},
            {"id": "packshot", "type": "generated", "description": "Case with ring, end card",
             "start_seconds": 9, "end_seconds": 12},
        ],
        "metadata": {"commercial_product": {
            "sku": "Pink clear AirPods Pro case",
            "invariants": ["translucent pink shell", "side hinge knobs", "front status LED"],
            "shots": [
                {"scene_id": "hero", "control": "image_to_video", "start_state": "closed case, three-quarter",
                 "allowed_change": "light sweep only", "reference_view": "top-left closed case"},
                {"scene_id": "open_lid", "control": "first_last_frame", "start_state": "lid closed",
                 "end_state": "lid open, earbuds visible", "allowed_change": "lid rotates open"},
                {"scene_id": "packshot", "control": "packshot_still", "use_provided": "inputs/hero.png"},
            ],
        }},
    }


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nref")
    (tmp_path / "inputs" / "angles.jpg").write_bytes(b"\xff\xd8ref")
    return tmp_path


@pytest.fixture
def img(monkeypatch):
    sel = FakeImageSelector()
    monkeypatch.setattr(StoryboardFrames, "_selector", staticmethod(lambda: sel))
    return sel


def _frames(ws, **extra):
    return StoryboardFrames().execute({"scene_plan": _plan(), "workspace_path": str(ws), **extra})


def _sb(ws):
    return json.loads((ws / "artifacts" / "storyboard.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- frames --
def test_frames_one_per_scene_end_frame_and_provided_packshot(ws, img):
    res = _frames(ws, reference_note="one image with six angles", aspect_ratio="9:16")
    assert res.success, res.error
    sb = _sb(ws)
    validate_artifact("storyboard", sb)
    f = {x["scene_id"]: x for x in sb["frames"]}
    assert [x["scene_id"] for x in sb["frames"]] == ["hero", "open_lid", "packshot"]
    assert f["hero"]["start_frame"]["path"] == "assets/storyboard/hero_start.png"
    assert f["hero"]["end_frame"] is None and f["hero"]["status"] == "draft"
    assert f["open_lid"]["end_frame"]["path"] == "assets/storyboard/open-lid_end.png"
    assert f["packshot"]["start_frame"] == {"path": "assets/storyboard/packshot_start.png", "source": "provided"}
    assert (ws / "assets/storyboard/packshot_start.png").read_bytes().endswith(b"ref")
    assert len(img.calls) == 3  # hero start, open_lid start+end; packshot copied
    assert sb["identity_references"] == ["inputs/angles.jpg", "inputs/hero.png"]
    call = img.calls[0]
    assert call["generation_mode"] == "edit" and len(call["image_paths"]) == 2
    assert call["aspect_ratio"] == "9:16"
    p = call["prompt"]
    assert "six angles" in p and "translucent pink shell" in p and "top-left closed case" in p
    assert "85mm" in p and "No text" in p
    assert "lid open, earbuds visible" in img.calls[2]["prompt"]  # end state for the end frame


def test_frames_falls_back_across_allowed_providers(ws, monkeypatch):
    sel = FakeImageSelector(fail_providers={"geminiweb"})
    monkeypatch.setattr(StoryboardFrames, "_selector", staticmethod(lambda: sel))
    res = _frames(ws, scenes=["hero"], allowed_providers=["geminiweb", "muse"])
    assert res.success and not res.data["failed"]
    assert [c["allowed_providers"] for c in sel.calls] == [["geminiweb"], ["muse"]]
    assert _sb(ws)["frames"][0]["start_frame"]["provider"] == "muse"
    assert res.data["generated"][0]["fallback_errors"] == ["geminiweb: geminiweb down"]


def test_frames_partial_failure_and_all_failed(ws, monkeypatch):
    sel = FakeImageSelector(fail_scenes={"open-lid"})
    monkeypatch.setattr(StoryboardFrames, "_selector", staticmethod(lambda: sel))
    res = _frames(ws)
    assert res.success
    assert res.data["failed"] == [{"scene_id": "open_lid", "frame": "start", "error": "geminiweb down"}]
    assert {f["scene_id"] for f in _sb(ws)["frames"]} == {"hero", "packshot"}

    sel2 = FakeImageSelector(fail_providers={"geminiweb"})
    monkeypatch.setattr(StoryboardFrames, "_selector", staticmethod(lambda: sel2))
    res2 = StoryboardFrames().execute({"scene_plan": _plan(), "workspace_path": str(ws / "other"),
                                       "identity_references": [str(ws / "inputs/hero.png")],
                                       "scenes": ["hero", "open_lid"]})
    assert not res2.success and "geminiweb down" in res2.error


def test_frames_reuse_keeps_status_overwrite_keeps_take(ws, img):
    _frames(ws)
    StoryboardFrames().execute({"workspace_path": str(ws), "approve": "all"})
    assert all(f["status"] == "approved" for f in _sb(ws)["frames"])
    again = _frames(ws)
    assert again.success and len(img.calls) == 3  # nothing regenerated
    assert all(f["status"] == "approved" for f in _sb(ws)["frames"])

    redo = _frames(ws, scenes=["hero"], overwrite=True, notes={"hero": "label is warped"})
    assert redo.success and len(img.calls) == 4
    hero = _sb(ws)["frames"][0]
    assert hero["status"] == "draft" and hero["notes"] == "label is warped"
    assert hero["takes"] == [{"path": "assets/storyboard/takes/hero_start_1.png", "kind": "start_frame",
                              "rejected_reason": "label is warped"}]
    assert "label is warped" in img.calls[-1]["prompt"]
    assert (ws / hero["takes"][0]["path"]).is_file()
    others = {f["scene_id"]: f["status"] for f in _sb(ws)["frames"][1:]}
    assert others == {"open_lid": "approved", "packshot": "approved"}


def test_frames_inputs_validated(ws, img, tmp_path):
    assert not StoryboardFrames().execute({"scene_plan": _plan()}).success
    assert not StoryboardFrames().execute({"workspace_path": str(ws)}).success
    empty = tmp_path / "noinputs"
    empty.mkdir()
    res = StoryboardFrames().execute({"scene_plan": _plan(), "workspace_path": str(empty)})
    assert not res.success and "reference" in res.error
    assert not StoryboardFrames().execute({"workspace_path": str(empty), "approve": ["x"]}).success


def test_frame_prompt_is_self_contained():
    p = frame_prompt({"description": "d"}, {}, {}, which="start")
    assert "reference image" in p and "Aspect ratio 16:9" in p


# --------------------------------------------------------------- animate --
@pytest.fixture
def vid(monkeypatch):
    sel = FakeVideoSelector()
    monkeypatch.setattr(StoryboardAnimate, "_selector", staticmethod(lambda: sel))
    monkeypatch.setattr(StoryboardAnimate, "_tool", staticmethod(lambda name: None))
    return sel


def _ready(ws, img, approve="all"):
    _frames(ws)
    (ws / "artifacts" / "scene_plan.json").write_text(json.dumps(_plan()), encoding="utf-8")
    StoryboardFrames().execute({"workspace_path": str(ws), "approve": approve})


def _animate(ws, **extra):
    return StoryboardAnimate().execute({"workspace_path": str(ws), **extra})


def test_animate_refuses_unapproved_frames(ws, img, vid):
    _ready(ws, img, approve=["hero"])
    res = _animate(ws)
    assert res.success  # hero animated
    assert [c["scene_id"] for c in res.data["clips"]] == ["hero"]
    assert res.data["failed"] == [{"scene_id": "open_lid", "error": "frame not approved (status=draft)"}]
    assert res.data["skipped"][0]["scene_id"] == "packshot"


def test_animate_image_to_video_inputs_and_end_frame_warning(ws, img, vid):
    _ready(ws, img)
    res = _animate(ws, allowed_providers=["muse"])
    assert res.success, res.error
    hero, lid = vid.calls
    assert hero["operation"] == "image_to_video"
    assert hero["reference_image_path"].endswith("assets/storyboard/hero_start.png")
    assert hero["duration"] == 5 and hero["allowed_providers"] == ["muse"]
    assert "last_image_path" not in hero
    assert "slow push in" in hero["prompt"] and "light sweep only" in hero["prompt"]
    assert "Pink clear case" not in hero["prompt"]  # motion only, no product re-description
    assert lid["last_image_path"].endswith("assets/storyboard/open-lid_end.png")
    clips = {c["scene_id"]: c for c in res.data["clips"]}
    assert clips["open_lid"]["used_end_frame"] is False and clips["open_lid"]["warnings"]
    sb = _sb(ws)
    validate_artifact("storyboard", sb)
    assert sb["frames"][0]["clip"]["path"] == "assets/video/hero.mp4"
    assert sb["frames"][2]["clip"] is None  # packshot stays a still


def test_animate_end_frame_used_when_tool_supports_it(ws, img, monkeypatch):
    _ready(ws, img)
    sel = FakeVideoSelector(tool="atlas_video")
    monkeypatch.setattr(StoryboardAnimate, "_selector", staticmethod(lambda: sel))

    class Tool:
        input_schema = {"properties": {"last_image_path": {}}}
        supports = {}

    monkeypatch.setattr(StoryboardAnimate, "_tool", staticmethod(lambda name: Tool()))
    res = _animate(ws, scenes=["open_lid"])
    assert res.data["clips"][0]["used_end_frame"] is True and not res.data["clips"][0]["warnings"]


def test_animate_retry_cap_and_budget(ws, img, monkeypatch):
    _ready(ws, img)
    sel = FakeVideoSelector(fail_times=5, cost=0.5)
    monkeypatch.setattr(StoryboardAnimate, "_selector", staticmethod(lambda: sel))
    monkeypatch.setattr(StoryboardAnimate, "_tool", staticmethod(lambda name: None))
    res = _animate(ws, max_attempts_per_scene=2, budget_usd=1.2)
    assert not res.success
    assert len(sel.calls) == 3  # hero: 2 attempts (1.0 spent); open_lid: 1 attempt hits budget
    errs = {f["scene_id"]: f["error"] for f in res.data["failed"]}
    assert errs["hero"] == "provider hiccup | provider hiccup"
    assert res.cost_usd == 1.5


def test_animate_reuse_and_redo_keeps_take(ws, img, vid):
    _ready(ws, img)
    _animate(ws)
    again = _animate(ws)
    assert len(vid.calls) == 2 and {s["reason"] for s in again.data["skipped"]} >= {"exists"}
    redo = _animate(ws, scenes=["hero"], overwrite=True, notes={"hero": "lid flickers"})
    assert redo.success and len(vid.calls) == 3 and "lid flickers" in vid.calls[-1]["prompt"]
    hero = _sb(ws)["frames"][0]
    assert hero["takes"][-1] == {"path": "assets/video/takes/hero_1.mp4", "kind": "clip",
                                 "rejected_reason": "lid flickers"}


def test_motion_prompt_minimal():
    assert motion_prompt({}, {}, has_end=False).startswith("Camera: slow, steady move.")


def test_registered():
    registry.discover()
    assert registry.get("storyboard_frames") is not None
    assert registry.get("storyboard_animate") is not None


def test_failed_redo_restores_previous_frame(ws, img, monkeypatch):
    _frames(ws, scenes=["hero"])
    before = (ws / "assets/storyboard/hero_start.png").read_bytes()
    down = FakeImageSelector(fail_scenes={"hero"})
    monkeypatch.setattr(StoryboardFrames, "_selector", staticmethod(lambda: down))
    res = _frames(ws, scenes=["hero"], overwrite=True, notes={"hero": "warped"})
    assert not res.success
    hero = _sb(ws)["frames"][0]
    assert (ws / hero["start_frame"]["path"]).read_bytes() == before
    assert "takes" not in hero and not list((ws / "assets/storyboard/takes").glob("*"))
