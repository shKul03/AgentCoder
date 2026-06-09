/* CreateAgentWizard — Phase 9
   7-step guided flow for building a new custom agent from scratch.

   Step 1 — Identity    (name, display name, description, icon/color)
   Step 2 — Soul        (Monaco editor + side prompts)
   Step 3 — Skills      (Monaco editor + side prompts)
   Step 4 — Tools       (checklist → auto-populates md content)
   Step 5 — Ceiling     (structured 3-section form)
   Step 6 — Assignment  (optional pipeline post)
   Step 7 — Review      (file preview tabs + Create button)
*/

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Editor from '@monaco-editor/react';
import { api } from '../hooks/api';
import type { PipelinePost } from '../types';
import { PIPELINE_POSTS } from '../types';

// ─── Constants ────────────────────────────────────────────────────────────────

const BUILTIN_NAMES = new Set(['module_maker', 'prompt_generator', 'code_generator', 'code_reviewer']);
const VALID_NAME_RE = /^[a-z][a-z0-9_]*$/;

const AGENT_COLORS = [
  { label: 'Indigo',  value: 'indigo',  hex: '#818CF8' },
  { label: 'Purple',  value: 'purple',  hex: '#C084FC' },
  { label: 'Teal',    value: 'teal',    hex: '#2DD4BF' },
  { label: 'Blue',    value: 'blue',    hex: '#60A5FA' },
  { label: 'Rose',    value: 'rose',    hex: '#FB7185' },
  { label: 'Amber',   value: 'amber',   hex: '#FCD34D' },
  { label: 'Green',   value: 'green',   hex: '#4ADE80' },
  { label: 'Orange',  value: 'orange',  hex: '#FB923C' },
];

const AGENT_ICONS = ['🤖', '🧠', '🔬', '⚗', '🛠', '🔭', '🏗', '🧪', '🎯', '💡', '🧩', '🚀', '🔐', '📐', '🎨', '⚙'];

// ─── Available tools ──────────────────────────────────────────────────────────

interface ToolDef {
  id: string;
  label: string;
  description: string;
  mdSnippet: string;
}

const AVAILABLE_TOOLS: ToolDef[] = [
  {
    id: 'filesystem_read',
    label: 'Filesystem Read',
    description: 'Read files and directories from the project',
    mdSnippet: '- **filesystem_read** — Read files and inspect directory structure within the project.',
  },
  {
    id: 'filesystem_write',
    label: 'Filesystem Write',
    description: 'Write, create, and modify project files',
    mdSnippet: '- **filesystem_write** — Write, create, modify, and delete files in the project filesystem.',
  },
  {
    id: 'git_operations',
    label: 'Git Operations',
    description: 'Stage, commit, branch, and diff',
    mdSnippet: '- **git_operations** — Stage files, create commits, manage branches, and inspect diffs.',
  },
  {
    id: 'github_api',
    label: 'GitHub API',
    description: 'Create issues, PRs, and interact with GitHub',
    mdSnippet: '- **github_api** — Create and manage issues, pull requests, and repository metadata via the GitHub REST API.',
  },
  {
    id: 'linter',
    label: 'Linter',
    description: 'Run ESLint, Flake8, or similar linters',
    mdSnippet: '- **linter** — Execute code linters (ESLint, Flake8, Rubocop, etc.) and parse actionable output.',
  },
  {
    id: 'test_runner',
    label: 'Test Runner',
    description: 'Execute test suites and interpret results',
    mdSnippet: '- **test_runner** — Run unit and integration test suites and interpret pass/fail results.',
  },
  {
    id: 'shell_commands',
    label: 'Shell Commands',
    description: 'Run arbitrary shell commands',
    mdSnippet: '- **shell_commands** — Execute arbitrary shell commands within the project working directory.',
  },
  {
    id: 'code_search',
    label: 'Code Search',
    description: 'Search codebase by symbol, pattern, filename',
    mdSnippet: '- **code_search** — Search the codebase by symbol name, text pattern, or filename glob.',
  },
  {
    id: 'web_search',
    label: 'Web Search',
    description: 'Search the internet for docs and examples',
    mdSnippet: '- **web_search** — Search the internet for documentation, error solutions, and code examples.',
  },
  {
    id: 'api_calls',
    label: 'External API Calls',
    description: 'Make HTTP requests to external APIs',
    mdSnippet: '- **api_calls** — Make authenticated HTTP requests to external REST or GraphQL APIs.',
  },
];

