"""Tests use temporary data only; no live API calls or server credentials."""
import os
from pathlib import Path
import sys

os.environ["HOUHAU_ENV_FILE"] = ""  # never read a developer's backend/.env during tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
