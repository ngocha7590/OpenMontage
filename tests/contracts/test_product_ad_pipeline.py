"""product-ad pipeline manifest (PLAN-03 §4.4)."""

from pathlib import Path

from lib.checkpoint import _stage_requires_approval, get_pipeline_stages
from lib.pipeline_loader import get_stage_tools, load_pipeline
from tools.tool_registry import registry

REPO = Path(__file__).resolve().parents[2]


def test_stages_and_gates():
    m = load_pipeline("product-ad")
    names = [s["name"] for s in m["stages"]]
    assert names == ["proposal", "script", "scene_plan", "storyboard", "assets", "edit", "compose", "publish"]
    assert get_pipeline_stages("product-ad") == names
    gates = {n: _stage_requires_approval("product-ad", n) for n in names}
    assert gates == {"proposal": True, "script": True, "scene_plan": False, "storyboard": True,
                     "assets": True, "edit": False, "compose": False, "publish": True}


def test_storyboard_before_motion_tools():
    m = load_pipeline("product-ad")
    stages = {s["name"]: s for s in m["stages"]}
    assert stages["storyboard"]["produces"] == ["storyboard"]
    assert "storyboard_frames" in stages["storyboard"]["required_tools"]
    assert "video_selector" not in get_stage_tools(m, "storyboard")
    assert "storyboard_animate" in stages["assets"]["required_tools"]
    assert "storyboard" in stages["assets"]["required_artifacts_in"]
    registry.discover()
    for tool in ("storyboard_frames", "storyboard_animate"):
        assert registry.get(tool) is not None


def test_required_skills_exist():
    m = load_pipeline("product-ad")
    for skill in m["required_skills"]:
        assert (REPO / "skills" / f"{skill}.md").is_file(), skill
    for stage in m["stages"]:
        assert (REPO / "skills" / f"{stage['skill']}.md").is_file(), stage["skill"]


def test_routed_in_agent_guide():
    guide = (REPO / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "| `product-ad` |" in guide and "storyboard_animate" in guide
