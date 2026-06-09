/* PipelineFlowDiagram — Phase 5 v2
   SVG + CSS hybrid topological layout with advanced visual effects.

              ┌──────────────┐
              │ ORCHESTRATOR │
              └──────┬───────┘
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │  PROMPT  │ │  CODE    │ │  CODE    │
  │GENERATOR │ │GENERATOR │ │ REVIEWER │
  └──────────┘ └──────────┘ └──────────┘

  Active node: breathing neon glow + horizontal scanning line.
  Active edges: neon plasma line with energy pulses.
  Idle: subtle glass-morph cards on a hex-grid.
*/

import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { isTransferringStatus } from '../hooks/usePipelineFlow';

// ─────────────────────── inject global keyframes ─────────────────────────────

const STYLE_ID = '__pipeline-diagram-fx';
if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    @keyframes breathe-glow {
      0%, 100% { filter: drop-shadow(0 0 6px var(--glow-color)) drop-shadow(0 0 20px var(--glow-color)); opacity: 1; }
      50%  { filter: drop-shadow(0 0 14px var(--glow-color)) drop-shadow(0 0 38px var(--glow-color)); opacity: 0.92; }
    }
    @keyframes dash-flow {
      to { stroke-dashoffset: -40; }
    }
    @keyframes energy-pulse {
      0%   { offset-distance: 0%;   opacity: 0; }
      10%  { opacity: 1; }
      90%  { opacity: 1; }
      100% { offset-distance: 100%; opacity: 0; }
    }
  `;
  document.head.appendChild(style);
}

// ─── Types ────────────────────────────────────────────────────────────────────

export type NodeId = 'orchestrator' | 'prompt_generator' | 'code_generator' | 'code_reviewer';

interface NodeConfig {
  id: NodeId;
  label: string;
  sublabel: string;
  icon: string;
  hue: number;         // HSL hue for all colour derivations
}

const NODES: Record<NodeId, NodeConfig> = {
  orchestrator:     { id: 'orchestrator',     label: 'Orchestrator',     sublabel: 'Coordinator',  icon: '◎', hue: 240 },
  prompt_generator: { id: 'prompt_generator', label: 'Prompt Generator', sublabel: 'LLM',        icon: '✍', hue: 270 },
  code_generator:   { id: 'code_generator',   label: 'Code Generator',   sublabel: 'CLI Terminal',  icon: '⚒', hue: 210 },
  code_reviewer:    { id: 'code_reviewer',    label: 'Code Reviewer',    sublabel: 'Review Engine', icon: '🔍', hue: 170 },
};

// ─── Status mapping ───────────────────────────────────────────────────────────

type EdgeId = 'orch-prompt' | 'orch-gen' | 'orch-review' | 'prompt-orch' | 'review-orch';

function statusToActiveEdge(status: string): EdgeId | null {
  switch (status) {
    case 'LOADING_REQUIREMENTS':
    case 'PROMPT_GENERATION':
    case 'STORY_PROMPT_GENERATION':   return 'orch-prompt';
    case 'HITL_PROMPT_REVIEW':        return 'prompt-orch';
    case 'CODE_GENERATION':
    case 'STORY_CODE_GENERATION':     return 'orch-gen';
    case 'CODE_REVIEW':
    case 'STORY_CODE_REVIEW':         return 'orch-review';
    case 'HITL_REVIEW_DECISION':      return 'review-orch';
    default: return null;
  }
}

function statusToActiveNode(status: string): NodeId | null {
  switch (status) {
    case 'IDLE':                             return 'orchestrator';
    case 'LOADING_REQUIREMENTS':
    case 'ANALYSING_DEPENDENCIES':
    case 'QUEUE_READY':                      return 'orchestrator';
    case 'PROMPT_GENERATION':
    case 'HITL_PROMPT_REVIEW':
    case 'STORY_PROMPT_GENERATION':          return 'prompt_generator';
    case 'CODE_GENERATION':
    case 'STORY_CODE_GENERATION':
    case 'CODE_GEN_FAILED':                  return 'code_generator';
    case 'CODE_REVIEW':
    case 'STORY_CODE_REVIEW':
    case 'HITL_REVIEW_DECISION':             return 'code_reviewer';
    case 'STORY_COMPLETE':
    case 'PIPELINE_COMPLETE':                return 'orchestrator';
    default:                                 return null;
  }
}

// ─── Colour helpers ───────────────────────────────────────────────────────────

const hsl  = (h: number, s: number, l: number, a = 1) => `hsla(${h}, ${s}%, ${l}%, ${a})`;
const glow = (h: number) => hsl(h, 90, 60, 0.45);

// ─── NeonEdge (SVG line + energy pulses) ──────────────────────────────────────

interface NeonEdgeProps {
  x1: number; y1: number; x2: number; y2: number;
  isActive: boolean;
  isTransferring: boolean;
  hue: number;
  shimmerKey: number;
}

function NeonEdge({ x1, y1, x2, y2, isActive, isTransferring, hue, shimmerKey }: NeonEdgeProps) {
  const dy = y2 - y1;
  const d = `M${x1},${y1} C${x1},${y1 + dy * 0.55} ${x2},${y2 - dy * 0.55} ${x2},${y2}`;

  const neonStroke = hsl(hue, 85, 65, isActive ? 0.9 : 0.08);
  const pulseColor = hsl(hue, 95, 72, 1);
  const showPulse  = isActive && isTransferring;

  return (
    <g>
      <defs>
        <marker
          id={`arr-${x1}-${y1}`}
          markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"
        >
          <path d="M0,0 L0,7 L7,3.5 z" fill={isActive ? hsl(hue, 80, 60, 0.8) : 'rgba(255,255,255,0.1)'} style={{ transition: 'fill .3s' }} />
        </marker>
        {/* Wide soft glow behind neon stroke */}
        <filter id={`edge-glow-${hue}`} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="3.5" />
        </filter>
      </defs>

      {/* Soft glow layer */}
      {isActive && (
        <path d={d} fill="none" stroke={hsl(hue, 90, 60, 0.25)} strokeWidth={6}
          filter={`url(#edge-glow-${hue})`} />
      )}

      {/* Core neon path */}
      <path d={d} fill="none" stroke={neonStroke} strokeWidth={isActive ? 2 : 1}
        strokeDasharray={isActive ? undefined : '3 6'}
        markerEnd={`url(#arr-${x1}-${y1})`}
        style={{ transition: 'stroke .3s, stroke-width .3s' }} />

      {/* Animated flowing dash overlay */}
      {isActive && (
        <path d={d} fill="none" stroke={hsl(hue, 95, 75, 0.6)} strokeWidth={1.5}
          strokeDasharray="12 10"
          style={{ animation: 'dash-flow 1.2s linear infinite' }} />
      )}

      {/* Energy pulse particles — staggered along path */}
      {showPulse && [0, 0.55, 1.1].map((delay, i) => (
        <circle key={`${shimmerKey}-${i}`} r={i === 0 ? 5 : 3.5} fill={pulseColor}
          style={{
            offsetPath: `path('${d}')`,
            animation: `energy-pulse 1.5s ease-in-out ${delay}s infinite`,
            filter: `drop-shadow(0 0 4px ${pulseColor})`,
          } as React.CSSProperties} />
      ))}
    </g>
  );
}

