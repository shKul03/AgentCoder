/* Sidebar navigation — Phase 11 restructure
   5 primary tabs always visible + collapsible "Tools" section for secondary items.
*/

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

// ─── Tab definitions ──────────────────────────────────────────────────────────

const PRIMARY_TABS = [
  { id: 'dashboard',    label: 'Dashboard',    icon: '◉' },
  { id: 'agents',       label: 'Agents',       icon: '⬡' },
  { id: 'terminal-hub', label: 'Command Center', icon: '⬛' },
  { id: 'projects',     label: 'Projects',     icon: '⧉' },
  { id: 'settings',     label: 'Settings',     icon: '⚙' },
] as const;

const TOOL_TABS = [
  { id: 'workflow',      label: 'Workflow',       icon: '◩' },
  { id: 'insights',      label: 'Code Insights', icon: '◈' },
  { id: 'git',           label: 'Git & History', icon: '⎇' },
  { id: 'metrics',       label: 'Metrics',       icon: '◔' },
] as const;

// Combine for full TabId union
const ALL_TABS = [...PRIMARY_TABS, ...TOOL_TABS] as const;
export type TabId = (typeof ALL_TABS)[number]['id'];

// ─── Tab button ───────────────────────────────────────────────────────────────

function TabBtn({
  id, label, icon, active, anyActive, onSelect,
}: {
  id: TabId; label: string; icon: string;
  active: boolean; anyActive: boolean; onSelect: (id: TabId) => void;
}) {
  return (
    <button
      onClick={() => onSelect(id)}
      className={`relative text-left w-full px-3 py-2 rounded-lg text-sm transition-colors ${
        active
          ? 'text-white'
          : 'text-[var(--text-secondary)] hover:text-white hover:bg-white/5'
      }`}
    >
      {active && (
        <motion.div
          layoutId="sidebar-active"
          className="absolute inset-0 rounded-lg bg-indigo-500/20 border border-indigo-500/30"
          transition={{ type: 'spring', stiffness: 350, damping: 30 }}
        />
      )}
      <span className="relative z-10 flex items-center gap-2">
        <span className="w-4 text-center shrink-0 leading-none">{icon}</span>
        <span className="truncate">{label}</span>
        {id === 'terminal-hub' && anyActive && (
          <span className="ml-auto w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse shrink-0" />
        )}
      </span>
    </button>
  );
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

interface Props {
  active: TabId;
  onSelect: (id: TabId) => void;
  activeAgentPosts?: string[];
}

export default function Sidebar({ active, onSelect, activeAgentPosts = [] }: Props) {
  const anyActive = activeAgentPosts.length > 0;
  const [toolsOpen, setToolsOpen] = useState(
    TOOL_TABS.some((t) => t.id === active),
  );

  return (
    <nav
      className="shrink-0 flex flex-col gap-0.5 p-3 border-r border-[var(--border-glass)] overflow-y-auto"
      style={{ width: 'var(--sidebar-w)' }}
    >
      {/* Brand */}
      <h1 className="text-[15px] font-bold mb-4 px-2 bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent tracking-tight select-none">
        Agent OS
      </h1>

      {/* Primary navigation */}
      {PRIMARY_TABS.map((tab) => (
        <TabBtn
          key={tab.id}
          id={tab.id}
          label={tab.label}
          icon={tab.icon}
          active={active === tab.id}
          anyActive={anyActive}
          onSelect={onSelect}
        />
      ))}

      {/* Divider */}
      <div className="my-2 mx-2 border-t border-[var(--border-glass)]" />

      {/* Collapsible tools section */}
      <button
        onClick={() => setToolsOpen((v) => !v)}
        className="flex items-center justify-between w-full px-3 py-1.5 rounded-lg text-[11px] font-semibold uppercase tracking-widest text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
      >
        <span>Tools</span>
        <motion.span
          animate={{ rotate: toolsOpen ? 90 : 0 }}
          transition={{ duration: 0.15 }}
          className="text-xs"
        >
          ›
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {toolsOpen && (
          <motion.div
            key="tools"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="space-y-0.5 pt-0.5">
              {TOOL_TABS.map((tab) => (
                <TabBtn
                  key={tab.id}
                  id={tab.id}
                  label={tab.label}
                  icon={tab.icon}
                  active={active === tab.id}
                  anyActive={anyActive}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Active agents indicator */}
      {anyActive && (
        <div className="mt-auto pt-3 px-1">
          <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-2">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse shrink-0" />
              <span className="text-[9px] font-semibold text-green-400 uppercase tracking-wider">Running</span>
            </div>
            {activeAgentPosts.map((post) => (
              <div key={post} className="text-[9px] text-green-300/70 font-mono pl-3 truncate">
                {post.replace(/_/g, ' ').toLowerCase()}
              </div>
            ))}
          </div>
        </div>
      )}
    </nav>
  );
}
