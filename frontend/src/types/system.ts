export interface SlotStatus {
  slot: string
  state?: string
  stage?: string
  stage_zh?: string
  round?: number
  elapsed_s?: number
  proxy_display?: string
  last_reason?: string
  last_reason_zh?: string
  waiting_acquired?: boolean
  browser_mode?: string
  updated_at?: string
  round_started_at?: string
  stale?: boolean
  last_live_stage?: string
  result_stage?: string
  live_ticket_id?: string
  live_round?: number
  availability_days?: number
  availability_slots?: number
  matched_slots?: number
  target_hit?: boolean
  holders?: string[]
  booking_attempted?: boolean
  booking_ok?: boolean
  live_snapshot?: string
  live_snapshot_stage?: string
  live_snapshot_reason?: string
  live_page_stage?: string
  live_page_stage_zh?: string
  live_page_reason?: string
  live_page_url?: string
  live_page_title?: string
  realtime_status_zh?: string
  page_status_zh?: string
  query_status_zh?: string
  live_snapshot_page_stage?: string
  live_snapshot_page_stage_zh?: string
  live_snapshot_page_reason?: string
  live_snapshot_observed_at?: string
  live_snapshot_source?: string
  smart_query_wait_reason?: string
  smart_query_state?: string
  smart_query_next_allowed_at?: string
  smart_query_wait_seconds?: number
  dispatcher_candidate_role?: string
  dispatcher_candidate_score?: number
  dispatcher_candidate_reason?: string
  pool_role?: string
  scheduler_status?: string
  scheduler_status_zh?: string
  scheduler_reason?: string
  scheduler_blocked_reason?: string
  scheduler_recommended_action?: string
  query_eligible?: boolean
  reuse_score?: number
  session_success_count?: number
  session_failure_count?: number
  session_next_query_at?: string
  next_query_eta_seconds?: number
  last_success_age_seconds?: number
  recent_failure_kind?: string
  session_health_state?: string
  session_health_reason?: string
  session_health_score?: number
  session_query_ready?: boolean
  session_gate_allowed?: boolean
  recovery_error_type?: string
  recovery_action?: string
  recovery_component?: string
}

export interface SystemStatus {
  ts: string
  system: { name: string; pipeline_running: boolean; api_port: number; mode?: string; environment?: string; pipeline?: Record<string, any> }
  target: { post_name: string; cutoff_date: string; end_date: string; start_date?: string; post_aliases?: string[]; any_time?: boolean; post_id?: string; primary_id?: string; applications?: string[]; is_reschedule?: boolean; lang?: string }
  slot_policy: { total_slots: number; waiting_room_slots: number; direct_only_slots: string[]; queue_wait_seconds: number; direct_only_recycle_cooldown_seconds?: number; early_gate_timeout_seconds?: number; non_waiting_lane_timeout_seconds?: number; round_timeout_seconds?: number; login_wait_seconds?: number; login_submit_retries?: number }
  booking: { armed: boolean; max_parallel_submit: number }
  slots: SlotStatus[]
  latest_ticket: Record<string, any>
  ticket_history?: Array<Record<string, any>>
  ticket_query_count_today?: number
  availability_text: string
  booking_signal: Record<string, any>
  events: any[]
  smart_scheduler?: Record<string, any>
  sla_orchestrator?: Record<string, any>
}

export interface RuntimeConfig {
  system: Record<string, any>
  target: Record<string, any>
  slots: Record<string, any>
  producer: Record<string, any>
  smart_orchestrator?: Record<string, any>
  booking: Record<string, any>
  accounts?: Array<Record<string, any>>
  proxy: Record<string, any>
}
