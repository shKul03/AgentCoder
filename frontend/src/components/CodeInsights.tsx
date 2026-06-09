/* Code Insights — generated file browser, validation results, review feedback */

import { useEffect, useState, useRef } from 'react';
import Editor from '@monaco-editor/react';
import { api } from '../hooks/api';
import { useWebSocket } from '../hooks/useWebSocket';
import type { BusMessage, FileNode, ProjectInfo, FileContent } from '../types';

const severityBadge: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-orange-600 text-white',
  medium: 'bg-yellow-600 text-black',
  low: 'bg-slate-600 text-white',
};

// Map file extension to Monaco language
function langFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    py: 'python', js: 'javascript', ts: 'typescript', tsx: 'typescriptreact',
    json: 'json', yaml: 'yaml', yml: 'yaml', md: 'markdown', html: 'html',
    css: 'css', sql: 'sql', sh: 'shell', txt: 'plaintext', toml: 'toml',
  };
  return map[ext] || 'plaintext';
}

export default function CodeInsights() {
  const [reviews, setReviews] = useState<BusMessage[]>([]);
  const [validations, setValidations] = useState<BusMessage[]>([]);
  const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileContent | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [openStatus, setOpenStatus] = useState('');

  const { messages } = useWebSocket();
  const prevMsgLen = useRef(0);

  const loadData = () => {
    api.getBusHistory('review_feedback').then(setReviews).catch(() => {});
    api.getBusHistory('validation_results').then(setValidations).catch(() => {});
    api.getProjectInfo().then(setProjectInfo).catch(() => {});
    api.getProjectFiles().then(setFileTree).catch(() => setFileTree([]));
  };

  // Clear stale data immediately when the pipeline is reset, then refetch fresh
  useEffect(() => {
    const newMsgs = messages.slice(prevMsgLen.current);
    prevMsgLen.current = messages.length;
    const hasReset = newMsgs.some((m) => m.channel === 'pipeline' && m.event === 'reset');
    if (hasReset) {
      setReviews([]);
      setValidations([]);
      setFileTree([]);
      setSelectedFile(null);
      setProjectInfo(null);
      loadData();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  useEffect(() => {
    loadData();
    const id = setInterval(loadData, 5000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleOpenFile = (path: string) => {
    setFileLoading(true);
    api.getFileContent(path)
      .then((f) => { setSelectedFile(f); })
      .catch(() => setSelectedFile(null))
      .finally(() => setFileLoading(false));
  };

  const handleOpenVSCode = () => {
    api.openInVSCode()
      .then((r) => setOpenStatus(r.message))
      .catch(() => setOpenStatus('Failed to open VS Code'));
  };

  const handleOpenFinder = () => {
    api.openInFinder()
      .then((r) => setOpenStatus(r.message))
      .catch(() => setOpenStatus('Failed to open Finder'));
  };

  return (
    <div className="space-y-6">
      {/* Project Info + Actions */}
      <div className="glass-card">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold">Generated Project</h2>
            {projectInfo && (
              <p className="text-xs text-[var(--text-secondary)] mt-1">
                {projectInfo.name} &middot; {projectInfo.root_path}
                {projectInfo.exists && (
                  <span className={projectInfo.file_count === 0 ? ' text-yellow-400' : ''}>
                    {' '}&middot; {projectInfo.file_count} source files
                    {projectInfo.file_count === 0 && ' (no source code generated yet)'}
                  </span>
                )}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleOpenVSCode}
              className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-medium transition-colors flex items-center gap-1.5"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M17.583 2.213l-4.27 3.932L7.96 1.592 2.4 3.79v16.423l5.56 2.195 5.354-4.553 4.27 3.932L21.6 19.6V4.4l-4.017-2.187zm-4.27 6.073l4.27-3.21v13.847l-4.27-3.213V8.286zm-1.5 7.57l-4.27 3.633V4.512l4.27 3.632v7.712z"/>
              </svg>
              Open in VS Code
            </button>
            <button
              onClick={handleOpenFinder}
              className="px-3 py-1.5 rounded-lg bg-slate-600 hover:bg-slate-500 text-xs font-medium transition-colors"
            >
              Open in Finder
            </button>
            <button
              onClick={loadData}
              className="px-3 py-1.5 rounded-lg bg-slate-600 hover:bg-slate-500 text-xs font-medium transition-colors"
            >
              Refresh
            </button>
          </div>
        </div>
        {openStatus && (
          <p className="text-xs text-[var(--text-secondary)] mb-2">{openStatus}</p>
        )}
      </div>

      {/* File browser + viewer */}
      {projectInfo?.exists && fileTree.length > 0 && (
        <div className="glass-card">
          <h2 className="text-sm font-semibold mb-3">Project Files</h2>
          <div className="flex gap-4" style={{ minHeight: '400px' }}>
            {/* Tree panel */}
            <div className="w-64 shrink-0 overflow-auto border-r border-white/10 pr-3 max-h-[60vh]">
              {fileTree.map((node) => (
                <TreeNode
                  key={node.path}
                  node={node}
                  selectedPath={selectedFile?.path || null}
                  onSelect={handleOpenFile}
                />
              ))}
            </div>
            {/* Content panel */}
            <div className="flex-1 min-w-0">
              {fileLoading && (
                <div className="flex items-center justify-center h-40 text-[var(--text-secondary)]">
                  Loading…
                </div>
              )}
              {!fileLoading && selectedFile && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs text-[var(--text-secondary)] font-mono truncate">
                      {selectedFile.path}
                    </p>
                    <span className="text-xs text-[var(--text-secondary)]">
                      {(selectedFile.size / 1024).toFixed(1)} KB
                    </span>
                  </div>
                  <div className="rounded-lg overflow-hidden border border-white/10">
                    <Editor
                      height="50vh"
                      language={langFromPath(selectedFile.path)}
                      value={selectedFile.content}
                      theme="vs-dark"
                      options={{
                        readOnly: true,
                        minimap: { enabled: false },
                        wordWrap: 'on',
                        scrollBeyondLastLine: false,
                        fontSize: 12,
                      }}
                    />
                  </div>
                </div>
              )}
              {!fileLoading && !selectedFile && (
                <div className="flex items-center justify-center h-40 text-[var(--text-secondary)] text-sm">
                  Select a file to view its contents
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* No project yet */}
      {projectInfo && !projectInfo.exists && (
        <div className="glass-card">
          <p className="text-[var(--text-secondary)] text-sm">
            No generated project folder yet. Start the pipeline to auto-create one on your Desktop.
          </p>
        </div>
      )}

      {/* Validation Results */}
      <div className="glass-card">
        <h2 className="text-sm font-semibold mb-3">Validation Results</h2>
        {validations.length === 0 ? (
          <p className="text-[var(--text-secondary)] text-sm">No validation results yet.</p>
        ) : (
          <div className="space-y-2">
            {validations.map((v, i) => (
              <div key={i} className="text-xs border-b border-white/5 pb-2">
                <span className="text-[var(--text-secondary)]">{v.module_id}</span>
                <span className="text-[var(--text-secondary)] ml-2">iter {v.iteration}</span>
                <pre className="text-slate-300 mt-1 overflow-x-auto">
                  {JSON.stringify(v.payload, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Review Feedback */}
      <div className="glass-card">
        <h2 className="text-sm font-semibold mb-3">Review Feedback</h2>
        {reviews.length === 0 ? (
          <p className="text-[var(--text-secondary)] text-sm">No reviews yet.</p>
        ) : (
          <div className="space-y-2">
            {reviews.map((r, i) => {
              const issues = (r.payload?.issues as Array<Record<string, string>>) || [];
              return (
                <div key={i} className="text-xs border-b border-white/5 pb-2">
                  <span className="text-[var(--text-secondary)]">{r.module_id}</span>
                  <span className="text-[var(--text-secondary)] ml-2">
                    iter {r.iteration}
                  </span>
                  <span className="text-[var(--text-secondary)] ml-2">
                    status: {String(r.payload?.overall_status || 'unknown')}
                  </span>
                  {issues.map((issue, j) => (
                    <div key={j} className="pl-4 mt-1 flex items-center gap-2">
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] ${
                          severityBadge[issue.severity] || 'bg-slate-700'
                        }`}
                      >
                        {issue.severity}
                      </span>
                      <span className="text-slate-300">{issue.issue}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---- Tree component ---- */

function TreeNode({
  node,
  selectedPath,
  onSelect,
  depth = 0,
}: {
  node: FileNode;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  depth?: number;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.is_dir) {
    return (
      <div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 w-full text-left py-0.5 hover:bg-white/5 rounded px-1 text-xs text-[var(--text-secondary)]"
          style={{ paddingLeft: `${depth * 12 + 4}px` }}
        >
          <span className="text-[10px]">{expanded ? '▼' : '▶'}</span>
          <span className="text-amber-400">📁</span>
          <span>{node.name}</span>
        </button>
        {expanded && node.children?.map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            selectedPath={selectedPath}
            onSelect={onSelect}
            depth={depth + 1}
          />
        ))}
      </div>
    );
  }

  const isSelected = selectedPath === node.path;
  return (
    <button
      onClick={() => onSelect(node.path)}
      className={`flex items-center gap-1 w-full text-left py-0.5 rounded px-1 text-xs truncate transition-colors ${
        isSelected
          ? 'bg-indigo-600/30 text-white'
          : 'hover:bg-white/5 text-slate-300'
      }`}
      style={{ paddingLeft: `${depth * 12 + 4}px` }}
    >
      <span className="text-[10px] opacity-50">📄</span>
      <span className="truncate">{node.name}</span>
    </button>
  );
}
