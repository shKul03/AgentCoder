/* Settings — professional tabbed layout with real CLI-tool management */

import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../hooks/api';
import type { RequirementsUploadResponse, RemoteIngestRequest, RequirementsPreviewDoc } from '../hooks/api';
import type {
  AIToolCredential,
  CliToolStatus,
  Settings as SettingsType,
  TestGitHubResponse,
} from '../types';

/* ── Design tokens ─────────────────────────────────────────────────────────── */
const card  = 'rounded-xl border border-white/[0.06] bg-white/[0.03] p-6';
const label = 'block text-[11px] font-medium uppercase tracking-wider text-white/40 mb-1.5';
const input =
  'w-full rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-sm text-white/90 placeholder:text-white/20 focus:border-indigo-500/60 focus:ring-1 focus:ring-indigo-500/30 focus:outline-none transition-colors';
const btnPrimary =
  'rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40 transition-colors';
const btnSecondary =
  'rounded-lg border border-white/[0.08] bg-white/[0.04] px-4 py-2 text-xs font-medium text-white/70 hover:bg-white/[0.08] hover:text-white disabled:opacity-40 transition-colors';
const btnDanger =
  'rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-40 transition-colors';
const toggleBase =
  'relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer shrink-0';
const toggleDot = 'inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform';

/* ── Tab types ─────────────────────────────────────────────────────────────── */
type Tab = 'ai-tools' | 'github' | 'project' | 'pipeline' | 'requirements';

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'ai-tools',     label: 'AI Tools',        icon: '⚡' },
  { key: 'github',       label: 'VCS / Git',        icon: '🔗' },
  { key: 'project',      label: 'Project',          icon: '📁' },
  { key: 'pipeline',     label: 'Pipeline',         icon: '🔄' },
  { key: 'requirements', label: 'Requirements',     icon: '📋' },
];

/* ── Tool definitions ──────────────────────────────────────────────────────── */
type ToolKey = 'codex' | 'claude' | 'gemini' | 'qwen' | 'deepseek' | 'cursor' | 'copilot';

interface AuthMethod {
  id: string;
  label: string;
  description: string;
  type: 'api_key' | 'oauth' | 'cli_login';
}

interface ToolDef {
  key: ToolKey;
  name: string;
  accent: string;
  accentBg: string;
  description: string;
  methods: AuthMethod[];
}

const TOOLS: ToolDef[] = [
  {
    key: 'codex', name: 'OpenAI Codex CLI',
    accent: 'text-emerald-400', accentBg: 'bg-emerald-500/10 border-emerald-500/20',
    description: 'Code generation & editing powered by OpenAI models',
    methods: [
      { id: 'api_key', label: 'API Key', description: 'Paste your OpenAI API key', type: 'api_key' },
      { id: 'account', label: 'OpenAI Login', description: 'Sign in via browser', type: 'cli_login' },
    ],
  },
  {
    key: 'claude', name: 'Claude Code CLI',
    accent: 'text-orange-400', accentBg: 'bg-orange-500/10 border-orange-500/20',
    description: 'Anthropic\'s Claude for code analysis & generation',
    methods: [
      { id: 'api_key', label: 'API Key', description: 'Paste your Anthropic API key', type: 'api_key' },
      { id: 'account', label: 'Anthropic Login', description: 'Sign in via browser', type: 'cli_login' },
      { id: 'bedrock', label: 'AWS Bedrock', description: 'Use via AWS Bedrock', type: 'cli_login' },
    ],
  },
  {
    key: 'gemini', name: 'Gemini CLI',
    accent: 'text-blue-400', accentBg: 'bg-blue-500/10 border-blue-500/20',
    description: 'Google\'s Gemini models for code tasks',
    methods: [
      { id: 'api_key', label: 'API Key', description: 'Paste your Google AI API key', type: 'api_key' },
      { id: 'oauth', label: 'Google OAuth', description: 'Sign in with Google account', type: 'oauth' },
      { id: 'vertex', label: 'Vertex AI', description: 'Use via Google Cloud Vertex', type: 'cli_login' },
    ],
  },
  {
    key: 'qwen', name: 'Qwen Coder CLI',
    accent: 'text-violet-400', accentBg: 'bg-violet-500/10 border-violet-500/20',
    description: 'Alibaba\'s Qwen models for coding',
    methods: [
      { id: 'api_key',       label: 'API Key',                    description: 'Paste your DashScope API key',                type: 'api_key'   },
      { id: 'qwen-oauth',    label: 'Qwen OAuth',                 description: 'Sign in with Qwen account via browser',       type: 'cli_login' },
      { id: 'coding-plan',   label: 'Alibaba Cloud Coding Plan',  description: 'Sign in via Alibaba Cloud Coding Plan',       type: 'cli_login' },
    ],
  },
  {
    key: 'deepseek', name: 'DeepSeek CLI',
    accent: 'text-cyan-400', accentBg: 'bg-cyan-500/10 border-cyan-500/20',
    description: 'DeepSeek code models',
    methods: [
      { id: 'api_key', label: 'API Key', description: 'Paste your DeepSeek API key', type: 'api_key' },
      { id: 'local', label: 'Local Model', description: 'Endpoint for self-hosted model', type: 'api_key' },
    ],
  },
  {
    key: 'cursor', name: 'Cursor CLI',
    accent: 'text-slate-300', accentBg: 'bg-slate-500/10 border-slate-500/20',
    description: 'Cursor editor\'s AI capabilities via CLI',
    methods: [
      { id: 'account', label: 'Cursor Login', description: 'Sign in to Cursor account', type: 'cli_login' },
      { id: 'api_key', label: 'API Key', description: 'Passthrough API key', type: 'api_key' },
    ],
  },
  {
    key: 'copilot', name: 'GitHub Copilot CLI',
    accent: 'text-gray-300', accentBg: 'bg-gray-500/10 border-gray-500/20',
    description: 'GitHub Copilot via the gh CLI extension',
    methods: [
      { id: 'oauth', label: 'GitHub OAuth', description: 'Sign in with GitHub account', type: 'oauth' },
      { id: 'api_key', label: 'GitHub PAT', description: 'Use a personal access token', type: 'api_key' },
    ],
  },
];

const emptyCredential = (): AIToolCredential => ({
  enabled: false, auth_method: '', api_key: '', email: '', account_id: '', endpoint: '',
});

/* ── Requirements preview sub-components ─────────────────────────────────── */

import type { ReqEpic, ReqFeature, ReqStory } from '../hooks/api';

function ReqStoryBlock({ story }: { story: ReqStory }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.03] transition-colors"
      >
        <span className="text-[10px] text-white/30 font-mono w-16 shrink-0">{story.id}</span>
        <span className="flex-1 text-xs text-white/80 font-medium">{story.title}</span>
        <span className="text-[10px] text-white/30 shrink-0">{story.acceptance_criteria.length} AC</span>
        <svg className={`w-3 h-3 text-white/30 transition-transform shrink-0 ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-white/[0.04] px-3 py-2 space-y-1.5">
          {story.description && (
            <p className="text-[11px] text-white/40 leading-relaxed mb-2">{story.description}</p>
          )}
          {story.acceptance_criteria.length === 0 ? (
            <p className="text-[11px] text-white/25 italic">No acceptance criteria</p>
          ) : (
            story.acceptance_criteria.map((ac, i) => (
              <div key={ac.id} className="flex items-start gap-2">
                <span className="mt-0.5 w-4 h-4 rounded-full border border-emerald-500/40 bg-emerald-500/10 flex items-center justify-center shrink-0">
                  <svg className="w-2.5 h-2.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </span>
                <span className="text-[11px] text-white/60 leading-relaxed">
                  <span className="text-white/25 font-mono mr-1">{i + 1}.</span>{ac.title}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ReqFeatureBlock({ feature }: { feature: ReqFeature }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="ml-3 space-y-1.5">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-left group"
      >
        <svg className={`w-3 h-3 text-violet-400/60 transition-transform ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-[10px] font-mono text-white/25 w-14 shrink-0">{feature.id}</span>
        <span className="text-xs font-semibold text-violet-300/80 group-hover:text-violet-300">{feature.title}</span>
        <span className="text-[10px] text-white/25">{feature.stories.length} stor{feature.stories.length !== 1 ? 'ies' : 'y'}</span>
      </button>
      {open && (
        <div className="ml-5 space-y-1.5">
          {feature.stories.map((s) => <ReqStoryBlock key={s.id} story={s} />)}
        </div>
      )}
    </div>
  );
}

