"""Auto-reframe tool for aspect ratio conversion with face tracking.

Converts video between aspect ratios (e.g. 16:9 → 9:16 for Instagram Reels)
while keeping the speaker's face centered in frame. Uses face_tracker data
for smooth, content-aware cropping.

Primary use: converting talking-head footage shot in landscape to vertical
format for social media (TikTok, Reels, Shorts).

Strategies (input ``strategy``): ``face`` (talking heads), ``center``,
``fit_blur`` (full-width fit over blurred background) and ``content_aware``
(per-shot content bbox -> centred crop if it fits, else fit_blur; for graphics,
slides, screen content). ``auto`` = face if faces found, else content_aware.
See tools/video/_content_reframe.py.

Face approach: MediaPipe/OpenCV face detection → smoothed bounding box trajectory
→ FFmpeg crop filter. No GPU required.
"""

from __future__ import annotations

import json
from fractions import Fraction
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ResumeSupport,
    ToolResult,
    ToolStability,
    ToolTier,
)


# Common target aspect ratios
ASPECT_PRESETS = {
    "portrait": (9, 16),       # Instagram Reels, TikTok, YouTube Shorts
    "square": (1, 1),          # Instagram Feed
    "landscape": (16, 9),      # YouTube, LinkedIn
    "cinematic": (21, 9),      # Ultra-wide
    "vertical_4_5": (4, 5),    # Instagram portrait post
}


