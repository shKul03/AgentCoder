/* Agent accent color system — Phase 11
   Single source of truth for per-agent colors used across:
   cards, terminal headers, workflow nodes, status badges, notification tray.
*/

export type AgentPost = 'MODULE_MAKER' | 'PROMPT_GENERATOR' | 'CODE_GENERATOR' | 'CODE_REVIEWER';

export interface AgentAccent {
  /** CSS var name without -- prefix, e.g. 'agent-module-maker' */
  cssVar: string;
  /** Tailwind-compatible hex (for inline SVG, canvas, etc.) */
  hex: string;
  hexGlow: string;
  /** Tailwind bg class (approximated) */
  bgClass: string;
  borderClass: string;
  textClass: string;
  /** Display label */
  label: string;
  icon: string;
  /** Emoji icon for the station card */
  stationIcon: string;
}

export const AGENT_ACCENTS: Record<AgentPost, AgentAccent> = {
  MODULE_MAKER: {
    cssVar: 'agent-module-maker',
    hex: '#818CF8',
    hexGlow: 'rgba(129,140,248,0.25)',
    bgClass: 'bg-indigo-500/15',
    borderClass: 'border-indigo-500/40',
    textClass: 'text-indigo-300',
    label: 'Module Maker',
    icon: '⬡',
    stationIcon: '🏛',
  },
  PROMPT_GENERATOR: {
    cssVar: 'agent-prompt-gen',
    hex: '#A78BFA',
    hexGlow: 'rgba(167,139,250,0.25)',
    bgClass: 'bg-violet-500/15',
    borderClass: 'border-violet-500/40',
    textClass: 'text-violet-300',
    label: 'Prompt Gen',
    icon: '✎',
    stationIcon: '✍',
  },
  CODE_GENERATOR: {
    cssVar: 'agent-code-gen',
    hex: '#34D399',
    hexGlow: 'rgba(52,211,153,0.25)',
    bgClass: 'bg-emerald-500/15',
    borderClass: 'border-emerald-500/40',
    textClass: 'text-emerald-300',
    label: 'Code Gen',
    icon: '◈',
    stationIcon: '⚡',
  },
  CODE_REVIEWER: {
    cssVar: 'agent-code-reviewer',
    hex: '#FBBF24',
    hexGlow: 'rgba(251,191,36,0.25)',
    bgClass: 'bg-amber-500/15',
    borderClass: 'border-amber-500/40',
    textClass: 'text-amber-300',
    label: 'Code Reviewer',
    icon: '✓',
    stationIcon: '🔍',
  },
};

/** Map pipeline post strings (as returned by backend) to AgentPost key */
export function postToAgentKey(post: string): AgentPost | null {
  const map: Record<string, AgentPost> = {
    MODULE_MAKER: 'MODULE_MAKER',
    PROMPT_GENERATOR: 'PROMPT_GENERATOR',
    CODE_GENERATOR: 'CODE_GENERATOR',
    CODE_REVIEWER: 'CODE_REVIEWER',
    // lowercase variants
    module_maker: 'MODULE_MAKER',
    prompt_generator: 'PROMPT_GENERATOR',
    code_generator: 'CODE_GENERATOR',
    code_reviewer: 'CODE_REVIEWER',
  };
  return map[post] ?? null;
}

/** Get accent for an agent post, falling back to a neutral style. */
export function getAccent(post: string): AgentAccent | null {
  const key = postToAgentKey(post);
  return key ? AGENT_ACCENTS[key] : null;
}
