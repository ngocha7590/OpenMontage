"""character_reference_sheet: one reference image per character at the
character_design stage, via image_selector (faked here — no provider calls)."""

import json
from pathlib import Path

import pytest

from lib.pipeline_loader import load_pipeline
from schemas.artifacts import validate_artifact
from tools.base_tool import ToolResult, ToolStatus
from tools.character.character_animation import CharacterReferenceSheet, CharacterSpecGenerator
from tools.tool_registry import registry


class FakeSelector:
    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    def get_status(self):
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        self.calls.append(inputs)
        if any(cid in inputs["output_path"] for cid in self.fail_for):
            return ToolResult(success=False, error="provider down")
        Path(inputs["output_path"]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return ToolResult(success=True, data={"selected_tool": "gateway_geminiweb_image",
                                              "selected_provider": "geminiweb"},
                          cost_usd=0.0, model="gemini-web")


def _design():
    return CharacterSpecGenerator().execute({
        "characters": [
            {"id": "baby", "display_name": "Bé Bo", "role": "toddler", "silhouette_notes": "round head",
             "required_views": ["front"], "props": ["pacifier"]},
            {"id": "dad", "display_name": "Bố", "role": "dad", "required_views": ["front", "side"]},
        ],
        "style": {"visual_style": "flat pastel cartoon", "palette": ["#F5CBA7", "#6EC6FF"]},
    }).data["character_design"]


@pytest.fixture
def fake(monkeypatch):
    sel = FakeSelector()
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: sel))
    return sel


def test_generates_one_image_per_character_and_records_it(tmp_path, fake):
    out = tmp_path / "artifacts" / "character_design.json"
    res = CharacterReferenceSheet().execute({
        "character_design": _design(), "workspace_path": str(tmp_path),
        "output_path": str(out), "allowed_providers": ["geminiweb", "muse"],
    })
    assert res.success, res.error
    design = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("character_design", design)
    refs = {c["id"]: c["reference_images"] for c in design["characters"]}
    assert refs["baby"][0]["path"] == "assets/characters/baby_front.png"
    assert refs["dad"][0]["path"] == "assets/characters/dad_front.png"  # first required view only
    assert refs["baby"][0]["provider"] == "geminiweb" and refs["baby"][0]["model"] == "gemini-web"
    assert (tmp_path / "assets/characters/baby_front.png").is_file()
    prompt = fake.calls[0]["prompt"]
    assert "Bé Bo" in prompt and "round head" in prompt and "pacifier" in prompt
    assert "flat pastel cartoon" in prompt and "#F5CBA7" in prompt and "front view" in prompt
    assert all(c["allowed_providers"] == ["geminiweb"] for c in fake.calls)  # first provider worked
    assert all(c["aspect_ratio"] == "1:1" for c in fake.calls)


def test_existing_images_are_reused_unless_overwrite(tmp_path, fake):
    design = _design()
    first = CharacterReferenceSheet().execute({"character_design": design, "workspace_path": str(tmp_path)})
    assert len(fake.calls) == 2
    again = CharacterReferenceSheet().execute({"character_design": first.data["character_design"],
                                               "workspace_path": str(tmp_path)})
    assert again.success and len(fake.calls) == 2  # nothing regenerated
    assert all(g.get("skipped") == "exists" for g in again.data["generated"])
    redo = CharacterReferenceSheet().execute({"character_design": again.data["character_design"],
                                              "workspace_path": str(tmp_path),
                                              "characters": ["dad"], "overwrite": True})
    assert redo.success and len(fake.calls) == 3 and "dad_front" in fake.calls[-1]["output_path"]
    dad = next(c for c in redo.data["character_design"]["characters"] if c["id"] == "dad")
    assert len(dad["reference_images"]) == 1  # replaced, not duplicated


def test_partial_failure_keeps_going_and_reports(tmp_path, monkeypatch):
    sel = FakeSelector(fail_for={"dad"})
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: sel))
    res = CharacterReferenceSheet().execute({"character_design": _design(), "workspace_path": str(tmp_path)})
    assert res.success
    assert res.data["failed"] == [{"character": "dad", "view": "front", "error": "provider down"}]  # no allowed list → one try
    dad = next(c for c in res.data["character_design"]["characters"] if c["id"] == "dad")
    assert "reference_images" not in dad


def test_all_failed_is_not_success(tmp_path, monkeypatch):
    sel = FakeSelector(fail_for={"baby", "dad"})
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: sel))
    res = CharacterReferenceSheet().execute({"character_design": _design(), "workspace_path": str(tmp_path)})
    assert not res.success and "provider down" in res.error


def test_inputs_are_validated(tmp_path, fake):
    assert not CharacterReferenceSheet().execute({"workspace_path": str(tmp_path)}).success
    assert not CharacterReferenceSheet().execute({"character_design": _design()}).success


def test_status_follows_image_selector(monkeypatch):
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: None))
    assert CharacterReferenceSheet().get_status() == ToolStatus.UNAVAILABLE


def test_registered_and_listed_for_character_design():
    registry.discover()
    assert registry.get("character_reference_sheet") is not None
    manifest = load_pipeline("character-animation")
    stage = next(s for s in manifest["stages"] if s["name"] == "character_design")
    assert "character_reference_sheet" in stage["tools_available"]


def test_falls_back_to_next_allowed_provider(tmp_path, monkeypatch):
    class FlakySelector(FakeSelector):
        def execute(self, inputs):
            self.calls.append(inputs)
            if inputs.get("allowed_providers") == ["geminiweb"]:
                return ToolResult(success=False, error="geminiweb HTTP 401")
            Path(inputs["output_path"]).write_bytes(b"\x89PNG")
            return ToolResult(success=True, data={"selected_provider": "muse", "selected_tool": "gateway_muse_image"})

    sel = FlakySelector()
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: sel))
    res = CharacterReferenceSheet().execute({"character_design": _design(), "workspace_path": str(tmp_path),
                                             "allowed_providers": ["geminiweb", "muse"]})
    assert res.success and not res.data["failed"]
    assert [c["allowed_providers"] for c in sel.calls] == [["geminiweb"], ["muse"], ["geminiweb"], ["muse"]]
    baby = next(c for c in res.data["character_design"]["characters"] if c["id"] == "baby")
    assert baby["reference_images"][0]["provider"] == "muse"


def test_every_allowed_provider_failing_lists_each_error(tmp_path, monkeypatch):
    sel = FakeSelector(fail_for={"baby", "dad"})
    monkeypatch.setattr(CharacterReferenceSheet, "_selector", staticmethod(lambda: sel))
    res = CharacterReferenceSheet().execute({"character_design": _design(), "workspace_path": str(tmp_path),
                                             "allowed_providers": ["geminiweb", "muse"]})
    assert not res.success
    assert res.data["failed"][0]["error"] == "geminiweb: provider down | muse: provider down"
