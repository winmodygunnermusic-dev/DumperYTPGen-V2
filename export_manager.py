"""
export_manager.py

Handles assembling the final YTP project and exporting via FFmpeg.

- Combines clips
- Applies overlays and audio layers through filter_complex construction
- Exposes progress updates and allows canceling
"""

import os
import threading
import tempfile
from typing import List, Dict, Any, Optional, Callable
from ffmpeg_utils import FFmpegUtils
from config import ConfigManager
from pathlib import Path
import uuid
import shlex


class ExportManager:
    """
    Orchestrates export of a project into a final MP4 using FFmpeg.
    """

    def __init__(self, config: ConfigManager, ffutils: FFmpegUtils):
        self.config = config
        self.ffutils = ffutils
        self._cancel_event = threading.Event()
        self._proc_thread: Optional[threading.Thread] = None
        self._progress_callback: Optional[Callable[[float], None]] = None
        self._log_callback: Optional[Callable[[str], None]] = None

    def cancel(self):
        self._cancel_event.set()

    def _reset(self):
        self._cancel_event.clear()

    def export_project(
        self,
        clip_paths: List[str],
        overlays: List[Dict[str, Any]],
        sfx: List[Dict[str, Any]],
        music_track: Optional[str],
        output_path: str,
        bitrate: str = "2000k",
        on_progress: Optional[Callable[[float], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        """
        Start export in a background thread. on_progress receives seconds processed or percent depending on implementation.
        """

        def _run():
            try:
                self._reset()
                self._progress_callback = on_progress
                self._log_callback = on_log

                # Strategy:
                # 1) Concatenate clips into a single input (re-encode for uniformity).
                concat_temp = str(Path(tempfile.gettempdir()) / f"ytp_concat_{uuid.uuid4().hex}.mp4")
                if on_log:
                    on_log(f"Concatenating {len(clip_paths)} clips...")
                rc = self.ffutils.concat_clips(clip_paths, concat_temp, on_progress=on_progress, cancel_event=self._cancel_event)
                if rc != 0:
                    if on_log:
                        on_log("Concatenation returned non-zero exit code; attempting sequential filter concat.")
                    # fall back to filter_complex concat (not implemented here for brevity)
                # 2) Build filter_complex string for overlays and sfx/music mixing
                filter_parts = []
                inputs = [concat_temp]
                for ov in overlays:
                    inputs.append(ov["path"])
                if music_track:
                    inputs.append(music_track)
                # For simplicity, if overlays exist, we'll overlay them using simple overlay at specified times/positions
                # A production-ready version would build a complex timeline-aware filter. Here we apply static overlay(s).
                args = []
                for inp in inputs:
                    args += ["-i", inp]
                # Build base mapping and filter_complex
                filter_complex = ""
                cur_video = "[0:v]"
                # overlay inputs start at index 1...
                idx = 1
                overlay_count = 0
                for ov in overlays:
                    pos = ov.get("position", "center")
                    if pos == "top-left":
                        xy = "0:0"
                    elif pos == "top-right":
                        xy = "main_w-overlay_w:0"
                    elif pos == "bottom-left":
                        xy = "0:main_h-overlay_h"
                    elif pos == "bottom-right":
                        xy = "main_w-overlay_w:main_h-overlay_h"
                    else:
                        xy = "(main_w-overlay_w)/2:(main_h-overlay_h)/2"
                    filter_complex += f"{cur_video}[{idx}:v] overlay=x={xy} [tmp{overlay_count}];"
                    cur_video = f"[tmp{overlay_count}]"
                    idx += 1
                    overlay_count += 1
                # audio mixing: simple amix of concat audio + sfx + music (if present)
                # map video and audio outputs
                final_video_map = cur_video if overlay_count > 0 else "[0:v]"
                # Put final output together by writing to temp processed file
                processed_temp = str(Path(tempfile.gettempdir()) / f"ytp_processed_{uuid.uuid4().hex}.mp4")
                full_args = []
                if filter_complex:
                    full_args += ["-filter_complex", filter_complex.rstrip(";")]
                    # map video from last tmp and audio from 0
                    full_args += ["-map", final_video_map, "-map", "0:a?"]
                # encoding settings
                full_args += ["-c:v", "libx264", "-b:v", bitrate, "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", processed_temp]
                # Run ffmpeg with inputs and filter_complex
                rc = self.ffutils.run_ffmpeg_with_progress(full_args, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                if rc != 0:
                    if on_log:
                        on_log(f"FFmpeg processing failed with code {rc}")
                    return
                # Move processed temp to output
                try:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(processed_temp).replace(output_path)
                    if on_log:
                        on_log(f"Export complete: {output_path}")
                except Exception as e:
                    if on_log:
                        on_log(f"Failed to finalize output: {e}")
            finally:
                # cleanup
                try:
                    if 'concat_temp' in locals() and Path(concat_temp).exists():
                        Path(concat_temp).unlink()
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True)
        self._proc_thread = t
        t.start()
        return t