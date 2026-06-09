/* WorkflowView — Phase 5
   Full-page pipeline visualization:
   - PipelineFlowDiagram (full size, shimmer animations)
   - Live event feed (plain-English narration)
   - Iteration badge
*/

import { useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { usePipelineFlow } from '../hooks/usePipelineFlow';
import PipelineFlowDiagram from './PipelineFlowDiagram';

export default function WorkflowView() {
  const { pipelineStatus, currentIteration, statusText, loading, events } = usePipelineFlow();
  const feedRef = useRef<HTMLDivElement>(null);

  // Auto-scroll event feed to top on new events
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0;
  }, [events.length]);

  const statusColour = pipelineStatus === 'FAILED'           ? 'text-red-400'
    : pipelineStatus === 'PIPELINE_COMPLETE'                 ? 'text-green-400'
    : pipelineStatus.startsWith('HITL')                      ? 'text-yellow-400'
    : pipelineStatus === 'IDLE'                              ? 'text-slate-500'
    :                                                          'text-indigo-400';

  return (
    <div className="flex h-full min-h-0 gap-4">

      {/* ── Left: header + diagram ─────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0 gap-4">

        {/* Header bar */}
        <div className="flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-white">Pipeline Flow</h2>
            {!loading && (
              <p className="text-xs text-[var(--text-secondary)] mt-0.5 leading-relaxed max-w-2xl">
                {statusText}
              </p>
            )}
          </div>
          {currentIteration > 0 && (
            <motion.span
              layout
              className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/20 border border-indigo-500/40 text-indigo-300 shrink-0"
            >
              Iteration {currentIteration}
            </motion.span>
          )}
        </div>

        {/* Diagram */}
        <div className="glass-card flex-1 min-h-0 flex flex-col items-center justify-center py-6">
          {loading ? (
            <p className="text-sm text-[var(--text-muted)] animate-pulse">Connecting…</p>
          ) : (
            <PipelineFlowDiagram
              pipelineStatus={pipelineStatus}
              currentIteration={currentIteration}
              compact={false}
            />
          )}
          <p className={`text-xs font-mono mt-4 ${statusColour}`}>{pipelineStatus}</p>
        </div>
      </div>

      {/* ── Right: event feed sidebar ───────────────────────────────────────── */}
      <div className="glass-card w-72 shrink-0 flex flex-col min-h-0 gap-2">
        <p className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] shrink-0">Event Feed</p>
        <div ref={feedRef} className="flex-1 overflow-y-auto space-y-1.5 min-h-0">
          {events.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)] italic">No events yet — start the pipeline to begin.</p>
          ) : (
            events.map((ev) => (
              <div key={ev.id} className="flex items-start gap-2 text-xs">
                <span className="shrink-0 leading-relaxed">{ev.icon}</span>
                <span className="text-[var(--text-secondary)] flex-1 leading-relaxed">{ev.text}</span>
                <span className="shrink-0 text-[10px] text-slate-600 font-mono">
                  {new Date(ev.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

