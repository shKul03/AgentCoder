/* usePipelineFlow — real-time pipeline state for WorkflowView */

import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useWebSocket } from './useWebSocket';

// ─── Types ────────────────────────────────────────────────────────────────────

export type StationId =
  | 'idle'
  | 'orchestrator'
  | 'prompt_generator'
  | 'code_generator'
  | 'code_reviewer';

export interface FeedEvent {
  id: string;
  icon: string;
  text: string;
  timestamp: string;
}

// ─── Mapping helpers ──────────────────────────────────────────────────────────

function toStation(status: string): StationId {
  switch (status) {
    case 'IDLE':                 return 'orchestrator';
    case 'LOADING_REQUIREMENTS':
    case 'ANALYSING_DEPENDENCIES':
    case 'QUEUE_READY':
    case 'STORY_COMPLETE':       return 'orchestrator';
    case 'PROMPT_GENERATION':
    case 'HITL_PROMPT_REVIEW':
    case 'STORY_PROMPT_GENERATION': return 'prompt_generator';
    case 'CODE_GENERATION':
    case 'STORY_CODE_GENERATION':
    case 'CODE_GEN_FAILED':      return 'code_generator';
    case 'CODE_REVIEW':
    case 'STORY_CODE_REVIEW':
    case 'HITL_REVIEW_DECISION':
    case 'PIPELINE_COMPLETE':    return 'code_reviewer';
    default:                     return 'idle';
  }
}

function toStatusText(status: string): string {
  switch (status) {
    case 'IDLE':
      return 'Pipeline is idle — press Start to begin.';
    case 'LOADING_REQUIREMENTS':
      return 'Loading requirements from source…';
    case 'ANALYSING_DEPENDENCIES':
      return 'Analysing story dependencies and building the queue…';
    case 'QUEUE_READY':
      return 'Story queue ready — picking next story to process.';
    case 'PROMPT_GENERATION':
    case 'STORY_PROMPT_GENERATION':
      return 'The Writer is generating a prompt for the current story…';
    case 'HITL_PROMPT_REVIEW':
      return 'Waiting for your review of the prompt. Check the Prompt tab to approve or edit.';
    case 'CODE_GENERATION':
    case 'STORY_CODE_GENERATION':
      return 'The Builder is generating code from the approved prompt…';
    case 'CODE_GEN_FAILED':
      return 'Code generation failed — click Retry to try again.';
    case 'CODE_REVIEW':
    case 'STORY_CODE_REVIEW':
      return 'The Inspector is reviewing the generated code…';
    case 'STORY_COMPLETE':
      return 'Story complete — moving to next story in the queue.';
    case 'HITL_REVIEW_DECISION':
      return 'Waiting for your decision on the code review. Check the Review tab.';
    case 'PIPELINE_COMPLETE':
      return 'Pipeline complete! All work has been committed.';
    case 'FAILED':
      return 'Pipeline encountered an error and stopped.';
    default:
      return status;
  }
}

// ─── Feed event helpers ───────────────────────────────────────────────────────

function channelIcon(event: string): string {
  if (event.includes('prompt'))                           return '✍';
  if (event.includes('code_gen'))                        return '⚒';
  if (event.includes('review'))                          return '🔍';
  if (event.includes('hitl') || event.includes('gate'))  return '⏸';
  if (event.includes('git') || event.includes('push'))   return '⬆';
  if (event.includes('complete'))                        return '🎉';
  if (event.includes('error') || event.includes('fail')) return '⚠';
  return '●';
}

function toEventText(sender: string, event: string, msg?: Record<string, unknown>): string {
  if (event === 'loading_requirements')      return `${sender}: Loading requirements…`;
  if (event === 'prompt_generation_started')  return `${sender}: Generating prompt…`;
  if (event === 'prompt_generation_complete') return `${sender}: Prompt ready for review`;
  if (event === 'code_generation_started')    return `${sender}: Code generation started`;
  if (event === 'code_generation_complete')   return `${sender}: Code generation complete`;
  if (event === 'code_review_started')        return `${sender}: Reviewing code…`;
  if (event === 'code_review_complete')       return `${sender}: Code review finished`;
  if (event === 'hitl_gate')                  return `${sender}: Awaiting your approval`;
  if (event === 'pipeline_complete')          return 'Pipeline completed successfully 🎉';
  if (event === 'failed')                     return `${sender}: Pipeline failed`;
  if (event === 'paused')                     return `${sender}: Pipeline paused`;
  if (event === 'error')                      return `${sender}: Error occurred`;
  if (event === 'stopped' || event === 'reset') return `${sender}: Pipeline stopped`;
  if (event === 'pr_creation_failed')         return `${sender}: Pull request creation failed — click Retry`;
  if (event === 'pr_retry_progress')          return `${sender}: ${(msg?.message as string) || 'PR retry in progress…'}`;
  if (event === 'pr_retry_success')           return `${sender}: Pull request created successfully`;
  if (event === 'prompt_gen_failed')          return `${sender}: Prompt generation failed — click Retry`;
  if (event === 'code_gen_failed')            return `${sender}: Code generation failed — click Retry`;
  if (event === 'code_review_failed')         return `${sender}: Code review failed — click Retry`;
  return `${sender}: ${event}`;
}

