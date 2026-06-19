from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field


class SystemConfig(BaseModel):
    name: str = "OpenSands US Visa Refactor"
    environment: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 18890
    frontend_port: int = 18891
    log_level: str = "INFO"
    data_dir: str = "storage"


class TargetConfig(BaseModel):
    post_name: str = "BEIJING"
    post_aliases: list[str] = Field(default_factory=lambda: ["BEIJING", "北京"])
    post_id: str = ""
    primary_id: str = ""
    applications: list[str] = Field(default_factory=list)
    cutoff_date: str = "2026-06-22"
    start_date: str = ""
    end_date: str = "2026-06-22"
    any_time: bool = True
    is_reschedule: bool = False
    lang: str = "zh-CN"


class SlotConfig(BaseModel):
    total_slots: int = 2
    waiting_room_slots: int = 2
    direct_only_slots: list[str] = Field(default_factory=list)
    queue_wait_seconds: int = 180
    direct_only_recycle_cooldown_seconds: int = 120
    early_gate_timeout_seconds: int = 90
    non_waiting_lane_timeout_seconds: int = 90
    round_timeout_seconds: int = 900
    login_wait_seconds: int = 150
    login_submit_retries: int = 3
    login_cf_reentry_attempts: int = 3
    security_question_stable_wait_seconds: float = 12.0


class ProducerConfig(BaseModel):
    standalone_smoke: bool = True
    real_browser_probe: bool = False
    routes: str
    headless: bool = True
    cloak_browser_root: str = ""
    cf_click_mode: str = "cloak_curve_cdp"
    cf_click_strategy: str = "cloak_curve_cdp"
    max_cf_clicks: int = 99
    cf_no_screenshots: bool = True
    profile_scope: str = "round"
    proxy_sticky_session: bool = True
    proxy_api_retries: int = 3
    proxy_pool_size: int = 1
    inline_business_max_dates: int = 1
    inline_business_live_loop: bool = True
    inline_business_live_interval_seconds: float = 120.0
    inline_business_live_max_seconds: float = 0.0
    inline_business_live_failure_grace_rounds: int = 6
    business_page_retry_attempts: int = 1
    rate_limit_cooldown_seconds: int = 300
    rate_limit_refresh_attempts: int = 0
    rate_limit_429_cooldowns_seconds: list[int] = Field(default_factory=lambda: [60, 120, 180])
    ban_1015_immediate_recycle: bool = True
    protocol_only_post_selection: bool = True
    protocol_direct_from_home: bool = True
    schedule_direct_fallback_after_ms: int = 800
    slot_start_stagger_seconds: int = 30
    failed_round_cooldown_seconds: int = 20
    network_error_cooldown_seconds: int = 0
    network_error_round_cooldown_seconds: int = 60
    network_error_direct_fallback: bool = False
    business_fetch_timeout_ms: int = 15000
    business_validate: bool = False
    ignore_budget: bool = True


