"""Public MMD environment adapter for New Eden modules only."""
from pathlib import Path

import mmd_esi as esi
import mmd_esi_auth as esi_auth
import mmd_sso as sso
from platform_state import state_path


def map_cache_path() -> Path:
    return Path(state_path("cache", "eve_map_intel.json"))
