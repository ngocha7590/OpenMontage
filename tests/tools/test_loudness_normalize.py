import shutil
import subprocess

import pytest

from lib.loudness import measure, normalize_file
from tools.base_tool import ToolResult
from tools.video.video_compose import VideoCompose

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _make(path, volume, with_audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           "color=c=blue:s=160x90:d=6:r=25"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
                "-af", f"volume={volume}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)


@pytest.mark.parametrize("volume", [1.0, 0.02])
def test_normalize_lands_near_target(tmp_path, volume):
    f = tmp_path / "v.mp4"
    _make(f, volume)
    rep = normalize_file(f)
    assert rep["applied"]
    assert abs(rep["output_i"] - (-16.0)) <= 1.0
    assert rep["true_peak"] <= -1.0
    assert rep["input_i"] != rep["output_i"]
    # independent re-measure of the delivered file
    assert abs(float(measure(f)["input_i"]) - (-16.0)) <= 1.0
    # video stream copied
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(f)], text=True)
    assert "h264" in out


def test_no_audio_is_skipped(tmp_path):
    f = tmp_path / "silent.mp4"
    _make(f, 1.0, with_audio=False)
    rep = normalize_file(f)
    assert rep["applied"] is False and rep["reason"] == "no audio stream"


def test_video_compose_hook_and_opt_out(tmp_path):
    f = tmp_path / "v.mp4"
    _make(f, 1.0)
    tool = VideoCompose()
    res = ToolResult(success=True, data={"output": str(f)}, artifacts=[str(f)])
    tool._apply_loudness(res, {"normalize_loudness": False})
    assert "loudness" not in res.data
    tool._apply_loudness(res, {})
    assert abs(res.data["loudness"]["output_i"] + 16) <= 1.0
