"""Tests for the export_bundle publisher tool.

Covers the tool contract, registry discovery, the export bundle layout, a
schema-valid publish_log, chapter formatting, and the missing-video error path.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.publishers.export_bundle import ExportBundle
from tools.base_tool import ToolStatus, ToolTier
from tools.tool_registry import ToolRegistry
from schemas.artifacts import validate_artifact


def _make_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42fakevideo")


def test_contract_metadata():
    tool = ExportBundle()
    info = tool.get_info()
    assert info["name"] == "export_bundle"
    assert info["capability"] == "publish"
    assert info["tier"] == ToolTier.PUBLISH.value
    assert info["provider"] == "local"
    assert info["resource_profile"]["network_required"] is False
    assert tool.get_status() == ToolStatus.AVAILABLE
    assert tool.estimate_cost({}) == 0.0


def test_missing_video_errors(tmp_path):
    result = ExportBundle().execute(
        {"video_path": str(tmp_path / "nope.mp4"), "title": "X"}
    )
    assert result.success is False
    assert "not found" in (result.error or "")


def test_export_bundle_layout_and_publish_log(tmp_path):
    video = tmp_path / "projects" / "demo" / "renders" / "final.mp4"
    _make_video(video)
    subs = tmp_path / "subs.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    result = ExportBundle().execute(
        {
            "video_path": str(video),
            "title": "Vector Databases Explained in 60 Seconds",
            "export_dir": str(tmp_path / "out"),
            "description": "A quick explainer.",
            "tags": ["vector db", "explainer"],
            "hashtags": ["#ai", "#database"],
            "chapters": [
                {"start_seconds": 0, "title": "Intro"},
                {"start_seconds": 75, "title": "How it works"},
                {"start_seconds": 120, "title": "Wrap up"},
            ],
            "subtitles_path": str(subs),
            "thumbnail_concept": {"text_overlay": "100x FASTER"},
            "platform": "youtube",
            "visibility": "unlisted",
            "timestamp": "2026-06-29T10:30:00+00:00",
        }
    )
    assert result.success is True
    root = Path(result.data["export_path"])

    # Layout
    assert (root / "video" / "output.mp4").is_file()
    assert (root / "video" / "subtitles.srt").is_file()
    assert (root / "metadata" / "metadata.json").is_file()
    assert (root / "metadata" / "description.txt").is_file()
    assert (root / "metadata" / "tags.txt").is_file()
    assert (root / "metadata" / "chapters.txt").is_file()
    assert (root / "thumbnails" / "concept.json").is_file()

    # tags one-per-line
    assert (root / "metadata" / "tags.txt").read_text().splitlines() == ["vector db", "explainer"]
    # chapter formatting (75s -> 1:15)
    assert "1:15 - How it works" in (root / "metadata" / "chapters.txt").read_text()

    # publish_log is schema-valid and shaped right
    plog = result.data["publish_log"]
    validate_artifact("publish_log", plog)
    entry = plog["entries"][0]
    assert entry["status"] == "exported"
    assert entry["platform"] == "youtube"
    assert entry["visibility"] == "unlisted"
    assert entry["export_path"] == str(root)
    assert entry["metadata_used"]["title"].startswith("Vector Databases")


def test_chapter_time_formatting_hours(tmp_path):
    video = tmp_path / "p" / "renders" / "final.mp4"
    _make_video(video)
    result = ExportBundle().execute(
        {
            "video_path": str(video),
            "title": "Long",
            "export_dir": str(tmp_path / "out"),
            "chapters": [
                {"time_seconds": 0, "label": "Start"},
                {"time_seconds": 60, "label": "Middle"},
                {"time_seconds": 3725, "label": "Deep dive"},  # 1:02:05
            ],
        }
    )
    assert result.success is True
    txt = (Path(result.data["export_path"]) / "metadata" / "chapters.txt").read_text()
    assert "1:02:05 - Deep dive" in txt


def test_infer_project_name(tmp_path):
    video = tmp_path / "projects" / "my-cool-video" / "renders" / "final.mp4"
    _make_video(video)
    result = ExportBundle().execute(
        {"video_path": str(video), "title": "T", "export_dir": str(tmp_path / "out")}
    )
    # export still works; project name inference exercised via no-export_dir path below
    assert result.success is True


def test_missing_optional_asset_errors(tmp_path):
    video = tmp_path / "p" / "renders" / "final.mp4"
    _make_video(video)
    for key in ("subtitles_path", "thumbnail_path"):
        result = ExportBundle().execute(
            {
                "video_path": str(video),
                "title": "T",
                "export_dir": str(tmp_path / "out"),
                key: str(tmp_path / "does_not_exist.x"),
            }
        )
        assert result.success is False, key
        assert key in (result.error or "")


def test_default_export_dir_inside_project_workspace(tmp_path):
    # projects/<name>/renders/final.mp4 -> projects/<name>/exports (no export_dir given)
    video = tmp_path / "projects" / "demo" / "renders" / "final.mp4"
    _make_video(video)
    result = ExportBundle().execute({"video_path": str(video), "title": "T"})
    assert result.success is True
    assert Path(result.data["export_path"]) == (tmp_path / "projects" / "demo" / "exports").resolve()


def test_registry_discovers_export_bundle():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("export_bundle") is not None
    assert reg.get_by_capability("publish")[0].name == "export_bundle"


# ---- youtube.json + warnings ----

def _export(tmp_path, **kw):
    video = tmp_path / "p" / "renders" / "final.mp4"
    _make_video(video)
    args = {"video_path": str(video), "title": "T", "export_dir": str(tmp_path / "out")}
    args.update(kw)
    result = ExportBundle().execute(args)
    assert result.success is True, result.error
    root = Path(result.data["export_path"])
    return result, json.loads((root / "metadata" / "youtube.json").read_text(encoding="utf-8")), root


GOOD_CHAPTERS = [
    {"start_seconds": 0, "title": "A"},
    {"start_seconds": 30, "title": "B"},
    {"start_seconds": 70, "title": "C"},
]


def _png(path, size=(64, 36)):
    from PIL import Image

    Image.new("RGB", size, (10, 20, 30)).save(path)
    return str(path)


def test_youtube_json_shape(tmp_path):
    thumb = _png(tmp_path / "t.png")
    result, yt, root = _export(
        tmp_path, title="Vì sao pin chai?", description="Mô tả", tags=["pin", "li ion"],
        hashtags=["#pin"], chapters=GOOD_CHAPTERS, thumbnail_path=thumb,
        default_language="vi", thumbnail_concept={"text_overlay": "x"},
    )
    assert set(yt) == {"snippet", "status", "hashtags", "chapters", "thumbnail", "warnings"}
    assert yt["snippet"] == {
        "title": "Vì sao pin chai?",
        "description": "Mô tả\n\n0:00 - A\n0:30 - B\n1:10 - C",
        "tags": ["pin", "li ion"], "categoryId": "27",
        "defaultLanguage": "vi", "defaultAudioLanguage": "vi",
    }
    assert yt["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    assert yt["chapters"][1] == {"start_seconds": 30.0, "title": "B"}
    assert yt["thumbnail"] == "thumbnails/thumbnail.png"
    assert yt["warnings"] == [] and result.data["warnings"] == []
    assert (root / "thumbnails" / "concept.json").is_file()  # written even with an image
    entry = result.data["publish_log"]["entries"][0]
    validate_artifact("publish_log", result.data["publish_log"])
    mu = entry["metadata_used"]
    assert mu["tags"] == ["pin", "li ion"] and mu["thumbnail_path"] == "thumbnails/thumbnail.png"
    assert mu["category_id"] == "27" and mu["default_language"] == "vi"
    assert entry["visibility"] == "private"


def test_short_chapters_merge(tmp_path):
    chapters = [
        {"start_seconds": 0, "title": "A"},
        {"start_seconds": 5, "title": "tiny"},
        {"start_seconds": 40, "title": "B"},
        {"start_seconds": 80, "title": "C"},
    ]
    _, yt, _ = _export(tmp_path, chapters=chapters, thumbnail_path=_png(tmp_path / "t.png"))
    assert [c["title"] for c in yt["chapters"]] == ["A", "B", "C"]
    assert "chapters_invalid" not in yt["warnings"]


def test_too_few_chapters_dropped(tmp_path):
    result, yt, root = _export(
        tmp_path, description="D",
        chapters=[{"start_seconds": 0, "title": "A"}, {"start_seconds": 60, "title": "B"}],
    )
    assert "chapters_invalid" in yt["warnings"]
    assert yt["chapters"] == []
    assert yt["snippet"]["description"] == "D"
    assert "0:00" not in (root / "metadata" / "description.txt").read_text()


def test_first_chapter_not_zero_warns(tmp_path):
    chapters = [{"start_seconds": s, "title": "x"} for s in (5, 30, 70)]
    _, yt, _ = _export(tmp_path, chapters=chapters)
    assert "chapters_invalid" in yt["warnings"]


def test_title_and_description_warnings(tmp_path):
    _, yt, _ = _export(tmp_path, title="x" * 71)
    assert "title_long" in yt["warnings"] and "title_too_long" not in yt["warnings"]
    _, yt, _ = _export(tmp_path, title="x" * 101)
    assert "title_too_long" in yt["warnings"] and "title_long" not in yt["warnings"]
    _, yt, _ = _export(tmp_path, description="d" * 5001)
    assert "description_too_long" in yt["warnings"]
    _, yt, _ = _export(tmp_path, title="a < b")
    assert "angle_brackets" in yt["warnings"]
    _, yt, _ = _export(tmp_path, description="a > b")
    assert "angle_brackets" in yt["warnings"]


def test_tags_too_long(tmp_path):
    ok = ["a" * 49] * 10  # 490 + 9 commas = 499
    _, yt, _ = _export(tmp_path, tags=ok)
    assert "tags_too_long" not in yt["warnings"]
    _, yt, _ = _export(tmp_path, tags=["a b" * 20] * 8)  # (60+2)*8 + 7 > 500
    assert "tags_too_long" in yt["warnings"]


def test_thumbnail_warnings(tmp_path):
    _, yt, _ = _export(tmp_path)
    assert "thumbnail_missing" in yt["warnings"] and yt["thumbnail"] is None
    big = tmp_path / "big.png"
    big.write_bytes(b"\0" * (2 * 1024 * 1024 + 1))
    _, yt, _ = _export(tmp_path, thumbnail_path=str(big))
    assert "thumbnail_too_large" in yt["warnings"]
