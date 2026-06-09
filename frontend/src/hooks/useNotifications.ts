/* useNotifications — derives a notification stream from WebSocket BusMessages.
   Each notification has a type, color, and content derived from event payloads.
*/

import { useState, useCallback, useEffect, useRef } from 'react';
import type { BusMessage } from '../types';
import { getAccent } from '../agentAccents';

export type NotifLevel = 'info' | 'success' | 'warning' | 'error' | 'hitl';

export interface Notification {
  id: string;
  level: NotifLevel;
  title: string;
  body: string;
  /** Hex color for the left accent strip */
  accentHex: string;
  timestamp: Date;
  dismissed: boolean;
}

let _seq = 0;
const nextId = () => `notif-${Date.now()}-${++_seq}`;

// ─── Classify a BusMessage into a notification ───────────────────────────────

function classify(msg: BusMessage): Notification | null {
  const p = msg.payload;
  const event = String(p?.event ?? p?.type ?? '').toLowerCase();
  const status = String(p?.status ?? '').toLowerCase();
  const sender = msg.sender ?? '';

  // HITL gates
  const isHITL = msg.channel === 'PIPELINE_EVENTS' &&
    (event.startsWith('hitl') || String(p?.pipeline_status ?? '').includes('HITL'));
  if (isHITL) {
    return {
      id: nextId(), level: 'hitl',
      title: '🚦 Action Required',
      body: String(p?.message ?? p?.gate ?? `HITL gate: ${event}`),
      accentHex: '#f59e0b',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // Pipeline failure
  if (event === 'pipeline_failed' || status === 'failed') {
    return {
      id: nextId(), level: 'error',
      title: '✕ Pipeline Failed',
      body: String(p?.reason ?? p?.error_message ?? p?.message ?? `Failed at ${sender}`),
      accentHex: '#ef4444',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // Module complete
  if (event === 'module_complete' || event === 'module_completed') {
    const modId = msg.module_id ?? String(p?.module_id ?? '');
    return {
      id: nextId(), level: 'success',
      title: `✓ Module Complete`,
      body: modId ? `${modId} finished successfully` : 'A module completed',
      accentHex: '#22c55e',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // Pipeline complete
  if (event === 'pipeline_complete' || event === 'pipeline_completed') {
    return {
      id: nextId(), level: 'success',
      title: '🎉 Pipeline Complete',
      body: String(p?.message ?? 'All modules finished'),
      accentHex: '#22c55e',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // Agent started / finished (derive accent from sender)
  if (event === 'agent_started' || event === 'stage_started') {
    const accent = getAccent(sender) ?? getAccent(String(p?.agent ?? ''));
    if (accent) {
      return {
        id: nextId(), level: 'info',
        title: `${accent.stationIcon} ${accent.label} started`,
        body: String(p?.message ?? `Processing ${msg.module_id ?? ''}`),
        accentHex: accent.hex,
        timestamp: new Date(msg.timestamp),
        dismissed: false,
      };
    }
  }

  // Agent errored
  if (event === 'agent_error' || event === 'stage_error') {
    const accent = getAccent(sender) ?? null;
    return {
      id: nextId(), level: 'error',
      title: `${accent?.stationIcon ?? '✕'} Agent Error`,
      body: String(p?.error ?? p?.message ?? sender),
      accentHex: '#ef4444',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // Git commit
  if (event === 'git_commit') {
    const sha = String(p?.sha ?? p?.commit_sha ?? '').slice(0, 7);
    return {
      id: nextId(), level: 'success',
      title: '⎇ Git Commit',
      body: `${msg.module_id ?? ''} committed${sha ? ` (${sha})` : ''}`,
      accentHex: '#34d399',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  // PR created
  if (event === 'pr_created' || p?.pr_url) {
    return {
      id: nextId(), level: 'success',
      title: '↗ Pull Request Created',
      body: String(p?.pr_url ?? p?.message ?? ''),
      accentHex: '#34d399',
      timestamp: new Date(msg.timestamp),
      dismissed: false,
    };
  }

  return null;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

const MAX_NOTIFICATIONS = 50;

export function useNotifications(messages: BusMessage[]) {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (messages.length === 0) return;
    const latest = messages[messages.length - 1];
    // dedupe by channel+timestamp+event
    const key = `${latest.channel}:${latest.timestamp}:${latest.payload?.event ?? ''}`;
    if (seenRef.current.has(key)) return;
    seenRef.current.add(key);

    const notif = classify(latest);
    if (!notif) return;

    setNotifications((prev) => {
      const next = [notif, ...prev].slice(0, MAX_NOTIFICATIONS);
      return next;
    });
  }, [messages]);

  const dismiss = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const clearAll = useCallback(() => setNotifications([]), []);

  const undismissed = notifications.filter((n) => !n.dismissed);

  return { notifications: undismissed, dismiss, clearAll };
}