class SmartOrchestratorConfig(BaseModel):
    enabled: bool = True
    # Primary SLA target.  Normal mode now targets 30-second result freshness.
    normal_target_result_interval_seconds: float = 30.0
    target_success_interval_seconds: float = 30.0
    # SLA target: after one successful official days query, no other session
    # should successfully hit the business API before this global gap expires.
    target_global_query_interval_seconds: float = 30.0
    # Same live browser/session is useful, but should not be hammered.  Current
    # evidence shows 3-4 minutes is a safer reuse cadence.
    per_session_min_query_interval_seconds: float = 180.0
    per_session_query_jitter_seconds: list[int] = Field(default_factory=lambda: [0, 30])
    # Failed business probes must also back off.  Otherwise a CF/login-blocked
    # session can repeatedly take the single business API gate and starve
    # healthy hot sessions.
    query_failure_cooldown_seconds: float = 180.0
    query_failure_jitter_seconds: list[int] = Field(default_factory=lambda: [30, 90])
    active_query_lease_seconds: float = 90.0
    wait_poll_seconds: float = 2.0
    allow_early_query_launch: bool = True
    query_launch_lead_seconds: float = 12.0
    query_launch_lead_min_seconds: float = 5.0
    query_launch_lead_max_seconds: float = 25.0
    early_success_tolerance_seconds: float = 5.0
    coverage_window_seconds: int = 240
    min_slots: int = 2
    max_slots: int = 10
    normal_active_slots: int = 3
    target_ready_sessions: int = 4
    min_hot_sessions: int = 2
    # Soft cap for concurrently launched slot workers.  The hard cap remains
    # max_slots, but the producer should not fan out to 10 workers just because
    # warm sessions are temporarily cooling down.
    soft_active_slots: int = 4
    # Dynamic capacity caps.  Normal healthy hours stay near soft_active_slots;
    # if the hot-query pool is missing, allow a controlled expansion; if the
    # SLA has already missed one or more target intervals, expand harder.
    adaptive_inventory_active_slots: int = 6
    severe_gap_active_slots: int = 8
    # Cold production is not the same as hot-query concurrency.  When no
    # successful warm session exists yet, cap simultaneous browser/CF/login
    # production so release windows do not fan out 10 cold browsers and trigger
    # a CF/login storm.  Once hot sessions exist, peak/release caps can expand.
    cold_start_active_slots: int = 3
    release_cold_start_active_slots: int = 4
    scale_up_stale_success_seconds: int = 180
    critical_stale_success_seconds: int = 600
    scale_up_cooldown_seconds: int = 180
    prewarm_window_seconds: int = 45
    missed_target_grace_seconds: int = 30
    event_window_seconds: int = 300
    waiting_room_pressure_threshold: float = 0.45
    cf_pressure_threshold: float = 0.45
    risk_pressure_threshold: float = 0.25
    cf_storm_event_threshold: int = 12
    rate_limit_storm_event_threshold: int = 3
    churn_event_threshold: int = 25
    stable_success_window_count: int = 5
    successful_session_recovery_grace_rounds: int = 12
    successful_session_hard_recycle_rounds: int = 3
    inventory_enabled: bool = True
    target_hot_query_sessions: int = 4
    target_login_standby_sessions: int = 2
    target_candidate_sessions: int = 2
    peak_target_hot_query_sessions: int = 5
    peak_target_candidate_sessions: int = 3
    drain_enabled: bool = True
    drain_grace_seconds: int = 90
    drain_check_seconds: int = 30
    peak_prewarm_minutes: int = 5
    peak_cooldown_minutes: int = 3
    release_periods_enabled: bool = True
    release_period_windows: list[str] = Field(default_factory=lambda: ["58-03", "28-33"])
    release_period_target_success_interval_seconds: float = 1.0
    release_period_desired_active_slots: int = 10
    release_period_min_hot_sessions: int = 5
    release_force_full_fanout: bool = False
    normal_candidate_count: int = 3
    release_candidate_count: int = 3
    release_burst_interval_seconds: float = 1.0
    release_wait_poll_seconds: float = 0.3
    release_active_query_lease_seconds: float = 45.0
    release_per_session_min_query_interval_seconds: float = 1.0
    release_failure_cooldown_seconds: float = 1.0
    # During release/peak windows, waiting room is usually worse than
    # immediately recycling into a new proxy/profile attempt.  The user wants
    # 58-03 and 28-33 windows to keep producing login/home-ready sessions
    # instead of spending all 10 slots in queue.
    waiting_room_min_wait_seconds: int = 10
    waiting_room_default_wait_seconds: int = 30
    waiting_room_max_wait_seconds: int = 60
    waiting_room_loose_max_wait_seconds: int = 180
    waiting_room_peak_budget_slots: int = 0
    waiting_room_prewarm_budget_slots: int = 0
    waiting_room_stale_budget_slots: int = 0
    waiting_room_loose_budget_slots: int = 0
    # A release window may keep the global business API cadence at 1s, but a
    # session that just hit page-view/auth/rate-limit failure must not take the
    # next gate repeatedly.
    bad_session_cooldown_seconds: float = 90.0
    release_bad_session_cooldown_seconds: float = 30.0
    page_view_blocked_cooldown_seconds: float = 60.0
    release_page_view_blocked_cooldown_seconds: float = 60.0
    auth_or_cf_cooldown_seconds: float = 60.0
    release_auth_or_cf_cooldown_seconds: float = 60.0
    rate_limited_cooldown_seconds: float = 120.0
    release_rate_limited_cooldown_seconds: float = 120.0
    failed_fetch_cooldown_seconds: float = 120.0
    release_failed_fetch_cooldown_seconds: float = 120.0
    terminal_session_cooldown_seconds: float = 180.0
    normal_session_cooldown_bypass_after_seconds: float = 30.0
    slot_restart_cooldown_seconds: float = 15.0
    route_recovery_ramp_enabled: bool = True
    route_recovery_ramp_window_seconds: float = 900.0
    route_recovery_ramp_step_seconds: float = 30.0
    route_recovery_ramp_initial_slots: int = 3
    route_recovery_ramp_max_slots: int = 6
    route_recovery_ramp_peak_max_slots: int = 8
    # Route selection should follow recent observed quality, not a fixed
    # JP/SG/US rotation.  The selector reads route_health_update events and
    # weighs 10/30/60 minute evidence so bad routes are avoided quickly while
    # one lucky result does not monopolize all slots.
    route_score_enabled: bool = True
    route_score_event_tail_limit: int = 8000
    route_score_window_seconds: list[int] = Field(default_factory=lambda: [600, 1800, 3600])
    route_score_window_weights: list[float] = Field(default_factory=lambda: [0.50, 0.35, 0.15])
    route_max_active_share: float = 0.70
    # Account-login admission control.  These limits apply only to credential
    # submission, not to already-authenticated business queries.
    login_submit_min_gap_seconds: float = 1800.0
    login_submit_lease_seconds: float = 180.0
    login_door_wait_poll_seconds: float = 10.0
    login_stop_when_hot_sessions: int = 3
    # If B2C says "Login not allowed for your account", this is account-level
    # risk, not a bad proxy.  A 45-minute retry loop was still re-triggering the
    # block, so default to a long cooling window and allow only manual/single
    # probe after it expires.
    account_login_block_cooldown_seconds: float = 43200.0
    account_login_block_cooldown_schedule_seconds: list[int] = Field(default_factory=lambda: [43200, 86400, 86400])
    peak_windows: list["PeakWindowConfig"] = Field(default_factory=list)