// ─── HoloNodeCard ─────────────────────────────────────────────────────────────

interface NodeCardProps {
  config: NodeConfig;
  isActive: boolean;
  status: string;
  iteration: number;
  x: number; y: number;
  width: number; height: number;
}

function HoloNodeCard({ config, isActive, status, iteration, x, y, width, height }: NodeCardProps) {
  const { hue, icon, label, sublabel } = config;
  const statusLabel = isActive ? status.toLowerCase().replace(/_/g, ' ') : 'standby';

  // Colours derived from hue
  const borderColor  = isActive ? hsl(hue, 80, 55, 0.9) : hsl(hue, 20, 30, 0.25);
  const bgColor      = isActive ? hsl(hue, 35, 10, 0.85) : 'rgba(10, 14, 26, 0.8)';
  const glowColor    = glow(hue);
  const accentText   = isActive ? hsl(hue, 85, 72) : hsl(hue, 15, 45);

  return (
    <foreignObject x={x - width / 2} y={y - height / 2} width={width} height={height}>
      <div
        className="h-full rounded-xl flex flex-col items-center justify-center gap-1.5 p-3 select-none relative overflow-hidden"
        style={{
          border: `1.5px solid ${borderColor}`,
          background: bgColor,
          backdropFilter: 'blur(8px)',
          boxShadow: isActive
            ? `0 0 0 1px ${glowColor}, 0 0 22px ${glowColor}, 0 0 50px ${glow(hue).replace('0.45', '0.12')}`
            : 'none',
          transition: 'border-color .4s, background .4s, box-shadow .6s',
          ['--glow-color' as string]: glowColor,
          animation: isActive ? 'breathe-glow 2.8s ease-in-out infinite' : 'none',
        }}
      >
        {/* Scanning horizontal line on active nodes */}
        {isActive && (
          <motion.div
            className="absolute left-0 w-full pointer-events-none"
            style={{
              height: '1px',
              background: `linear-gradient(90deg, transparent 0%, ${hsl(hue, 90, 70, 0.5)} 40%, ${hsl(hue, 90, 80, 0.9)} 50%, ${hsl(hue, 90, 70, 0.5)} 60%, transparent 100%)`,
            }}
            animate={{ top: ['0%', '100%', '0%'] }}
            transition={{ duration: 3.5, repeat: Infinity, ease: 'easeInOut' }}
          />
        )}

        {/* Icon */}
        <span
          className="text-2xl leading-none z-10"
          style={{ opacity: isActive ? 1 : 0.35, filter: isActive ? `drop-shadow(0 0 6px ${accentText})` : 'none' }}
        >
          {icon}
        </span>

        {/* Label — always fully visible */}
        <p
          className="text-[11px] font-bold text-center leading-tight z-10 truncate w-full"
          style={{ color: isActive ? '#fff' : 'rgb(148,163,184)' }}
          title={label}
        >
          {label}
        </p>

        {/* Sublabel */}
        <p
          className="text-[9px] z-10 truncate w-full text-center"
          style={{ color: isActive ? 'rgb(148,163,184)' : 'rgb(100,116,139)' }}
        >
          {sublabel}
        </p>

        {/* Status badge on active */}
        {isActive && (
          <span
            className="text-[8px] px-1.5 py-0.5 rounded-full font-bold uppercase tracking-widest z-10 border mt-0.5 truncate max-w-full"
            style={{
              color: hsl(hue, 90, 72),
              borderColor: hsl(hue, 60, 45, 0.5),
              background: hsl(hue, 40, 18, 0.6),
            }}
            title={statusLabel}
          >
            {statusLabel}
          </span>
        )}
        {isActive && iteration > 0 && (
          <span className="text-[9px] text-slate-500 font-mono z-10">iter {iteration}</span>
        )}
      </div>
    </foreignObject>
  );
}

