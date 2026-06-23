"""
clip_generator.py

Generates random clips from source videos using FFmpegUtils and FFprobe metadata.

Features:
- Ranges for min/max clip length.
- Random seek positions.
- Export to temporary folder.
- Batch generation support.
"""

import os
import random
from typing import List, Dict, Any, Optional
from pathlib import Path
import threading
import uuid
import shutil
from ffmpeg_utils import FFmpegUtils
from config import ConfigManager


class ClipDescriptor:
    """
    Represents a generated clip on disk.
    """

    def __init__(self, src: str, start: float, duration: float, path: str):
        self.src = src
        self.start = start
        self.duration = duration
        self.path = path

    def to_dict(self):
        return {"src": self.src, "start": self.start, "duration": self.duration, "path": self.path}


class ClipGenerator:
    """
    Generates random clips using provided libraries and settings.
    """

    def __init__(self, config: ConfigManager, ffutils: FFmpegUtils):
        self.config = config
        self.ffutils = ffutils
        self.temp_dir = Path(self.config.get("temp_dir"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _unique_path(self, prefix="clip", ext=".mp4"):
        return str(self.temp_dir / f"{prefix}_{uuid.uuid4().hex}{ext}")

    def generate_random_clips(
        self,
        sources: List[str],
        count: int,
        min_len: float,
        max_len: float,
        allow_overlap: bool = True,
        progress_callback: Optional[callable] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[ClipDescriptor]:
        """
        Generate 'count' random clips sampled from 'sources' respecting min/max lengths.
        Returns a list of ClipDescriptor objects.

        progress_callback receives (index, total, path)
        """
        generated: List[ClipDescriptor] = []
        total = max(1, count)
        for i in range(count):
            if cancel_event and cancel_event.is_set():
                break
            src = random.choice(sources)
            dur = self.ffutils.get_duration(src)
            if dur <= 0:
                continue
            clip_len = random.uniform(min_len, max_len)
            if clip_len > dur:
                clip_len = min(max_len, dur / 2)
            start = random.uniform(0, max(0, dur - clip_len))
            outpath = self._unique_path(prefix="clip")
            try:
                self.ffutils.build_trim_clip(src, start, clip_len, outpath)
                desc = ClipDescriptor(src, start, clip_len, outpath)
                generated.append(desc)
            except Exception as e:
                # skip failing clip but continue
                print(f"Failed to create clip from {src}: {e}")
                continue
            if progress_callback:
                try:
                    progress_callback(i + 1, total, outpath)
                except Exception:
                    pass
        return generated

    def clear_temp(self):
        """
        Remove temporary generated files.
        """
        for p in Path(self.temp_dir).glob("*"):
            try:
                if p.is_file():
                    p.unlink()
                else:
                    shutil.rmtree(p)
            except Exception:
                pass