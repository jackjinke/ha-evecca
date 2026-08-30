"""Test helpers for importing protocol modules without Home Assistant."""

import sys
from pathlib import Path
from types import ModuleType

PACKAGE = "custom_components.evecca"
PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "evecca"

if PACKAGE not in sys.modules:
    module = ModuleType(PACKAGE)
    module.__path__ = [str(PACKAGE_DIR)]
    sys.modules[PACKAGE] = module
