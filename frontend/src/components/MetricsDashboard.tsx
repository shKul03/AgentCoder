/* MetricsDashboard — Rich real-time project metrics
   Visualises every meaningful dimension of the current pipeline run:
   - Live status + active module card
   - Animated summary stat cards
   - Donut chart (module status distribution)
   - Horizontal module completion list with gradient fill bars
   - Iteration bar chart per module
   - Token usage + estimated cost
   - Project & settings summary
*/

import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

import { api } from '../hooks/api';
import type { Metrics, PipelineStatus, Settings, Iteration } from '../types';

// ─── Constants ────────────────────────────────────────────────────────────────

// ─── Animated number counter ──────────────────────────────────────────────────

function AnimatedNumber({
  value,
  format,
}: {
  value: number;
  format?: (n: number) => string;
}) {
  const [displayed, setDisplayed] = useState(0);
  const rafRef = useRef(0);
  const prevRef = useRef(0);

  useEffect(() => {
    const start = prevRef.current;
    prevRef.current = value;
    const diff = value - start;
    if (diff === 0) return;
    const duration = 600;
    const startTime = performance.now();

    function frame(now: number) {
      const t = Math.min(1, (now - startTime) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplayed(Math.round(start + diff * eased));
      if (t < 1) rafRef.current = requestAnimationFrame(frame);
    }

    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value]);

  return <>{format ? format(displayed) : displayed}</>;
}

// ─── Stat card ────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
  accent,
  icon,
  format,
}: {
  label: string;
  value: number;
  sub?: string;
  accent: string;
  icon: string;
  format?: (n: number) => string;
}) {
  return (
    <motion.div
      className="glass-card relative overflow-hidden"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      {/* Accent glow strip */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 rounded-l-xl"
        style={{ background: accent }}
      />
      <div className="pl-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] uppercase tracking-widest text-slate-500">{label}</span>
          <span className="text-base opacity-60">{icon}</span>
        </div>
        <p className="text-3xl font-bold text-white tabular-nums">
          <AnimatedNumber value={value} format={format} />
        </p>
        {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
      </div>
    </motion.div>
  );
}

// ─── Active module card ───────────────────────────────────────────────────────

const AGENT_LABELS: Record<string, { name: string; icon: string; color: string }> = {
  PROMPT_GENERATION:    { name: 'The Writer',    icon: '✍',  color: '#C084FC' },
  HITL_PROMPT_REVIEW:   { name: 'HITL Review',   icon: '⏸',  color: '#f59e0b' },
  CODE_GENERATION:      { name: 'The Builder',   icon: '⚒',  color: '#60A5FA' },
  CODE_REVIEW:          { name: 'The Inspector', icon: '🔍',  color: '#2DD4BF' },
  HITL_REVIEW_DECISION: { name: 'HITL Decision', icon: '⚖',  color: '#f59e0b' },
};

function ActiveCard({ ps }: { ps: PipelineStatus }) {
  const status = ps.pipeline_status;
  const isIdle = status === 'IDLE';
  const isDone = status === 'PIPELINE_COMPLETE';
  const isFailed = status === 'FAILED';
  const isHitl = status.startsWith('HITL_');
  const agent = AGENT_LABELS[status];
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!agent) { setElapsed(0); return; }
    const start = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, [status]);

  const fmtElapsed = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  };

  const borderColor = isFailed
    ? '#ef4444'
    : isHitl
    ? '#f59e0b'
    : isDone
    ? '#22c55e'
    : agent
    ? agent.color
    : '#334155';

  return (
    <div
      className="rounded-xl border p-4 transition-all duration-500"
      style={{
        borderColor,
        background: `${borderColor}08`,
        boxShadow: agent ? `0 0 24px ${borderColor}18` : 'none',
      }}
    >
      <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">Currently Active</p>

      {isIdle && (
        <p className="text-slate-500 text-sm">Pipeline is idle — press Start to begin.</p>
      )}

      {isDone && (
        <p className="text-green-400 font-semibold">🎉 Pipeline complete!</p>
      )}

      {isFailed && (
        <div>
          <p className="text-red-400 font-semibold">⚠ Pipeline failed</p>
          {ps.metadata?.pre_failure_status ? (
            <p className="text-xs text-red-400/70 mt-0.5">
              During: {String(ps.metadata.pre_failure_status)}
            </p>
          ) : null}
        </div>
      )}

      {isHitl && (
        <div>
          <p className="text-amber-400 font-semibold">⏸ Waiting for your approval</p>
          <p className="text-xs text-amber-400/70 mt-0.5">{status.replace('_', ' ')}</p>
        </div>
      )}

      {agent && !isHitl && !isFailed && !isDone && (
        <div className="flex items-start gap-3">
          <motion.span
            className="text-3xl"
            animate={{ scale: [1, 1.08, 1] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
          >
            {agent.icon}
          </motion.span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-white">{agent.name}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded font-medium animate-pulse"
                style={{ background: `${agent.color}20`, color: agent.color }}>
                Working
              </span>
            </div>
            {ps.current_iteration > 0 && (
              <p className="text-[11px] text-slate-600 mt-0.5">
                Attempt {ps.current_iteration}
              </p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="text-sm font-mono text-slate-400">{fmtElapsed(elapsed)}</p>
            <p className="text-[10px] text-slate-600">elapsed</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function MetricsDashboard() {
  const [metrics, setMetrics]     = useState<Metrics | null>(null);
  const [pipStatus, setPipStatus] = useState<PipelineStatus | null>(null);
  const [settings, setSettings]   = useState<Settings | null>(null);
  const [iterData, setIterData]   = useState<Iteration[]>([]);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  useEffect(() => {
    const load = async () => {
      const [m, ps, iters] = await Promise.allSettled([
        api.getMetrics(),
        api.getPipelineStatus(),
        api.getIterations(),
      ]);
      if (m.status     === 'fulfilled') setMetrics(m.value);
      if (ps.status    === 'fulfilled') setPipStatus(ps.value);
      if (iters.status === 'fulfilled') setIterData(iters.value.iterations);
      setLastRefresh(new Date());
    };
    const loadSettings = async () => {
      try { setSettings(await api.getSettings()); } catch {}
    };
    load(); loadSettings();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);

  // ── Derived metrics ──────────────────────────────────────────────────────────
  const iterations  = metrics?.total_iterations ?? 0;
  const tokens      = metrics?.total_token_usage ?? 0;
  const cost        = metrics?.total_cost ?? 0;

  const isRunning = pipStatus
    ? !['IDLE', 'FAILED', 'PIPELINE_COMPLETE'].includes(pipStatus.pipeline_status)
    : false;
  const projectName = settings?.project?.name || 'Untitled Project';
  const language    = settings?.project?.language || '';
  const rootPath    = settings?.project?.root_path || '';

  return (
    <div className="space-y-5 pb-8">

      {/* ── Page header ──────────────────────────────────────────────────────── */}
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h2 className="text-2xl font-bold text-white truncate">{projectName}</h2>
          {(language || rootPath) && (
            <p className="text-xs text-slate-500 mt-0.5">
              {language}{rootPath ? ` · ${rootPath}` : ''}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 mt-1">
          <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-slate-600'}`} />
          <span className="text-xs text-slate-500">{isRunning ? 'Live' : 'Idle'}</span>
          <span className="text-[10px] text-slate-700 ml-2">
            ↻ {lastRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        </div>
      </div>

      {/* ── Stat cards row ────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          label="Iterations"
          value={iterations}
          accent="#f59e0b"
          icon="↻"
        />
        <StatCard
          label="Tokens Used"
          value={tokens}
          format={(n) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)}
          sub={cost > 0 ? `$${cost.toFixed(4)} est.` : undefined}
          accent="#ec4899"
          icon="◈"
        />
        <StatCard
          label="Est. Cost (USD)"
          value={cost}
          format={(n) => `$${n.toFixed(4)}`}
          accent="#22c55e"
          icon="💵"
        />
      </div>

      {/* ── Active card ───────────────────────────────────────────────────────── */}
      <AnimatePresence mode="wait">
        {pipStatus && (
          <motion.div
            key={pipStatus.pipeline_status}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <ActiveCard ps={pipStatus} />
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Main grid ─────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* Pipeline status badge */}
        {pipStatus && (
          <div className="glass-card">
            <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">Pipeline Status</p>
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full shrink-0 ${
                  isRunning ? 'animate-pulse bg-indigo-400' :
                  pipStatus.pipeline_status === 'PIPELINE_COMPLETE' ? 'bg-green-400' :
                  pipStatus.pipeline_status === 'FAILED' ? 'bg-red-400' : 'bg-slate-500'
                }`}
              />
              <span className="text-sm font-mono text-white">{pipStatus.pipeline_status}</span>
            </div>
            {pipStatus.current_iteration > 0 && (
              <p className="text-xs text-slate-500 mt-1.5">
                Iteration {pipStatus.current_iteration}
              </p>
            )}
          </div>
        )}

        {/* Project info */}
        {settings && (
          <div className="glass-card">
            <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-3">Project Info</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
              {[
                { label: 'Name',          val: settings.project?.name || '—' },
                { label: 'Language',      val: settings.project?.language || '—' },
                { label: 'Mode',          val: settings.pipeline_mode || '—' },
                { label: 'Max Iterations', val: String(settings.pipeline?.max_iterations ?? '—') },
                { label: 'Convergence',   val: settings.pipeline?.convergence_rule || '—' },
                { label: 'Auto-HITL',     val: settings.pipeline?.auto_approve_hitl ? 'Yes' : 'No' },
              ].map(({ label, val }) => (
                <div key={label}>
                  <p className="text-slate-600 mb-0.5">{label}</p>
                  <p className="text-slate-200 font-medium truncate" title={val}>{val}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Usage summary ──────────────────────────────────────────────────────── */}
      {(tokens > 0 || cost > 0) && (
        <div className="glass-card">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-3">Usage Summary</p>
          <div className="flex flex-wrap gap-6">
            {[
              { label: '🔢 Total Tokens',     val: tokens.toLocaleString() },
              { label: '💵 Estimated Cost',   val: cost > 0 ? `$${cost.toFixed(4)}` : '—' },
              { label: '🔁 Total Iterations', val: iterations.toString() },
            ].map(({ label, val }) => (
              <div key={label} className="flex flex-col gap-0.5">
                <span className="text-[10px] text-slate-600">{label}</span>
                <span className="text-sm font-semibold text-white">{val}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Iteration history ─────────────────────────────────────────────────── */}
      {iterData.length > 0 && (
        <div className="glass-card">
          <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-4">Iteration History</p>
          {/* Tokens per iteration — horizontal bar chart */}
          <div className="space-y-2.5">
            {(() => {
              const maxTok = Math.max(...iterData.map((i) => i.token_usage), 1);
              return iterData.map((iter) => {
                const pct = Math.round((iter.token_usage / maxTok) * 100);
                const duration = iter.completed_at
                  ? Math.round((new Date(iter.completed_at).getTime() - new Date(iter.started_at).getTime()) / 1000)
                  : null;
                const statusColor =
                  iter.status === 'completed'   ? 'bg-green-500' :
                  iter.status === 'failed'      ? 'bg-red-500' :
                  iter.status === 'in_progress' ? 'bg-indigo-400' : 'bg-slate-600';
                return (
                  <div key={iter.iteration_number} className="text-xs">
                    <div className="flex items-center gap-3 mb-1">
                      <span className="text-slate-500 w-20 shrink-0">Iter {iter.iteration_number}</span>
                      <div className="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden">
                        <motion.div
                          className={`h-full rounded-full ${statusColor}`}
                          initial={{ width: 0 }}
                          animate={{ width: `${pct}%` }}
                          transition={{ duration: 0.5, delay: iter.iteration_number * 0.05 }}
                        />
                      </div>
                      <span className="text-slate-500 w-20 text-right shrink-0">
                        {iter.token_usage > 0 ? `${(iter.token_usage / 1000).toFixed(1)}k tok` : '—'}
                      </span>
                      {duration !== null && (
                        <span className="text-slate-600 w-12 text-right shrink-0">{duration}s</span>
                      )}
                    </div>
                  </div>
                );
              });
            })()}
          </div>
          <div className="flex gap-4 mt-4 text-[10px] text-slate-600">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500" /> Completed</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> Failed</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-indigo-400" /> In Progress</span>
          </div>
        </div>
      )}
    </div>
  );
}

