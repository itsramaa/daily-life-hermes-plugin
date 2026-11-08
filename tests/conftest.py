from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
else:
    sys.path.remove(str(PLUGIN_ROOT))
    sys.path.insert(0, str(PLUGIN_ROOT))

# Clear any conflicting top-level module names cached from other plugins
for mod in ("config", "models", "exceptions", "db", "commands", "hooks"):
    if mod in sys.modules and not str(getattr(sys.modules[mod], "__file__", "")).startswith(str(PLUGIN_ROOT)):
        sys.modules.pop(mod, None)
