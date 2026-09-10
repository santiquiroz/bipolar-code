export type Tier = 'trivial' | 'simple' | 'standard' | 'complex'
export type AgentId = 'claude' | 'codex' | 'copilot' | 'antigravity' | 'ollama'
export type QuotaReset = 'none' | '5h' | 'daily' | 'weekly'

export interface RouteTarget { provider_id: string; model: string }
export interface TierPolicy { tier: Tier; targets: RouteTarget[] }
export interface BudgetWindow {
  target_key: string
  window: 'day' | 'week' | 'month'
  max_requests: number
  max_cost_usd: number
}
export interface SmartRoutingConfig {
  enabled: boolean
  mode: 'shadow' | 'active'
  thresholds: Record<string, number>
  tiers: TierPolicy[]
  budgets: BudgetWindow[]
  honor_tier_header: boolean
  sticky_tool_loops: boolean
  sticky_ttl_seconds: number
  skip_cooling_providers: boolean
  respect_capabilities: boolean
}
export interface CliAgent {
  id: AgentId
  name: string
  enabled: boolean
  exe_path: string
  default_model: string
  model_by_tier: Record<string, string>
  alt_model_on_quota: string
  supported_tiers: string[]
  agentic: boolean
  priority: number
  cost_weight: number
  max_concurrency: number
  timeout_s: number
  cooldown_s: number
  quota_reset: QuotaReset
  max_credits: number
  extra_args: string[]
}
export interface DelegationConfig {
  enabled: boolean
  workspace_allowlist: string[]
  tier_order: Record<string, string[]>
  max_parallel_jobs: number
  max_attempts: number
  job_retention: number
}
export interface AgentStatus {
  id: string
  installed: boolean
  exe: string
  version: string
  auth: 'ok' | 'auth_error' | 'unknown'
  state: string
  seconds_left: number
  last_signal: string
  last_excerpt: string
  quota: Record<string, { remaining_fraction?: number; reset_time?: string; name?: string; models?: string[]; context_capped?: boolean }>
  default_model: string
  deny_list_present?: boolean | null
  running: number
  checked_at: string
  error: string
}
export interface TargetHealth {
  state: string
  until?: string
  last_signal?: string
  last_excerpt?: string
  consecutive_failures?: number
  total_ok?: number
  total_failed?: number
  seconds_left?: number
}
export interface BudgetUsage extends BudgetWindow {
  requests: number
  cost_usd: number
  exceeded: boolean
}
export interface Recommendation { code: string; message: string; patch?: { provider_id: string; updates: Record<string, unknown> } }
export interface SmartConfigResponse {
  smart: SmartRoutingConfig
  cli_agents: CliAgent[]
  delegation: DelegationConfig
  agents: AgentStatus[]
  health: Record<string, TargetHealth>
  budgets: Record<string, BudgetUsage>
  recommendations: Recommendation[]
  tiers: string[]
}
export interface Classification {
  tier: Tier
  score: number
  intent: string
  reasons: string[]
  source: string
  signals: Record<string, unknown>
}
export interface RouteDecision extends Classification {
  id: string
  chosen_key: string
  chosen_model: string
  would_key: string
  would_model: string
  mode: string
  rejected: [string, string][]
  decision_ms: number
  is_active: boolean
}
export interface DecisionRow extends RouteDecision {
  timestamp: string
  surface: string
  requested_model: string
  prompt_tokens: number
  outcome: string
  outcome_latency_ms?: number
  outcome_error?: string
}
export interface DecisionsSummary {
  period: string
  count: number
  by_tier: Record<string, number>
  by_target: Record<string, number>
  by_source: Record<string, number>
  outcomes: { ok: number; error: number; pending: number }
  agreement_rate: number
  avg_decision_ms: number
}
export interface Attempt {
  agent_id: string; model: string; started_at: string; finished_at?: string | null
  returncode?: number | null; signal: string; duration_s: number; error: string
}
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timeout' | 'cancelled' | 'quota' | 'auth_error'
export interface Job {
  id: string; created_at: string; started_at?: string | null; finished_at?: string | null
  status: JobStatus; mode: 'task' | 'text'; workspace: string; task_preview: string
  tier: string; score: number; reasons: string[]; skipped: string[][]; agent_id?: string | null
  model: string; attempts: Attempt[]; output_tail: string; files_touched: string[]
  error: string; tokens_in: number; tokens_out: number; cost_usd?: number | null; log_path: string
}
export interface JobRequest {
  task: string; workspace: string; mode: 'task' | 'text'; tier_hint?: Tier
  agent_id?: string; model?: string; timeout_s?: number; dry_run?: boolean
}
export interface SseEvent { event: 'status' | 'line' | 'attempt' | 'ping' | 'done'; [key: string]: unknown }
