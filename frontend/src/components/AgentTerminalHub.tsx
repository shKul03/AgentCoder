/* AgentTerminalHub — Phase 7
   Option B: 2×2 grid of TerminalPanel, one per pipeline post.
   Clicking any panel header expands it to full-screen (Option A).
*/

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { AgentTerminalState } from '../types';
import { PIPELINE_POSTS } from '../types';
import TerminalPanel from './TerminalPanel';
import { POST_DISPLAY_NAME } from '../hooks/useAgentTerminals';

interface Props {
  states: Record<string, AgentTerminalState>;
  wsConnected: boolean;
}

export default function AgentTerminalHub({ states, wsConnected }: Props) {
  const [focused, setFocused] = useState<string | null>(null);

  const focusedState = focused ? states[focused] : null;

  return (
    <div className="h-full flex flex-col">
      {/* Page header */}
      <div className="mb-5 shrink-0 flex items-center gap-3">
        <div>
          <h2 className="text-2xl font-bold text-white">Terminal Hub</h2>
          <p className="text-sm text-[var(--text-secondary)] mt-1">
            Live Codex output from all four pipeline agents.
          </p>
        </div>
        <div className="flex-1" />
        {/* WS connection pill */}
        <span
          className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border ${
            wsConnected
              ? 'bg-green-500/10 text-green-400 border-green-500/30'
              : 'bg-red-500/10 text-red-400 border-red-500/30'
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`} />
          {wsConnected ? 'Live' : 'Disconnected'}
        </span>
      </div>

      {/* 2 × 2 grid */}
      <div className="flex-1 min-h-0 grid grid-cols-2 grid-rows-2 gap-4">
        {PIPELINE_POSTS.map((post) => {
          const agentState = states[post];
          return (
            <TerminalPanel
              key={post}
              state={agentState}
              compact={true}
              onExpand={() => setFocused(post)}
            />
          );
        })}
      </div>

      {/* Full-screen overlay — Option A click-through */}
      <AnimatePresence>
        {focusedState && (
          <motion.div
            key="terminal-fullscreen"
            className="fixed inset-0 z-50 flex flex-col bg-[var(--bg-primary)]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {/* Overlay header */}
            <div className="flex items-center gap-3 px-5 py-3 border-b border-[var(--border-glass)] shrink-0">
              <button
                onClick={() => setFocused(null)}
                className="text-slate-400 hover:text-white transition-colors text-sm mr-1"
                title="Back to hub"
              >
                ← Back
              </button>
              <span className="text-slate-500 text-sm">Terminal Hub</span>
              <span className="text-slate-500">/</span>
              <span className="font-semibold text-white">
                {POST_DISPLAY_NAME[focusedState.agentPost] ?? focusedState.agentPost}
              </span>
            </div>

            {/* Full-size panel */}
            <div className="flex-1 p-4 min-h-0">
              <TerminalPanel
                state={focusedState}
                compact={false}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
