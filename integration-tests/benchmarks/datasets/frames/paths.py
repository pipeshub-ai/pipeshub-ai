"""Where the FRAMES dataset keeps its own files."""

from __future__ import annotations

from pathlib import Path

DATASET_NAME = "frames"
PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
CONFIG_DIR = PACKAGE_DIR / "configs"
SPLIT_FILE = DATA_DIR / "split_v1.json"