// ─── Main diagram ─────────────────────────────────────────────────────────────

interface Props {
  pipelineStatus: string;
  currentIteration: number;
  compact?: boolean;
}

export default function PipelineFlowDiagram({ pipelineStatus, currentIteration, compact = false }: Props) {
  const [shimmerKey, setShimmerKey] = useState(0);
  const prevStatusRef = useRef(pipelineStatus);

  useEffect(() => {
    if (pipelineStatus !== prevStatusRef.current) {
      prevStatusRef.current = pipelineStatus;
      setShimmerKey((k) => k + 1);
    }
  }, [pipelineStatus]);

  const activeEdge   = statusToActiveEdge(pipelineStatus);
  const activeNode   = statusToActiveNode(pipelineStatus);
  const transferring = isTransferringStatus(pipelineStatus);

  // SVG layout
  const W  = compact ? 740 : 780;
  const H  = compact ? 390 : 430;
  const NW = compact ? 185 : 200;
  const NH = compact ? 140 : 155;

  const orchX = W / 2;
  const orchY = NH / 2 + 22;
  const bottomY = orchY + (compact ? 185 : 200);
  const promptX = W / 2 - (compact ? 220 : 235);
  const genX    = W / 2;
  const reviewX = W / 2 + (compact ? 220 : 235);

  const orchBottom = { x: orchX, y: orchY + NH / 2 };
  const promptTop  = { x: promptX, y: bottomY - NH / 2 };
  const genTop     = { x: genX,    y: bottomY - NH / 2 };
  const reviewTop  = { x: reviewX, y: bottomY - NH / 2 };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, display: 'block' }} aria-label="Pipeline flow diagram">
      <defs>
        {/* Hex-grid background */}
        <pattern id="hex-grid" x="0" y="0" width="30" height="26" patternUnits="userSpaceOnUse">
          <path d="M15,0 L30,7.5 L30,22.5 L15,26 L0,22.5 L0,7.5 Z" fill="none"
            stroke="rgba(255,255,255,0.03)" strokeWidth="0.5" />
        </pattern>
        {/* Radial vignette */}
        <radialGradient id="vignette" cx="50%" cy="45%" r="65%">
          <stop offset="0%" stopColor="rgba(15,20,40,0)" />
          <stop offset="100%" stopColor="rgba(5,8,20,0.55)" />
        </radialGradient>
        <filter id="glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" result="coloredBlur" />
          <feMerge><feMergeNode in="coloredBlur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      {/* Backgrounds — hex-grid + vignette only; outer container provides the box */}
      <rect width={W} height={H} rx={12} fill="url(#hex-grid)" />
      <rect width={W} height={H} rx={12} fill="url(#vignette)" />

      {/* Edges */}
      <NeonEdge
        x1={orchBottom.x} y1={orchBottom.y} x2={promptTop.x} y2={promptTop.y}
        isActive={activeEdge === 'orch-prompt' || activeEdge === 'prompt-orch'}
        isTransferring={transferring} hue={NODES.prompt_generator.hue} shimmerKey={shimmerKey}
      />
      <NeonEdge
        x1={orchBottom.x} y1={orchBottom.y} x2={genTop.x} y2={genTop.y}
        isActive={activeEdge === 'orch-gen'}
        isTransferring={transferring} hue={NODES.code_generator.hue} shimmerKey={shimmerKey}
      />
      <NeonEdge
        x1={orchBottom.x} y1={orchBottom.y} x2={reviewTop.x} y2={reviewTop.y}
        isActive={activeEdge === 'orch-review' || activeEdge === 'review-orch'}
        isTransferring={transferring} hue={NODES.code_reviewer.hue} shimmerKey={shimmerKey}
      />

      {/* Nodes */}
      <HoloNodeCard config={NODES.orchestrator}     isActive={activeNode === 'orchestrator'}     status={pipelineStatus} iteration={currentIteration} x={orchX}   y={orchY}   width={NW} height={NH} />
      <HoloNodeCard config={NODES.prompt_generator}  isActive={activeNode === 'prompt_generator'} status={pipelineStatus} iteration={currentIteration} x={promptX} y={bottomY} width={NW} height={NH} />
      <HoloNodeCard config={NODES.code_generator}    isActive={activeNode === 'code_generator'}   status={pipelineStatus} iteration={currentIteration} x={genX}    y={bottomY} width={NW} height={NH} />
      <HoloNodeCard config={NODES.code_reviewer}     isActive={activeNode === 'code_reviewer'}    status={pipelineStatus} iteration={currentIteration} x={reviewX} y={bottomY} width={NW} height={NH} />
    </svg>
  );
}