class PeakWindowConfig(BaseModel):
    name: str
    start: str
    end: str
    target_success_interval_seconds: float = 30.0
    desired_active_slots: int = 8
    min_hot_sessions: int = 4


class BookingConfig(BaseModel):
    armed: bool = True
    max_parallel_submit: int = 3
    parallel_submit_delays_ms: list[int] = Field(default_factory=lambda: [0, 80, 180])
    submit_timeout_seconds: int = 20
    success_latch: bool = True
    prefer_in_process_submit: bool = True
    ui_fallback_enabled: bool = True
    ui_fallback_allow_navigation: bool = False
    ui_fallback_on_classes: list[str] = Field(default_factory=lambda: ["business_error", "failed", "exception"])


class AccountQuestion(BaseModel):
    id: str
    answer: str
    input_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class AccountConfig(BaseModel):
    id: str = "main"
    username: str
    password: str
    required_security_questions: int = 2
    security_questions: list[AccountQuestion] = Field(default_factory=list)


class ProxyProviderConfig(BaseModel):
    name: str = "your_proxy_provider"
    host: str = "proxy.example.com"
    port: int = 10000
    account: str = "YOUR_PROXY_ACCOUNT"
    password: str = "YOUR_PROXY_PASSWORD"
    default_type: str = "socks5"
    sticky_session: bool = True
    sticky_minutes: int = 90


class ProxyRouteConfig(BaseModel):
    country: str
    proxy_type: str = "socks5"
    asn: str = ""
    weight: int = 1


class ProxyConfig(BaseModel):
    provider: ProxyProviderConfig
    routes: list[ProxyRouteConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    project_root: Path
    system: SystemConfig
    target: TargetConfig
    slots: SlotConfig
    producer: ProducerConfig
    smart_orchestrator: SmartOrchestratorConfig = Field(default_factory=SmartOrchestratorConfig)
    booking: BookingConfig
    accounts: list[AccountConfig]
    proxy: ProxyConfig

    @property
    def data_dir(self) -> Path:
        p = Path(self.system.data_dir)
        return p if p.is_absolute() else self.project_root / p


