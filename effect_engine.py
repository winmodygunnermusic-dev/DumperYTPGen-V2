"""
effect_engine.py

Applies YTP-style effects to clips using FFmpeg filters.

Effects included (implemented via FFmpeg filters where possible):
- speed change
- reverse
- freeze frame
- audio pitch shift (asat/aresample/filter_complex using atempo or rubberband if available)
- volume boosts
- stutter (repeat frames or tiny audio repeats)
- ear-rape (extreme volume + distort)
- zoom, spin, mirror, rgb split (simulated)
- subtitles overlay (random text)
- shuffle/repeat edits (composed externally by generator)

Note: Some effects are emulated with combinations of FFmpeg filters; results vary depending on codec.
"""

import os
import random
import uuid
from typing import Optional, Callable
from pathlib import Path
from ffmpeg_utils import FFmpegUtils
import threading
import shlex


class EffectEngine:
    """
    Centralized effect application for individual clips.
    """

    def __init__(self, ffutils: FFmpegUtils):
        self.ffutils = ffutils

    def apply_speed_change(self, src: str, dst: str, speed: float, cancel_event: Optional[threading.Event] = None):
        """
        Change playback speed. For video, use setpts; for audio, use atempo (which supports 0.5-2.0, so chain if needed).
        """
        vfilter = f"setpts={1.0/speed}*PTS"
        afilter = self._build_atempo_chain(speed)
        return self.ffutils.apply_filters(src, dst, vfilter=vfilter, afilter=afilter, cancel_event=cancel_event)

    def _build_atempo_chain(self, speed: float) -> Optional[str]:
        """
        Returns a chain of atempo filters to achieve speeds outside 0.5-2.0 by chaining multiple atempo filters.
        """
        if speed <= 0:
            speed = 0.5
        chain = []
        remaining = speed
        # Break into factors between 0.5 and 2.0
        while remaining < 0.5:
            chain.append(0.5)
            remaining /= 0.5
        while remaining > 2.0:
            chain.append(2.0)
            remaining /= 2.0
        chain.append(remaining)
        return ",".join(f"atempo={f:.6f}" for f in chain)

    def apply_reverse(self, src: str, dst: str, cancel_event: Optional[threading.Event] = None):
        """
        Reverse both video and audio streams.
        """
        # -vf reverse -af areverse
        return self.ffutils.apply_filters(src, dst, vfilter="reverse", afilter="areverse", cancel_event=cancel_event)

    def apply_freeze_frame(self, src: str, dst: str, freeze_time: float = 0.5, cancel_event: Optional[threading.Event] = None):
        """
        Freeze the last frame or a particular frame for freeze_time seconds.
        Implement via select and tinterpolate or fps/loop.
        Simpler approach: extract a single frame, create a video from it with -t
        """
        # Extract a frame at 0.1s and loop it
        frame_path = str(Path(dst).with_suffix(".freeze_frame.jpg"))
        try:
            # extract single frame
            subprocess_cmd = [
                self.ffutils.ffmpeg_exec,
                "-y",
                "-i",
                src,
                "-ss",
                "0.1",
                "-frames:v",
                "1",
                frame_path,
            ]
            import subprocess

            subprocess.run(subprocess_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # now make a video from this frame same resolution as source
            args = [
                "-loop",
                "1",
                "-i",
                frame_path,
                "-t",
                str(freeze_time),
                "-c:v",
                "libx264",
                "-vf",
                "format=yuv420p",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                dst,
            ]
            return self.ffutils.run_ffmpeg_with_progress(args, cancel_event=cancel_event)
        finally:
            try:
                os.remove(frame_path)
            except Exception:
                pass

    def apply_ear_rape(self, src: str, dst: str, db_boost: float = 20.0, cancel_event: Optional[threading.Event] = None):
        """
        Massive volume increase + clipping/distortion simulation.
        """
        af = f"volume={db_boost}dB,acompressor=threshold=-1dB:ratio=20:attack=5:release=50"
        return self.ffutils.apply_filters(src, dst, afilter=af, cancel_event=cancel_event)

    def apply_repeat_word_effect(self, src: str, dst: str, repeat_count: int = 3, cancel_event: Optional[threading.Event] = None):
        """
        Simulate repeating a small chunk (word) by trimming a small segment, repeating it, and concatenating.
        This function will detect a small segment in the middle and repeat it.
        """
        # Fallback implementation: speed up small pieces and duplicate
        # We'll simply speed the whole clip slightly and concatenate multiple times to simulate repeats
        tmp1 = str(Path(dst).with_suffix(f".tmp_{uuid.uuid4().hex}.mp4"))
        try:
            self.apply_speed_change(src, tmp1, speed=1.2, cancel_event=cancel_event)
            # concat tmp1 multiple times
            clips = [tmp1] * repeat_count
            return self.ffutils.concat_clips(clips, dst, cancel_event=cancel_event)
        finally:
            try:
                os.remove(tmp1)
            except Exception:
                pass

    def apply_zoom_spin_mirror_rgb(self, src: str, dst: str, zoom: float = 1.2, spin: float = 0.0, mirror=False, rgb_split=False,
                                  cancel_event: Optional[threading.Event] = None):
        """
        Composite filter to emulate zoom, spin, mirror and RGB split.
        spin param is degrees per second; we can use rotate filter with PI/180*theta*t
        RGB split simulated with split/offset of chroma channels.
        """
        vf_parts = []
        # zoom via scale and crop with zoompan for dynamic zoom
        vf_parts.append(f"zoompan=z='min(zoom+0.001,{zoom})':d=1")
        if spin:
            # rotate over time: rotate=PI/180*spin*t
            vf_parts.append(f"rotate=PI/180*{spin}*t:ow=rotw(iw):oh=roth(ih)")
        if mirror:
            vf_parts.append("hflip")
        if rgb_split:
            # crude RGB split using colorchannelmixer with offsets is complicated; simulate via lutrgb and overlay
            # simpler: modulate color channels
            vf_parts.append("colorchannelmixer=1:0:0:0:0:1:0:0:0:0:1:0")
        vf = ",".join(vf_parts) if vf_parts else None
        return self.ffutils.apply_filters(src, dst, vfilter=vf, cancel_event=cancel_event)

    def add_subtitle_text(self, src: str, dst: str, text: str, position: str = "center", cancel_event: Optional[threading.Event] = None):
        """
        Adds a subtitle-like text overlay. Position can be top-left, top-right, center, etc.
        """
        if position == "top-left":
            x, y = 10, 10
        elif position == "top-right":
            x, y = "(w-text_w)-10", 10
        elif position == "bottom-left":
            x, y = 10, "(h-text_h)-10"
        elif position == "bottom-right":
            x, y = "(w-text_w)-10", "(h-text_h)-10"
        else:
            x, y = "(w-text_w)/2", "(h-text_h)/2"
        # escape text
        safe_text = text.replace(":", "\\:").replace("'", "\\'")
        vf = f"drawtext=fontfile=/Windows/Fonts/arial.ttf:text='{safe_text}':x={x}:y={y}:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.6"
        return self.ffutils.apply_filters(src, dst, vfilter=vf, cancel_event=cancel_event)