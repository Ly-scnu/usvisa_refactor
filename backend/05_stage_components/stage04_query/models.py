from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BLOCKING_STAGES = {
    "cf_challenge",
    "login",
    "security_questions",
    "waiting_room",
    "access_denied",
    "rate_limit_1015",
    "network_error",
    "blank",
    "login_failed",
}


@dataclass
class ScheduleContext:
    app_id: str
    applications: list[str]
    referrer: str
    page_view: dict[str, Any] = field(default_factory=dict)
    family_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostSelection:
    post_id: str
    post_name: str
    source: str
    posts: list[dict[str, Any]] = field(default_factory=list)
    clicked: bool = False
    click_method: str = ""


@dataclass
class DateCollection:
    days: list[str]
    token: str = ""
    source: str = "protocol"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DateDecision:
    acceptable_dates: list[str]
    rejected_dates: list[str]
    selected_date: str = ""
    target_hit: bool = False


@dataclass
class SlotCollection:
    slots: list[dict[str, Any]] = field(default_factory=list)
    matched_slots: list[dict[str, Any]] = field(default_factory=list)
    entries_by_date: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    token_for_submit: str = ""
    selected: dict[str, Any] | None = None