// Only these events are surfaced in the event feed — everything else (codex_stdout,
// reviewer_stdout, terminal:*, state_changed polling noise, etc.) is discarded.
const FEED_EVENTS = new Set([
  'loading_requirements',
  'prompt_generation_started',
  'prompt_generation_complete',
  'code_generation_started',
  'code_generation_complete',
  'code_review_started',
  'code_review_complete',
  'hitl_gate',
  'pipeline_complete',
  'failed',
  'paused',
  'stopped',
  'reset',
  'error',
  'pr_creation_failed',
  'pr_retry_progress',
  'pr_retry_success',
  'prompt_gen_failed',
  'code_gen_failed',
  'code_review_failed',
]);

const TRANSFERRING_STATUSES = new Set([
  'LOADING_REQUIREMENTS',
  'ANALYSING_DEPENDENCIES',
  'PROMPT_GENERATION',
  'STORY_PROMPT_GENERATION',
  'CODE_GENERATION',
  'STORY_CODE_GENERATION',
  'CODE_REVIEW',
  'STORY_CODE_REVIEW',
]);

export function isTransferringStatus(status: string): boolean {
  return TRANSFERRING_STATUSES.has(status);
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function usePipelineFlow() {
  const [loading, setLoading] = useState(true);
  const [pipelineStatus, setPipelineStatus] = useState('IDLE');
  const [currentIteration, setCurrentIteration] = useState(0);
  const [activeStation, setActiveStation] = useState<StationId>('idle');
  const [prevStation, setPrevStation] = useState<StationId | null>(null);
  const [events, setEvents] = useState<FeedEvent[]>([]);

  const activeStationRef = useRef<StationId>('idle');
  const seenIdsRef = useRef(new Set<string>());

  // Poll pipeline status every 3 s
  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const ps = await api.getPipelineStatus();
        if (!active) return;

        const next = toStation(ps.pipeline_status);
        if (next !== activeStationRef.current) {
          setPrevStation(activeStationRef.current);
          activeStationRef.current = next;
          setActiveStation(next);
        }
        setPipelineStatus(ps.pipeline_status);
        setCurrentIteration(ps.current_iteration);
      } catch {
        // silently ignore transient errors
      } finally {
        if (active) setLoading(false);
      }
    };

    load();
    const id = setInterval(load, 3000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  // Convert incoming WebSocket messages to feed events and live status updates
  const { messages } = useWebSocket();
  const prevMsgLenRef = useRef(0);

  useEffect(() => {
    if (messages.length === 0) return;

    // useWebSocket caps messages at 500 (ring buffer). When older items are
    // evicted from the front, prevMsgLenRef can exceed messages.length — fall
    // back to processing just the last message. seenIdsRef prevents duplicates.
    const start =
      prevMsgLenRef.current >= messages.length
        ? Math.max(messages.length - 1, 0)
        : prevMsgLenRef.current;
    prevMsgLenRef.current = messages.length;

    const newMessages = messages.slice(start);
    if (newMessages.length === 0) return;

    const newEvents: FeedEvent[] = [];
    for (const msg of newMessages) {
      // Live status update — no need to wait for the next poll
      if (msg.pipeline_status) {
        const next = toStation(msg.pipeline_status);
        if (next !== activeStationRef.current) {
          setPrevStation(activeStationRef.current);
          activeStationRef.current = next;
          setActiveStation(next);
        }
        setPipelineStatus(msg.pipeline_status);
        if (msg.current_iteration !== undefined) {
          setCurrentIteration(msg.current_iteration as number);
        }
      }

      // Build dedup key using event + timestamp (both guaranteed by orchestrator _emit)
      const eventName = msg.event || msg.channel || 'unknown';
      const key = `${msg.timestamp ?? Date.now()}-${eventName}-${msg.sender}`;
      if (seenIdsRef.current.has(key)) continue;
      seenIdsRef.current.add(key);

      // Only surface meaningful pipeline events — skip noisy stdout/terminal events
      if (!eventName || !FEED_EVENTS.has(eventName)) continue;

      newEvents.push({
        id: key,
        icon: channelIcon(eventName),
        text: toEventText(msg.sender || 'orchestrator', eventName, msg as Record<string, unknown>),
        timestamp: msg.timestamp ?? new Date().toISOString(),
      });
    }

    if (newEvents.length > 0) {
      setEvents((prev) => [...newEvents.reverse(), ...prev].slice(0, 50));
    }
  }, [messages]);

  return {
    loading,
    pipelineStatus,
    activeStation,
    prevStation,
    isHitlGate: pipelineStatus.startsWith('HITL'),
    isTransferring: isTransferringStatus(pipelineStatus),
    currentIteration,
    statusText: toStatusText(pipelineStatus),
    events,
  };
}


