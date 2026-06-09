/* TerminalPanel — Phase 7
   Single-agent terminal view with:
   - Color-coded monospace output
   - Auto-scroll with pause on manual scroll
   - Session boundary markers
   - Header: display name, status badge, model, module/iter, elapsed time
   - Footer: Copy / Download / Scroll-lock toggle
*/

import { useEffect, useRef, useState, useCallback } from 'react';
import type { AgentTerminalState, AgentStatus, TerminalLine } from '../types';
import { POST_DISPLAY_NAME } from '../hooks/useAgentTerminals';

// ─── Elapsed time ─────────────────────────────────────────────────────────────

function useElapsedSeconds(startedAt: string | null, endedAt: string | null, isRunning: boolean): number {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);

  if (!startedAt) return 0;
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  return Math.max(0, Math.floor((end - new Date(startedAt).getTime()) / 1000));
}

function fmtElapsed(secs: number): string {
  if (secs === 0) return '';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ─── Status badge ─────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<AgentStatus, string> = {
  idle:    'bg-slate-500/20 text-slate-400 border-slate-500/40',
  running: 'bg-green-500/20 text-green-400 border-green-500/40',
  done:    'bg-blue-500/20  text-blue-400  border-blue-500/40',
  error:   'bg-red-500/20   text-red-400   border-red-500/40',
};

function StatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-semibold uppercase border ${STATUS_STYLES[status]}`}>
      {status === 'running' && (
        <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
      )}
      {status}
    </span>
  );
}

// ─── Line renderer ────────────────────────────────────────────────────────────

const LINE_STYLES: Record<string, string> = {
  reasoning:  'text-cyan-400/60',
  file_write: 'text-green-400',
  error:      'text-red-400',
  normal:     'text-slate-200',
};

const BOUNDARY_STYLES: Record<string, string> = {
  session_start: 'text-slate-500 border-t border-[var(--border-glass)] pt-2 mt-1',
  session_end:   'text-slate-500 border-b border-[var(--border-glass)] pb-2 mb-1',
};

function TerminalLineRow({ line }: { line: TerminalLine }) {
  const isBoundary = line.eventType === 'session_start' || line.eventType === 'session_end';
  if (isBoundary) {
    return (
      <div className={`text-[10px] font-mono px-1 my-1 italic select-none ${BOUNDARY_STYLES[line.eventType] ?? ''}`}>
        {line.text}
      </div>
    );
  }

  return (
    <div className={`flex gap-2 font-mono text-xs leading-5 hover:bg-white/5 px-1 rounded-sm ${LINE_STYLES[line.style] ?? 'text-slate-200'}`}>
      <span className="shrink-0 text-slate-600 select-none w-16 text-right">
        {new Date(line.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </span>
      <span className="break-all">{line.text}</span>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

interface Props {
  state: AgentTerminalState;
  /** If provided, clicking the header triggers expand to full-page */
  onExpand?: () => void;
  /** Whether this panel is shown in the compact hub grid */
  compact?: boolean;
}

export default function TerminalPanel({ state, onExpand, compact = false }: Props) {
  const { agentPost, lines, status, model, currentModuleId, currentIteration, sessionStartedAt, sessionEndedAt } = state;
  const displayName = POST_DISPLAY_NAME[agentPost] ?? agentPost;
  const isRunning = status === 'running';

  const elapsed = useElapsedSeconds(sessionStartedAt, sessionEndedAt, isRunning);
  const elapsedLabel = fmtElapsed(elapsed);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollLocked, setScrollLocked] = useState(true);
  // Tracks whether the current scroll event was triggered by our own code,
  // so handleScroll doesn't inadvertently release the lock mid-animation.
  const isProgrammaticRef = useRef(false);

  // Auto-scroll when locked — instant jump keeps handleScroll at-bottom immediately
  useEffect(() => {
    if (!scrollLocked) return;
    const el = scrollRef.current;
    if (!el) return;
    isProgrammaticRef.current = true;
    el.scrollTop = el.scrollHeight;
    // One rAF is enough: by next frame the scroll event has already fired
    requestAnimationFrame(() => { isProgrammaticRef.current = false; });
  }, [lines.length, scrollLocked]);

  // Detect manual scroll-up to release lock; re-lock when user scrolls back to bottom
  const handleScroll = useCallback(() => {
    if (isProgrammaticRef.current) return; // ignore our own programmatic scrolls
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setScrollLocked(atBottom);
  }, []);

  const handleScrollLockToggle = () => {
    setScrollLocked((prev) => {
      if (!prev) {
        // Re-lock → instant jump to bottom so user sees latest output
        const el = scrollRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      }
      return !prev;
    });
  };

  // ── Copy full log to clipboard ────────────────────────────────────────────
  const handleCopy = () => {
    const text = lines.map((l) => `${l.timestamp}  ${l.text}`).join('\n');
    navigator.clipboard.writeText(text).catch(() => null);
  };

  // ── Download as .txt ──────────────────────────────────────────────────────
  const handleDownload = () => {
    const text = lines.map((l) => `${l.timestamp}  ${l.text}`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${agentPost.toLowerCase()}_terminal.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const isEmpty = lines.length === 0;

  return (
    <div
      className={`flex flex-col h-full rounded-xl border bg-[#0a0e1a] overflow-hidden transition-colors ${
        isRunning
          ? 'border-green-500/50 shadow-[0_0_16px_rgba(34,197,94,0.08)]'
          : status === 'error'
          ? 'border-red-500/40'
          : 'border-[var(--border-glass)]'
      }`}
    >
      {/* Header */}
      <div
        className={`flex items-center gap-2 px-3 py-2 border-b border-[var(--border-glass)] bg-black/20 shrink-0 ${onExpand ? 'cursor-pointer hover:bg-white/5' : ''}`}
        onClick={onExpand}
        title={onExpand ? 'Click to expand' : undefined}
      >
        <span className="font-semibold text-sm text-white">{displayName}</span>
        <StatusBadge status={status} />
        {model && (
          <span className="text-[10px] font-mono text-slate-500 hidden sm:inline">
            {model}
          </span>
        )}
        {currentModuleId && (
          <span className="text-[10px] font-mono text-slate-600 hidden md:inline">
            {currentModuleId} #{currentIteration}
          </span>
        )}
        <div className="flex-1" />
        {elapsedLabel && (
          <span className={`text-[10px] font-mono ${isRunning ? 'text-green-400' : 'text-slate-500'}`}>
            {isRunning ? '⏱' : '✓'} {elapsedLabel}
          </span>
        )}
        {/* Prevent expand from firing on button clicks */}
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={handleCopy}
            title="Copy log"
            className="px-1.5 py-0.5 text-[10px] rounded text-slate-500 hover:text-slate-300 hover:bg-white/10 transition-colors"
          >
            ⎘ Copy
          </button>
          <button
            onClick={handleDownload}
            title="Download log"
            className="px-1.5 py-0.5 text-[10px] rounded text-slate-500 hover:text-slate-300 hover:bg-white/10 transition-colors"
          >
            ↓ Log
          </button>
          <button
            onClick={handleScrollLockToggle}
            title={scrollLocked ? 'Scroll lock on — click to release' : 'Scroll lock off — click to lock'}
            className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
              scrollLocked
                ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
                : 'text-slate-500 hover:text-slate-300 hover:bg-white/10'
            }`}
          >
            {scrollLocked ? '⇣ Locked' : '⇣ Free'}
          </button>
        </div>
      </div>

      {/* Terminal body */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={`flex-1 overflow-y-auto p-2 ${compact ? 'min-h-0' : ''}`}
        style={{ fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace" }}
      >
        {isEmpty ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-slate-600 text-xs italic font-mono">
              {status === 'idle' ? '— Waiting for agent to start —' : 'No output yet'}
            </p>
          </div>
        ) : (
          lines.map((line) => <TerminalLineRow key={line.id} line={line} />)
        )}
      </div>

      {/* Footer: line count */}
      <div className="shrink-0 flex items-center gap-3 px-3 py-1 border-t border-[var(--border-glass)] bg-black/20">
        <span className="text-[10px] text-slate-600 font-mono">
          {lines.length} line{lines.length !== 1 ? 's' : ''}
        </span>
        {!scrollLocked && (
          <button
            onClick={handleScrollLockToggle}
            className="text-[10px] text-indigo-400 hover:text-indigo-300 underline"
          >
            ↓ scroll to bottom
          </button>
        )}
      </div>
    </div>
  );
}
