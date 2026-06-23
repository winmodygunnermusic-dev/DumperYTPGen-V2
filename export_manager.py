"""
export_manager.py

Handles assembling the final YTP project and exporting via FFmpeg.

- Concatenates clips (using FFmpegUtils.concat_clips).
- Applies overlays and mixes audio using filter_complex when needed.
- Runs export in a background thread with progress/log callbacks and cancel support.
"""

import os
import threading
import tempfile
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
import uuid
from config import ConfigManager
from ffmpeg_utils import FFmpegUtils


class ExportManager:
    """
    Orchestrates export of a project into a final MP4 using FFmpeg.
    """

    def __init__(self, config: ConfigManager, ffutils: FFmpegUtils):
        self.config = config
        self.ffutils = ffutils
        self._cancel_event = threading.Event()
        self._proc_thread: Optional[threading.Thread] = None

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
    ) -> threading.Thread:
        """
        Start export in a background thread.

        - clip_paths: list of generated clip file paths
        - overlays: list of dicts with keys: path, duration, position
        - sfx: list of dicts with keys: path, start_time, volume (optional)
        - music_track: optional path to music track
        - output_path: output mp4 path
        - bitrate: video bitrate string for output
        - on_progress(line_seconds): callback for ffmpeg time= progress
        - on_log(line): callback for log lines
        """

        def _run():
            concat_temp = None
            try:
                self._reset()
                if on_log:
                    on_log("[export_project] Starting export...")

                # 1) Concatenate clips into a single file (fast path with demuxer, fallback handled inside ffutils)
                concat_temp = str(Path(tempfile.gettempdir()) / f"ytp_concat_{uuid.uuid4().hex}.mp4")
                if on_log:
                    on_log(f"[export_project] Concatenating {len(clip_paths)} clips...")
                rc = self.ffutils.concat_clips(clip_paths, concat_temp, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                if rc != 0:
                    if on_log:
                        on_log(f"[export_project] Concatenation failed with code {rc}")
                    return

                # If no overlays/sfx/music -> transcode concatenated file to final output
                if (not overlays) and (not sfx) and (not music_track):
                    if on_log:
                        on_log("[export_project] No overlays or extra audio layers – simple transcode to output.")
                    args = ["-i", concat_temp, "-c:v", "libx264", "-b:v", bitrate, "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", output_path]
                    rc2 = self.ffutils.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                    if rc2 == 0:
                        if on_log:
                            on_log(f"[export_project] Export complete: {output_path}")
                    else:
                        if on_log:
                            on_log(f"[export_project] Transcode failed with code {rc2}")
                    return

                # Otherwise, we need a combined ffmpeg invocation with overlays and audio mixing
                # Build inputs: base concat file + overlay inputs + optional music + sfx
                inputs: List[str] = [concat_temp]
                overlay_count = 0
                for ov in overlays:
                    path = ov.get("path")
                    inputs.append(path)
                    overlay_count += 1
                if music_track:
                    inputs.append(music_track)
                for s in sfx:
                    inputs.append(s.get("path"))

                # Build -i arguments
                in_args: List[str] = []
                for inp in inputs:
                    in_args += ["-i", inp]

                # Build video overlay filter chain
                filter_parts: List[str] = []
                # Start from input 0 video
                prev_label = "[0:v]"
                current_input_index = 1
                ov_index = 0
                for ov in overlays:
                    ov_path = ov.get("path")
                    if not ov_path or not os.path.exists(ov_path):
                        if on_log:
                            on_log(f"[export_project] Skipping missing overlay: {ov_path}")
                        current_input_index += 1
                        continue
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
                    ov_label = f"[{current_input_index}:v]"
                    out_label = f"[vo{ov_index}]"
                    filter_parts.append(f"{prev_label}{ov_label} overlay=x={xy} {out_label}")
                    prev_label = out_label
                    current_input_index += 1
                    ov_index += 1

                final_video_label = prev_label if ov_index > 0 else "[0:v]"

                # Build audio mixing: start with concat audio "[0:a]"
                audio_labels: List[str] = ["[0:a]"]
                # music and sfx come after overlays in inputs list
                # compute starting index: 1 + number of overlays
                audio_input_idx = 1 + overlay_count
                if music_track:
                    audio_labels.append(f"[{audio_input_idx}:a]")
                    audio_input_idx += 1
                for s in sfx:
                    audio_labels.append(f"[{audio_input_idx}:a]")
                    audio_input_idx += 1

                audio_out_label = None
                if len(audio_labels) > 1:
                    amix_label = "[aout]"
                    amix_inputs = "".join(audio_labels)
                    # amix to one audio stream
                    filter_parts.append(f"{amix_inputs} amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0 {amix_label}")
                    audio_out_label = amix_label
                else:
                    audio_out_label = audio_labels[0]

                # assemble filter_complex
                filter_complex = ";".join(filter_parts) if filter_parts else ""

                # build final argument list
                final_args: List[str] = []
                final_args += in_args
                if filter_complex:
                    final_args += ["-filter_complex", filter_complex, "-map", final_video_label, "-map", audio_out_label]
                else:
                    final_args += ["-map", "0:v", "-map", "0:a?"]

                final_args += ["-c:v", "libx264", "-b:v", bitrate, "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", output_path]

                if on_log:
                    on_log("[export_project] Running ffmpeg with filter_complex and mappings.")
                rc3 = self.ffutils.run_ffmpeg_with_progress(final_args, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                if rc3 == 0:
                    if on_log:
                        on_log(f"[export_project] Export complete: {output_path}")
                else:
                    if on_log:
                        on_log(f"[export_project] FFmpeg processing failed with code {rc3}")
            finally:
                # cleanup temporary concat file
                try:
                    if concat_temp and os.path.exists(concat_temp):
                        os.remove(concat_temp)
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True)
        self._proc_thread = t
        t.start()
        return t