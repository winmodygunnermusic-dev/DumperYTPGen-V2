"""
library_manager.py

Manages source libraries: videos, audio, images, sfx, dance, transitions.

Provides import/remove and simple preview helpers.
"""

import os
from config import ConfigManager
from typing import List
from pathlib import Path
import threading
import subprocess
import sys


class LibraryManager:
    """
    High-level management of application libraries.
    """

    LIB_KEYS = ["videos", "audios", "images", "sfx", "dance", "transitions"]

    def __init__(self, config: ConfigManager):
        self.config = config
        # ensure keys exist
        for k in self.LIB_KEYS:
            _ = self.config.get_library(k)

    def list(self, lib_name: str) -> List[str]:
        return self.config.get_library(lib_name)

    def add(self, lib_name: str, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        self.config.add_to_library(lib_name, path)

    def remove(self, lib_name: str, path: str):
        self.config.remove_from_library(lib_name, path)

    def preview(self, path: str):
        """
        Preview a file by trying os.startfile (Windows) or using the system default viewer.
        This does not embed a video player—keeps the app simple and portable.
        """
        if sys.platform.startswith("win"):
            try:
                os.startfile(path)
                return
            except Exception:
                pass
        # fallback to opening via subprocess
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            raise