// ─── Templates ────────────────────────────────────────────────────────────────

function soulTemplate(displayName: string, description: string): string {
  return `# ${displayName} — Soul

## Identity

You are ${displayName}, an AI agent in the Agent OS pipeline. ${description}

## Core Values

- **Precision** — Produce accurate, well-reasoned output every time.
- **Collaboration** — Work within the pipeline context and respect upstream/downstream agents.
- **Quality** — Prioritise correctness and maintainability over speed.

## Communication Style

Write responses that are clear, concise, and actionable. Avoid ambiguity. When uncertain,
state your assumptions explicitly.

## Expertise Domain

Describe the specific domain of expertise for this agent...
`;
}

function skillsTemplate(displayName: string): string {
  return `# ${displayName} — Skills

## Primary Capabilities

- Describe what this agent can produce or accomplish...
- Add more capabilities...

## Output Artifacts

The following deliverables are produced by this agent:

- **Primary output** — Description of main output artifact
- **Secondary output** — Any secondary artifacts

## Quality Standards

All outputs must meet the following standards:

- Standard 1...
- Standard 2...

## Constraints

- This agent does NOT ...
- This agent always ...
`;
}

function toolsTemplate(selected: ToolDef[], custom: string): string {
  const lines = selected.map((t) => t.mdSnippet);
  const customLines = custom.trim()
    ? custom
        .split('\n')
        .filter((l) => l.trim())
        .map((l) => `- ${l.replace(/^[-*]\s*/, '')}`)
    : [];

  return `# Tools

## Available Tools

${[...lines, ...customLines].join('\n') || '(No tools selected yet)'}

## Tool Usage Guidelines

- Only invoke tools that are necessary for the current task.
- Prefer read operations before write operations.
- Always verify file paths before writing.
`;
}

function ceilingContent(canDo: string, mustEscalate: string, mustNotDo: string): string {
  const section = (label: string, text: string) => {
    const items = text
      .split('\n')
      .filter((l) => l.trim())
      .map((l) => `- ${l.replace(/^[-*]\s*/, '')}`)
      .join('\n');
    return `## ${label}\n\n${items || '- (none defined yet)'}`;
  };

  return `# Ceiling

This file defines the operational boundaries for this agent.

${section('Can Do', canDo)}

${section('Must Escalate', mustEscalate)}

${section('Must Not Do', mustNotDo)}
`;
}

// ─── Wizard state ─────────────────────────────────────────────────────────────

interface WizardState {
  // Step 1 — Identity
  name: string;
  displayName: string;
  description: string;
  icon: string;
  color: string;

  // Step 2–4 content
  soul: string;
  skills: string;
  selectedTools: Set<string>;
  customToolsText: string;

  // Step 5 — Ceiling sections
  ceilingCanDo: string;
  ceilingMustEscalate: string;
  ceilingMustNotDo: string;

  // Step 6 — Assignment
  postAssignment: PipelinePost | '';

  // Tracks which displayName||name was used to generate soul/skills templates.
  // When the name changes and the user clicks Next, templates are regenerated.
  templateKey: string;
}

function initialState(): WizardState {
  return {
    name: '',
    displayName: '',
    description: '',
    icon: '🤖',
    color: 'indigo',
    soul: '',
    skills: '',
    selectedTools: new Set(),
    customToolsText: '',
    ceilingCanDo: '',
    ceilingMustEscalate: '',
    ceilingMustNotDo: '',
    postAssignment: '',
    templateKey: '',
  };
}

// ─── Shared UI helpers ────────────────────────────────────────────────────────

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1.5">{children}</label>;
}

function SidePrompts({ prompts }: { prompts: string[] }) {
  return (
    <div className="w-64 shrink-0 space-y-3 pl-5">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Guiding Questions</p>
      {prompts.map((p, i) => (
        <div key={i} className="flex gap-2.5 text-xs text-slate-400">
          <span className="text-indigo-500 shrink-0 mt-0.5">◆</span>
          <span>{p}</span>
        </div>
      ))}
    </div>
  );
}

function ValidationError({ msg }: { msg: string }) {
  return msg ? (
    <p className="mt-1.5 text-xs text-red-400 flex items-center gap-1">
      <span>⚠</span> {msg}
    </p>
  ) : null;
}

