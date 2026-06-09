/* ProjectsView — Phase 6
   Per-iteration project insights.

   Layout:
     ┌─ Project Header ─────────────────────────────────────────────┐
     │  name · language · path · pipeline status · GitHub repo link │
     └──────────────────────────────────────────────────────────────┘
     ┌─ Tabs [Iterations] [Dependencies] ──────────────────────────┐
     │  • Iterations: card per iteration — expandable detail        │
     │  • Dependencies: parsed package.json / requirements.txt      │
     └─────────────────────────────────────────────────────────────┘
*/

import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../hooks/api';
import { useWebSocket } from '../hooks/useWebSocket';
import type { Iteration, PipelineStatus, Settings, ProjectInfo, FileNode } from '../types';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDuration(startIso: string, endIso: string | null): string {
  if (!endIso) return 'In progress';
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms <= 0) return '—';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

function fmtTs(iso: string): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const STATUS_BADGE: Record<string, string> = {
  completed:   'text-green-300 bg-green-500/10 border-green-500/30',
  in_progress: 'text-indigo-300 bg-indigo-500/10 border-indigo-500/30',
  failed:      'text-red-300 bg-red-500/10 border-red-500/30',
  pending:     'text-slate-400 bg-slate-500/10 border-slate-500/20',
};

const STATUS_LABEL: Record<string, string> = {
  completed: 'Completed', in_progress: 'In Progress', failed: 'Failed', pending: 'Pending',
};

// ─── Iteration card ───────────────────────────────────────────────────────────

