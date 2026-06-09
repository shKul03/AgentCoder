/* NotificationTray — Phase 11
   Persistent top-right notification panel driven by WebSocket events.
   Features:
   - Bell icon with unread count badge (anchored to top-right of viewport)
   - Dropdown panel listing active notifications
   - Per-notification: color-coded left strip, level icon, title, body, timestamp, dismiss ✕
   - "Clear all" action
   - HITL notifications amber-highlighted with "Action Required" label
*/

import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import type { Notification, NotifLevel } from '../hooks/useNotifications';

// ─── Level styles ─────────────────────────────────────────────────────────────

const LEVEL_ICON: Record<NotifLevel, string> = {
  info:    '◈',
  success: '✓',
  warning: '⚠',
  error:   '✕',
  hitl:    '🚦',
};

const LEVEL_BG: Record<NotifLevel, string> = {
  info:    'bg-[var(--bg-card)]',
  success: 'bg-green-500/5',
  warning: 'bg-amber-500/5',
  error:   'bg-red-500/5',
  hitl:    'bg-amber-500/8',
};

const LEVEL_BORDER: Record<NotifLevel, string> = {
  info:    'border-[var(--border-glass)]',
  success: 'border-green-500/20',
  warning: 'border-amber-500/25',
  error:   'border-red-500/25',
  hitl:    'border-amber-500/50',
};

const LEVEL_TEXT: Record<NotifLevel, string> = {
  info:    'text-slate-400',
  success: 'text-green-400',
  warning: 'text-amber-400',
  error:   'text-red-400',
  hitl:    'text-amber-300',
};

function fmtAge(ts: Date): string {
  const s = Math.floor((Date.now() - ts.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

// ─── Tray item ────────────────────────────────────────────────────────────────

function NotifItem({ notif, onDismiss }: { notif: Notification; onDismiss: () => void }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 16, height: 0 }}
      animate={{ opacity: 1, x: 0, height: 'auto' }}
      exit={{ opacity: 0, x: 16, height: 0 }}
      transition={{ duration: 0.18 }}
      className={`relative flex gap-0 rounded-lg border overflow-hidden ${LEVEL_BG[notif.level]} ${LEVEL_BORDER[notif.level]} ${notif.level === 'hitl' ? 'ring-1 ring-amber-500/30' : ''}`}
    >
      {/* Color strip */}
      <div
        className="w-1 shrink-0"
        style={{ backgroundColor: notif.accentHex }}
      />

      <div className="flex-1 px-3 py-2.5 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={`text-xs shrink-0 ${LEVEL_TEXT[notif.level]}`}>
              {LEVEL_ICON[notif.level]}
            </span>
            <p className={`text-xs font-semibold truncate ${notif.level === 'hitl' ? 'text-amber-200' : 'text-white'}`}>
              {notif.title}
            </p>
          </div>
          <button
            onClick={onDismiss}
            className="shrink-0 text-slate-600 hover:text-slate-300 text-xs transition-colors leading-none pt-0.5"
          >
            ✕
          </button>
        </div>
        {notif.body && (
          <p className="text-[11px] text-slate-500 mt-0.5 leading-snug line-clamp-2">
            {notif.body}
          </p>
        )}
        <p className="text-[10px] text-slate-700 mt-1">{fmtAge(notif.timestamp)}</p>
      </div>
    </motion.div>
  );
}

// ─── Main tray ────────────────────────────────────────────────────────────────

interface Props {
  notifications: Notification[];
  onDismiss: (id: string) => void;
  onClearAll: () => void;
}

export default function NotificationTray({ notifications, onDismiss, onClearAll }: Props) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const hitlCount = notifications.filter((n) => n.level === 'hitl').length;
  const total = notifications.length;

  // Auto-open when a HITL notification arrives
  useEffect(() => {
    if (hitlCount > 0) setOpen(true);
  }, [hitlCount]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={panelRef} className="relative z-[var(--notif-z)]">
      {/* Bell button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className={`relative flex items-center justify-center w-8 h-8 rounded-lg border transition-colors ${
          open
            ? 'border-indigo-500/50 bg-indigo-500/15 text-white'
            : hitlCount > 0
            ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
            : 'border-[var(--border-glass)] hover:border-[var(--border-glass-hover)] text-[var(--text-secondary)] hover:text-white'
        }`}
        title="Notifications"
      >
        <span className="text-sm leading-none">{hitlCount > 0 ? '🚦' : '🔔'}</span>
        {total > 0 && (
          <span
            className={`absolute -top-1 -right-1 min-w-[16px] h-4 rounded-full text-[9px] font-bold flex items-center justify-center px-1 ${
              hitlCount > 0
                ? 'bg-amber-500 text-black'
                : 'bg-indigo-500 text-white'
            }`}
          >
            {total > 99 ? '99+' : total}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.96 }}
            transition={{ duration: 0.14 }}
            className="absolute right-0 top-10 w-80 max-h-[480px] flex flex-col rounded-xl border border-[var(--border-glass)] bg-[var(--bg-surface)] shadow-2xl overflow-hidden"
            style={{ backdropFilter: 'blur(18px)' }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-[var(--border-glass)] shrink-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-white">Notifications</span>
                {hitlCount > 0 && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold">
                    {hitlCount} HITL
                  </span>
                )}
              </div>
              {total > 0 ? (
                <button
                  onClick={onClearAll}
                  className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors"
                >
                  Clear all
                </button>
              ) : null}
            </div>

            {/* List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
              <AnimatePresence initial={false}>
                {total === 0 ? (
                  <div className="py-8 text-center text-xs text-slate-600">
                    No notifications
                  </div>
                ) : (
                  notifications.map((n) => (
                    <NotifItem key={n.id} notif={n} onDismiss={() => onDismiss(n.id)} />
                  ))
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
