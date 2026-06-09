/* Shared TypeScript types matching the backend API schemas. */

export interface PipelineStatus {
  pipeline_status: string;
  current_iteration: number;
  last_checkpoint: string;
  metadata: Record<string, unknown>;
  is_hitl_gate: boolean;
  // GitHub Review mode fields (Phase 8)
  mode: string;
  current_story_id: string | null;
  stories_completed: number;
  stories_total: number;
}

export interface Iteration {
  id: number | null;
  iteration_number: number;
  status: string;
  prompt_path: string;
  prompt_content: string;
  review_json_path: string;
  review_json_content: string;
  summary_path: string;
  token_usage: number;
  cli_tool_used: string;
  ci_result: string;
  ci_output: string;
  started_at: string;
  completed_at: string | null;
}

export interface Requirement {
  id: string;
  type: string;
  parent_id: string | null;
  title: string;
  description: string;
  status: string;
}

export interface CurrentPrompt {
  iteration: number;
  content: string;
  path: string;
}

export interface CurrentReview {
  iteration: number;
  content: string;
  path: string;
}

export interface BusMessage {
  channel: string;
  sender: string;
  timestamp: string;
  // Orchestrator pipeline events use 'event' for the event type name
  event?: string;
  // Pipeline state snapshot included on every orchestrator event
  pipeline_status?: string;
  current_iteration?: number;
  // Legacy bus fields (still used by some routes)
  module_id?: string | null;
  iteration?: number;
  correlation_id?: string;
  payload?: Record<string, unknown>;
  // Allow any additional fields from the backend
  [key: string]: unknown;
}

export interface Metrics {
  total_iterations: number;
  total_token_usage: number;
  pipeline_status: string;
  total_cost?: number;
}

export interface ApproveGateResponse {
  approved: boolean;
  message: string;
}

export interface SecretsSettings {
  openai_api_key: string;
  github_token: string;
}

export interface GitHubSettings {
  owner: string;
  repo: string;
  auto_push: boolean;
  auto_create_pr: boolean;
}

export interface ProjectSettings {
  name: string;
  root_path: string;
  language: string;
  repo_name: string;
  feature_branch: string;
  prompt_file_path: string;
}

export interface PipelineSettings {
  max_iterations: number;
  convergence_rule: string;
  auto_approve_hitl: boolean;
}

export interface CliRoutingSettings {
  PROMPT_GENERATOR: string;
  CODE_GENERATOR: string;
  CODE_REVIEWER: string;
}

export interface GitHubReviewSettings {
  source_repo_url: string;
  requirements_path: string;
  fork_repo_name: string;
  branch_name: string;
}

export interface RequirementsSettings {
  path: string;
  source: 'device' | 'jira' | 'asana' | 'ado';
  jira_url?: string;
  jira_email?: string;
  jira_api_token?: string;
  jira_project_key?: string;
  asana_token?: string;
  asana_project_id?: string;
  ado_org?: string;
  ado_token?: string;
  ado_project?: string;
}

export interface AIToolCredential {
  enabled: boolean;
  auth_method: string;
  api_key?: string;
  email?: string;
  account_id?: string;
  endpoint?: string;
  extra?: Record<string, unknown>;
}

export interface AIToolsSettings {
  codex: AIToolCredential;
  claude: AIToolCredential;
  gemini: AIToolCredential;
  qwen: AIToolCredential;
  deepseek: AIToolCredential;
  cursor: AIToolCredential;
  copilot: AIToolCredential;
}

export interface VCSSettings {
  provider: 'github' | 'ado';
}

export interface OllamaSettings {
  base_url: string;
  model: string;
  timeout_seconds: number;
}

export interface PromptGeneratorSettings {
  provider: 'ollama' | 'openai';
  ollama_model: string;
  openai_model: string;
}

