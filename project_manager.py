"""
project_manager.py

Save and load projects in JSON format.

Project stores:
- References to library elements used
- Generated clips meta
- Effect chains
- Overlay definitions
- Export settings
"""

import json
from typing import Dict, Any, List
from pathlib import Path
import threading
from config import ConfigManager


class ProjectManager:
    """
    Handles saving/loading projects and storing recent projects.
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self._lock = threading.RLock()

    def save_project(self, path: str, data: Dict[str, Any]):
        with self._lock:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.config.push_recent_project(str(p))

    def load_project(self, path: str) -> Dict[str, Any]:
        with self._lock:
            p = Path(path)
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.config.push_recent_project(str(p))
            return data

    def list_recent(self) -> List[str]:
        return self.config.get_recent_projects()