function IterationCard({ iter }: { iter: Iteration }) {
  const [expanded, setExpanded] = useState(false);
  const badge = STATUS_BADGE[iter.status] ?? STATUS_BADGE.pending;
  const label = STATUS_LABEL[iter.status] ?? iter.status;
  const circleClass =
    iter.status === 'completed'   ? 'bg-green-500/20 text-green-400' :
    iter.status === 'failed'      ? 'bg-red-500/20 text-red-400' :
    iter.status === 'in_progress' ? 'bg-indigo-500/20 text-indigo-400' :
                                    'bg-slate-700 text-slate-400';

  const stages = ['Prompt generated', 'Code generated'];
  if (iter.review_json_path) stages.push('Review completed');
  if (iter.status === 'completed') stages.push('Accepted');

  return (
    <div className="glass-card">
      <button
        className="w-full flex items-center justify-between gap-3"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${circleClass}`}>
            {iter.iteration_number}
          </div>
          <div className="text-left min-w-0">
            <p className="text-sm font-semibold text-white">Iteration {iter.iteration_number}</p>
            <p className="text-[11px] text-slate-500">{stages.join(' → ')}</p>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {iter.token_usage > 0 && (
            <span className="text-[11px] text-slate-500 font-mono">{iter.token_usage.toLocaleString()} tok</span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${badge}`}>{label}</span>
          <span className="text-slate-600 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
            transition={{ duration: 0.2 }}
          >
            <div className="mt-4 pt-4 border-t border-[var(--border-glass)] grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
              <div>
                <p className="text-slate-600 mb-0.5">Started</p>
                <p className="text-slate-300">{fmtTs(iter.started_at)}</p>
              </div>
              <div>
                <p className="text-slate-600 mb-0.5">Duration</p>
                <p className="text-slate-300">{fmtDuration(iter.started_at, iter.completed_at)}</p>
              </div>
              {iter.prompt_path && (
                <div className="col-span-2">
                  <p className="text-slate-600 mb-0.5">Prompt file</p>
                  <p className="text-slate-400 font-mono text-[11px] break-all">{iter.prompt_path}</p>
                </div>
              )}
              {iter.review_json_path && (
                <div className="col-span-2">
                  <p className="text-slate-600 mb-0.5">Review JSON</p>
                  <p className="text-slate-400 font-mono text-[11px] break-all">{iter.review_json_path}</p>
                </div>
              )}
              {iter.summary_path && (
                <div className="col-span-2">
                  <p className="text-slate-600 mb-0.5">Summary</p>
                  <p className="text-slate-400 font-mono text-[11px] break-all">{iter.summary_path}</p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Iterations tab ───────────────────────────────────────────────────────────

function IterationsTab({ iterations }: { iterations: Iteration[] }) {
  if (iterations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-600">
        <p className="text-4xl mb-3">◼︎</p>
        <p className="text-sm">No iterations yet — start the pipeline to begin.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {[...iterations].reverse().map((iter) => (
        <IterationCard key={iter.iteration_number} iter={iter} />
      ))}
    </div>
  );
}

// ─── Dependencies tab ─────────────────────────────────────────────────────────

function findDepFile(nodes: FileNode[]): string | null {
  const targets = ['package.json', 'requirements.txt', 'pyproject.toml', 'Pipfile'];
  for (const node of nodes) {
    if (!node.is_dir && targets.includes(node.name)) return node.path;
    if (node.is_dir && node.children) {
      const f = findDepFile(node.children);
      if (f) return f;
    }
  }
  return null;
}

function DepsTab({ files }: { files: FileNode[] }) {
  const [content, setContent] = useState<string | null>(null);
  const [depFile, setDepFile] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const path = findDepFile(files);
    setDepFile(path);
    if (!path) return;
    setLoading(true);
    api.getFileContent(path)
      .then((fc) => setContent(fc.content))
      .catch(() => setContent(null))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files.length]);

  if (loading) return <p className="text-sm text-slate-500 animate-pulse py-6">Loading dependencies…</p>;

  if (!depFile || content === null) {
    return (
      <div className="py-8 text-center text-slate-600 text-sm">
        No dependency file found (<code className="font-mono text-xs">package.json</code>,{' '}
        <code className="font-mono text-xs">requirements.txt</code>,{' '}
        <code className="font-mono text-xs">pyproject.toml</code>).
        <br />
        <span className="text-[11px] mt-1 block">Start the pipeline to generate project files.</span>
      </div>
    );
  }

  let deps: { name: string; version: string }[] = [];
  try {
    const fileName = depFile.split('/').pop() ?? '';
    if (fileName === 'package.json') {
      const pkg = JSON.parse(content) as {
        dependencies?: Record<string, string>;
        devDependencies?: Record<string, string>;
      };
      deps = Object.entries({ ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) })
        .map(([n, v]) => ({ name: n, version: v }));
    } else if (fileName === 'requirements.txt') {
      deps = content
        .split('\n')
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith('#'))
        .map((l) => {
          const m = l.match(/^([A-Za-z0-9_\-.]+)([=><~!].+)?$/);
          return { name: m?.[1] ?? l, version: m?.[2]?.trim() ?? '*' };
        });
    }
  } catch { /* keep deps empty */ }

  return (
    <div>
      <p className="text-[11px] text-slate-500 mb-3 font-mono">{depFile}</p>
      {deps.length === 0 ? (
        <pre className="text-xs text-slate-400 bg-black/30 rounded-lg p-4 overflow-x-auto">{content.slice(0, 2000)}</pre>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {deps.map((d) => (
            <div key={d.name} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-slate-800/50 border border-slate-700/50 text-xs">
              <span className="text-slate-200 font-medium truncate">{d.name}</span>
              <span className="text-slate-500 font-mono shrink-0">{d.version}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export default function ProjectsView() {
  const [iterations, setIterations]   = useState<Iteration[]>([]);
  const [pipStatus, setPipStatus]     = useState<PipelineStatus | null>(null);
  const [settings, setSettings]       = useState<Settings | null>(null);
  const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
  const [files, setFiles]             = useState<FileNode[]>([]);
  const [tab, setTab]                 = useState<'iterations' | 'dependencies'>('iterations');

  const { messages } = useWebSocket();
  const prevMsgLen = useRef(0);

  const loadDynamic = async () => {
    const [iters, ps] = await Promise.allSettled([
      api.getIterations(),
      api.getPipelineStatus(),
    ]);
    if (iters.status === 'fulfilled') setIterations(iters.value.iterations);
    if (ps.status   === 'fulfilled') setPipStatus(ps.value);
  };

  // Clear stale data immediately when the pipeline is reset, then refetch
  useEffect(() => {
    const newMsgs = messages.slice(prevMsgLen.current);
    prevMsgLen.current = messages.length;
    const hasReset = newMsgs.some((m) => m.channel === 'pipeline' && m.event === 'reset');
    if (hasReset) {
      setIterations([]);
      setPipStatus(null);
      setFiles([]);
      setProjectInfo(null);
      // Refetch everything (including project info with new root_path)
      loadDynamic();
      Promise.allSettled([
        api.getSettings(),
        api.getProjectInfo(),
        api.getProjectFiles(),
      ]).then(([s, info, f]) => {
        if (s.status    === 'fulfilled') setSettings(s.value);
        if (info.status === 'fulfilled') setProjectInfo(info.value);
        if (f.status    === 'fulfilled') setFiles(f.value);
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  useEffect(() => {
    const loadStatic = async () => {
      const [s, info, f] = await Promise.allSettled([
        api.getSettings(),
        api.getProjectInfo(),
        api.getProjectFiles(),
      ]);
      if (s.status    === 'fulfilled') setSettings(s.value);
      if (info.status === 'fulfilled') setProjectInfo(info.value);
      if (f.status    === 'fulfilled') setFiles(f.value);
    };
    loadDynamic(); loadStatic();
    const id = setInterval(loadDynamic, 4000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const projectName = projectInfo?.name     || settings?.project?.name     || 'Untitled Project';
  const language    = projectInfo?.language  || settings?.project?.language  || '';
  const rootPath    = projectInfo?.root_path || settings?.project?.root_path || '';
  const ghOwner     = settings?.github?.owner;
  const ghRepo      = settings?.project?.repo_name || settings?.github?.repo;
  const ghUrl       = ghOwner && ghRepo ? `https://github.com/${ghOwner}/${ghRepo}` : null;

  const pipelineSt  = pipStatus?.pipeline_status ?? 'IDLE';
  const pipelineBadge =
    pipelineSt === 'PIPELINE_COMPLETE' ? 'text-green-400 bg-green-500/15 border-green-500/30' :
    pipelineSt === 'FAILED'            ? 'text-red-400 bg-red-500/15 border-red-500/30' :
    pipelineSt === 'IDLE'              ? 'text-slate-400 bg-slate-700/50 border-slate-600/30' :
    pipelineSt.startsWith('HITL')      ? 'text-amber-400 bg-amber-500/15 border-amber-500/30' :
                                         'text-indigo-400 bg-indigo-500/15 border-indigo-500/30';

  return (
    <div className="flex flex-col h-full min-h-0 gap-5">

      {/* ── Project header ────────────────────────────────────────────────── */}
      <div className="glass-card shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-white truncate">{projectName}</h2>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-xs text-slate-500">
              {language  && <span>{language}</span>}
              {rootPath  && <span className="font-mono truncate max-w-xs">{rootPath}</span>}
              {projectInfo && <span>{projectInfo.file_count} files</span>}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className={`text-[11px] px-2 py-0.5 rounded border font-medium ${pipelineBadge}`}>
              {pipelineSt}
            </span>
            {ghUrl && (
              <a
                href={ghUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-purple-400 hover:text-purple-300 flex items-center gap-1"
              >
                <span>↗</span> {ghOwner}/{ghRepo}
              </a>
            )}
          </div>
        </div>
        {pipStatus && pipStatus.current_iteration > 0 && (
          <div className="flex items-center gap-4 mt-3 pt-3 border-t border-[var(--border-glass)] text-xs text-slate-500">
            <span>Iteration <span className="text-white font-semibold">{pipStatus.current_iteration}</span></span>
            <span>{iterations.filter((i) => i.status === 'completed').length} completed</span>
            {iterations.some((i) => i.status === 'failed') && (
              <span className="text-red-400">{iterations.filter((i) => i.status === 'failed').length} failed</span>
            )}
          </div>
        )}
      </div>

      {/* ── Tabs ──────────────────────────────────────────────────────────── */}
      <div className="flex gap-1 shrink-0">
        {(['iterations', 'dependencies'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              tab === t
                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {t === 'iterations' ? `Iterations (${iterations.length})` : 'Dependencies'}
          </button>
        ))}
      </div>

      {/* ── Tab content ───────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {tab === 'iterations'   && <IterationsTab iterations={iterations} />}
        {tab === 'dependencies' && <DepsTab files={files} />}
      </div>

    </div>
  );
}
