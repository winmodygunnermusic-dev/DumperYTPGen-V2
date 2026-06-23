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
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1
        )

        def _reader(stream):
            for line in stream:
                line_stripped = line.strip()
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
                # wait a little
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
        Skips missing files and logs them. Returns ffmpeg exit code.
        """
        from tempfile import NamedTemporaryFile

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

        # Create list file appropriate for platform
        with NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as f:
            for c in existing:
                abspath = os.path.abspath(c)
                if os.name == "nt":
                    # Windows: use double quotes around the path (avoids issues with backslashes)
                    line = 'file "{}"\n'.format(abspath)
                else:
                    # POSIX: escape single quotes and use single-quoted path
                    safe = abspath.replace("'", "'\\''")
                    line = "file '{}'\n".format(safe)
                f.write(line)
            listpath = f.name

        try:
            args = ["-f", "concat", "-safe", "0", "-i", listpath, "-c", "copy", dst]
            rc = self.run_ffmpeg_with_progress(args, on_progress=on_progress, on_log=on_log, cancel_event=cancel_event)
            if rc != 0 and on_log:
                on_log(f"[concat_clips] ffmpeg concat demuxer returned code {rc}")
            return rc
        finally:
            try:
                os.remove(listpath)
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