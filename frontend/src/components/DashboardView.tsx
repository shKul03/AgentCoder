/* DashboardView — Phase 5 / Phase 7 (GitHub Review mode story queue)
   Left: pipeline status card, GHR context, iteration counter, Start/Pause/Approve/Reset controls.
   Right: Story Queue (GHR mode) + PipelineFlowDiagram (compact embed).
*/

import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../hooks/api';
import type { StoryQueueItem, StoryQueueResponse } from '../hooks/api';
import { usePipelineFlow } from '../hooks/usePipelineFlow';
import PipelineFlowDiagram from './PipelineFlowDiagram';

export default function DashboardView() {
  const { pipelineStatus, isHitlGate, currentIteration, statusText, loading } = usePipelineFlow();
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetMsg, setResetMsg] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  // â”€â”€ Story Queue (GitHub Review mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const [storyQueue, setStoryQueue] = useState<StoryQueueResponse | null>(null);
  const [expandedStory, setExpandedStory] = useState<string | null>(null);
  const queuePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const q = await api.getStoryQueue();
        setStoryQueue(q);
      } catch {
        // Silently ignore — not in GHR mode or endpoint unavailable
      }
    };
    poll();
    queuePollRef.current = setInterval(poll, 3000);
    return () => { if (queuePollRef.current) clearInterval(queuePollRef.current); };
  }, []);

  const isGhrMode = storyQueue?.mode === 'github_review';

  // â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const [pauseRequested, setPauseRequested] = useState(false);

  // Clear the "pause pending" indicator whenever the pipeline status changes
  useEffect(() => { setPauseRequested(false); }, [pipelineStatus]);

  const withError = <T,>(p: Promise<T>) =>
    p.catch((e: unknown) => setActionError(String(e)));

  const handleStart   = () => { setActionError(null); withError(api.startPipeline()); };
  const handlePause   = () => {
    setActionError(null);
    setPauseRequested(true);
    withError(api.pausePipeline());
  };
  const handleApprove = () => { setActionError(null); withError(api.approveGate()); };
  const handleReset = () =>
    api.resetPipeline()
      .then((r) => {
        setResetMsg((r as { message?: string }).message ?? 'Reset queued');
        setShowResetConfirm(false);
      })
      .catch(() => { setResetMsg('Reset failed'); setShowResetConfirm(false); });

  const isFailed = pipelineStatus === 'FAILED';
  const isComplete = pipelineStatus === 'PIPELINE_COMPLETE';

  // Status badge colour
  const statusColour = isFailed  ? 'text-red-400'
    : isComplete                 ? 'text-green-400'
    : isHitlGate                 ? 'text-yellow-400'
    : pipelineStatus === 'IDLE'  ? 'text-slate-500'
    :                              'text-indigo-400';

  // â”€â”€ Story status badge â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function storyStatusBadge(status: StoryQueueItem['status']) {
    const base = 'px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide';
    switch (status) {
      case 'completed':   return <span className={`${base} bg-green-500/20 text-green-400`}>done</span>;
      case 'in_progress': return <span className={`${base} bg-indigo-500/20 text-indigo-300 animate-pulse`}>active</span>;
      case 'failed':      return <span className={`${base} bg-red-500/20 text-red-400`}>failed</span>;
      default:            return <span className={`${base} bg-white/[0.06] text-white/40`}>queued</span>;
    }
  }

  // Active story for GHR left-panel context
  const activeStory = storyQueue?.stories.find(s => s.story_id === storyQueue.current_story_id)
    ?? storyQueue?.stories.find(s => s.status === 'in_progress');

  return (
    <div className="flex gap-5 h-full min-h-0">

      {/* â”€â”€ Left: Control panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <aside className="w-[360px] shrink-0 flex flex-col gap-4 overflow-y-auto pr-1" style={{ minWidth: 260 }}>

        {/* Action error banner */}
        <AnimatePresence>
          {actionError && (
            <motion.div
              key="action-error"
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-red-400 text-xs"
            >
              <span className="flex-1">{actionError}</span>
              <button onClick={() => setActionError(null)} className="hover:text-white transition-colors">âœ•</button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Pipeline status card */}
        <div className="glass-card">
          <p className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] mb-1">Pipeline Status</p>
          {loading ? (
            <p className="text-sm text-[var(--text-muted)] animate-pulse">Loadingâ€¦</p>
          ) : (
            <>
              <p className={`text-lg font-semibold font-mono ${statusColour}`}>{pipelineStatus}</p>
              <p className="text-xs text-[var(--text-secondary)] mt-1 leading-relaxed">{statusText}</p>
              {currentIteration > 0 && !isGhrMode && (
                <p className="text-[10px] font-mono text-slate-500 mt-1">Iteration {currentIteration}</p>
              )}
            </>
          )}
        </div>

        {/* GitHub Review mode context card */}
        {isGhrMode && storyQueue && (
          <motion.div
            key="ghr-context"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-card border-indigo-500/20"
          >
            <p className="text-[10px] uppercase tracking-widest text-indigo-400/60 mb-3">GitHub Review Mode</p>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-white/40">Progress</span>
                <span className="font-mono text-white/70">
                  {storyQueue.stories_completed} / {storyQueue.stories_total || storyQueue.stories.length} stories
                </span>
              </div>
              {storyQueue.current_story_id && (
                <div className="flex justify-between">
                  <span className="text-white/40">Current Story</span>
                  <span className="font-mono text-indigo-300 truncate max-w-[160px]" title={storyQueue.current_story_id}>
                    {storyQueue.current_story_id}
                  </span>
                </div>
              )}
              {activeStory && (
                <>
                  {activeStory.story_iteration > 0 && (
                    <div className="flex justify-between">
                      <span className="text-white/40">Story Iteration</span>
                      <span className="font-mono text-white/70">{activeStory.story_iteration}</span>
                    </div>
                  )}
                  {activeStory.branch_name && (
                    <div className="flex justify-between">
                      <span className="text-white/40">Branch</span>
                      <span className="font-mono text-white/50 truncate max-w-[160px]" title={activeStory.branch_name}>
                        {activeStory.branch_name}
                      </span>
                    </div>
                  )}
                  {activeStory.pr_number && (
                    <div className="flex justify-between items-center">
                      <span className="text-white/40">PR</span>
                      {activeStory.pr_url ? (
                        <a
                          href={activeStory.pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-indigo-400 hover:text-indigo-300 transition-colors"
                        >
                          #{activeStory.pr_number} 
                        </a>
                      ) : (
                        <span className="font-mono text-white/50">#{activeStory.pr_number}</span>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </motion.div>
        )}

        {/* Primary controls */}
        <div className="glass-card space-y-2">
          <p className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] mb-2">Controls</p>

          <div className="flex gap-2">
            <button
              onClick={handleStart}
              className="flex-1 px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-medium transition-colors"
            >
              Start / Resume
            </button>
            <button
              onClick={handlePause}
              disabled={pauseRequested}
              className="px-3 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm font-medium transition-colors disabled:opacity-60"
              title={pauseRequested ? 'Pause pending — current step will finish first' : 'Pause after the current step completes'}
            >
              {pauseRequested ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                  Pausing…
                </span>
              ) : 'Pause'}
            </button>
          </div>

          <AnimatePresence>
            {isHitlGate && (
              <motion.button
                key="approve-gate"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
                onClick={handleApprove}
                className="w-full px-3 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-sm font-medium"
                style={{ boxShadow: '0 0 14px rgba(34,197,94,0.35)' }}
              >
                Approve Gate
              </motion.button>
            )}
          </AnimatePresence>
        </div>

        {/* Danger zone — directly below Controls */}
        <div className="glass-card border-red-500/15">
          <p className="text-[10px] uppercase tracking-widest text-red-400/60 mb-2">Danger Zone</p>
          {!showResetConfirm ? (
            <button
              onClick={() => setShowResetConfirm(true)}
              className="w-full px-3 py-2 rounded-lg border border-red-500/25 text-red-400 hover:bg-red-500/10 text-sm font-medium transition-colors"
            >
              Reset Pipeline
            </button>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-red-300">This will clear all progress. Are you sure?</p>
              <div className="flex gap-2">
                <button onClick={handleReset} className="flex-1 px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-xs font-medium transition-colors">
                  Confirm Reset
                </button>
                <button onClick={() => setShowResetConfirm(false)} className="flex-1 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-xs font-medium transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          )}
          {resetMsg && <p className="text-xs text-slate-400 mt-2">{resetMsg}</p>}
        </div>
      </aside>

      {/* â”€â”€ Right: Story Queue (GHR) + Pipeline flow diagram â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
      <div className="flex-1 min-w-0 flex flex-col gap-4 min-h-0">

        {/* Story Queue panel — GitHub Review mode only */}
        {isGhrMode && storyQueue && (
          <div className="glass-card flex flex-col overflow-hidden" style={{ maxHeight: 360 }}>
            <div className="flex items-center justify-between mb-3 shrink-0">
              <p className="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Story Queue</p>
              <span className="text-[10px] text-white/30">
                {storyQueue.stories_completed} / {storyQueue.stories_total || storyQueue.stories.length} complete
              </span>
            </div>
            <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
              {storyQueue.stories.length === 0 && (
                <p className="text-xs text-white/30 text-center py-4">No stories in queue yet</p>
              )}
              {storyQueue.stories.map((story) => {
                const isExpanded = expandedStory === story.story_id;
                const isActive   = story.story_id === storyQueue.current_story_id
                                || story.status === 'in_progress';
                return (
                  <div
                    key={story.story_id}
                    className={`rounded-lg border transition-colors cursor-pointer select-none ${
                      isActive
                        ? 'border-indigo-500/40 bg-indigo-500/[0.08]'
                        : story.status === 'completed'
                        ? 'border-green-500/20 bg-green-500/[0.04]'
                        : story.status === 'failed'
                        ? 'border-red-500/20 bg-red-500/[0.04]'
                        : 'border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.04]'
                    }`}
                    onClick={() => setExpandedStory(isExpanded ? null : story.story_id)}
                  >
                    {/* Summary row */}
                    <div className="flex items-center gap-2 px-3 py-2">
                      <span className="text-[10px] font-mono text-white/25 w-5 shrink-0">{story.position}</span>
                      <span className="text-[10px] font-mono text-white/40 shrink-0 w-20 truncate" title={story.story_id}>
                        {story.story_id}
                      </span>
                      <span className="flex-1 text-xs text-white/70 truncate" title={story.title}>
                        {story.title}
                      </span>
                      <div className="flex items-center gap-2 shrink-0">
                        {story.story_iteration > 1 && (
                          <span className="text-[10px] text-white/30">iter {story.story_iteration}</span>
                        )}
                        {storyStatusBadge(story.status)}
                        <span className="text-white/20 text-[10px]">{isExpanded ? '▲' : '▼'}</span>
                      </div>
                    </div>

                    {/* Expanded detail */}
                    {isExpanded && (
                      <div className="px-3 pb-3 border-t border-white/[0.04] space-y-2 pt-2">
                        {story.acceptance_criteria.length > 0 && (
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-white/25 mb-1">Acceptance Criteria</p>
                            <ul className="space-y-0.5">
                              {story.acceptance_criteria.map((ac, i) => (
                                <li key={i} className="flex gap-1.5 text-[11px] text-white/50">
                                  <span className="text-white/20 shrink-0">—</span>
                                  <span>{ac}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-white/40">
                          {story.branch_name && (
                            <span>🌿 <span className="font-mono">{story.branch_name}</span></span>
                          )}
                          {story.pr_url ? (
                            <a
                              href={story.pr_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-indigo-400 hover:text-indigo-300 transition-colors"
                              onClick={(e) => e.stopPropagation()}
                            >
                              🔗 PR #{story.pr_number}
                            </a>
                          ) : story.pr_number ? (
                            <span>🔗 PR #{story.pr_number}</span>
                          ) : null}
                        </div>
                        {story.depends_on.length > 0 && (
                          <p className="text-[11px] text-white/35">
                            ⛓ Depends on: <span className="font-mono">{story.depends_on.join(', ')}</span>
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Flow diagram */}
        <div className="glass-card flex-1 min-h-0 flex flex-col items-center justify-center py-6">
          <PipelineFlowDiagram
            pipelineStatus={pipelineStatus}
            currentIteration={currentIteration}
            compact={false}
          />
        </div>
      </div>
    </div>
  );
}
