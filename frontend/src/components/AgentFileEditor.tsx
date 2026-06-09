/* Full-screen Markdown editor with live preview.
   Used by AgentDetail when the user clicks "Edit" on any agent file tab.
   Phase 6 — Agents Management Page.
*/

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Editor from '@monaco-editor/react';

interface Props {
  fileName: string;
  agentName: string;
  initialContent: string;
  onSave: (content: string) => Promise<void>;
  onDiscard: () => void;
}

/** Very simple Markdown → plain HTML renderer (no external dep). */
function renderMarkdown(md: string): string {
  return md
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/^#{3} (.+)$/gm, '<h3 class="text-base font-semibold mt-4 mb-1 text-indigo-300">$1</h3>')
    .replace(/^#{2} (.+)$/gm, '<h2 class="text-lg font-bold mt-5 mb-2 text-indigo-200">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold mt-6 mb-2 text-white">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-white">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em class="italic text-slate-300">$1</em>')
    .replace(/`(.+?)`/g, '<code class="bg-black/30 rounded px-1 text-emerald-300 text-xs font-mono">$1</code>')
    .replace(/^[-*] (.+)$/gm, '<li class="ml-4 list-disc text-slate-300">$1</li>')
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-slate-300">$1</li>')
    .replace(/^---$/gm, '<hr class="border-slate-700 my-3" />')
    .replace(/\n{2,}/g, '</p><p class="mb-2 text-slate-300">')
    .replace(/^(?!<[h\dl])(.+)$/gm, (line) => line ? line : '');
}

export default function AgentFileEditor({ fileName, agentName, initialContent, onSave, onDiscard }: Props) {
  const [content, setContent] = useState(initialContent);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState('');

  const handleChange = useCallback((val: string | undefined) => {
    setContent(val ?? '');
    setDirty(true);
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(content);
      setDirty(false);
      setToast('Saved!');
      setTimeout(() => setToast(''), 2000);
    } catch {
      setToast('Save failed');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  };

  const handleDiscard = () => {
    if (dirty && !confirm('Discard unsaved changes?')) return;
    onDiscard();
  };

  return (
    <motion.div
      className="fixed inset-0 z-50 flex flex-col bg-[var(--bg-primary)]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border-glass)] shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--text-secondary)]">{agentName}</span>
          <span className="text-[var(--text-secondary)]">/</span>
          <span className="font-semibold text-white capitalize">{fileName}.md</span>
          {dirty && <span className="w-2 h-2 rounded-full bg-amber-400" title="Unsaved changes" />}
        </div>
        <div className="flex items-center gap-2">
          <AnimatePresence>
            {toast && (
              <motion.span
                className={`text-xs px-2 py-1 rounded ${toast.includes('fail') ? 'text-red-400' : 'text-green-400'}`}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                {toast}
              </motion.span>
            )}
          </AnimatePresence>
          <button
            onClick={handleDiscard}
            className="px-3 py-1.5 text-sm rounded-lg border border-[var(--border-glass)] text-[var(--text-secondary)] hover:text-white hover:border-white/30 transition-colors"
          >
            Discard
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !dirty}
            className="px-4 py-1.5 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* Split: editor left, preview right */}
      <div className="flex flex-1 overflow-hidden">
        <div className="w-1/2 border-r border-[var(--border-glass)]">
          <Editor
            height="100%"
            defaultLanguage="markdown"
            value={content}
            onChange={handleChange}
            theme="vs-dark"
            options={{
              fontSize: 13,
              lineHeight: 22,
              wordWrap: 'on',
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              padding: { top: 16, bottom: 16 },
              renderLineHighlight: 'none',
            }}
          />
        </div>
        <div className="w-1/2 overflow-y-auto p-6">
          <div
            className="prose prose-invert max-w-none text-sm leading-relaxed"
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: `<p class="mb-2 text-slate-300">${renderMarkdown(content)}</p>` }}
          />
        </div>
      </div>
    </motion.div>
  );
}
