/* Git & History — Phase 9
   Enhanced iteration timeline · GitHub-style PR comments · Deep CI output */

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../hooks/api';
import type { Iteration } from '../types';

// ─── Severity config ──────────────────────────────────────────────────────────

const SEV_BORDER: Record<string, string> = {
  critical: 'border-red-500/50',
  high:     'border-orange-500/50',
  medium:   'border-yellow-500/40',
  low:      'border-slate-600/40',
};
const SEV_BG: Record<string, string> = {
  critical: 'bg-red-500/8',
  high:     'bg-orange-500/8',
  medium:   'bg-yellow-500/5',
  low:      'bg-slate-800/40',
};
const SEV_TEXT: Record<string, string> = {
  critical: 'text-red-300',
  high:     'text-orange-300',
  medium:   'text-yellow-300',
  low:      'text-slate-400',
};
const SEV_DOT: Record<string, string> = {
  critical: 'bg-red-500', high: 'bg-orange-500', medium: 'bg-yellow-400', low: 'bg-slate-500',
};
const SEV_BADGE: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  high:     'bg-orange-500/20 text-orange-400 border-orange-500/30',
  medium:   'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  low:      'bg-slate-700/40 text-slate-500 border-slate-600/30',
};

// ─── Shared helpers ───────────────────────────────────────────────────────────

interface ReviewData {
  overall_status?: string;
  overall_score?: number;
  line_comments?: Array<{
    file: string;
    line?: number | string;
    comment: string;
    severity?: string;
    checklist_item?: string;
  }>;
  global_comments?: Array<{
    comment: string;
    category?: string;
    severity?: string;
  }>;
  summary?: string;
}

function parseReviewJson(raw: string): ReviewData | null {
  if (!raw) return null;
  try { return JSON.parse(raw) as ReviewData; }
  catch { return null; }
}

function fmtTs(iso: string) {
  return new Date(iso).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function durationSecs(started: string, completed: string | null): number | null {
  if (!completed) return null;
  return Math.round((new Date(completed).getTime() - new Date(started).getTime()) / 1000);
}

// ─── Status dot + label ───────────────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  completed:   'bg-green-400',
  in_progress: 'bg-indigo-400 animate-pulse',
  failed:      'bg-red-400',
  pending:     'bg-slate-600',
};
const STATUS_LBL: Record<string, { text: string; color: string }> = {
  completed:   { text: 'Completed', color: 'text-green-400' },
  in_progress: { text: 'In Progress', color: 'text-indigo-400' },
  failed:      { text: 'Failed', color: 'text-red-400' },
  pending:     { text: 'Pending', color: 'text-slate-500' },
};

// ─── Verdict badge ────────────────────────────────────────────────────────────