class AutoReframe(BaseTool):
    name = "auto_reframe"
    version = "0.2.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg"]
    install_instructions = (
        "FFmpeg is required. For face-tracked reframing, also install:\n"
        "pip install mediapipe opencv-python\n\n"
        "Without MediaPipe/OpenCV, falls back to center-crop."
    )
    agent_skills = ["ffmpeg"]

    capabilities = [
        "aspect_ratio_conversion",
        "face_tracked_crop",
        "smart_reframe",
        "center_crop",
        "content_aware_crop",
        "fit_blur",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "output_path": {"type": "string"},
            "target_aspect": {
                "type": "string",
                "enum": list(ASPECT_PRESETS.keys()),
                "default": "portrait",
                "description": "Target aspect ratio preset",
            },
            "target_width": {
                "type": "integer",
                "description": "Explicit target width (overrides preset)",
            },
            "target_height": {
                "type": "integer",
                "description": "Explicit target height (overrides preset)",
            },
            "face_tracking_json": {
                "type": "string",
                "description": "Path to pre-computed face_tracker JSON. If omitted, runs face detection internally.",
            },
            "smoothing_window": {
                "type": "integer",
                "default": 15,
                "minimum": 1,
                "description": "Number of frames for position smoothing (higher = smoother pan, lower = more responsive)",
            },
            "face_padding": {
                "type": "number",
                "default": 0.4,
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Extra space around face as fraction of face size (0.4 = 40% padding)",
            },
            "sample_fps": {
                "type": "number",
                "default": 5,
                "description": "Face detection sample rate (only used if no face_tracking_json)",
            },
            "strategy": {
                "type": "string",
                "enum": ["auto", "face", "center", "fit_blur", "content_aware"],
                "default": "auto",
                "description": (
                    "auto: faces found -> face, otherwise content_aware. "
                    "face: face-tracked crop (talking heads). "
                    "center: fixed centre crop. "
                    "fit_blur: whole frame scaled to target width over a blurred, darkened copy of itself. "
                    "content_aware: per shot, crop window centred on the measured content bbox when it fits "
                    "the target window, else fit_blur. Use for graphics, slides, screen content, wide shots. "
                    "Never use center on content with on-screen text/numbers."
                ),
            },
            "fit_scale": {
                "type": "number", "default": 1.0, "minimum": 1.0, "maximum": 1.3,
                "description": "fit_blur: 1.0 = full width; 1.0-1.15 = slight zoom for narrow-margin content",
            },
            "fit_y": {
                "type": "number", "default": 0.5, "minimum": 0.0, "maximum": 1.0,
                "description": "fit_blur: vertical position of the fitted frame in the free space (0 top, 0.5 centred, 1 bottom). "
                               "Use ~0.35 to leave room for captions below.",
            },
            "scene_threshold": {"type": "number", "default": 0.3, "description": "content_aware: ffmpeg scene score for shot cuts"},
            "content_margin": {"type": "number", "default": 0.025, "description": "content_aware: margin each side of the bbox, fraction of source width"},
            "codec": {"type": "string", "default": "libx264"},
            "crf": {"type": "integer", "default": 18},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=4000, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["FFmpeg error"])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = [
        "input_path", "target_aspect", "target_width", "target_height",
        "smoothing_window", "face_padding", "face_tracking_json", "sample_fps",
        "codec", "crf", "strategy", "fit_scale", "fit_y", "scene_threshold", "content_margin",
    ]
    side_effects = ["writes reframed video to output_path"]
    user_visible_verification = [
        "Play reframed output — verify face stays centered and framing is smooth",
        "Check that no important content is cropped out",
        "content_aware/fit_blur: make a contact sheet; no text/numbers cut, no empty frames; read <output>.reframe.json",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"])
        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        start = time.time()

        # Get source video dimensions
        src_w, src_h, src_fps, src_sar = self._get_video_info(input_path)
        if src_w == 0 or src_h == 0:
            return ToolResult(success=False, error="Could not read video dimensions")

        # Determine target crop dimensions (in source pixel space)
        target_w, target_h = self._compute_crop_size(inputs, src_w, src_h, src_sar)

        # Matching aspect can still require an explicitly requested resize.
        out_w, out_h = self._compute_output_resolution(inputs, target_w, target_h, src_w, src_h)
        needs_crop = target_w != src_w or target_h != src_h
        explicit_size = "target_width" in inputs and "target_height" in inputs
        if (
            not needs_crop
            and src_sar == 1.0
            and (not explicit_size or (out_w, out_h) == (src_w, src_h))
        ):
            return ToolResult(
                success=True,
                data={"message": "Source already matches target aspect ratio", "output": str(input_path)},
                artifacts=[str(input_path)],
            )

        # Choose strategy
        strategy = inputs.get("strategy") or "auto"
        width_crop = target_h == src_h and target_w < src_w  # 16:9 -> narrower
        if strategy not in ("auto", "face", "center", "fit_blur", "content_aware"):
            return ToolResult(success=False, error=f"Unknown strategy: {strategy}")
        if strategy in ("fit_blur", "content_aware") and not width_crop:
            strategy = "center"  # these only make sense when narrowing the frame
        face_data = []
        if strategy in ("auto", "face") and needs_crop:
            face_data = self._get_face_data(inputs, input_path, src_fps)
        if strategy == "auto":
            strategy = "face" if face_data else ("content_aware" if width_crop else "center")

        if strategy in ("fit_blur", "content_aware"):
            return self._execute_content(inputs, input_path, strategy, src_w, src_h, target_w, target_h,
                                         out_w, out_h, start)

        # Compute per-frame crop positions
        if face_data and len(face_data) > 0:
            crop_x, crop_y = self._compute_face_tracked_crop(
                face_data, src_w, src_h, target_w, target_h,
                src_fps,
                inputs.get("smoothing_window", 15),
                inputs.get("face_padding", 0.4),
            )
            method = "face_tracked"
        else:
            # Fallback: center crop
            crop_x = (src_w - target_w) // 2
            crop_y = (src_h - target_h) // 2
            method = "center_crop"

        # Build output path
        aspect_name = inputs.get("target_aspect", "portrait")
        output_path = Path(
            inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_{aspect_name}")))
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Render via FFmpeg
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 18)

        if method == "face_tracked" and isinstance(crop_x, list):
            # Dynamic crop: write crop coordinates to a file and use sendcmd
            result = self._render_dynamic_crop(
                input_path, output_path, crop_x, crop_y,
                target_w, target_h, out_w, out_h,
                src_fps, codec, crf,
            )
        else:
            # Static crop
            result = self._render_static_crop(
                input_path, output_path,
                crop_x, crop_y, target_w, target_h,
                out_w, out_h, codec, crf,
            )

        if not result.success:
            return result

        elapsed = time.time() - start

        return ToolResult(
            success=True,
            data={
                "strategy": "face" if method == "face_tracked" else "center",
                "input": str(input_path),
                "output": str(output_path),
                "source_resolution": f"{src_w}x{src_h}",
                "crop_resolution": f"{target_w}x{target_h}",
                "output_resolution": f"{out_w}x{out_h}",
                "method": method,
                "target_aspect": inputs.get("target_aspect", "portrait"),
            },
            artifacts=[str(output_path)],
            duration_seconds=round(elapsed, 2),
        )

    def _execute_content(
        self, inputs: dict[str, Any], input_path: Path, strategy: str,
        src_w: int, src_h: int, crop_w: int, crop_h: int, out_w: int, out_h: int, start: float,
    ) -> ToolResult:
        from tools.video import _content_reframe as cr

        aspect_name = inputs.get("target_aspect", "portrait")
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_{aspect_name}"))))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = self._probe_duration(input_path)
        if duration <= 0:
            return ToolResult(success=False, error="Could not read video duration")

        if strategy == "content_aware":
            shots = cr.detect_shots(str(input_path), duration, inputs.get("scene_threshold", cr.SCENE_THRESHOLD))
            frames, fps = cr.sample_gray_frames(str(input_path), duration)
            if len(frames) == 0:
                return ToolResult(success=False, error="Could not decode frames for content analysis")
            decisions = cr.analyse_shots(frames, fps, shots, duration, src_w, crop_w,
                                         inputs.get("content_margin", cr.CONTENT_MARGIN))
        else:
            decisions = cr.whole_video_decisions(strategy, duration, src_w, crop_w)

        fit_scale = float(inputs.get("fit_scale", 1.0))
        fit_y = float(inputs.get("fit_y", 0.5))
        graph = cr.build_filter_graph(decisions, crop_w, crop_h, out_w, out_h, fit_scale, fit_y)
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-i", str(input_path),
            "-filter_complex", graph,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", inputs.get("codec", "libx264"), "-crf", str(inputs.get("crf", 18)), "-preset", "fast",
            "-c:a", "copy", str(output_path),
        ]
        try:
            self.run_command(cmd, timeout=1800)
        except Exception as e:
            return ToolResult(success=False, error=f"FFmpeg render failed: {e}")

        report = {
            "strategy": strategy, "source": f"{src_w}x{src_h}", "output": f"{out_w}x{out_h}",
            "window_px": crop_w, "fit_scale": fit_scale, "fit_y": fit_y, "shots": decisions,
        }
        report_path = output_path.with_suffix(".reframe.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return ToolResult(
            success=True,
            data={
                "input": str(input_path), "output": str(output_path),
                "source_resolution": f"{src_w}x{src_h}", "crop_resolution": f"{crop_w}x{crop_h}",
                "output_resolution": f"{out_w}x{out_h}", "method": strategy, "strategy": strategy,
                "target_aspect": aspect_name, "report": str(report_path), "shots": decisions,
            },
            artifacts=[str(output_path), str(report_path)],
            duration_seconds=round(time.time() - start, 2),
        )

    def _probe_duration(self, path: Path) -> float:
        try:
            r = self.run_command(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                                  "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
            return float(r.stdout.strip().splitlines()[0])
        except Exception:
            return 0.0

    def _get_video_info(self, path: Path) -> tuple[int, int, float, float]:
        """Get encoded dimensions, FPS, and sample aspect ratio via ffprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,sample_aspect_ratio",
            "-of", "json", str(path),
        ]
        try:
            result = self.run_command(cmd)
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            w = int(stream["width"])
            h = int(stream["height"])
            # Parse r_frame_rate (e.g. "30000/1001")
            fps_parts = stream["r_frame_rate"].split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
            # Unspecified sample aspect ratios are commonly reported as N/A
            # or 0:1; retain the existing square-pixel interpretation for them.
            try:
                sar = float(Fraction(stream.get("sample_aspect_ratio", "1:1").replace(":", "/")))
            except (ValueError, ZeroDivisionError):
                sar = 1.0
            if sar <= 0:
                sar = 1.0
            return w, h, fps, sar
        except Exception:
            return 0, 0, 30.0, 1.0

    def _compute_crop_size(
        self, inputs: dict[str, Any], src_w: int, src_h: int, src_sar: float = 1.0
    ) -> tuple[int, int]:
        """Compute crop dimensions in source pixel space that match the target aspect ratio."""
        if "target_width" in inputs and "target_height" in inputs:
            # Explicit dimensions — compute crop in source space matching this ratio
            tw, th = inputs["target_width"], inputs["target_height"]
        else:
            aspect_name = inputs.get("target_aspect", "portrait")
            tw, th = ASPECT_PRESETS.get(aspect_name, (9, 16))

        # Face positions and crop dimensions are encoded-pixel coordinates;
        # convert the requested display ratio into that coordinate space.
        target_ratio = (tw / th) / src_sar
        src_ratio = src_w / src_h

        if target_ratio > src_ratio:
            # Target is wider — crop height
            crop_w = src_w
            crop_h = int(src_w / target_ratio)
        else:
            # Target is taller/narrower — crop width
            crop_h = src_h
            crop_w = int(src_h * target_ratio)

        # Ensure even dimensions (required by most codecs)
        crop_w = crop_w - (crop_w % 2)
        crop_h = crop_h - (crop_h % 2)

        return crop_w, crop_h

    def _compute_output_resolution(
        self, inputs: dict[str, Any],
        crop_w: int, crop_h: int,
        src_w: int, src_h: int,
    ) -> tuple[int, int]:
        """Determine final output resolution. Scales to standard sizes."""
        if "target_width" in inputs and "target_height" in inputs:
            out_w = inputs["target_width"]
            out_h = inputs["target_height"]
        else:
            aspect_name = inputs.get("target_aspect", "portrait")
            if aspect_name == "portrait":
                out_w, out_h = 1080, 1920
            elif aspect_name == "square":
                out_w, out_h = 1080, 1080
            elif aspect_name == "landscape":
                out_w, out_h = 1920, 1080
            elif aspect_name == "cinematic":
                out_w, out_h = 2560, 1080
            elif aspect_name == "vertical_4_5":
                out_w, out_h = 1080, 1350
            else:
                out_w, out_h = crop_w, crop_h

        # Ensure even
        out_w = out_w - (out_w % 2)
        out_h = out_h - (out_h % 2)
        return out_w, out_h

    def _get_face_data(
        self, inputs: dict[str, Any], input_path: Path, src_fps: float
    ) -> list[dict]:
        """Get face tracking data — from pre-computed JSON or by running detection."""
        # Check for pre-computed tracking data
        tracking_json = inputs.get("face_tracking_json")
        if tracking_json:
            p = Path(tracking_json)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("faces", [])

        # Try to run face_tracker internally
        try:
            from tools.analysis.face_tracker import FaceTracker
            tracker = FaceTracker()
            if tracker.get_status().name == "UNAVAILABLE":
                return []
            sample_fps = inputs.get("sample_fps", 5)
            result = tracker.execute({
                "input_path": str(input_path),
                "sample_fps": sample_fps,
            })
            if result.success and result.data:
                # Read the generated JSON
                output_file = result.data.get("output")
                if output_file:
                    data = json.loads(Path(output_file).read_text(encoding="utf-8"))
                    return data.get("faces", [])
        except Exception:
            pass

        return []

    def _compute_face_tracked_crop(
        self,
        faces: list[dict],
        src_w: int, src_h: int,
        crop_w: int, crop_h: int,
        fps: float,
        smoothing_window: int,
        face_padding: float,
    ) -> tuple[list[int], list[int]]:
        """Compute smoothed crop positions from face tracking data.

        Returns a single (x, y) if face positions are stable enough,
        or lists of per-frame positions for dynamic cropping.
        """
        if not faces:
            cx = (src_w - crop_w) // 2
            cy = (src_h - crop_h) // 2
            return cx, cy

        # Convert relative bbox centers to pixel positions
        face_centers_x = []
        face_centers_y = []
        face_timestamps = []

        for f in faces:
            bbox = f["bbox"]
            # Center of face in pixel space
            center_x = (bbox["x"] + bbox["width"] / 2) * src_w
            center_y = (bbox["y"] + bbox["height"] / 2) * src_h
            face_centers_x.append(center_x)
            face_centers_y.append(center_y)
            face_timestamps.append(f["timestamp_seconds"])

        # Check if face position is stable (talking head usually is)
        x_range = max(face_centers_x) - min(face_centers_x)
        y_range = max(face_centers_y) - min(face_centers_y)

        # If face barely moves (<10% of frame), use a single static crop
        if x_range < src_w * 0.10 and y_range < src_h * 0.10:
            avg_x = sum(face_centers_x) / len(face_centers_x)
            avg_y = sum(face_centers_y) / len(face_centers_y)

            # Position crop window centered on face, with bias toward upper third
            crop_x = int(avg_x - crop_w / 2)
            crop_y = int(avg_y - crop_h * 0.35)  # Face in upper 35% of frame

            # Clamp to frame bounds
            crop_x = max(0, min(crop_x, src_w - crop_w))
            crop_y = max(0, min(crop_y, src_h - crop_h))

            return crop_x, crop_y

        # Dynamic crop: smooth the trajectory
        smoothed_x = self._smooth_positions(face_centers_x, smoothing_window)
        smoothed_y = self._smooth_positions(face_centers_y, smoothing_window)

        # Convert to crop positions (top-left corner), clamped
        crop_xs = []
        crop_ys = []
        for sx, sy in zip(smoothed_x, smoothed_y):
            cx = int(sx - crop_w / 2)
            cy = int(sy - crop_h * 0.35)
            cx = max(0, min(cx, src_w - crop_w))
            cy = max(0, min(cy, src_h - crop_h))
            crop_xs.append(cx)
            crop_ys.append(cy)

        return crop_xs, crop_ys

    def _smooth_positions(self, values: list[float], window: int) -> list[float]:
        """Simple moving average smoothing."""
        smoothed = []
        for i in range(len(values)):
            start = max(0, i - window // 2)
            end = min(len(values), i + window // 2 + 1)
            smoothed.append(sum(values[start:end]) / (end - start))
        return smoothed

    def _render_static_crop(
        self,
        input_path: Path, output_path: Path,
        crop_x: int, crop_y: int,
        crop_w: int, crop_h: int,
        out_w: int, out_h: int,
        codec: str, crf: int,
    ) -> ToolResult:
        """Render with a static crop position."""
        vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={out_w}:{out_h},setsar=1"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", codec, "-crf", str(crf), "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]

        try:
            self.run_command(cmd, timeout=600)
        except Exception as e:
            return ToolResult(success=False, error=f"FFmpeg render failed: {e}")

        return ToolResult(success=True)

    def _render_dynamic_crop(
        self,
        input_path: Path, output_path: Path,
        crop_xs: list[int], crop_ys: list[int],
        crop_w: int, crop_h: int,
        out_w: int, out_h: int,
        fps: float,
        codec: str, crf: int,
    ) -> ToolResult:
        """Render with dynamic crop positions that follow the face.

        Uses FFmpeg's sendcmd filter to update crop position over time.
        For simplicity and reliability, we interpolate between key positions
        using FFmpeg expression-based crop.
        """
        # Build a piecewise-linear x(t) and y(t) using FFmpeg expressions
        # We'll sample at the face tracking rate and interpolate between points
        if not crop_xs:
            return ToolResult(success=False, error="No crop positions computed")

        # If very few data points, fall back to static using average
        if len(crop_xs) < 3:
            avg_x = int(sum(crop_xs) / len(crop_xs))
            avg_y = int(sum(crop_ys) / len(crop_ys))
            return self._render_static_crop(
                input_path, output_path, avg_x, avg_y,
                crop_w, crop_h, out_w, out_h, codec, crf,
            )

        # Build sendcmd script for crop filter position updates
        # Each command sets the crop x,y at the corresponding timestamp
        temp_dir = output_path.parent / ".reframe_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        sendcmd_path = temp_dir / "crop_commands.txt"

        # Approximate timestamps from the face tracking sample rate
        # The face data was sampled at sample_fps intervals
        sample_interval = 1.0 / (fps / max(1, int(fps / 5)))  # Approximate

        lines = []
        for i, (cx, cy) in enumerate(zip(crop_xs, crop_ys)):
            ts = i * sample_interval
            lines.append(f"{ts:.3f} [enter] crop x {cx};")
            lines.append(f"{ts:.3f} [enter] crop y {cy};")

        sendcmd_path.write_text("\n".join(lines), encoding="utf-8")

        # Use crop with sendcmd for dynamic positioning
        vf = (
            f"sendcmd=f='{str(sendcmd_path).replace(chr(92), '/')}':flags=enter,"
            f"crop={crop_w}:{crop_h}:{crop_xs[0]}:{crop_ys[0]},"
            f"scale={out_w}:{out_h},setsar=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", codec, "-crf", str(crf), "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]

        try:
            self.run_command(cmd, timeout=600)
        except Exception:
            # sendcmd can be finicky — fall back to static crop with average position
            avg_x = int(sum(crop_xs) / len(crop_xs))
            avg_y = int(sum(crop_ys) / len(crop_ys))
            result = self._render_static_crop(
                input_path, output_path, avg_x, avg_y,
                crop_w, crop_h, out_w, out_h, codec, crf,
            )
            if result.success:
                result.data = result.data or {}
                result.data["fallback"] = "sendcmd failed, used static average crop"
            return result
        finally:
            # Clean up
            if sendcmd_path.exists():
                sendcmd_path.unlink()
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        return ToolResult(success=True)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Estimate runtime in seconds. Roughly 1x realtime for face tracking + render."""
        return 60.0  # Conservative default

    @staticmethod
    def list_presets() -> dict[str, str]:
        """Return available aspect ratio presets."""
        return {
            name: f"{w}:{h}"
            for name, (w, h) in ASPECT_PRESETS.items()
        }