function ReqEpicBlock({ epic }: { epic: ReqEpic }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-white/[0.03] transition-colors"
      >
        <svg className={`w-3.5 h-3.5 text-indigo-400/60 transition-transform ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-400/70 font-mono">{epic.id}</span>
        <span className="flex-1 text-sm font-semibold text-white/90">{epic.title}</span>
        <span className="text-[10px] text-white/25">{epic.features.length} feature{epic.features.length !== 1 ? 's' : ''}</span>
      </button>
      {open && (
        <div className="border-t border-white/[0.05] px-4 py-3 space-y-3">
          {epic.description && (
            <p className="text-[11px] text-white/35 leading-relaxed">{epic.description}</p>
          )}
          {epic.features.map((f) => <ReqFeatureBlock key={f.id} feature={f} />)}
        </div>
      )}
    </div>
  );
}

/* ═════════════════════════════════════════════════════════════════════════════ */

export default function SettingsView() {
  /* ── Top-level state ────────────────────────────────────────────────────── */
  const [settings, setSettings]     = useState<SettingsType | null>(null);
  const [saving, setSaving]         = useState(false);
  const [toast, setToast]           = useState('');
  const [activeTab, setActiveTab]   = useState<Tab>('ai-tools');
  const [ghTest, setGhTest]         = useState<TestGitHubResponse | null>(null);

  /* ── Editable fields ────────────────────────────────────────────────────── */
  const [ghToken, setGhToken]             = useState('');
  const [ghOwner, setGhOwner]             = useState('');
  const [ghRepo, setGhRepo]               = useState('');
  const [autoPush, setAutoPush]           = useState(false);
  const [autoCreatePr, setAutoCreatePr]   = useState(false);
  const [vcsProvider, setVcsProvider]     = useState<'github' | 'ado'>('github');
  const [projName, setProjName]           = useState('');
  const [projRoot, setProjRoot]           = useState('');
  const [projLang, setProjLang]           = useState('python');
  const [repoName, setRepoName]           = useState('');
  const [featureBranch, setFeatureBranch] = useState('dev');
  const [promptFilePath, setPromptFilePath] = useState('');
  const [maxIter, setMaxIter]             = useState(5);
  const [convergence, setConvergence]     = useState('no_high_severity');
  const [autoApprove, setAutoApprove]     = useState(false);

  /* ── AI Tools ───────────────────────────────────────────────────────────── */
  const [aiTools, setAiTools] = useState<Record<ToolKey, AIToolCredential>>({
    codex: emptyCredential(), claude: emptyCredential(), gemini: emptyCredential(),
    qwen: emptyCredential(), deepseek: emptyCredential(), cursor: emptyCredential(),
    copilot: emptyCredential(),
  });
  const [expandedTool, setExpandedTool]   = useState<ToolKey | null>(null);
  const [toolStatuses, setToolStatuses]   = useState<Record<string, CliToolStatus>>({});
  const [toolLoading, setToolLoading]     = useState<Record<string, boolean>>({});
  const [toolMessage, setToolMessage]     = useState<Record<string, { ok: boolean; text: string }>>({});
  const [apiKeyInputs, setApiKeyInputs]   = useState<Record<string, string>>({});
  const [copiedCmd, setCopiedCmd]         = useState<Record<string, boolean>>({});

  /* ── Requirements ───────────────────────────────────────────────────────── */
  const [reqSource, setReqSource] = useState<'device' | 'jira' | 'asana' | 'ado'>('device');
  const [reqPath, setReqPath]     = useState('');
  const [reqStats, setReqStats]   = useState<{ epics: number; features: number; stories: number } | null>(null);
  const [reqError, setReqError]   = useState('');
  const [reqUploading, setReqUploading]   = useState(false);
  const [reqIngesting, setReqIngesting]   = useState(false);
  const [reqViewOpen, setReqViewOpen]     = useState(false);
  const [reqViewDoc, setReqViewDoc]       = useState<RequirementsPreviewDoc | null>(null);
  const [reqViewLoading, setReqViewLoading] = useState(false);
  const [reqValidating, setReqValidating] = useState(false);
  const [reqValidationResult, setReqValidationResult] = useState<{ valid: boolean; errors: string[]; warnings: string[] } | null>(null);
  const [reqViewError, setReqViewError]   = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [jiraUrl, setJiraUrl]             = useState('');
  const [jiraEmail, setJiraEmail]         = useState('');
  const [jiraToken, setJiraToken]         = useState('');
  const [jiraProject, setJiraProject]     = useState('');
  const [asanaToken, setAsanaToken]       = useState('');
  const [asanaProjectId, setAsanaProjectId] = useState('');
  const [adoOrg, setAdoOrg]               = useState('');
  const [adoToken, setAdoToken]           = useState('');
  const [adoProject, setAdoProject]       = useState('');
  const [adoProjects, setAdoProjects]     = useState<string[]>([]);
  const [adoProjectsLoading, setAdoProjectsLoading] = useState(false);
  const [adoProjectsFetchError, setAdoProjectsFetchError] = useState('');
  const [adoMcpOpen, setAdoMcpOpen]       = useState<Record<string, boolean>>({});

  /* ── Pipeline mode ──────────────────────────────────────────────────────── */
  const [pipelineMode, setPipelineMode]   = useState<'standard' | 'github_review'>('standard');
  const [ghReviewUrl, setGhReviewUrl]     = useState('');
  const [ghReviewReqPath, setGhReviewReqPath] = useState('');
  const [ghReviewReqUploading, setGhReviewReqUploading] = useState(false);
  const ghReviewFileInputRef = useRef<HTMLInputElement>(null);
  const [ghReviewForkName, setGhReviewForkName] = useState('');
  const [ghReviewBranch, setGhReviewBranch]     = useState('story-');

  /* ── Prompt Generator / Ollama ──────────────────────────────────────────── */
  const [pgProvider, setPgProvider]         = useState<'ollama' | 'openai'>('ollama');
  const [pgOllamaModel, setPgOllamaModel]   = useState('llama3.1:8b');
  const [pgOpenAIModel, setPgOpenAIModel]   = useState('gpt-4.1-mini');
  const [ollamaBaseUrl, setOllamaBaseUrl]   = useState('http://localhost:11434');
  const [ollamaTimeout, setOllamaTimeout]   = useState(300);

  /* ── Code Reviewer LLM Provider ─────────────────────────────────────────── */
  const [crProvider, setCrProvider]         = useState<'openai' | 'copilot' | 'ollama'>('openai');
  const [crModel, setCrModel]               = useState('gpt-4.1-mini');
  const [crOllamaModel, setCrOllamaModel]   = useState('llama3.1:8b');
  const [crCopilotModels, setCrCopilotModels] = useState<string[]>([]);
  const [crCopilotLoading, setCrCopilotLoading] = useState(false);

  /* ── Load settings ──────────────────────────────────────────────────────── */
  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setGhToken(s.secrets.github_token);
      setGhOwner(s.github.owner);
      setGhRepo(s.github.repo);
      // Auto-derive repo name from project root folder when not configured
      if (!s.github.repo && s.project.root_path) {
        const derivedRepo = s.project.root_path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || '';
        if (derivedRepo) setGhRepo(derivedRepo);
      }
      setAutoPush(s.github.auto_push);
      setAutoCreatePr(s.github.auto_create_pr);
      setVcsProvider(s.vcs?.provider === 'ado' ? 'ado' : 'github');
      setProjName(s.project.name);
      setProjRoot(s.project.root_path);
      setProjLang(s.project.language);
      setRepoName(s.project.repo_name ?? '');
      setFeatureBranch(s.project.feature_branch ?? 'dev');
      setPromptFilePath(s.project.prompt_file_path ?? '');
      setMaxIter(s.pipeline.max_iterations);
      setConvergence(s.pipeline.convergence_rule);
      setAutoApprove(s.pipeline.auto_approve_hitl);
      if (s.ai_tools) {
        setAiTools({
          codex:    { ...emptyCredential(), ...s.ai_tools.codex },
          claude:   { ...emptyCredential(), ...s.ai_tools.claude },
          gemini:   { ...emptyCredential(), ...s.ai_tools.gemini },
          qwen:     { ...emptyCredential(), ...s.ai_tools.qwen },
          deepseek: { ...emptyCredential(), ...s.ai_tools.deepseek },
          cursor:   { ...emptyCredential(), ...s.ai_tools.cursor },
          copilot:  { ...emptyCredential(), ...s.ai_tools.copilot },
        });
      }
      if (s.requirements) {
        setReqPath(s.requirements.path ?? '');
        setReqSource((s.requirements.source as typeof reqSource) ?? 'device');
        setJiraUrl(s.requirements.jira_url ?? '');
        setJiraEmail(s.requirements.jira_email ?? '');
        setJiraToken(s.requirements.jira_api_token ? '***' : '');
        setJiraProject(s.requirements.jira_project_key ?? '');
        setAsanaToken(s.requirements.asana_token ? '***' : '');
        setAsanaProjectId(s.requirements.asana_project_id ?? '');
        setAdoOrg(s.requirements.ado_org ?? '');
        setAdoToken(s.requirements.ado_token ? '***' : '');
        setAdoProject(s.requirements.ado_project ?? '');
      }
      if (s.pipeline_mode) {
        setPipelineMode(s.pipeline_mode === 'github_review' ? 'github_review' : 'standard');
      }
      if (s.github_review) {
        setGhReviewUrl(s.github_review.source_repo_url ?? '');
        setGhReviewReqPath(s.github_review.requirements_path ?? '');
        setGhReviewForkName(s.github_review.fork_repo_name ?? '');
        setGhReviewBranch(s.github_review.branch_name || 'story-');
      }
      if (s.ollama) {
        setOllamaBaseUrl(s.ollama.base_url || 'http://localhost:11434');
        setOllamaTimeout(s.ollama.timeout_seconds ?? 300);
      }
      if (s.prompt_generator) {
        setPgProvider(s.prompt_generator.provider === 'openai' ? 'openai' : 'ollama');
        setPgOllamaModel(s.prompt_generator.ollama_model || 'llama3.1:8b');
        setPgOpenAIModel(s.prompt_generator.openai_model || 'gpt-4.1-mini');
      }
      if (s.code_reviewer) {
        const p = (s.code_reviewer.provider as 'openai' | 'copilot' | 'ollama') || 'openai';
        setCrProvider(p);
        setCrModel(s.code_reviewer.model || 'gpt-4.1-mini');
        setCrOllamaModel(s.code_reviewer.ollama_model || 'llama3.1:8b');
      }
    }).catch(() => {});
  }, []);

  /* ── Load CLI tool statuses ──────────────────────────────────────────────── */
  const loadToolStatuses = useCallback(() => {
    api.getCliTools().then(({ tools }) => {
      const map: Record<string, CliToolStatus> = {};
      for (const t of tools) map[t.key] = t;
      setToolStatuses(map);
    }).catch(() => {});
  }, []);

  useEffect(() => { loadToolStatuses(); }, [loadToolStatuses]);

  /* ── Helpers ─────────────────────────────────────────────────────────────── */
  const flash = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  };

  const copyCommand = (toolKey: string, cmd: string) => {
    navigator.clipboard.writeText(cmd).then(() => {
      setCopiedCmd((p) => ({ ...p, [toolKey]: true }));
      setTimeout(() => setCopiedCmd((p) => ({ ...p, [toolKey]: false })), 2000);
    }).catch(() => {});
  };

  const openTerminal = (cmd: string) => {
    api.openInTerminal(cmd).catch(() => {});
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const aiToolsPayload = Object.fromEntries(
        (Object.keys(aiTools) as ToolKey[]).map((k) => {
          const cred = aiTools[k];
          return [k, {
            ...cred,
            api_key: cred.api_key && cred.api_key.startsWith('***') ? '' : (cred.api_key ?? ''),
          }];
        })
      ) as Record<ToolKey, AIToolCredential>;

      const updated = await api.updateSettings({
        secrets: { github_token: ghToken },
        github: { owner: ghOwner, repo: ghRepo, auto_push: autoPush, auto_create_pr: autoCreatePr },
        project: { name: projName, root_path: projRoot, language: projLang, repo_name: repoName, feature_branch: featureBranch, prompt_file_path: promptFilePath },
        pipeline: { max_iterations: maxIter, convergence_rule: convergence, auto_approve_hitl: autoApprove },
        ai_tools: aiToolsPayload,
        pipeline_mode: pipelineMode,
        vcs: { provider: vcsProvider },
        github_review: {
          source_repo_url: ghReviewUrl,
          requirements_path: reqPath,
          fork_repo_name: ghReviewForkName,
          branch_name: ghReviewBranch || 'story-',
        },
        requirements: {
          path: reqPath, source: reqSource,
          jira_url: jiraUrl, jira_email: jiraEmail,
          jira_api_token: jiraToken.startsWith('***') ? '' : jiraToken,
          jira_project_key: jiraProject,
          asana_token: asanaToken.startsWith('***') ? '' : asanaToken,
          asana_project_id: asanaProjectId,
          ado_org: adoOrg,
          ado_token: adoToken.startsWith('***') ? '' : adoToken,
          ado_project: adoProject,
        },
        ollama: {
          base_url: ollamaBaseUrl,
          model: pgOllamaModel,
          timeout_seconds: ollamaTimeout,
        },
        prompt_generator: {
          provider: pgProvider,
          ollama_model: pgOllamaModel,
          openai_model: pgOpenAIModel,
        },
        code_reviewer: {
          provider: crProvider,
          model: crModel,
          ollama_model: crOllamaModel,
        },
      });
      setSettings(updated);
      setGhToken(updated.secrets.github_token);
      flash('Settings saved');
    } catch {
      flash('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleTestGH = async () => {
    setGhTest(null);
    try {
      // Pass the live token from the input (unless it's a masked placeholder)
      const tokenToTest = ghToken && !ghToken.startsWith('***') && !ghToken.includes('...') ? ghToken : undefined;
      const r = await api.testGitHub(tokenToTest);
      setGhTest(r);
      // Auto-populate Owner from the authenticated GitHub user
      if (r.valid && r.user) setGhOwner(r.user);
      // Auto-populate Repo from project root folder name if still empty
      if (!ghRepo && projRoot) {
        const derivedRepo = projRoot.replace(/\\/g, '/').split('/').filter(Boolean).pop() || '';
        if (derivedRepo) setGhRepo(derivedRepo);
      }
    } catch {
      setGhTest({ valid: false, user: '', message: 'Request failed' });
    }
  };

  const fetchAdoProjects = async (org: string, token: string) => {
    if (!org) return;  // token may be '***' — backend resolves the saved PAT automatically
    setAdoProjectsLoading(true);
    setAdoProjectsFetchError('');
    try {
      const res = await api.getAdoProjects(org, token);
      setAdoProjects(res.projects);
      // If the currently stored project isn't in the fetched list, auto-select the first
      // available project so the controlled <select> value always matches an option.
      if (res.projects.length > 0 && !res.projects.includes(adoProject)) {
        setAdoProject(res.projects[0]);
      }
    } catch (err) {
      setAdoProjectsFetchError(err instanceof Error ? err.message : 'Failed to fetch projects');
      setAdoProjects([]);
    } finally {
      setAdoProjectsLoading(false);
    }
  };

  /* ── CLI tool actions ───────────────────────────────────────────────────── */
  const handleToolLogin = async (toolKey: string, authMethod: string, apiKey?: string) => {
    setToolLoading((p) => ({ ...p, [toolKey]: true }));
    setToolMessage((p) => ({ ...p, [toolKey]: { ok: true, text: '' } }));
    try {
      const res = await api.loginCliTool(toolKey, { auth_method: authMethod, api_key: apiKey });
      setToolMessage((p) => ({ ...p, [toolKey]: { ok: res.success, text: res.message } }));
      if (res.success) {
        setAiTools((prev) => ({
          ...prev,
          [toolKey]: { ...prev[toolKey as ToolKey], enabled: true, auth_method: authMethod },
        }));
        // Refresh status after a delay to let CLI auth complete
        setTimeout(() => {
          api.refreshCliTool(toolKey).then((st) => {
            setToolStatuses((p) => ({ ...p, [toolKey]: st }));
          }).catch(() => {});
        }, 2000);
      }
    } catch (err) {
      setToolMessage((p) => ({
        ...p,
        [toolKey]: { ok: false, text: err instanceof Error ? err.message : 'Login failed' },
      }));
    } finally {
      setToolLoading((p) => ({ ...p, [toolKey]: false }));
    }
  };

  const handleToolLogout = async (toolKey: string) => {
    setToolLoading((p) => ({ ...p, [toolKey]: true }));
    try {
      const res = await api.logoutCliTool(toolKey);
      setToolMessage((p) => ({ ...p, [toolKey]: { ok: res.success, text: res.message } }));
      if (res.success) {
        setAiTools((prev) => ({ ...prev, [toolKey]: emptyCredential() }));
        // Optimistically mark as unauthenticated so UI updates immediately
        setToolStatuses((p) => {
          const prev = p[toolKey];
          if (!prev) return p;
          return { ...p, [toolKey]: { ...prev, authenticated: false, auth_user: '', auth_method: '' } };
        });
        // Then confirm with a real status refresh
        api.refreshCliTool(toolKey).then((st) => {
          setToolStatuses((p) => ({ ...p, [toolKey]: st }));
        }).catch(() => {});
      }
    } catch (err) {
      setToolMessage((p) => ({
        ...p,
        [toolKey]: { ok: false, text: err instanceof Error ? err.message : 'Logout failed' },
      }));
    } finally {
      setToolLoading((p) => ({ ...p, [toolKey]: false }));
    }
  };

  const handleRefreshTool = async (toolKey: string) => {
    setToolLoading((p) => ({ ...p, [toolKey]: true }));
    try {
      const st = await api.refreshCliTool(toolKey);
      setToolStatuses((p) => ({ ...p, [toolKey]: st }));
      setToolMessage((p) => ({ ...p, [toolKey]: { ok: true, text: 'Status refreshed' } }));
    } catch {
      setToolMessage((p) => ({ ...p, [toolKey]: { ok: false, text: 'Could not refresh status' } }));
    } finally {
      setToolLoading((p) => ({ ...p, [toolKey]: false }));
    }
  };

  /* ── Loading state ──────────────────────────────────────────────────────── */
  if (!settings) {
    return (
      <div className="flex items-center justify-center h-64 text-white/40 text-sm">
        Loading settings…
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* RENDER                                                                     */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  return (
    <div className="max-w-5xl mx-auto">
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-white">Settings</h2>
          <p className="text-xs text-white/40 mt-0.5">Configure AI tools, integrations, and pipeline behavior</p>
        </div>
        <button onClick={handleSave} disabled={saving} className={btnPrimary}>
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>

      {/* ── Toast ────────────────────────────────────────────────────────── */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mb-4 rounded-lg bg-indigo-600/90 backdrop-blur px-4 py-2 text-sm text-white"
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Tab navigation ───────────────────────────────────────────────── */}
      <div className="flex gap-1 mb-6 border-b border-white/[0.06] pb-px">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`px-4 py-2.5 text-xs font-medium rounded-t-lg transition-colors relative ${
              activeTab === t.key
                ? 'text-white bg-white/[0.06]'
                : 'text-white/40 hover:text-white/70'
            }`}
          >
            <span className="mr-1.5">{t.icon}</span>
            {t.label}
            {activeTab === t.key && (
              <motion.div
                layoutId="tab-indicator"
                className="absolute bottom-0 left-0 right-0 h-px bg-indigo-500"
              />
            )}
          </button>
        ))}
      </div>

      {/* ── Tab content ──────────────────────────────────────────────────── */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.15 }}
        >
          {activeTab === 'ai-tools' && renderAITools()}
          {activeTab === 'github' && renderGitHub()}
          {activeTab === 'project' && renderProject()}
          {activeTab === 'pipeline' && renderPipeline()}
          {activeTab === 'requirements' && renderRequirements()}
        </motion.div>
      </AnimatePresence>

      {/* ── Global Requirements Preview Modal (shared across tabs) ───────── */}
      {renderReqPreviewModal()}
    </div>
  );

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* TAB: AI Tools                                                              */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  function renderAITools() {
    // OS detection — used to serve platform-appropriate commands
    const isWindows: boolean = (
      // Modern API (Chrome 90+) — most reliable
      ((navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData?.platform ?? '')
        .toLowerCase().includes('windows') ||
      // Legacy fallback
      navigator.platform.toLowerCase().startsWith('win')
    );
    const isMac: boolean = (
      ((navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData?.platform ?? '')
        .toLowerCase().includes('mac') ||
      navigator.platform.toLowerCase().startsWith('mac')
    );

    const adoOrgUrl = adoOrg ? `https://dev.azure.com/${adoOrg}` : 'https://dev.azure.com/{your-org}';
    const orgName = adoOrg || '{your-org}';

    // MCP JSON config — Windows must launch npx via cmd.exe; Mac/Linux call npx directly
    const mcpJsonFull = (org: string) => JSON.stringify({
      mcpServers: {
        'azure-devops': isWindows
          ? { command: 'cmd', args: ['/c', 'npx', '-y', '@azure-devops/mcp', org, '--authentication', 'azcli'] }
          : { command: 'npx', args: ['-y', '@azure-devops/mcp', org, '--authentication', 'azcli'] },
      },
    }, null, 2);

    type StepEntry = { label: string; content: string; isRunnable: boolean; isJson?: boolean; note?: string };
    type McpEntry = { steps: StepEntry[] };

    // Step 1 — Azure CLI install (OS-specific)
    const azCliInstallStep: StepEntry = isWindows
      ? {
          label: 'Step 1 — Install Azure CLI',
          content: 'winget install Microsoft.AzureCLI',
          isRunnable: true,
          note: 'After install, if az is still not recognised, run Step 2 below — no need to restart VS Code.',
        }
      : {
          label: 'Step 1 — Install Azure CLI',
          content: 'brew update && brew install azure-cli',
          isRunnable: true,
          note: 'After install, open a new terminal tab — Homebrew updates your PATH automatically.',
        };

    // Step 2 — PATH refresh (Windows only; Mac/Linux handled by Homebrew / new shell)
    const pathRefreshStep: StepEntry = isWindows
      ? {
          label: 'Step 2 — Refresh PATH (if az is still not recognised after install)',
          content: '$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")',
          isRunnable: true,
          note: 'Run this in the same terminal. It loads the new PATH immediately — no need to restart VS Code.',
        }
      : {
          label: 'Step 2 — Reload shell config (if az is still not recognised after install)',
          content: 'source ~/.zshrc',
          isRunnable: true,
          note: 'Run this in the same terminal. If you use bash instead of zsh, run `source ~/.bash_profile`.',
        };

    const ADO_MCP_SETUP: Partial<Record<ToolKey, McpEntry>> = {
      codex: {
        steps: [
          azCliInstallStep,
          pathRefreshStep,
          {
            label: 'Step 3 — Authenticate with Microsoft (opens browser)',
            content: 'az login',
            isRunnable: true,
          },
          {
            label: 'Step 4 — Remove any previously broken config (safe to skip if first time)',
            content: 'codex mcp remove azure-devops',
            isRunnable: true,
            note: 'Clears the old incorrect config. Ignore any "not found" error — it just means there was nothing to remove.',
          },
          {
            label: isWindows
              ? 'Step 5 — Register ADO MCP server with Codex CLI (Windows-compatible)'
              : 'Step 5 — Register ADO MCP server with Codex CLI',
            content: isWindows
              ? `codex mcp add azure-devops -- cmd /c npx -y @azure-devops/mcp ${orgName} --authentication azcli`
              : `codex mcp add azure-devops -- npx -y @azure-devops/mcp ${orgName} --authentication azcli`,
            isRunnable: true,
            note: isWindows
              ? 'Saves to ~/.codex/config.toml. Uses cmd /c npx (required on Windows) and the correct package @azure-devops/mcp.'
              : 'Saves to ~/.codex/config.toml. Uses the correct package @azure-devops/mcp.',
          },
          {
            label: 'Step 6 — Start Codex CLI',
            content: 'codex',
            isRunnable: true,
          },
          {
            label: 'Step 7 — Verify MCP connection (run this inside the Codex session)',
            content: '/mcp',
            isRunnable: false,
            note: 'Lists active MCP servers. You should see "azure-devops" in the output.',
          },
        ],
      },
      claude: {
        steps: [
          azCliInstallStep,
          pathRefreshStep,
          {
            label: 'Step 3 — Authenticate with Microsoft (opens browser)',
            content: 'az login',
            isRunnable: true,
          },
          {
            label: isWindows
              ? 'Step 4 — Register ADO MCP server with Claude Code (Windows-compatible)'
              : 'Step 4 — Register ADO MCP server with Claude Code',
            content: isWindows
              ? `claude mcp add azure-devops cmd -- /c npx -y @azure-devops/mcp ${orgName} --authentication azcli`
              : `claude mcp add azure-devops npx -- -y @azure-devops/mcp ${orgName} --authentication azcli`,
            isRunnable: true,
            note: isWindows
              ? 'Uses cmd /c npx (required on Windows) and the correct package @azure-devops/mcp.'
              : 'Uses the correct package @azure-devops/mcp.',
          },
          {
            label: 'Step 5 — Start Claude Code',
            content: 'claude',
            isRunnable: true,
          },
          {
            label: 'Step 6 — Verify MCP connection (run this inside the Claude session)',
            content: '/mcp',
            isRunnable: false,
            note: 'Lists active MCP servers. You should see "azure-devops" in the output.',
          },
        ],
      },
      gemini: {
        steps: [
          azCliInstallStep,
          pathRefreshStep,
          {
            label: 'Step 3 — Authenticate with Microsoft (opens browser)',
            content: 'az login',
            isRunnable: true,
          },
          {
            label: 'Step 4 — Open (or create) the Gemini config file',
            content: isWindows
              ? 'New-Item -Path "$env:USERPROFILE\\.gemini" -ItemType Directory -Force | Out-Null; notepad "$env:USERPROFILE\\.gemini\\settings.json"'
              : 'mkdir -p ~/.gemini && open -e ~/.gemini/settings.json',
            isRunnable: true,
            note: isWindows
              ? 'Windows path: C:\\Users\\<YourName>\\.gemini\\settings.json — If Notepad asks to create a new file, click Yes.'
              : isMac
              ? 'Mac path: ~/.gemini/settings.json — If TextEdit opens in Rich Text mode, choose Format → Make Plain Text before pasting.'
              : 'Linux path: ~/.gemini/settings.json — use your preferred text editor.',
          },
          {
            label: 'Step 5 — Paste this into the file (replace entire contents if file is new):',
            content: mcpJsonFull(orgName),
            isRunnable: false,
            isJson: true,
            note: 'If the file already has other settings, add only the "mcpServers" block — do not overwrite the rest.',
          },
          {
            label: 'Step 6 — Start Gemini CLI',
            content: 'gemini',
            isRunnable: true,
          },
          {
            label: 'Step 7 — Verify MCP connection (run this inside the Gemini session)',
            content: '/mcp',
            isRunnable: false,
            note: 'Lists active MCP servers. You should see "azure-devops" in the output.',
          },
        ],
      },
      cursor: {
        steps: [
          azCliInstallStep,
          pathRefreshStep,
          {
            label: 'Step 3 — Authenticate with Microsoft (opens browser)',
            content: 'az login',
            isRunnable: true,
          },
          {
            label: 'Step 4 — Open (or create) .cursor/mcp.json in your project root',
            content: isWindows
              ? 'New-Item -Path ".cursor" -ItemType Directory -Force | Out-Null; notepad ".cursor\\mcp.json"'
              : 'mkdir -p .cursor && open -e .cursor/mcp.json',
            isRunnable: true,
            note: isWindows
              ? 'Run this from your project root. If Notepad asks to create a new file, click Yes.'
              : isMac
              ? 'Run this from your project root. If TextEdit opens in Rich Text mode, choose Format → Make Plain Text before pasting.'
              : 'Run this from your project root using your preferred text editor.',
          },
          {
            label: 'Step 5 — Paste this into the file (replace entire contents if file is new):',
            content: mcpJsonFull(orgName),
            isRunnable: false,
            isJson: true,
            note: 'If the file already has other settings, add only the "mcpServers" block — do not overwrite the rest.',
          },
          {
            label: 'Step 6 — Reopen Cursor to load the new MCP configuration',
            content: 'cursor .',
            isRunnable: true,
            note: 'Or use Cursor → Settings → MCP to reload without fully restarting.',
          },
          {
            label: 'Step 7 — Verify: Cursor → Settings → MCP',
            content: '',
            isRunnable: false,
            note: 'Open Cursor Settings and navigate to the MCP section. You should see "azure-devops" listed as an active server.',
          },
        ],
      },
    };

    return (
      <div className="space-y-6">
        {/* Tool cards */}
        <div className="space-y-3">
          {TOOLS.map((tool) => {
            const status = toolStatuses[tool.key];
            const cred   = aiTools[tool.key];
            const isOpen = expandedTool === tool.key;
            const loading = toolLoading[tool.key] ?? false;
            const msg    = toolMessage[tool.key];

            const isInstalled    = status?.installed ?? false;
            const isAuthenticated = status?.authenticated ?? false;
            const authUser       = status?.auth_user ?? '';

            return (
              <div
                key={tool.key}
                className={`rounded-xl border transition-colors ${
                  isAuthenticated
                    ? 'border-green-500/20 bg-green-500/[0.02]'
                    : isInstalled
                    ? 'border-white/[0.06] bg-white/[0.02]'
                    : 'border-white/[0.04] bg-white/[0.01]'
                }`}
              >
                {/* ── Card header ────────────────────────────────────── */}
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer select-none"
                  onClick={() => setExpandedTool(isOpen ? null : tool.key)}
                >
                  {/* Status indicator */}
                  <div className={`w-2 h-2 rounded-full shrink-0 ${
                    isAuthenticated ? 'bg-green-400' :
                    isInstalled     ? 'bg-amber-400' :
                    'bg-white/20'
                  }`} />

                  {/* Name + description */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-medium ${tool.accent}`}>{tool.name}</span>
                      {!isInstalled && status && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-white/40 font-medium">
                          Not Installed
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-white/30 mt-0.5 truncate">{tool.description}</p>
                  </div>

                  {/* Auth badge */}
                  {isAuthenticated && (
                    <div className="flex items-center gap-1.5 shrink-0">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                      <span className="text-xs text-green-400/80 font-medium">
                        {authUser || 'Configured'}
                      </span>
                    </div>
                  )}

                  {/* Chevron */}
                  <svg
                    className={`w-4 h-4 text-white/30 shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </div>

                {/* ── Expanded panel ─────────────────────────────────── */}
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="px-5 pb-5 pt-1 border-t border-white/[0.04]">
                        {/* Not installed → Installation guide */}
                        {!isInstalled && status && (
                          <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.04] p-4 space-y-3">
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex items-start gap-2">
                                <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                                </svg>
                                <div>
                                  <p className="text-sm font-medium text-amber-300">
                                    {tool.name} is not installed
                                  </p>
                                  <p className="text-xs text-white/40 mt-1">
                                    Install it to enable configuration. Run the following in your terminal:
                                  </p>
                                </div>
                              </div>
                              {/* Copy + Open in Terminal icon buttons */}
                              <div className="flex items-center gap-1 shrink-0 ml-1">
                                <button
                                  title={copiedCmd[tool.key] ? 'Copied!' : 'Copy command'}
                                  onClick={(e) => { e.stopPropagation(); copyCommand(tool.key, status.install_cmd); }}
                                  className="p-1.5 rounded-md text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
                                >
                                  {copiedCmd[tool.key] ? (
                                    <svg className="w-3.5 h-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                    </svg>
                                  ) : (
                                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                    </svg>
                                  )}
                                </button>
                                <button
                                  title="Run in Terminal"
                                  onClick={(e) => { e.stopPropagation(); openTerminal(status.install_cmd); }}
                                  className="p-1.5 rounded-md text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
                                >
                                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                                  </svg>
                                </button>
                              </div>
                            </div>
                            <div className="rounded-lg bg-black/40 px-3 py-2.5">
                              <code className="text-xs text-amber-300/90 font-mono select-all">
                                {status.install_cmd}
                              </code>
                            </div>
                            <div className="flex items-center gap-3">
                              <a
                                href={status.docs_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs text-indigo-400 hover:text-indigo-300 hover:underline transition-colors"
                                onClick={(e) => e.stopPropagation()}
                              >
                                View setup documentation →
                              </a>
                              <button
                                onClick={(e) => { e.stopPropagation(); handleRefreshTool(tool.key); }}
                                disabled={loading}
                                className={btnSecondary}
                              >
                                {loading ? 'Checking…' : 'Re-check installation'}
                              </button>
                            </div>
                          </div>
                        )}

                        {/* Installed → Auth options */}
                        {isInstalled && (
                          <div className="space-y-4">
                            {/* Already authenticated → Show status & manage */}
                            {isAuthenticated && (
                              <div className="rounded-lg border border-green-500/20 bg-green-500/[0.04] p-4">
                                <div className="flex items-center justify-between">
                                  <div className="flex items-center gap-3">
                                    <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center">
                                      <svg className="w-4 h-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                      </svg>
                                    </div>
                                    <div>
                                      <p className="text-sm font-medium text-green-300">Authenticated</p>
                                      <p className="text-xs text-white/40 mt-0.5">
                                        {authUser}
                                        {status?.auth_method && (
                                          <span className="ml-1.5 text-white/25">
                                            ({status.auth_method})
                                          </span>
                                        )}
                                      </p>
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleRefreshTool(tool.key); }}
                                      disabled={loading}
                                      className={btnSecondary}
                                    >
                                      Refresh
                                    </button>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleToolLogout(tool.key); }}
                                      disabled={loading}
                                      className={btnDanger}
                                    >
                                      {loading ? 'Logging out…' : 'Logout'}
                                    </button>
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Auth methods — show when NOT authenticated or when user wants to switch */}
                            {!isAuthenticated && (
                              <>
                                <p className="text-xs text-white/40">
                                  Choose an authentication method to configure {tool.name}:
                                </p>
                                <div className="grid gap-3">
                                  {tool.methods.map((method) => (
                                    <div
                                      key={method.id}
                                      className={`rounded-lg border p-4 transition-colors ${
                                        cred.auth_method === method.id
                                          ? `${tool.accentBg}`
                                          : 'border-white/[0.06] hover:border-white/[0.12]'
                                      }`}
                                    >
                                      <div className="flex items-center justify-between mb-2">
                                        <div>
                                          <p className="text-sm font-medium text-white/80">{method.label}</p>
                                          <p className="text-xs text-white/30 mt-0.5">{method.description}</p>
                                        </div>
                                        {method.type === 'api_key' ? (
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              const key = apiKeyInputs[`${tool.key}_${method.id}`] ?? '';
                                              if (key && !key.startsWith('***')) {
                                                handleToolLogin(tool.key, method.id, key);
                                              }
                                            }}
                                            disabled={loading || !(apiKeyInputs[`${tool.key}_${method.id}`] ?? '').trim()}
                                            className={btnPrimary}
                                          >
                                            {loading ? 'Saving…' : 'Save Key'}
                                          </button>
                                        ) : (
                                          <button
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              handleToolLogin(tool.key, method.id);
                                            }}
                                            disabled={loading}
                                            className={btnPrimary}
                                          >
                                            {loading ? 'Launching…' : 'Sign In'}
                                          </button>
                                        )}
                                      </div>

                                      {/* API key input */}
                                      {method.type === 'api_key' && (
                                        <input
                                          type="password"
                                          className={input + ' mt-2'}
                                          value={apiKeyInputs[`${tool.key}_${method.id}`] ?? ''}
                                          onChange={(e) =>
                                            setApiKeyInputs((p) => ({
                                              ...p,
                                              [`${tool.key}_${method.id}`]: e.target.value,
                                            }))
                                          }
                                          onClick={(e) => e.stopPropagation()}
                                          placeholder={
                                            method.id === 'local' ? 'http://localhost:8080/v1' : 'Paste your API key…'
                                          }
                                        />
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </>
                            )}

                            {/* Refresh status button for installed-but-not-authenticated state */}
                            {isInstalled && !isAuthenticated && (
                              <div className="pt-1 flex justify-end">
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleRefreshTool(tool.key); }}
                                  disabled={loading}
                                  className={btnSecondary + ' flex items-center gap-1.5'}
                                >
                                  <svg className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                  </svg>
                                  {loading ? 'Checking…' : 'Refresh Status'}
                                </button>
                              </div>
                            )}

                            {/* Switch account (when authenticated) */}
                            {isAuthenticated && (
                              <div className="pt-2 border-t border-white/[0.04]">
                                <p className="text-xs text-white/30 mb-3">Switch authentication method:</p>
                                <div className="flex flex-wrap gap-2">
                                  {tool.methods.map((method) => (
                                    <button
                                      key={method.id}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        if (method.type === 'api_key') {
                                          setAiTools((prev) => ({
                                            ...prev,
                                            [tool.key]: { ...prev[tool.key], auth_method: method.id },
                                          }));
                                        } else {
                                          handleToolLogin(tool.key, method.id);
                                        }
                                      }}
                                      disabled={loading}
                                      className={btnSecondary}
                                    >
                                      {method.label}
                                    </button>
                                  ))}
                                </div>

                                {/* Inline API key input when switching to api_key method */}
                                {tool.methods.some(
                                  (m) => m.type === 'api_key' && cred.auth_method === m.id
                                ) && (
                                  <div className="mt-3 flex items-center gap-2">
                                    <input
                                      type="password"
                                      className={input}
                                      value={apiKeyInputs[`${tool.key}_switch`] ?? ''}
                                      onChange={(e) =>
                                        setApiKeyInputs((p) => ({
                                          ...p,
                                          [`${tool.key}_switch`]: e.target.value,
                                        }))
                                      }
                                      onClick={(e) => e.stopPropagation()}
                                      placeholder="New API key…"
                                    />
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        const key = apiKeyInputs[`${tool.key}_switch`] ?? '';
                                        if (key) handleToolLogin(tool.key, cred.auth_method, key);
                                      }}
                                      disabled={loading}
                                      className={btnPrimary}
                                    >
                                      Update
                                    </button>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Docs link */}
                            {status?.docs_url && (
                              <a
                                href={status.docs_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-block text-xs text-indigo-400 hover:text-indigo-300 hover:underline mt-1"
                                onClick={(e) => e.stopPropagation()}
                              >
                                Documentation →
                              </a>
                            )}

                            {/* ADO MCP Integration */}
                            {ADO_MCP_SETUP[tool.key] && (() => {
                              const mcpCfg = ADO_MCP_SETUP[tool.key]!;
                              const mcpKey = `${tool.key}-mcp`;
                              return (
                                <div className="mt-3 pt-3 border-t border-white/[0.04]">
                                  <button
                                    onClick={(e) => { e.stopPropagation(); setAdoMcpOpen(p => ({ ...p, [tool.key]: !p[tool.key] })); }}
                                    className="flex items-center gap-2 w-full text-left text-xs text-white/50 hover:text-white/70 transition-colors"
                                  >
                                    <svg className="w-3.5 h-3.5 text-blue-400/70 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                                    </svg>
                                    <span>Azure DevOps MCP</span>
                                    <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400/70 font-medium">Connect</span>
                                    <svg className={`w-3 h-3 ml-auto transition-transform ${adoMcpOpen[tool.key] ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                                    </svg>
                                  </button>

                                  {adoMcpOpen[tool.key] && (
                                    <div className="mt-3 space-y-4">
                                      <p className="text-xs text-white/40">
                                        Connect {tool.name} to the Azure DevOps MCP server so the agent can update work item
                                        states during code generation. Authenticate via your Microsoft account — no PAT stored in config.
                                      </p>

                                      {mcpCfg.steps.map((step, idx) => (
                                        <div key={idx}>
                                          <p className="text-[10px] text-white/40 mb-1.5 font-semibold uppercase tracking-wider">
                                            {step.label}
                                          </p>
                                          {step.content && (
                                            <div className="flex items-start gap-2">
                                              {step.isJson ? (
                                                <pre className="flex-1 min-w-0 rounded-lg bg-black/40 border border-white/[0.06] px-3 py-2.5 text-xs font-mono text-green-300/90 overflow-x-auto whitespace-pre-wrap break-all">
                                                  {step.content}
                                                </pre>
                                              ) : (
                                                <code className="flex-1 min-w-0 block rounded-lg bg-black/40 border border-white/[0.06] px-3 py-2 text-xs font-mono text-blue-300/90 break-all">
                                                  {step.content}
                                                </code>
                                              )}
                                              <div className="flex flex-col gap-1 shrink-0">
                                                <button
                                                  title={copiedCmd[`${mcpKey}-${idx}`] ? 'Copied!' : 'Copy'}
                                                  onClick={(e) => { e.stopPropagation(); copyCommand(`${mcpKey}-${idx}`, step.content); }}
                                                  className="p-1.5 rounded-md text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
                                                >
                                                  {copiedCmd[`${mcpKey}-${idx}`]
                                                    ? <svg className="w-3.5 h-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" /></svg>
                                                    : <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                                                  }
                                                </button>
                                                {step.isRunnable && (
                                                  <button
                                                    title="Run in Terminal"
                                                    onClick={(e) => { e.stopPropagation(); api.runInMcpTerminal(`ado-mcp-${tool.key}`, step.content).catch(() => {}); }}
                                                    className="p-1.5 rounded-md text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
                                                  >
                                                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                                                  </button>
                                                )}
                                              </div>
                                            </div>
                                          )}
                                          {step.note && (
                                            <p className="text-[10px] text-amber-400/60 mt-1.5">{step.note}</p>
                                          )}
                                          {step.isJson && !adoOrg && (
                                            <p className="text-[10px] text-amber-400/60 mt-1">
                                              ⚠ Set your ADO organisation in the Requirements tab to pre-fill the org URL.
                                            </p>
                                          )}
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                        )}

                        {/* Feedback message */}
                        {msg?.text && (
                          <motion.p
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className={`text-xs mt-3 ${msg.ok ? 'text-green-400/80' : 'text-red-400/80'}`}
                          >
                            {msg.text}
                          </motion.p>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>


      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* TAB: GitHub                                                                */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  function renderGitHub() {
    return (
      <div className="space-y-6">
        {/* VCS Provider selector */}
        <section className={card}>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
            VCS Target Provider
          </h3>
          <p className="text-[11px] text-white/40 mb-3">
            Choose where code is pushed and PRs are created. This is independent of where requirements are sourced.
          </p>
          <div className="flex gap-3">
            {[
              ['github', '🐙', 'GitHub'] as const,
              ['ado', '🔷', 'Azure DevOps'] as const,
            ].map(([val, icon, name]) => (
              <button
                key={val}
                onClick={() => setVcsProvider(val)}
                className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-all ${
                  vcsProvider === val
                    ? 'border-indigo-500/50 bg-indigo-500/10 text-white'
                    : 'border-white/[0.08] bg-white/[0.03] text-white/50 hover:border-white/20 hover:text-white/70'
                }`}
              >
                <span className="text-base">{icon}</span>
                {name}
              </button>
            ))}
          </div>
        </section>

        {/* GitHub-specific fields */}
        {vcsProvider === 'github' && (
          <>
            {/* Token */}
            <section className={card}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
                GitHub Authentication
              </h3>
              <div>
                <label className={label}>GitHub Personal Access Token</label>
                <div className="flex gap-2">
                  <input
                    type="password"
                    className={input}
                    value={ghToken}
                    onChange={(e) => setGhToken(e.target.value)}
                    placeholder="ghp_… or github_pat_…"
                  />
                  <button onClick={handleTestGH} className={btnSecondary}>
                    Test
                  </button>
                </div>
                {ghTest && (
                  <p className={`text-xs mt-2 ${ghTest.valid ? 'text-green-400/80' : 'text-red-400/80'}`}>
                    {ghTest.message}
                  </p>
                )}
                <p className="text-[10px] text-white/25 mt-1.5">
                  Also reads from GITHUB_TOKEN env var or .env file
                </p>
              </div>
            </section>

            {/* Repo */}
            <section className={card}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
                Repository
              </h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={label}>Owner (org or user)</label>
                  <input className={input} value={ghOwner} onChange={(e) => setGhOwner(e.target.value)} placeholder="my-org" />
                </div>
                <div>
                  <label className={label}>Repository</label>
                  <input className={input} value={ghRepo} onChange={(e) => setGhRepo(e.target.value)} placeholder="my-repo" />
                </div>
              </div>
              <div className="flex gap-6 mt-5">
                <label className="flex items-center gap-2.5 text-xs text-white/60 cursor-pointer">
                  <button
                    type="button"
                    onClick={() => setAutoPush(!autoPush)}
                    className={`${toggleBase} ${autoPush ? 'bg-indigo-600' : 'bg-white/10'}`}
                  >
                    <span className={`${toggleDot} ${autoPush ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                  </button>
                  Auto-push branches
                </label>
                <label className="flex items-center gap-2.5 text-xs text-white/60 cursor-pointer">
                  <button
                    type="button"
                    onClick={() => setAutoCreatePr(!autoCreatePr)}
                    className={`${toggleBase} ${autoCreatePr ? 'bg-indigo-600' : 'bg-white/10'}`}
                  >
                    <span className={`${toggleDot} ${autoCreatePr ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                  </button>
                  Auto-create PRs
                </label>
              </div>
            </section>
          </>
        )}

        {/* ADO-specific fields */}
        {vcsProvider === 'ado' && (
          <section className={card}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
              Azure DevOps Configuration
            </h3>
            <p className="text-[11px] text-white/40 mb-4">
              Provide your ADO credentials for git &amp; PR operations. Ensure your Personal Access Token has{' '}
              <strong className="text-white/60">Code (Read &amp; Write)</strong> and{' '}
              <strong className="text-white/60">Pull Request Contribute</strong> scopes.
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={label}>Organization</label>
                <input
                  className={input}
                  value={adoOrg}
                  onChange={(e) => { setAdoOrg(e.target.value); setAdoProjects([]); setAdoProjectsFetchError(''); }}
                  placeholder="my-ado-org"
                />
              </div>
              <div>
                <label className={label}>Project</label>
                <div className="flex gap-2">
                  <select
                    className={`${input} flex-1`}
                    value={adoProject}
                    onChange={(e) => setAdoProject(e.target.value)}
                    disabled={adoProjectsLoading}
                  >
                    {adoProjects.length === 0 ? (
                      <option value="">{adoProjectsLoading ? 'Loading…' : '— fetch projects —'}</option>
                    ) : (
                      <>
                        <option value="">— select a project —</option>
                        {adoProjects.map((p) => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </>
                    )}
                  </select>
                  <button
                    type="button"
                    disabled={adoProjectsLoading || !adoOrg || !adoToken || adoToken.startsWith('***')}
                    onClick={() => fetchAdoProjects(adoOrg, adoToken)}
                    className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed text-white whitespace-nowrap"
                  >
                    {adoProjectsLoading ? '…' : 'Fetch'}
                  </button>
                </div>
                {adoProjectsFetchError && <p className="text-[10px] text-red-400/80 mt-1">{adoProjectsFetchError}</p>}
              </div>
            </div>
            <div className="mt-4">
              <label className={label}>ADO Personal Access Token</label>
              <input
                type="password"
                className={input}
                value={adoToken}
                onChange={(e) => setAdoToken(e.target.value)}
                placeholder="Enter your ADO PAT"
              />
            </div>
          </section>
        )}
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* TAB: Project                                                               */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  function renderProject() {
    return (
      <section className={card}>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
          Project Information
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={label}>Project Name</label>
            <input className={input} value={projName} onChange={(e) => setProjName(e.target.value)} />
          </div>
          <div>
            <label className={label}>Language</label>
            <select className={input} value={projLang} onChange={(e) => setProjLang(e.target.value)}>
              <option value="python">Python</option>
              <option value="typescript">TypeScript</option>
              <option value="javascript">JavaScript</option>
              <option value="java">Java</option>
              <option value="go">Go</option>
              <option value="rust">Rust</option>
            </select>
          </div>
        </div>
        <div className="mt-4">
          <label className={label}>Root Path</label>
          <input className={input} value={projRoot} onChange={(e) => setProjRoot(e.target.value)} placeholder="/path/to/project" />
        </div>
        <div className="grid grid-cols-2 gap-4 mt-4">
          <div>
            <label className={label}>Repository Name</label>
            <input
              className={input}
              value={repoName}
              onChange={(e) => setRepoName(e.target.value)}
              placeholder="my-project"
            />
            <p className="text-[10px] text-white/25 mt-1">
              Used for GitHub/ADO repo creation and local folder name
            </p>
          </div>
          <div>
            <label className={label}>Feature Branch Name</label>
            <input
              className={input}
              value={featureBranch}
              onChange={(e) => setFeatureBranch(e.target.value)}
              placeholder="dev"
            />
            <p className="text-[10px] text-white/25 mt-1">
              Branch used for all fix iterations (default: dev)
            </p>
          </div>
        </div>
        <div className="mt-4">
          <label className={label}>Prompt File Path</label>
          <input
            className={input}
            value={promptFilePath}
            onChange={(e) => setPromptFilePath(e.target.value)}
            placeholder="data/prompts/latest.md"
          />
          <p className="text-[10px] text-white/25 mt-1">
            Fixed location where the Prompt Generator writes the generated prompt
          </p>
        </div>
      </section>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* TAB: Pipeline                                                              */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  function renderPipeline() {
    return (
      <div className="space-y-6">
        {/* Execution settings */}
        <section className={card}>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
            Execution
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={label}>Max Iterations</label>
              <input
                type="number" min={1} max={20}
                className={input}
                value={maxIter}
                onChange={(e) => setMaxIter(Number(e.target.value))}
              />
            </div>
            <div>
              <label className={label}>Convergence Rule</label>
              <select className={input} value={convergence} onChange={(e) => setConvergence(e.target.value)}>
                <option value="no_high_severity">No High Severity</option>
                <option value="no_critical">No Critical</option>
                <option value="all_accepted">All Accepted</option>
              </select>
            </div>
          </div>
          <div className="mt-4">
            <label className="flex items-center gap-2.5 text-xs text-white/60 cursor-pointer">
              <button
                type="button"
                onClick={() => setAutoApprove(!autoApprove)}
                className={`${toggleBase} ${autoApprove ? 'bg-indigo-600' : 'bg-white/10'}`}
              >
                <span className={`${toggleDot} ${autoApprove ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              Auto-approve HITL gates (debug mode)
            </label>
          </div>
        </section>

        {/* Pipeline mode */}
        <section className={card}>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-4">
            Mode
          </h3>
          <div className="space-y-3">
            <label className="flex items-start gap-3 cursor-pointer p-3 rounded-lg border border-white/[0.06] hover:border-white/[0.1] transition-colors">
              <input
                type="radio" name="pipeline_mode" value="standard"
                checked={pipelineMode === 'standard'}
                onChange={() => setPipelineMode('standard')}
                className="mt-0.5 accent-indigo-500"
              />
              <div>
                <p className="text-sm font-medium text-white/80">Standard — Generate code from requirements</p>
                <p className="text-xs text-white/30 mt-0.5">
                  Module Maker builds modules from requirements, then iterates through Code Gen → Review.
                </p>
              </div>
            </label>
            <label className="flex items-start gap-3 cursor-pointer p-3 rounded-lg border border-white/[0.06] hover:border-white/[0.1] transition-colors">
              <input
                type="radio" name="pipeline_mode" value="github_review"
                checked={pipelineMode === 'github_review'}
                onChange={() => setPipelineMode('github_review')}
                className="mt-0.5 accent-indigo-500"
              />
              <div>
                <p className="text-sm font-medium text-white/80">GitHub Review — Improve an existing repo</p>
                <p className="text-xs text-white/30 mt-0.5">
                  Forks a GitHub repo, runs a full requirements-aware code review, then iterates fixes.
                </p>
              </div>
            </label>
          </div>

          {pipelineMode === 'github_review' && (
            <div className="mt-4 pt-4 border-t border-white/[0.04] space-y-4">

              {/* Source Repository URL */}
              <div>
                <label className={label}>Source Repository URL</label>
                <input
                  className={input}
                  value={ghReviewUrl}
                  onChange={(e) => setGhReviewUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo"
                />
              </div>

              {/* Requirements Source — shares state with the Requirements tab */}
              <div>
                <p className={label}>Requirements Source</p>
                <div className="grid grid-cols-4 gap-2 mb-3">
                  {([
                    ['device', '📁', 'From Device'],
                    ['jira',   '🟦', 'JIRA'],
                    ['asana',  '🟧', 'Asana'],
                    ['ado',    '🟦', 'Azure DevOps'],
                  ] as [typeof reqSource, string, string][]).map(([val, icon, name]) => (
                    <button
                      key={val}
                      onClick={() => { setReqSource(val); setReqError(''); setReqStats(null); }}
                      className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-medium transition-colors ${
                        reqSource === val
                          ? 'border-indigo-500/40 bg-indigo-500/10 text-white'
                          : 'border-white/[0.06] text-white/50 hover:border-white/[0.12]'
                      }`}
                    >
                      <span>{icon}</span> {name}
                    </button>
                  ))}
                </div>

                {/* Device */}
                {reqSource === 'device' && (
                  <div className="space-y-2">
                    <input
                      ref={fileInputRef} type="file"
                      accept=".xlsx,.csv,.txt,.yaml,.yml"
                      className="hidden"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        setReqError(''); setReqStats(null); setReqUploading(true);
                        try {
                          const res: RequirementsUploadResponse = await api.uploadRequirements(file);
                          setReqPath(res.path);
                          const s = res.stats;
                          setReqStats({
                            epics:    s?.epics    ?? (res as any).epics    ?? 0,
                            features: s?.features ?? (res as any).features ?? 0,
                            stories:  s?.stories  ?? (res as any).tasks    ?? 0,
                          });
                        } catch (err) {
                          setReqError(err instanceof Error ? err.message : 'Upload failed');
                        } finally {
                          setReqUploading(false);
                          if (fileInputRef.current) fileInputRef.current.value = '';
                        }
                      }}
                    />
                    <div className="flex items-center gap-3">
                      <button onClick={() => fileInputRef.current?.click()} disabled={reqUploading} className={btnSecondary}>
                        {reqUploading ? 'Uploading…' : 'Browse & Upload…'}
                      </button>
                      {reqPath ? (
                        <span className="text-xs font-mono text-indigo-300/80 truncate max-w-xs">{reqPath.split('/').pop()}</span>
                      ) : (
                        <span className="text-xs text-white/25">No file chosen</span>
                      )}
                    </div>
                  </div>
                )}

                {/* JIRA */}
                {reqSource === 'jira' && (
                  <div className="space-y-3">
                    <div>
                      <label className={label}>JIRA Base URL</label>
                      <input className={input} value={jiraUrl} onChange={(e) => setJiraUrl(e.target.value)} placeholder="https://yourorg.atlassian.net" />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className={label}>Email</label>
                        <input className={input} type="email" value={jiraEmail} onChange={(e) => setJiraEmail(e.target.value)} placeholder="you@example.com" />
                      </div>
                      <div>
                        <label className={label}>Project Key</label>
                        <input className={input} value={jiraProject} onChange={(e) => setJiraProject(e.target.value.toUpperCase())} placeholder="PROJ" />
                      </div>
                    </div>
                    <div>
                      <label className={label}>API Token</label>
                      <input className={input} type="password" value={jiraToken} onChange={(e) => setJiraToken(e.target.value)} placeholder="Atlassian API token" />
                    </div>
                    <button
                      disabled={reqIngesting || !jiraUrl || !jiraEmail || !jiraToken || !jiraProject}
                      onClick={async () => {
                        setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                        try {
                          const res = await api.ingestRemoteRequirements({
                            source: 'jira', jira_url: jiraUrl, jira_email: jiraEmail,
                            jira_api_token: jiraToken.startsWith('***') ? '' : jiraToken,
                            jira_project_key: jiraProject,
                          });
                          setReqPath(res.path);
                          const s = res.stats;
                          setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                        } catch (err) {
                          setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                        } finally { setReqIngesting(false); }
                      }}
                      className={btnPrimary}
                    >
                      {reqIngesting ? 'Importing…' : 'Import from JIRA'}
                    </button>
                  </div>
                )}

                {/* Asana */}
                {reqSource === 'asana' && (
                  <div className="space-y-3">
                    <div>
                      <label className={label}>Personal Access Token</label>
                      <input className={input} type="password" value={asanaToken} onChange={(e) => setAsanaToken(e.target.value)} placeholder="Asana PAT" />
                    </div>
                    <div>
                      <label className={label}>Project GID</label>
                      <input className={input} value={asanaProjectId} onChange={(e) => setAsanaProjectId(e.target.value)} placeholder="1234567890123456" />
                    </div>
                    <button
                      disabled={reqIngesting || !asanaToken || !asanaProjectId}
                      onClick={async () => {
                        setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                        try {
                          const res = await api.ingestRemoteRequirements({
                            source: 'asana',
                            asana_token: asanaToken.startsWith('***') ? '' : asanaToken,
                            asana_project_id: asanaProjectId,
                          });
                          setReqPath(res.path);
                          const s = res.stats;
                          setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                        } catch (err) {
                          setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                        } finally { setReqIngesting(false); }
                      }}
                      className={btnPrimary}
                    >
                      {reqIngesting ? 'Importing…' : 'Import from Asana'}
                    </button>
                  </div>
                )}

                {/* ADO */}
                {reqSource === 'ado' && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className={label}>Organisation</label>
                        <input
                          className={input}
                          value={adoOrg}
                          onChange={(e) => { setAdoOrg(e.target.value); setAdoProjects([]); setAdoProjectsFetchError(''); }}
                          placeholder="my-org"
                        />
                      </div>
                      <div>
                        <label className={label}>Project</label>
                        <div className="flex gap-2">
                          <select
                            className={`${input} flex-1`}
                            value={adoProject}
                            onChange={(e) => setAdoProject(e.target.value)}
                            disabled={adoProjectsLoading}
                          >
                            {adoProjects.length === 0 ? (
                              <option value="">{adoProjectsLoading ? 'Loading…' : '— fetch projects —'}</option>
                            ) : (
                              <>
                                <option value="">— select a project —</option>
                                {adoProjects.map((p) => (
                                  <option key={p} value={p}>{p}</option>
                                ))}
                              </>
                            )}
                          </select>
                          <button
                            type="button"
                            disabled={adoProjectsLoading || !adoOrg}
                            onClick={() => fetchAdoProjects(adoOrg, adoToken)}
                            className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed text-white whitespace-nowrap"
                          >
                            {adoProjectsLoading ? '…' : 'Fetch'}
                          </button>
                        </div>
                        {adoProjectsFetchError && <p className="text-[10px] text-red-400/80 mt-1">{adoProjectsFetchError}</p>}
                      </div>
                    </div>
                    <div>
                      <label className={label}>Personal Access Token</label>
                      <input
                        className={input}
                        type="password"
                        value={adoToken}
                        onChange={(e) => { setAdoToken(e.target.value); setAdoProjects([]); setAdoProjectsFetchError(''); }}
                        placeholder="ADO PAT"
                      />
                    </div>
                    <button
                      disabled={reqIngesting || !adoOrg || !adoToken || !adoProject}
                      onClick={async () => {
                        setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                        try {
                          const res = await api.ingestRemoteRequirements({
                            source: 'ado', ado_org: adoOrg,
                            ado_token: adoToken.startsWith('***') ? '' : adoToken,
                            ado_project: adoProject,
                          });
                          setReqPath(res.path);
                          const s = res.stats;
                          setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                        } catch (err) {
                          setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                        } finally { setReqIngesting(false); }
                      }}
                      className={btnPrimary}
                    >
                      {reqIngesting ? 'Importing…' : 'Import from ADO'}
                    </button>
                  </div>
                )}

                {/* Ingestion feedback */}
                {reqStats && (
                  <p className="text-xs text-green-400/80 mt-2">
                    Loaded: {reqStats.epics} epics · {reqStats.features} features · {reqStats.stories} stories
                  </p>
                )}
                {reqError && <p className="text-xs text-red-400/80 mt-2">{reqError}</p>}
              </div>

              {/* Fork Name Override + Branch Name Prefix */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={label}>Fork Name Override</label>
                  <input className={input} value={ghReviewForkName} onChange={(e) => setGhReviewForkName(e.target.value)} placeholder="my-repo" />
                </div>
                <div>
                  <label className={label}>Branch Name Prefix</label>
                  <input className={input} value={ghReviewBranch} onChange={(e) => setGhReviewBranch(e.target.value)} placeholder="story-" />
                  <p className="text-[10px] text-white/25 mt-1">Branches are auto-named: prefix + story-id (e.g. story-42-auth)</p>
                </div>
              </div>

              {/* View Requirements — same modal used in the Requirements tab */}
              <div className="pt-2 border-t border-white/[0.04] flex items-center gap-3">
                <p className="text-xs text-white/30 flex-1">
                  Preview the requirements that will be used for this GitHub Review run.
                </p>
                <button
                  onClick={async () => {
                    setReqViewError('');
                    setReqViewDoc(null);
                    setReqViewOpen(true);
                    setReqViewLoading(true);
                    try {
                      const doc = await api.previewRequirements();
                      setReqViewDoc(doc);
                    } catch (err) {
                      setReqViewError(err instanceof Error ? err.message : 'Failed to load preview');
                    } finally {
                      setReqViewLoading(false);
                    }
                  }}
                  className="flex items-center gap-1.5 px-3 py-1 rounded-lg border border-indigo-500/30 bg-indigo-500/10 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20 transition-colors shrink-0"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                  View Requirements
                </button>
              </div>
            </div>
          )}
        </section>

        {/* ── Prompt Generator LLM Provider ──────────────────────────────── */}
        <section className={card}>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-1">
            Prompt Generator
          </h3>
          <p className="text-[11px] text-white/30 mb-4">
            Choose the LLM backend used when generating implementation and fix prompts.
            Ollama connects to your local or remote GPU; OpenAI uses the API key.
          </p>

          {/* Provider toggle */}
          <div className="flex gap-3 mb-5">
            {([
              ['ollama', '🦙', 'Ollama (GPU)'] as const,
              ['openai', '✦', 'OpenAI API'] as const,
            ]).map(([val, icon, name]) => (
              <button
                key={val}
                onClick={() => setPgProvider(val)}
                className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-all ${
                  pgProvider === val
                    ? 'border-indigo-500/50 bg-indigo-500/10 text-white'
                    : 'border-white/[0.08] bg-white/[0.03] text-white/50 hover:border-white/20 hover:text-white/70'
                }`}
              >
                <span>{icon}</span>
                {name}
              </button>
            ))}
          </div>

          {/* Ollama config */}
          {pgProvider === 'ollama' && (
            <div className="space-y-4">
              <div>
                <label className={label}>Ollama Base URL</label>
                <input
                  className={input}
                  value={ollamaBaseUrl}
                  onChange={(e) => setOllamaBaseUrl(e.target.value)}
                  placeholder="http://192.168.x.x:11434"
                />
                <p className="text-[10px] text-white/25 mt-1.5">
                  Remote GPU over VPN — set the full URL including port, e.g.{' '}
                  <code className="text-white/40">http://192.168.10.5:11434</code>. 
                  Also configurable via <code className="text-white/40">OLLAMA_BASE_URL</code> in <code className="text-white/40">.env</code>.
                </p>
              </div>
              <div>
                <label className={label}>Model</label>
                <select
                  className={input}
                  value={pgOllamaModel}
                  onChange={(e) => setPgOllamaModel(e.target.value)}
                >
                  {[
                    'llama3.1:8b',
                    'llama3.2:3b',
                    'llama3:latest',
                    'qwen2.5:7b',
                    'qwen2.5-coder:32b',
                    'gemma3:4b',
                    'mistral-nemo:latest',
                    'ibm/granite-docling:latest',
                    'nomic-embed-text:latest',
                  ].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <p className="text-[10px] text-white/25 mt-1.5">
                  Default: <span className="text-white/40">llama3.1:8b</span>. All models listed are available on the remote GPU.
                </p>
              </div>
              <div>
                <label className={label}>Request Timeout (seconds)</label>
                <input
                  type="number" min={30} max={1200}
                  className={input}
                  value={ollamaTimeout}
                  onChange={(e) => setOllamaTimeout(Number(e.target.value))}
                />
              </div>
            </div>
          )}

          {/* OpenAI config */}
          {pgProvider === 'openai' && (
            <div className="space-y-4">
              <div>
                <label className={label}>OpenAI Model</label>
                <input
                  className={input}
                  value={pgOpenAIModel}
                  onChange={(e) => setPgOpenAIModel(e.target.value)}
                  placeholder="gpt-4.1-mini"
                />
                <p className="text-[10px] text-white/25 mt-1.5">
                  Any OpenAI chat model, e.g. <code className="text-white/40">gpt-4.1-mini</code>, <code className="text-white/40">gpt-4.1</code>, <code className="text-white/40">o3</code>.
                  The API key is configured under <span className="text-white/40">VCS / Git → GitHub Authentication</span> or the <code className="text-white/40">OPENAI_API_KEY</code> env var.
                </p>
              </div>
            </div>
          )}
        </section>

        {/* ── Code Reviewer LLM Provider ─────────────────────────────────── */}
        <section className={card}>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-1">
            Code Reviewer
          </h3>
          <p className="text-[11px] text-white/30 mb-4">
            Choose the LLM backend for automated PR code review.
            Copilot uses your GitHub token; OpenAI uses the OpenAI API key; Ollama connects to your local GPU.
          </p>

          {/* Provider toggle */}
          <div className="flex gap-3 mb-5">
            {([
              ['openai', '✦', 'OpenAI API'] as const,
              ['copilot', '🤖', 'GitHub Copilot'] as const,
              ['ollama', '🦙', 'Ollama (GPU)'] as const,
            ]).map(([val, icon, name]) => (
              <button
                key={val}
                onClick={() => setCrProvider(val)}
                className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm transition-all ${
                  crProvider === val
                    ? 'border-indigo-500/50 bg-indigo-500/10 text-white'
                    : 'border-white/[0.08] bg-white/[0.03] text-white/50 hover:border-white/20 hover:text-white/70'
                }`}
              >
                <span>{icon}</span>
                {name}
              </button>
            ))}
          </div>

          {/* OpenAI config */}
          {crProvider === 'openai' && (
            <div className="space-y-4">
              <div>
                <label className={label}>OpenAI Model</label>
                <select
                  className={input}
                  value={crModel}
                  onChange={(e) => setCrModel(e.target.value)}
                >
                  {[
                    'gpt-5.2', 'gpt-5-mini',
                    'gpt-4.1', 'gpt-4.1-2025-04-14',
                    'gpt-4o', 'gpt-4o-mini',
                    'gpt-4', 'gpt-3.5-turbo',
                  ].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <p className="text-[10px] text-white/25 mt-1.5">
                  Requires <code className="text-white/40">OPENAI_API_KEY</code> env var.
                </p>
              </div>
            </div>
          )}

          {/* Copilot config */}
          {crProvider === 'copilot' && (
            <div className="space-y-4">
              <div>
                <label className={label}>Copilot Model</label>
                <select
                  className={input}
                  value={crModel}
                  onChange={(e) => setCrModel(e.target.value)}
                >
                  {[
                    // GPT-5 (chat models only — codex variants not accessible via /chat/completions)
                    'gpt-5.2', 'gpt-5-mini',
                    // GPT-4
                    'gpt-4.1', 'gpt-4.1-2025-04-14',
                    'gpt-4o', 'gpt-4o-2024-11-20', 'gpt-4o-2024-08-06', 'gpt-4o-mini',
                    'gpt-4', 'gpt-3.5-turbo',
                    // Claude
                    'claude-haiku-4.5',
                    // Gemini
                    'gemini-3.1-pro-preview', 'gemini-2.5-pro',
                  ].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <p className="text-[10px] text-white/25 mt-1.5">
                  Uses your <code className="text-white/40">GITHUB_TOKEN</code> — no OpenAI key required.
                  If the selected model is not in your plan, the terminal will show a "model not available" message.
                </p>
              </div>
            </div>
          )}

          {/* Ollama config */}
          {crProvider === 'ollama' && (
            <div className="space-y-4">
              <div>
                <label className={label}>Ollama Model</label>
                <select
                  className={input}
                  value={crOllamaModel}
                  onChange={(e) => setCrOllamaModel(e.target.value)}
                >
                  {[
                    'llama3.1:8b',
                    'llama3.2:3b',
                    'llama3:latest',
                    'qwen2.5:7b',
                    'qwen2.5-coder:32b',
                    'gemma3:4b',
                    'mistral-nemo:latest',
                  ].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <p className="text-[10px] text-white/25 mt-1.5">
                  Ollama base URL is shared with Prompt Generator settings above.
                </p>
              </div>
            </div>
          )}
        </section>
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════════ */
  /* TAB: Requirements                                                          */
  /* ═══════════════════════════════════════════════════════════════════════════ */

  function renderRequirements() {
    return (
      <section className={card}>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-white/50 mb-1">
          Requirements Ingestion
        </h3>
        <p className="text-xs text-white/30 mb-5">
          Choose where the pipeline pulls requirements from.
        </p>

        {/* Source selector */}
        <div className="mb-5">
          <label className={label}>Source</label>
          <div className="grid grid-cols-4 gap-2">
            {([
              ['device', '📁', 'From Device'],
              ['jira',   '🟦', 'JIRA'],
              ['asana',  '🟧', 'Asana'],
              ['ado',    '🟦', 'Azure DevOps'],
            ] as [typeof reqSource, string, string][]).map(([val, icon, name]) => (
              <button
                key={val}
                onClick={() => { setReqSource(val); setReqError(''); setReqStats(null); }}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-medium transition-colors ${
                  reqSource === val
                    ? 'border-indigo-500/40 bg-indigo-500/10 text-white'
                    : 'border-white/[0.06] text-white/50 hover:border-white/[0.12]'
                }`}
              >
                <span>{icon}</span> {name}
              </button>
            ))}
          </div>
        </div>

        {/* Device */}
        {reqSource === 'device' && (
          <div className="space-y-3">
            <div>
              <label className={label}>Active Requirements File</label>
              <p className="text-sm font-mono text-indigo-300/80 break-all">
                {reqPath || '(default requirements.yaml)'}
              </p>
            </div>
            <input
              ref={fileInputRef} type="file"
              accept=".xlsx,.csv,.txt,.yaml,.yml"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setReqError(''); setReqStats(null); setReqUploading(true);
                try {
                  const res: RequirementsUploadResponse = await api.uploadRequirements(file);
                  setReqPath(res.path);
                  const s = res.stats;
                  setReqStats({
                    epics:    s?.epics    ?? (res as any).epics    ?? 0,
                    features: s?.features ?? (res as any).features ?? 0,
                    stories:  s?.stories  ?? (res as any).tasks    ?? 0,
                  });
                } catch (err) {
                  setReqError(err instanceof Error ? err.message : 'Upload failed');
                } finally {
                  setReqUploading(false);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                }
              }}
            />
            <button onClick={() => fileInputRef.current?.click()} disabled={reqUploading} className={btnSecondary}>
              {reqUploading ? 'Uploading…' : 'Browse & Upload…'}
            </button>
            <p className="text-[10px] text-white/25">Accepted: .xlsx · .csv · .txt · .yaml / .yml (up to 5 MB)</p>
          </div>
        )}

        {/* JIRA */}
        {reqSource === 'jira' && (
          <div className="space-y-4">
            <div>
              <label className={label}>JIRA Base URL</label>
              <input className={input} value={jiraUrl} onChange={(e) => setJiraUrl(e.target.value)} placeholder="https://yourorg.atlassian.net" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={label}>Email</label>
                <input className={input} type="email" value={jiraEmail} onChange={(e) => setJiraEmail(e.target.value)} placeholder="you@example.com" />
              </div>
              <div>
                <label className={label}>Project Key</label>
                <input className={input} value={jiraProject} onChange={(e) => setJiraProject(e.target.value.toUpperCase())} placeholder="PROJ" />
              </div>
            </div>
            <div>
              <label className={label}>API Token</label>
              <input className={input} type="password" value={jiraToken} onChange={(e) => setJiraToken(e.target.value)} placeholder="Atlassian API token" />
              <p className="text-[10px] text-white/25 mt-1">Generate at: atlassian.com/manage-profile/security/api-tokens</p>
            </div>
            <div className="flex gap-2">
              <button
                disabled={reqValidating || !jiraUrl || !jiraEmail || !jiraToken || !jiraProject}
                onClick={async () => {
                  setReqValidationResult(null); setReqError(''); setReqValidating(true);
                  try {
                    const res = await api.validateRemoteConnection({
                      source: 'jira', jira_url: jiraUrl, jira_email: jiraEmail,
                      jira_api_token: jiraToken.startsWith('***') ? '' : jiraToken,
                      jira_project_key: jiraProject,
                    });
                    setReqValidationResult(res);
                    if (!res.valid) setReqError(res.errors.join(' '));
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Validation failed');
                  } finally { setReqValidating(false); }
                }}
                className={`${btnPrimary} !bg-slate-700 hover:!bg-slate-600`}
              >
                {reqValidating ? 'Validating…' : 'Validate Connection'}
              </button>
              <button
                disabled={reqIngesting || !jiraUrl || !jiraEmail || !jiraToken || !jiraProject}
                onClick={async () => {
                  setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                  try {
                    const res = await api.ingestRemoteRequirements({
                      source: 'jira', jira_url: jiraUrl, jira_email: jiraEmail,
                      jira_api_token: jiraToken.startsWith('***') ? '' : jiraToken,
                      jira_project_key: jiraProject,
                    });
                    setReqPath(res.path);
                    const s = res.stats;
                    setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                  } finally { setReqIngesting(false); }
                }}
                className={btnPrimary}
              >
                {reqIngesting ? 'Importing…' : 'Import from JIRA'}
              </button>
            </div>
          </div>
        )}

        {/* Asana */}
        {reqSource === 'asana' && (
          <div className="space-y-4">
            <div>
              <label className={label}>Personal Access Token</label>
              <input className={input} type="password" value={asanaToken} onChange={(e) => setAsanaToken(e.target.value)} placeholder="Asana PAT" />
              <p className="text-[10px] text-white/25 mt-1">Generate at: app.asana.com/0/my-apps</p>
            </div>
            <div>
              <label className={label}>Project GID</label>
              <input className={input} value={asanaProjectId} onChange={(e) => setAsanaProjectId(e.target.value)} placeholder="1234567890123456" />
            </div>
            <div className="flex gap-2">
              <button
                disabled={reqValidating || !asanaToken || !asanaProjectId}
                onClick={async () => {
                  setReqValidationResult(null); setReqError(''); setReqValidating(true);
                  try {
                    const res = await api.validateRemoteConnection({
                      source: 'asana',
                      asana_token: asanaToken.startsWith('***') ? '' : asanaToken,
                      asana_project_id: asanaProjectId,
                    });
                    setReqValidationResult(res);
                    if (!res.valid) setReqError(res.errors.join(' '));
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Validation failed');
                  } finally { setReqValidating(false); }
                }}
                className={`${btnPrimary} !bg-slate-700 hover:!bg-slate-600`}
              >
                {reqValidating ? 'Validating…' : 'Validate Connection'}
              </button>
              <button
                disabled={reqIngesting || !asanaToken || !asanaProjectId}
                onClick={async () => {
                  setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                  try {
                    const res = await api.ingestRemoteRequirements({
                      source: 'asana',
                      asana_token: asanaToken.startsWith('***') ? '' : asanaToken,
                      asana_project_id: asanaProjectId,
                    });
                    setReqPath(res.path);
                    const s = res.stats;
                    setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                  } finally { setReqIngesting(false); }
                }}
                className={btnPrimary}
              >
                {reqIngesting ? 'Importing…' : 'Import from Asana'}
              </button>
            </div>
          </div>
        )}

        {/* ADO */}
        {reqSource === 'ado' && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={label}>Organisation</label>
                <input
                  className={input}
                  value={adoOrg}
                  onChange={(e) => { setAdoOrg(e.target.value); setAdoProjects([]); setAdoProjectsFetchError(''); }}
                  placeholder="my-org"
                />
              </div>
              <div>
                <label className={label}>Project</label>
                <div className="flex gap-2">
                  <select
                    className={`${input} flex-1`}
                    value={adoProject}
                    onChange={(e) => setAdoProject(e.target.value)}
                    disabled={adoProjectsLoading}
                  >
                    {adoProjects.length === 0 ? (
                      <option value="">{adoProjectsLoading ? 'Loading…' : '— fetch projects —'}</option>
                    ) : (
                      <>
                        <option value="">— select a project —</option>
                        {adoProjects.map((p) => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </>
                    )}
                  </select>
                  <button
                    type="button"
                    disabled={adoProjectsLoading || !adoOrg}
                    onClick={() => fetchAdoProjects(adoOrg, adoToken)}
                    className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed text-white whitespace-nowrap"
                  >
                    {adoProjectsLoading ? '…' : 'Fetch'}
                  </button>
                </div>
                {adoProjectsFetchError && <p className="text-[10px] text-red-400/80 mt-1">{adoProjectsFetchError}</p>}
              </div>
            </div>
            <div>
              <label className={label}>Personal Access Token</label>
              <input
                className={input}
                type="password"
                value={adoToken}
                onChange={(e) => { setAdoToken(e.target.value); setAdoProjects([]); setAdoProjectsFetchError(''); }}
                placeholder="ADO PAT"
              />
              <p className="text-[10px] text-white/25 mt-1">Generate at: dev.azure.com → User settings → Personal access tokens</p>
            </div>
            <div className="flex gap-2">
              <button
                disabled={reqValidating || !adoOrg || !adoProject}
                onClick={async () => {
                  setReqValidationResult(null); setReqError(''); setReqValidating(true);
                  try {
                    const res = await api.validateRemoteConnection({
                      source: 'ado', ado_org: adoOrg,
                      ado_token: adoToken.startsWith('***') ? '' : adoToken,
                      ado_project: adoProject,
                    });
                    setReqValidationResult(res);
                    if (!res.valid) setReqError(res.errors.join(' '));
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Validation failed');
                  } finally { setReqValidating(false); }
                }}
                className={`${btnPrimary} !bg-slate-700 hover:!bg-slate-600`}
              >
                {reqValidating ? 'Validating…' : 'Validate Connection'}
              </button>
              <button
                disabled={reqIngesting || !adoOrg || !adoToken || !adoProject}
                onClick={async () => {
                  setReqError(''); setReqStats(null); setReqIngesting(true); setReqValidationResult(null);
                  try {
                    const res = await api.ingestRemoteRequirements({
                      source: 'ado', ado_org: adoOrg,
                      ado_token: adoToken.startsWith('***') ? '' : adoToken,
                      ado_project: adoProject,
                    });
                    setReqPath(res.path);
                    const s = res.stats;
                    setReqStats({ epics: s?.epics ?? 0, features: s?.features ?? 0, stories: s?.stories ?? 0 });
                  } catch (err) {
                    setReqError(err instanceof Error ? err.message : 'Ingestion failed');
                  } finally { setReqIngesting(false); }
                }}
                className={btnPrimary}
              >
                {reqIngesting ? 'Importing…' : 'Import from ADO'}
              </button>
            </div>
          </div>
        )}

        {/* Feedback */}
        {reqValidationResult && reqValidationResult.valid && (
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-xs text-green-400/80 mt-4">
            ✓ Connection validated successfully.
            {reqValidationResult.warnings.length > 0 && (
              <span className="text-yellow-400/80 ml-2">
                Warnings: {reqValidationResult.warnings.join('; ')}
              </span>
            )}
          </motion.p>
        )}
        {reqStats && (
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-xs text-green-400/80 mt-4">
            Loaded: {reqStats.epics} epics · {reqStats.features} features · {reqStats.stories} stories
          </motion.p>
        )}
        {reqError && <p className="text-xs text-red-400/80 mt-4">{reqError}</p>}

        {/* Saved source badge + View Requirements */}
        {settings?.requirements?.source && (
          <div className="mt-5 pt-4 border-t border-white/[0.04] flex items-center gap-2 flex-wrap">
            <span className="text-[10px] uppercase tracking-widest text-white/25">Active:</span>
            <span className="text-xs font-medium text-white/60 px-2 py-0.5 rounded bg-white/[0.06]">
              {settings.requirements.source === 'device' ? '📁 From Device' :
               settings.requirements.source === 'jira'   ? '🟦 JIRA' :
               settings.requirements.source === 'asana'  ? '🟧 Asana' : '🟦 Azure DevOps'}
            </span>
            {settings.requirements.source === 'device' && settings.requirements.path && (
              <span className="text-xs font-mono text-white/30 truncate max-w-xs">{settings.requirements.path.split('/').pop()}</span>
            )}
            <button
              disabled={!settings.requirements.path}
              onClick={async () => {
                setReqViewError('');
                setReqViewDoc(null);
                setReqViewOpen(true);
                setReqViewLoading(true);
                try {
                  const doc = await api.previewRequirements();
                  setReqViewDoc(doc);
                } catch (err) {
                  setReqViewError(err instanceof Error ? err.message : 'Failed to load preview');
                } finally {
                  setReqViewLoading(false);
                }
              }}
              className="ml-auto flex items-center gap-1.5 px-3 py-1 rounded-lg border border-indigo-500/30 bg-indigo-500/10 text-xs font-medium text-indigo-300 hover:bg-indigo-500/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
              View Requirements
            </button>
          </div>
        )}

        {/* Requirements Preview Modal is rendered globally — see renderReqPreviewModal() */}
      </section>
    );
  }

  /* ── Requirements preview modal (shared — rendered at root level) ─────── */
  function renderReqPreviewModal() {
    return (
      <AnimatePresence>
        {reqViewOpen && (
          <motion.div
            key="req-preview-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-4"
              style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
              onClick={(e) => { if (e.target === e.currentTarget) setReqViewOpen(false); }}
            >
              <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 12 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 12 }}
                transition={{ duration: 0.18 }}
                className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-2xl border border-white/[0.08] bg-[#0f1117] shadow-2xl overflow-hidden"
              >
                {/* Modal header */}
                <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] shrink-0">
                  <div>
                    <h2 className="text-sm font-semibold text-white">Requirements Preview</h2>
                    {reqViewDoc && (() => {
                      const nEpics = reqViewDoc.epics.length;
                      const nFeat  = reqViewDoc.epics.reduce((a, e) => a + e.features.length, 0);
                      const nStory = reqViewDoc.epics.reduce((a, e) => a + e.features.reduce((b, f) => b + f.stories.length, 0), 0);
                      const nAC    = reqViewDoc.epics.reduce((a, e) => a + e.features.reduce((b, f) => b + f.stories.reduce((c, s) => c + s.acceptance_criteria.length, 0), 0), 0);
                      return (
                        <p className="text-[11px] text-white/40 mt-0.5">
                          {nEpics} epic{nEpics !== 1 ? 's' : ''} · {nFeat} feature{nFeat !== 1 ? 's' : ''} · {nStory} stor{nStory !== 1 ? 'ies' : 'y'} · {nAC} acceptance criteria
                        </p>
                      );
                    })()}
                  </div>
                  <button
                    onClick={() => setReqViewOpen(false)}
                    className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/[0.06] transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>

                {/* Modal body */}
                <div className="overflow-y-auto flex-1 px-5 py-4 space-y-4">
                  {reqViewLoading && (
                    <div className="flex items-center justify-center py-12 text-white/40 text-sm">
                      <svg className="w-5 h-5 mr-2 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                      Loading…
                    </div>
                  )}
                  {reqViewError && (
                    <p className="text-sm text-red-400/80 py-4">{reqViewError}</p>
                  )}
                  {reqViewDoc && reqViewDoc.epics.map((epic) => (
                    <ReqEpicBlock key={epic.id} epic={epic} />
                  ))}
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
    );
  }
}