function VerdictBadge({ status, score }: { status?: string; score?: number }) {
  if (!status) return null;
  const cls =
    status === 'accepted'   ? 'bg-green-500/20 text-green-400 border-green-500/30' :
    status === 'rejected'   ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                              'bg-amber-500/20 text-amber-400 border-amber-500/30';
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${cls}`}>
      {status.replace('_', ' ')}
      {score !== undefined && ` · ${score}/100`}
    </span>
  );
}

// ─── Iteration timeline ───────────────────────────────────────────────────────

function IterationRow({ iter }: { iter: Iteration }) {
  const [expanded, setExpanded] = useState(false);
  const dot  = STATUS_DOT[iter.status] ?? 'bg-slate-600';
  const lbl  = STATUS_LBL[iter.status] ?? { text: iter.status, color: 'text-slate-400' };
  const dur  = durationSecs(iter.started_at, iter.completed_at);
  const rev  = parseReviewJson(iter.review_json_content ?? '');
  const prompt = (iter.prompt_content ?? '').trim();

  return (
    <div className="relative mb-4">
      {/* Timeline dot */}
      <div className={`absolute -left-4 top-1.5 w-2.5 h-2.5 rounded-full ${dot}`} />

      {/* Header row */}
      <div className="text-[11px] text-slate-600">{fmtTs(iter.started_at)}</div>
      <div className="flex items-center gap-2 flex-wrap mt-0.5">
        <span className="text-sm font-semibold text-white">Iteration {iter.iteration_number}</span>
        <span className={`text-[11px] font-medium ${lbl.color}`}>{lbl.text}</span>
        {iter.cli_tool_used && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 border border-indigo-500/25 text-indigo-300 font-mono">
            {iter.cli_tool_used}
          </span>
        )}
        <VerdictBadge status={rev?.overall_status} score={rev?.overall_score} />
        {dur !== null && (
          <span className="text-[10px] text-slate-600 ml-auto font-mono">{dur}s</span>
        )}
      </div>

      {/* Secondary row */}
      <div className="flex flex-wrap gap-3 mt-1 text-[11px] text-slate-500">
        {(iter.token_usage ?? 0) > 0 && (
          <span>{iter.token_usage.toLocaleString()} tokens</span>
        )}
        {prompt && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            {expanded ? '▲ hide prompt' : '▼ prompt'}
          </button>
        )}
      </div>

      {/* Expandable prompt snippet */}
      <AnimatePresence>
        {expanded && prompt && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="mt-2 rounded-lg border border-slate-700/50 bg-slate-900/60 px-3 py-2 text-[11px] text-slate-400 font-mono whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto">
              {prompt.slice(0, 400)}{prompt.length > 400 ? '…' : ''}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function IterationTimeline({ iterations }: { iterations: Iteration[] }) {
  if (iterations.length === 0)
    return <p className="text-xs text-slate-600 italic">No iterations yet.</p>;

  return (
    <div className="relative pl-5">
      <div className="absolute left-1.5 top-2 bottom-2 w-px bg-slate-700/70" />
      {[...iterations].reverse().map(iter => (
        <IterationRow key={iter.iteration_number} iter={iter} />
      ))}
    </div>
  );
}

// ─── GitHub-style PR comments ─────────────────────────────────────────────────

interface FileGroup {
  file: string;
  comments: NonNullable<ReviewData['line_comments']>;
}

function FileCommentGroup({ group, defaultOpen }: { group: FileGroup; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-slate-700/50 overflow-hidden">
      {/* File header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-slate-800/60 text-left hover:bg-slate-800/80 transition-colors"
      >
        <span className="text-[10px] text-slate-500">{open ? '▼' : '▶'}</span>
        <span className="text-xs font-mono text-slate-300 flex-1 truncate">{group.file}</span>
        <span className="text-[10px] text-slate-500 shrink-0">{group.comments.length} comment{group.comments.length !== 1 ? 's' : ''}</span>
      </button>

      {/* Comments */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="divide-y divide-slate-700/30">
              {group.comments.map((c, i) => {
                const sev = (c.severity ?? 'low').toLowerCase();
                return (
                  <div key={i} className={`border-l-2 ${SEV_BORDER[sev] ?? SEV_BORDER.low}`}>
                    {/* Line indicator */}
                    <div className={`px-3 py-1.5 flex items-center gap-2 ${SEV_BG[sev] ?? SEV_BG.low}`}>
                      {c.line && (
                        <span className="text-[10px] font-mono text-slate-500">L{c.line}</span>
                      )}
                      {c.checklist_item && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded border font-medium
                          bg-slate-700/40 text-slate-400 border-slate-600/40">
                          {c.checklist_item}
                        </span>
                      )}
                      <span className={`ml-auto text-[10px] font-semibold rounded px-1.5 py-0.5 border
                        ${SEV_BADGE[sev] ?? SEV_BADGE.low}`}>
                        {sev}
                      </span>
                    </div>
                    {/* Comment body — GitHub style */}
                    <div className="px-3 py-2.5 bg-slate-900/40">
                      <div className="flex gap-2">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1 ${SEV_DOT[sev] ?? SEV_DOT.low}`} />
                        <p className={`text-xs leading-relaxed ${SEV_TEXT[sev] ?? SEV_TEXT.low}`}>{c.comment}</p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function PRCommentsSection({ review }: { review: ReviewData | null }) {
  if (!review) {
    return (
      <p className="text-xs text-slate-600 italic">
        No review available yet — code review results will appear here after the first review cycle.
      </p>
    );
  }

  const lineComments   = review.line_comments   ?? [];
  const globalComments = review.global_comments ?? [];

  // Group inline comments by file path
  const fileGroups: FileGroup[] = [];
  const fileMap = new Map<string, FileGroup>();
  for (const c of lineComments) {
    const key = c.file ?? '(unknown)';
    if (!fileMap.has(key)) {
      const g: FileGroup = { file: key, comments: [] };
      fileMap.set(key, g);
      fileGroups.push(g);
    }
    fileMap.get(key)!.comments.push(c);
  }

  return (
    <div className="space-y-4">
      {/* Verdict strip */}
      {review.overall_status && (
        <div className="flex items-center gap-3 flex-wrap">
          <VerdictBadge status={review.overall_status} score={review.overall_score} />
          {review.summary && (
            <span className="text-xs text-slate-400 italic truncate max-w-sm">
              {review.summary.slice(0, 140)}
            </span>
          )}
        </div>
      )}

      {/* Inline comments — grouped by file */}
      {fileGroups.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
            Inline Comments — {lineComments.length} across {fileGroups.length} file{fileGroups.length !== 1 ? 's' : ''}
          </p>
          <div className="space-y-2">
            {fileGroups.map((g, i) => (
              <FileCommentGroup key={g.file} group={g} defaultOpen={i === 0} />
            ))}
          </div>
        </div>
      )}

      {/* Global comments */}
      {globalComments.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
            Global Comments ({globalComments.length})
          </p>
          <div className="space-y-2">
            {globalComments.map((c, i) => {
              const sev = (c.severity ?? 'low').toLowerCase();
              return (
                <div key={i} className={`rounded-lg border px-3 py-2.5 text-xs
                  ${SEV_BORDER[sev] ?? SEV_BORDER.low} ${SEV_BG[sev] ?? SEV_BG.low}`}
                >
                  <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${SEV_DOT[sev] ?? SEV_DOT.low}`} />
                    {c.category && (
                      <span className="text-[10px] font-medium text-slate-400">{c.category}</span>
                    )}
                    <span className={`ml-auto text-[10px] font-semibold rounded px-1.5 py-0.5 border
                      ${SEV_BADGE[sev] ?? SEV_BADGE.low}`}>
                      {sev}
                    </span>
                  </div>
                  <p className={`leading-relaxed ${SEV_TEXT[sev] ?? SEV_TEXT.low}`}>{c.comment}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {lineComments.length === 0 && globalComments.length === 0 && (
        <p className="text-xs text-slate-600 italic">No review comments in the latest review.</p>
      )}
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

type Section = 'timeline' | 'pr-comments';

export default function GitHistory() {
  const [iterations, setIterations] = useState<Iteration[]>([]);
  const [section, setSection]       = useState<Section>('timeline');

  // Use the latest completed iteration's review for the PR Comments tab
  const latestReviewIter = [...iterations].reverse().find(i => i.review_json_content);
  const latestReview = latestReviewIter ? parseReviewJson(latestReviewIter.review_json_content) : null;

  useEffect(() => {
    const load = async () => {
      const res = await api.getIterations().catch(() => null);
      if (res) setIterations(res.iterations);
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const tabs: { id: Section; label: string }[] = [
    { id: 'timeline',    label: `Timeline (${iterations.length})` },
    { id: 'pr-comments', label: 'PR Comments' },
  ];

  return (
    <div className="flex flex-col gap-5 h-full min-h-0">

      {/* ── Section tabs ──────────────────────────────────────────────────── */}
      <div className="flex gap-1 shrink-0">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setSection(t.id)}
            className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              section === t.id
                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Section content ────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto min-h-0">
        <AnimatePresence mode="wait">
          <motion.div
            key={section}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.15 }}
          >
            <div className="glass-card">
              {section === 'timeline' && (
                <>
                  <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-3">Iteration Timeline</p>
                  <IterationTimeline iterations={iterations} />
                </>
              )}
              {section === 'pr-comments' && (
                <>
                  <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-3">PR Review Comments</p>
                  <PRCommentsSection review={latestReview} />
                </>
              )}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

    </div>
  );
}