export interface Settings {
  secrets: SecretsSettings;
  github: GitHubSettings;
  project: ProjectSettings;
  pipeline: PipelineSettings;
  cli_routing: CliRoutingSettings;
  requirements: RequirementsSettings;
  github_review: GitHubReviewSettings;
  pipeline_mode: string;
  ai_tools?: AIToolsSettings;
  vcs?: VCSSettings;
  ollama?: OllamaSettings;
  prompt_generator?: PromptGeneratorSettings;
}

export interface TestGitHubResponse {
  valid: boolean;
  user: string;
  message: string;
}

export interface PromptEntry {
  iteration: number;
  content: string;
}

export interface ReviewEntry {
  iteration: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  content: Record<string, any>;
}

// ---------------------------------------------------------------------------
// Module (pipeline work unit)
// ---------------------------------------------------------------------------

export interface Module {
  id: string;
  name: string;
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  execution_order: number;
  feature_name?: string;
  dependency_ids: string[];
  pr_url?: string;
  pr_number?: number;
}

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: FileNode[];
  size?: number;
}

export interface ProjectInfo {
  name: string;
  root_path: string;
  language: string;
  exists: boolean;
  file_count: number;
}

export interface FileContent {
  path: string;
  content: string;
  size: number;
}

export interface OpenResponse {
  success: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Agents (Phase 6)
// ---------------------------------------------------------------------------

export interface AgentMeta {
  name: string;
  display_name: string;
  is_builtin: boolean;
  is_custom: boolean;
  files_present: string[];
  post_assignment: string | null;
}

export interface AgentDetail {
  name: string;
  is_builtin: boolean;
  files: Record<string, string>; // filename → content
}

export type AgentFile = 'soul' | 'skills' | 'tools' | 'ceiling' | 'brain';

export const AGENT_FILES: AgentFile[] = ['soul', 'skills', 'tools', 'ceiling', 'brain'];

export const PIPELINE_POSTS = [
  'PROMPT_GENERATOR',
  'CODE_GENERATOR',
  'CODE_REVIEWER',
] as const;

export type PipelinePost = (typeof PIPELINE_POSTS)[number];

// ---------------------------------------------------------------------------
// CLI Tool Management
// ---------------------------------------------------------------------------

export interface CliToolStatus {
  key: string;
  display_name: string;
  installed: boolean;
  install_cmd: string;
  docs_url: string;
  authenticated: boolean;
  auth_user: string;
  auth_method: string;
  env_configured: boolean;
  available: boolean;
  error: string;
}

export interface CliToolActionResponse {
  success: boolean;
  message: string;
  auth_user?: string;
  requires_browser?: boolean;
  browser_url?: string;
}

// ---------------------------------------------------------------------------
// Terminal output (Phase 7)
// ---------------------------------------------------------------------------

export type TerminalLineStyle = 'reasoning' | 'file_write' | 'error' | 'normal';

export interface TerminalLine {
  /** Monotonically increasing ID used as React key. */
  id: number;
  timestamp: string;
  eventType: 'line' | 'token' | 'session_start' | 'session_end';
  text: string;
  stream: 'stdout' | 'stderr' | null;
  style: TerminalLineStyle;
  sessionId: string;
}

export type AgentStatus = 'idle' | 'running' | 'done' | 'error';

export interface AgentTerminalState {
  /** e.g. "MODULE_MAKER" */
  agentPost: string;
  /** e.g. "module_maker" */
  senderName: string;
  /** Ring buffer — last N lines. */
  lines: TerminalLine[];
  status: AgentStatus;
  model: string | null;
  currentModuleId: string | null;
  currentIteration: number;
  /** ISO timestamp when current/last session started. */
  sessionStartedAt: string | null;
  /** Timestamp of session_end — used to compute final elapsed time. */
  sessionEndedAt: string | null;
  lastExitCode: number | null;
  /** Total sessions processed since page load. */
  sessionCount: number;
  /** The specific CLI tool running (e.g. 'codex', 'claude'). Only set for CODE_GENERATOR. */
  activeTool: string | null;
}