// ─── Step components ──────────────────────────────────────────────────────────

// Step 1 — Identity
function StepIdentity({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  const nameError =
    state.name && !VALID_NAME_RE.test(state.name)
      ? 'Use only lowercase letters, numbers, and underscores. Must start with a letter.'
      : state.name && BUILTIN_NAMES.has(state.name)
      ? 'This name is reserved for a built-in agent.'
      : '';

  return (
    <div className="space-y-5 max-w-xl">
      <div>
        <FieldLabel>Agent Name (internal identifier)</FieldLabel>
        <input
          type="text"
          value={state.name}
          onChange={(e) => onChange({ name: e.target.value.toLowerCase().replace(/\s/g, '_') })}
          placeholder="e.g. security_scanner"
          className="w-full bg-[var(--bg-primary)] border border-[var(--border-glass)] rounded-lg px-3 py-2 text-sm text-white font-mono outline-none focus:border-indigo-500 transition-colors"
        />
        <ValidationError msg={nameError} />
        <p className="mt-1 text-[10px] text-slate-600">Lowercase letters, numbers and underscores only.</p>
      </div>

      <div>
        <FieldLabel>Display Name</FieldLabel>
        <input
          type="text"
          value={state.displayName}
          onChange={(e) => onChange({ displayName: e.target.value })}
          placeholder="e.g. Security Scanner"
          className="w-full bg-[var(--bg-primary)] border border-[var(--border-glass)] rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-indigo-500 transition-colors"
        />
      </div>

      <div>
        <FieldLabel>Description</FieldLabel>
        <textarea
          value={state.description}
          onChange={(e) => onChange({ description: e.target.value })}
          rows={3}
          placeholder="Briefly describe what this agent does..."
          className="w-full bg-[var(--bg-primary)] border border-[var(--border-glass)] rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-indigo-500 resize-none transition-colors"
        />
      </div>

      <div>
        <FieldLabel>Icon</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {AGENT_ICONS.map((icon) => (
            <button
              key={icon}
              onClick={() => onChange({ icon })}
              className={`w-10 h-10 rounded-lg text-xl flex items-center justify-center border transition-colors ${
                state.icon === icon
                  ? 'border-indigo-500 bg-indigo-500/15'
                  : 'border-[var(--border-glass)] hover:border-indigo-500/50 bg-[var(--bg-primary)]'
              }`}
            >
              {icon}
            </button>
          ))}
        </div>
      </div>

      <div>
        <FieldLabel>Color</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {AGENT_COLORS.map((c) => (
            <button
              key={c.value}
              onClick={() => onChange({ color: c.value })}
              title={c.label}
              className={`w-8 h-8 rounded-full border-2 transition-all ${
                state.color === c.value ? 'scale-125 border-white' : 'border-transparent opacity-60 hover:opacity-100'
              }`}
              style={{ backgroundColor: c.hex }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// Step 2 — Soul
function StepSoul({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  return (
    <div className="flex flex-1 min-h-0 gap-0">
      <div className="flex-1 border border-[var(--border-glass)] rounded-xl overflow-hidden">
        <Editor
          height="100%"
          defaultLanguage="markdown"
          value={state.soul}
          onChange={(v) => onChange({ soul: v ?? '' })}
          theme="vs-dark"
          options={{ fontSize: 13, wordWrap: 'on', minimap: { enabled: false }, scrollBeyondLastLine: false, padding: { top: 12, bottom: 12 } }}
        />
      </div>
      <SidePrompts prompts={[
        "What is this agent's primary goal or purpose?",
        'What personality traits define how it communicates?',
        'What is its specific domain of knowledge or expertise?',
        "How should it handle ambiguity or incomplete instructions?",
        "What makes this agent different from a generic coding assistant?",
      ]} />
    </div>
  );
}

// Step 3 — Skills
function StepSkills({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  return (
    <div className="flex flex-1 min-h-0 gap-0">
      <div className="flex-1 border border-[var(--border-glass)] rounded-xl overflow-hidden">
        <Editor
          height="100%"
          defaultLanguage="markdown"
          value={state.skills}
          onChange={(v) => onChange({ skills: v ?? '' })}
          theme="vs-dark"
          options={{ fontSize: 13, wordWrap: 'on', minimap: { enabled: false }, scrollBeyondLastLine: false, padding: { top: 12, bottom: 12 } }}
        />
      </div>
      <SidePrompts prompts={[
        'What can this agent produce or accomplish by the end of its run?',
        'What formats or file types does it output?',
        'What tasks can it perform autonomously without human review?',
        'What quality bar must its output meet before being accepted?',
        'What are its limitations that the pipeline should be aware of?',
      ]} />
    </div>
  );
}

// Step 4 — Tools
function StepTools({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  const toggle = useCallback((id: string) => {
    onChange({
      selectedTools: new Set(
        state.selectedTools.has(id)
          ? [...state.selectedTools].filter((x) => x !== id)
          : [...state.selectedTools, id],
      ),
    });
  }, [state.selectedTools, onChange]);

  const selectedDefs = AVAILABLE_TOOLS.filter((t) => state.selectedTools.has(t.id));

  return (
    <div className="flex flex-1 min-h-0 gap-5">
      {/* Checklist */}
      <div className="w-72 shrink-0 overflow-y-auto space-y-2">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Available Tools</p>
        {AVAILABLE_TOOLS.map((tool) => {
          const checked = state.selectedTools.has(tool.id);
          return (
            <button
              key={tool.id}
              onClick={() => toggle(tool.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors flex items-start gap-3 ${
                checked
                  ? 'border-indigo-500/50 bg-indigo-500/10'
                  : 'border-[var(--border-glass)] bg-[var(--bg-card)] hover:border-indigo-500/30'
              }`}
            >
              <span
                className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                  checked ? 'bg-indigo-500 border-indigo-500' : 'border-slate-600'
                }`}
              >
                {checked && <span className="text-white text-[10px]">✓</span>}
              </span>
              <div>
                <p className="text-sm text-white font-medium">{tool.label}</p>
                <p className="text-xs text-slate-500 mt-0.5">{tool.description}</p>
              </div>
            </button>
          );
        })}
      </div>

      {/* Auto-generated content preview + custom input */}
      <div className="flex-1 flex flex-col gap-4 min-h-0">
        <div className="flex-1 border border-[var(--border-glass)] rounded-xl overflow-hidden">
          <div className="h-full bg-[#0a0e1a] overflow-y-auto p-4 font-mono text-xs">
            {selectedDefs.length === 0 ? (
              <p className="text-slate-600 italic">Select tools from the list to auto-generate the Tools.md content.</p>
            ) : (
              <pre className="text-slate-300 whitespace-pre-wrap leading-relaxed">
                {toolsTemplate(selectedDefs, state.customToolsText)}
              </pre>
            )}
          </div>
        </div>
        <div>
          <FieldLabel>Custom Tool Descriptions (one per line)</FieldLabel>
          <textarea
            value={state.customToolsText}
            onChange={(e) => onChange({ customToolsText: e.target.value })}
            rows={3}
            placeholder="custom_db_connector — reads from the project's database schema file"
            className="w-full bg-[var(--bg-primary)] border border-[var(--border-glass)] rounded-lg px-3 py-2 text-sm text-white font-mono outline-none focus:border-indigo-500 resize-none transition-colors"
          />
        </div>
      </div>
    </div>
  );
}

// Step 5 — Ceiling
function StepCeiling({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  const SectionField = ({
    label, value, placeholder, colorClass, helpText, key: _key,
    onUpdate,
  }: {
    label: string; value: string; placeholder: string; colorClass: string; helpText: string; key?: string;
    onUpdate: (v: string) => void;
  }) => (
    <div className={`rounded-xl border p-4 ${colorClass}`}>
      <h3 className="text-sm font-semibold text-white mb-1">{label}</h3>
      <p className="text-xs text-slate-500 mb-3">{helpText}</p>
      <textarea
        value={value}
        onChange={(e) => onUpdate(e.target.value)}
        rows={5}
        placeholder={placeholder}
        className="w-full bg-black/20 border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-white/30 resize-none transition-colors font-mono"
      />
      <p className="mt-1 text-[10px] text-slate-600">One item per line. Leading dashes are added automatically.</p>
    </div>
  );

  return (
    <div className="flex flex-1 min-h-0 gap-5">
      <div className="flex-1 space-y-4 overflow-y-auto pr-1">
        <SectionField
          label="✓ Can Do"
          value={state.ceilingCanDo}
          colorClass="border-green-500/25 bg-green-500/5"
          helpText="List tasks this agent is fully authorised to perform independently."
          placeholder={"Write and modify source files\nCreate new files in the project directory\nRun read-only analysis tools"}
          onUpdate={(v) => onChange({ ceilingCanDo: v })}
        />
        <SectionField
          label="⚠ Must Escalate"
          value={state.ceilingMustEscalate}
          colorClass="border-amber-500/25 bg-amber-500/5"
          helpText="List situations where the agent must pause and request human review."
          placeholder={"Ambiguous or conflicting requirements\nSecurity-sensitive file modifications\nChanges to infrastructure or deployment configs"}
          onUpdate={(v) => onChange({ ceilingMustEscalate: v })}
        />
        <SectionField
          label="✕ Must Not Do"
          value={state.ceilingMustNotDo}
          colorClass="border-red-500/25 bg-red-500/5"
          helpText="List hard constraints — things this agent is strictly forbidden from doing."
          placeholder={"Delete files without explicit instruction\nModify test files unless instructed\nExpose secrets or API keys in output"}
          onUpdate={(v) => onChange({ ceilingMustNotDo: v })}
        />
      </div>
      <SidePrompts prompts={[
        "What can this agent do completely on its own without asking?",
        "When should it stop and ask a human before continuing?",
        "What actions are completely off-limits, no matter what?",
        "Think about: file deletion, secrets, production environments.",
      ]} />
    </div>
  );
}

// Step 6 — Post Assignment
function StepAssignment({ state, onChange }: { state: WizardState; onChange: (patch: Partial<WizardState>) => void }) {
  return (
    <div className="max-w-lg space-y-5">
      <div>
        <FieldLabel>Pipeline Post (optional)</FieldLabel>
        <p className="text-xs text-slate-500 mb-3">
          Assign this agent to a pipeline post. If a built-in agent currently holds the post, it will be displaced
          (a warning will confirm). Leave blank to keep the agent unassigned for now.
        </p>
        <select
          value={state.postAssignment}
          onChange={(e) => onChange({ postAssignment: e.target.value as PipelinePost | '' })}
          className="w-full bg-[var(--bg-primary)] border border-[var(--border-glass)] rounded-lg px-3 py-2.5 text-sm text-white outline-none focus:border-indigo-500 transition-colors"
        >
          <option value="">— No assignment —</option>
          {PIPELINE_POSTS.map((post) => (
            <option key={post} value={post}>{post.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      {state.postAssignment && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 flex gap-3">
          <span className="text-amber-400 shrink-0">⚠</span>
          <p className="text-xs text-amber-200">
            Assigning to <strong>{state.postAssignment}</strong> will displace the current agent
            holding that post. The pipeline will use <strong>{state.displayName || state.name}</strong> instead.
            You can change this later on the Agents page.
          </p>
        </div>
      )}
    </div>
  );
}

// Step 7 — Review
function StepReview({
  state,
  submitting,
  submitError,
  builtFiles,
}: {
  state: WizardState;
  submitting: boolean;
  submitError: string;
  builtFiles: Record<string, string>;
}) {
  const [previewTab, setPreviewTab] = useState<string>('soul.md');
  const tabs = Object.keys(builtFiles);

  return (
    <div className="flex flex-1 min-h-0 gap-5">
      {/* Summary */}
      <div className="w-60 shrink-0 space-y-4">
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Summary</p>
          <div className="space-y-2 text-sm">
            <div className="flex gap-2 items-center">
              <span className="text-2xl">{state.icon}</span>
              <div>
                <p className="text-white font-semibold">{state.displayName || state.name}</p>
                <p className="text-xs text-slate-500 font-mono">custom/{state.name}</p>
              </div>
            </div>
            {state.description && (
              <p className="text-xs text-slate-400 italic">{state.description}</p>
            )}
          </div>
        </div>

        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Files</p>
          <div className="space-y-1">
            {tabs.map((f) => {
              const len = builtFiles[f].length;
              const ok = len > 20;
              return (
                <div key={f} className="flex items-center gap-2 text-xs">
                  <span className={ok ? 'text-green-400' : 'text-red-400'}>{ok ? '✓' : '✕'}</span>
                  <span className={`font-mono ${ok ? 'text-slate-300' : 'text-red-400'}`}>{f}</span>
                  <span className="text-slate-600 ml-auto">{len} ch</span>
                </div>
              );
            })}
          </div>
        </div>

        {state.postAssignment && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Assignment</p>
            <span className="text-xs px-2 py-1 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-mono">
              {state.postAssignment}
            </span>
          </div>
        )}

        {submitError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
            {submitError}
          </div>
        )}

        {submitting && (
          <div className="flex items-center gap-2 text-xs text-indigo-300">
            <span className="w-4 h-4 rounded-full border-2 border-indigo-400 border-t-transparent animate-spin" />
            Creating agent...
          </div>
        )}
      </div>

      {/* File preview */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="flex gap-1 mb-2 border-b border-[var(--border-glass)] pb-1 shrink-0 flex-wrap">
          {tabs.map((t) => (
            <button
              key={t}
              onClick={() => setPreviewTab(t)}
              className={`px-3 py-1.5 text-xs font-mono rounded-t-md transition-colors ${
                previewTab === t ? 'text-white bg-indigo-500/15 border-b-2 border-indigo-400' : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex-1 border border-[var(--border-glass)] rounded-xl overflow-hidden">
          <Editor
            height="100%"
            defaultLanguage="markdown"
            value={builtFiles[previewTab] ?? ''}
            theme="vs-dark"
            options={{
              readOnly: true,
              fontSize: 12,
              wordWrap: 'on',
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              renderLineHighlight: 'none',
            }}
          />
        </div>
      </div>
    </div>
  );
}

// ─── Step config ──────────────────────────────────────────────────────────────

interface StepDef {
  label: string;
  shortLabel: string;
}

const STEPS: StepDef[] = [
  { label: 'Identity',   shortLabel: '1' },
  { label: 'Soul',       shortLabel: '2' },
  { label: 'Skills',     shortLabel: '3' },
  { label: 'Tools',      shortLabel: '4' },
  { label: 'Ceiling',    shortLabel: '5' },
  { label: 'Assignment', shortLabel: '6' },
  { label: 'Review',     shortLabel: '7' },
];

// ─── Validation per step ──────────────────────────────────────────────────────

function validateStep(step: number, s: WizardState): string {
  switch (step) {
    case 0:
      if (!s.name) return 'Agent name is required.';
      if (!VALID_NAME_RE.test(s.name)) return 'Name must be lowercase letters, numbers, and underscores.';
      if (BUILTIN_NAMES.has(s.name)) return 'This name is reserved.';
      if (!s.displayName.trim()) return 'Display name is required.';
      return '';
    case 1:
      if (!s.soul.trim()) return 'Soul content cannot be empty.';
      return '';
    case 2:
      if (!s.skills.trim()) return 'Skills content cannot be empty.';
      return '';
    case 3:
      return ''; // tools optional
    case 4:
      return ''; // ceiling optional but encouraged
    case 5:
      return ''; // post assignment optional
    default:
      return '';
  }
}

// ─── Build final files dict ───────────────────────────────────────────────────

function buildFiles(s: WizardState): Record<string, string> {
  const selectedDefs = AVAILABLE_TOOLS.filter((t) => s.selectedTools.has(t.id));
  return {
    'soul.md':    s.soul,
    'skills.md':  s.skills,
    'tools.md':   toolsTemplate(selectedDefs, s.customToolsText),
    'ceiling.md': ceilingContent(s.ceilingCanDo, s.ceilingMustEscalate, s.ceilingMustNotDo),
  };
}

// ─── Main wizard component ────────────────────────────────────────────────────

interface Props {
  onDone: (agentName: string) => void;
  onCancel: () => void;
}

export default function CreateAgentWizard({ onDone, onCancel }: Props) {
  const [step, setStep] = useState(0);
  const [state, setState] = useState<WizardState>(initialState);
  const [stepError, setStepError] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const patch = useCallback((p: Partial<WizardState>) => setState((prev) => ({ ...prev, ...p })), []);

  // Auto-populate templates when advancing from Step 1
  const handleNext = useCallback(() => {
    const err = validateStep(step, state);
    if (err) { setStepError(err); return; }
    setStepError('');

    // Pre-populate (or re-populate) templates whenever the identity name changes
    if (step === 0) {
      setState((prev) => {
        const newKey = prev.displayName || prev.name;
        if (newKey === prev.templateKey) return prev; // name unchanged — keep current content
        return {
          ...prev,
          soul:   soulTemplate(prev.displayName || prev.name, prev.description),
          skills: skillsTemplate(prev.displayName || prev.name),
          templateKey: newKey,
        };
      });
    }

    setStep((s) => s + 1);
  }, [step, state]);

  const handleBack = () => {
    setStepError('');
    setStep((s) => s - 1);
  };

  const handleSubmit = async () => {
    setSubmitError('');
    setSubmitting(true);
    try {
      const files = buildFiles(state);
      await api.createAgent(state.name, files);

      // Optionally assign to a pipeline post
      if (state.postAssignment) {
        const regRes = await api.getRegistry();
        const mapping = { ...regRes.mapping, [state.postAssignment]: `custom/${state.name}` };
        await api.updateRegistry(mapping);
      }

      onDone(`custom/${state.name}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error';
      setSubmitError(msg.includes('409') ? `An agent named "${state.name}" already exists.` : `Creation failed: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const builtFiles = buildFiles(state);

  const isLastStep = step === STEPS.length - 1;

  return (
    <motion.div
      className="fixed inset-0 z-50 flex flex-col bg-[var(--bg-primary)]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      {/* Top bar */}
      <div className="flex items-center gap-4 px-6 py-3 border-b border-[var(--border-glass)] shrink-0">
        <button
          onClick={onCancel}
          className="text-slate-400 hover:text-white text-sm transition-colors"
        >
          ← Cancel
        </button>
        <span className="text-white font-semibold">Create Custom Agent</span>
        <div className="flex-1" />

        {/* Step indicator */}
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => (
            <div key={i} className="flex items-center gap-1">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
                  i === step
                    ? 'bg-indigo-600 text-white'
                    : i < step
                    ? 'bg-indigo-500/30 text-indigo-300'
                    : 'bg-slate-700 text-slate-500'
                }`}
              >
                {i < step ? '✓' : s.shortLabel}
              </div>
              {i < STEPS.length - 1 && (
                <div className={`w-4 h-px ${i < step ? 'bg-indigo-500/50' : 'bg-slate-700'}`} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Step title */}
      <div className="px-6 pt-5 pb-4 shrink-0">
        <div className="flex items-baseline gap-3">
          <span className="text-xs text-slate-500 uppercase tracking-wide font-semibold">
            Step {step + 1} of {STEPS.length}
          </span>
          <h2 className="text-xl font-bold text-white">{STEPS[step].label}</h2>
        </div>
        {stepError && (
          <p className="mt-1.5 text-xs text-red-400 flex items-center gap-1.5">
            <span>⚠</span> {stepError}
          </p>
        )}
      </div>

      {/* Step content */}
      <div className="flex-1 px-6 min-h-0 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            className="h-full flex flex-col"
            initial={{ opacity: 0, x: 16 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -16 }}
            transition={{ duration: 0.15 }}
          >
            {step === 0 && <StepIdentity state={state} onChange={patch} />}
            {step === 1 && <StepSoul state={state} onChange={patch} />}
            {step === 2 && <StepSkills state={state} onChange={patch} />}
            {step === 3 && <StepTools state={state} onChange={patch} />}
            {step === 4 && <StepCeiling state={state} onChange={patch} />}
            {step === 5 && <StepAssignment state={state} onChange={patch} />}
            {step === 6 && (
              <StepReview
                state={state}
                submitting={submitting}
                submitError={submitError}
                builtFiles={builtFiles}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Footer nav */}
      <div className="shrink-0 flex items-center justify-between px-6 py-4 border-t border-[var(--border-glass)]">
        <button
          onClick={handleBack}
          disabled={step === 0}
          className="px-4 py-2 text-sm rounded-lg border border-[var(--border-glass)] text-slate-400 hover:text-white hover:border-white/30 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          ← Back
        </button>
        <div className="flex items-center gap-2">
          {isLastStep ? (
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="px-6 py-2 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? 'Creating...' : '🚀 Create Agent'}
            </button>
          ) : (
            <button
              onClick={handleNext}
              className="px-6 py-2 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold transition-colors"
            >
              Next →
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}
