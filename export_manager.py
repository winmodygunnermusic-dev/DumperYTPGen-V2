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

        Parameters:
        - clip_paths: list of generated clip file paths (strings)
        - overlays: list of dicts {path, duration, position}
        - sfx: list of dicts {path, start_time, volume}
        - music_track: optional path to music track
        - output_path: final mp4 path
        - bitrate: video bitrate string for output
        - on_progress: callback(seconds) called with progress time parsed from ffmpeg
        - on_log: callback(line) to receive ffmpeg/stage log lines
        """

        def _run():
            try:
                self._reset()
                if on_log:
                    on_log("[export_project] Starting export...")
                # 1) Concatenate clips into a single file
                concat_temp = str(Path(tempfile.gettempdir()) / f"ytp_concat_{uuid.uuid4().hex}.mp4")
                if on_log:
                    on_log(f"[export_project] Concatenating {len(clip_paths)} clips...")
                rc = self.ffutils.concat_clips(clip_paths, concat_temp, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                if rc != 0:
                    if on_log:
                        on_log(f"[export_project] Concatenation failed with code {rc}")
                    return

                # If no overlays and no extra audio layers, just transcode to final output with bitrate
                if (not overlays) and (not sfx) and (not music_track):
                    if on_log:
                        on_log("[export_project] No overlays or extra audio — transcoding to final output.")
                    args = ["-i", concat_temp, "-c:v", "libx264", "-b:v", bitrate, "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", output_path]
                    rc2 = self.ffutils.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                    if rc2 == 0:
                        if on_log:
                            on_log(f"[export_project] Export complete: {output_path}")
                    else:
                        if on_log:
                            on_log(f"[export_project] Transcode failed with code {rc2}")
                    return

                # Build inputs list: base concat file + overlay inputs + optional music + sfx
                inputs = [concat_temp]
                for ov in overlays:
                    inputs.append(ov.get("path"))
                if music_track:
                    inputs.append(music_track)
                for s in sfx:
                    inputs.append(s.get("path"))

                # Build ffmpeg args with -i entries
                args = []
                for inp in inputs:
                    args += ["-i", inp]

                filter_parts = []
                # Video overlay chain: chain overlays onto base video
                base_label = "[0:v]"
                current_input_index = 1
                ov_count = 0
                for ov in overlays:
                    path = ov.get("path")
                    if not path or not os.path.exists(path):
                        if on_log:
                            on_log(f"[export_project] Skipping missing overlay: {path}")
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
                    out_label = f"[v{ov_count}]"
                    # overlay_filter: take current base and overlay, output a new label
                    filter_parts.append(f"{base_label}{ov_label} overlay=x={xy} {out_label}")
                    base_label = out_label
                    current_input_index += 1
                    ov_count += 1

                final_video_label = base_label if ov_count > 0 else "[0:v]"

                # Build audio mixing: start with concat audio (input 0)
                audio_labels = ["[0:a]"]
                # overlays usually don't have audio; inputs after overlays: music then sfx
                # compute indices for music and sfx in inputs
                idx = 1 + len(overlays)
                if music_track:
                    audio_labels.append(f"[{idx}:a]")
                    idx += 1
                for s in sfx:
                    audio_labels.append(f"[{idx}:a]")
                    idx += 1

                audio_out_label = None
                if len(audio_labels) > 1:
                    amix_label = "[aout]"
                    amix_inputs = "".join(audio_labels)
                    filter_parts.append(f"{amix_inputs} amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0 {amix_label}")
                    audio_out_label = amix_label
                else:
                    audio_out_label = audio_labels[0]

                # Combine filter_parts into filter_complex (if any)
                filter_complex = ";".join(filter_parts) if filter_parts else None

                # Build final ffmpeg args
                final_args = []
                for inp in inputs:
                    final_args += ["-i", inp]
                if filter_complex:
                    final_args += ["-filter_complex", filter_complex]
                    final_args += ["-map", final_video_label, "-map", audio_out_label]
                else:
                    final_args += ["-map", "0:v", "-map", "0:a?"]

                final_args += ["-c:v", "libx264", "-b:v", bitrate, "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", output_path]

                if on_log:
                    on_log("[export_project] Running FFmpeg with constructed filter_complex and mappings.")
                rc3 = self.ffutils.run_ffmpeg_with_progress(final_args, on_progress=on_progress, on_log=on_log, cancel_event=self._cancel_event)
                if rc3 == 0:
                    if on_log:
                        on_log(f"[export_project] Export complete: {output_path}")
                else:
                    if on_log:
                        on_log(f"[export_project] FFmpeg processing failed with code {rc3}")
            finally:
                # cleanup concat temp
                try:
                    if 'concat_temp' in locals() and os.path.exists(concat_temp):
                        os.remove(concat_temp)
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True)
        self._proc_thread = t
        t.start()
        return t