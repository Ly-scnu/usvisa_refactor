from __future__ import annotations

from .base import CfClickResult, click_point
from .cdp_precise import CdpPreciseClickStrategy
from .cloak_curve_cdp import CloakCurveCdpClickStrategy
from .cloak_humanized_mouse import CloakHumanizedMouseStrategy
from .hybrid_cdp import HybridCdpClickStrategy


def normalize_strategy_name(value: str | None) -> str:
    name = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "": "hybrid_cdp",
        "cdp": "cdp_precise",
        "precise": "cdp_precise",
        "old_cdp": "cdp_precise",
        "humanized": "cloak_humanized_mouse",
        "humanized_mouse": "cloak_humanized_mouse",
        "cloak_mouse": "cloak_humanized_mouse",
        "mouse": "cloak_humanized_mouse",
        "cloak_curve": "cloak_curve_cdp",
        "cloak_cdp": "cloak_curve_cdp",
        "hybrid": "hybrid_cdp",
    }
    return aliases.get(name, name)


def build_strategy(value: str | None):
    name = normalize_strategy_name(value)
    if name == "cdp_precise":
        return CdpPreciseClickStrategy()
    if name == "cloak_humanized_mouse":
        return CloakHumanizedMouseStrategy()
    if name == "cloak_curve_cdp":
        return CloakCurveCdpClickStrategy()
    return HybridCdpClickStrategy()
