"""User settings, stored as JSON in the platform config folder."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("COMFY_SUITE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "comfy-suite"


@dataclass
class Settings:
    comfy_url: str = "http://127.0.0.1:8188"
    photosuite_host: str = "127.0.0.1"
    photosuite_port: int = 7878
    photosuite_token: str = ""
    photosuite_token_file: str = ""  # default: <config>/photosuite-control.token
    photosuite_executable: str = ""  # for `comfy-suite launch`
    exchange_dir: str = ""  # default: <config>/exchange; PhotoSuite's automation root
    style: str = ""
    batch_size: int = 2
    selection_grow: int = 8
    selection_feather: int = 12
    context_padding: float = 0.25
    live_interval_ms: int = 600
    history_size: int = 40
    always_on_top: bool = True
    language: str = ""  # "" = system, "uk" or "en"

    # -- derived paths ------------------------------------------------------------------------

    def token_file_path(self) -> Path:
        return Path(self.photosuite_token_file).expanduser() if self.photosuite_token_file else config_dir() / "photosuite-control.token"

    def exchange_path(self) -> Path:
        return Path(self.exchange_dir).expanduser() if self.exchange_dir else config_dir() / "exchange"

    def styles_path(self) -> Path:
        return config_dir() / "styles"

    # -- persistence --------------------------------------------------------------------------

    @staticmethod
    def file() -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        s = cls()
        for k, v in data.items() if isinstance(data, dict) else []:
            if k in known and isinstance(v, type(getattr(s, k))):
                setattr(s, k, v)
            elif k in known and isinstance(getattr(s, k), float) and isinstance(v, int):
                setattr(s, k, float(v))
        return s

    def save(self) -> None:
        path = self.file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
