from __future__ import annotations

from typing import Any


def route_key(country: str = "", proxy_type: str = "", asn: str = "") -> str:
    return f"{str(country or '').upper()}:{str(proxy_type or '').lower()}:{str(asn or '').upper()}"


def route_key_from_route(route: Any, default_type: str = "") -> str:
    return route_key(
        getattr(route, "country", "") or "",
        getattr(route, "proxy_type", "") or default_type or "",
        getattr(route, "asn", "") or "",
    )


def route_key_from_material(material: Any) -> str:
    if material is None:
        return ""
    if isinstance(material, dict):
        return route_key(material.get("country", ""), material.get("proxy_type", ""), material.get("asn", ""))
    return route_key(
        getattr(material, "country", "") or "",
        getattr(material, "proxy_type", "") or "",
        getattr(material, "asn", "") or "",
    )
