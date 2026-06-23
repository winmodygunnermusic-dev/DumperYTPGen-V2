"""
config.py

Configuration manager for DumperYTPGen.

Stores application configuration (paths, presets, recent projects, libraries)
in a JSON file in the user's AppData (Windows) or home directory.

Provides defaults and helper methods to load/save settings.
"""

import json
import os
import threading
from pathlib import Path
from typing import Dict, Any

APP_NAME = "DumperYTPGen"
DEFAULT_CONFIG_FILENAME = "dumperytpgen_config.json"


def get_user_config_dir() -> Path:
    """
    Returns an appropriate user config directory for storing app data.
    On Windows, uses %APPDATA%/DumperYTPGen else fallback to ~/.dumperytpgen
    """
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


class ConfigManager:
    """
    Simple thread-safe config manager.
    """

    _lock = threading.RLock()

    def __init__(self, path: Path = None):
        self.config_dir = path or get_user_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / DEFAULT_CONFIG_FILENAME
        self._data: Dict[str, Any] = {}
        self._load_or_init()

    def _default(self):
        return {
            "ffmpeg_path": None,
            "ffprobe_path": None,
            "libraries": {
                "videos": [],
                "audios": [],
                "images": [],
                "sfx": [],
                "dance": [],
                "transitions": [],
            },
            "presets": {},
            "recent_projects": [],
            "temp_dir": str(self.config_dir / "temp"),
            "last_project_dir": str(self.config_dir),
            "random_seed": None,
        }

    def _load_or_init(self):
        with self._lock:
            if self.config_file.exists():
                try:
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                except Exception:
                    # If corrupted, reset to defaults but keep a backup
                    backup = self.config_file.with_suffix(".bak.json")
                    try:
                        self.config_file.rename(backup)
                    except Exception:
                        pass
                    self._data = self._default()
                    self.save()
            else:
                self._data = self._default()
                self.save()

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value):
        with self._lock:
            self._data[key] = value
            self.save()

    def save(self):
        with self._lock:
            tmp = self.config_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            tmp.replace(self.config_file)

    # Helper library accessors
    def get_library(self, lib_name: str):
        with self._lock:
            return list(self._data.get("libraries", {}).get(lib_name, []))

    def add_to_library(self, lib_name: str, path: str):
        with self._lock:
            libs = self._data.setdefault("libraries", {})
            arr = libs.setdefault(lib_name, [])
            if path not in arr:
                arr.append(path)
                self.save()

    def remove_from_library(self, lib_name: str, path: str):
        with self._lock:
            libs = self._data.setdefault("libraries", {})
            arr = libs.setdefault(lib_name, [])
            if path in arr:
                arr.remove(path)
                self.save()

    def get_presets(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data.get("presets", {}))

    def save_preset(self, name: str, preset_data: Dict[str, Any]):
        with self._lock:
            presets = self._data.setdefault("presets", {})
            presets[name] = preset_data
            self.save()

    def remove_preset(self, name: str):
        with self._lock:
            presets = self._data.setdefault("presets", {})
            if name in presets:
                del presets[name]
                self.save()

    def push_recent_project(self, path: str, limit: int = 10):
        with self._lock:
            rp = self._data.setdefault("recent_projects", [])
            if path in rp:
                rp.remove(path)
            rp.insert(0, path)
            while len(rp) > limit:
                rp.pop()
            self.save()

    def get_recent_projects(self):
        with self._lock:
            return list(self._data.get("recent_projects", []))