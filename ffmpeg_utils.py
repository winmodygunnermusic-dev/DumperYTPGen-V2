# ffmpeg_utils.py
"""
ffmpeg_utils.py

Utility wrapper around ffmpeg and ffprobe.

- Detects ffmpeg/ffprobe in PATH or configured locations.
- Provides methods to probe media (duration, streams).
- Provides helpers to run ffmpeg commands with progress parsing and cancellation.
"""

import subprocess
import threading
import shutil
import os
import json
import re
from typing import Optional, Dict, Any, Callable, List
from pathlib import Path
from tempfile import NamedTemporaryFile
from config import ConfigManager

class FFmpegNotFoundError(RuntimeError):
    pass


class FFmpegUtils:
    """
    Wrapper for FFmpeg and FFprobe invocation.
    """

    TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

    def __init__(self, config: ConfigManager):
        self.config = config
        self.ffmpeg_path = config.get("ffmpeg_path")
        self.ffprobe_path = config.get("ffprobe_path")
        self._detect()

    def _detect(self):
        # Use configured paths if provided, else try PATH
        if self.ffmpeg_path and Path(self.ffmpeg_path).exists():
            ffmpeg = self.ffmpeg_path
        else:
            ffmpeg = shutil.which("ffmpeg")

        if self.ffprobe_path and Path(self.ffprobe_path).exists():
            ffprobe = self.ffprobe_path
        else:
            ffprobe = shutil.which("ffprobe")

        self.ffmpeg_exec = ffmpeg
        self.ffprobe_exec = ffprobe

        if not self.ffmpeg_exec or not self.ffprobe_exec:
            raise FFmpegNotFoundError(
                "FFmpeg or FFprobe not found. Please install them and ensure they are on PATH, "
                "or set their paths in the app configuration."
            )

    def probe(self, filepath: str) -> Dict[str, Any]:
        """
        Run ffprobe to get file info JSON.
        """
        cmd = [
            self.ffprobe_exec,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            filepath,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {proc.stderr.decode(errors='ignore')}")
        return json.loads(proc.stdout.decode("utf-8"))

    def get_duration(self, filepath: str) -> float:
        info = self.probe(filepath)
        fmt = info.get("format", {})
        duration = fmt.get("duration")
        if duration is None:
            # try stream durations
            streams = info.get("streams", [])
            for s in streams:
                if "duration" in s:
                    duration = s["duration"]
                    break
        try:
            return float(duration) if duration else 0.0
        except Exception:
            return 0.0

    def run_ffmpeg_with_progress(
        self,
        args: List[str],
        on_progress: Optional[Callable[[float], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        """
        Run ffmpeg command (full args list) and parse stderr for progress. Returns exit code.

        on_progress receives seconds processed (float) if time= is parsed.
        on_log receives raw stderr lines.
        cancel_event if set will terminate the process when set.
        """
        cmd = [self.ffmpeg_exec, "-y"] + args
        if on_log:
            on_log(f"[run_ffmpeg_with_progress] Running: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1
        )

        def _reader(stream):
            for line in stream:
                line_stripped = line.rstrip("\n")
                if on_log:
                    on_log(line_stripped)
                m = self.TIME_RE.search(line_stripped)
                if m and on_progress:
                    try:
                        hours = float(m.group(1))
                        mins = float(m.group(2))
                        secs = float(m.group(3))
                        total_seconds = hours * 3600 + mins * 60 + secs
                        on_progress(total_seconds)
                    except Exception:
                        pass

        stderr_thread = threading.Thread(target=_reader, args=(proc.stderr,), daemon=True)
        stderr_thread.start()

        try:
            while proc.poll() is None:
                if cancel_event and cancel_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break
                try:
                    proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                stderr_thread.join(timeout=1.0)
            except Exception:
                pass

        return proc.returncode if proc.returncode is not None else -1

    def _add_silent_audio_if_needed(self, src: str, on_log: Optional[Callable[[str], None]] = None) -> str:
        """
        If `src` lacks an audio stream, create a temporary file with a silent audio track merged and return its path.
        If audio exists, return original path.
        """
        try:
            info = self.probe(src)
        except Exception as e:
            if on_log:
                on_log(f"[add_silent_audio] ffprobe failed for {src}: {e}")
            # will attempt to include it; let downstream handle failures
            return src

        streams = info.get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        if has_audio:
            return src

        # create a temp file with silent audio
        tf = NamedTemporaryFile(delete=False, suffix=".mp4")
        temp_path = tf.name
        tf.close()
        # Determine duration to set anullsrc shortness (ffmpeg -shortest will limit to video length)
        duration = info.get("format", {}).get("duration")
        if duration is None:
            duration = 0

        args = [
            "-i", src,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            temp_path
        ]
        if on_log:
            on_log(f"[add_silent_audio] Adding silent audio to {src} -> {temp_path}")
        rc = self.run_ffmpeg_with_progress(args, on_progress=None, on_log=on_log, cancel_event=None)
        if rc != 0:
            if on_log:
                on_log(f"[add_silent_audio] Failed to add silent audio to {src}, rc={rc}")
            # On failure, remove temp and return original; caller will notice and may fail
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            return src
        return temp_path

    def build_trim_clip(
        self,
        src: str,
        start: float,
        duration: float,
        dst: str,
        extra_video_filters: Optional[str] = None,
        extra_audio_filters: Optional[str] = None,
    ) -> None:
        """
        Create a trimmed clip from src using -ss and -t (re-encode for uniform output).
        """
        args = [
            "-ss",
            str(start),
            "-i",
            src,
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
        vf = []
        if extra_video_filters:
            vf.append(extra_video_filters)
        if vf:
            args += ["-vf", ",".join(vf)]
        if extra_audio_filters:
            args += ["-af", extra_audio_filters]

        args += [dst]
        # Run blocking
        ret = subprocess.run([self.ffmpeg_exec, "-y"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if ret.returncode != 0:
            raise RuntimeError(f"ffmpeg failed creating clip: {ret.stderr.decode(errors='ignore')}")

    def concat_clips(
        self,
        clip_paths: List[str],
        dst: str,
        on_progress: Optional[Callable[[float], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> int:
        """
        Concat clips using ffmpeg concat demuxer.
        Writes a temporary list file in a format FFmpeg accepts on the current platform.
        If the demuxer fails, falls back to an encoded filter_complex concat.
        Ensures each input has audio (adds silent audio where needed) before filter_complex concat.
        Returns ffmpeg exit code.
        """
        # Filter only existing files and log missing ones
        existing = []
        for p in clip_paths:
            if p and os.path.exists(p):
                existing.append(p)
            else:
                if on_log:
                    on_log(f"[concat_clips] Skipping non-existent file: {p}")

        if not existing:
            if on_log:
                on_log("[concat_clips] No valid input files found for concatenation.")
            return 1

        # Try concat demuxer first (fast copy)
        with NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as f:
            for c in existing:
                abspath = os.path.abspath(c)
                if os.name == "nt":
                    line = 'file "{}"\n'.format(abspath)
                else:
                    safe = abspath.replace("'", "'\\''")
                    line = "file '{}'\n".format(safe)
                f.write(line)
            listpath = f.name

        try:
            args = ["-f", "concat", "-safe", "0", "-i", listpath, "-c", "copy", dst]
            if on_log:
                on_log(f"[concat_clips] Attempting concat demuxer with {len(existing)} files.")
            rc = self.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=cancel_event)
            if rc == 0:
                return rc
            else:
                if on_log:
                    on_log(f"[concat_clips] concat demuxer failed (code {rc}), falling back to filter_complex concat.")
        finally:
            try:
                os.remove(listpath)
            except Exception:
                pass

        # Prepare inputs for fallback: ensure every file has audio stream
        temp_files_to_cleanup: List[str] = []
        prepared_inputs: List[str] = []
        try:
            for src in existing:
                try:
                    prep = self._add_silent_audio_if_needed(src, on_log=on_log)
                except Exception as e:
                    if on_log:
                        on_log(f"[concat_clips] Error while ensuring audio for {src}: {e}")
                    prep = src
                if prep != src:
                    temp_files_to_cleanup.append(prep)
                prepared_inputs.append(prep)

            n = len(prepared_inputs)
            args = []
            for p in prepared_inputs:
                args += ["-i", p]

            # Build filter_complex: [0:v:0][0:a:0][1:v:0][1:a:0]... concat=n={n}:v=1:a=1 [v][a]
            # Validate that inputs are present; if any input is missing streams, ffmpeg will error—this is best-effort.
            v_and_a = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
            filter_complex = f"{v_and_a}concat=n={n}:v=1:a=1[v][a]"
            args += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-c:a", "aac", "-b:a", "192k", dst]
            if on_log:
                on_log(f"[concat_clips] Running filter_complex concat (re-encode) for {n} files.")
            rc2 = self.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=cancel_event)
            if on_log and rc2 != 0:
                on_log(f"[concat_clips] filter_complex concat failed with code {rc2}")
            return rc2
        finally:
            # cleanup any temporary files we created
            for tf in temp_files_to_cleanup:
                try:
                    if os.path.exists(tf):
                        os.remove(tf)
                        if on_log:
                            on_log(f"[concat_clips] Removed temp file {tf}")
                except Exception:
                    pass

    def apply_filters(
        self,
        src: str,
        dst: str,
        vfilter: Optional[str] = None,
        afilter: Optional[str] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        extra_args: Optional[List[str]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> int:
        """
        Apply video/audio filters to a single source file to produce dst.
        Returns ffmpeg exit code.
        """
        args = ["-i", src]
        if vfilter:
            args += ["-vf", vfilter]
        if afilter:
            args += ["-af", afilter]
        if extra_args:
            args += extra_args
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "192k", dst]
        return self.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=cancel_event)