'use client';

import { useMemo, useState } from 'react';
import { Box, Card, Flex, Heading, IconButton, Text } from '@radix-ui/themes';
import { useConnectorsStore } from '@/app/(main)/workspace/connectors/store';
import { useIndexingProgress } from './use-indexing-progress';
import type { ConnectorProgress, ProgressSnapshot } from './types';

// Records store the connector *type* (e.g. "DRIVE", "ONEDRIVE", "KB") in
// connectorName; the unique instance name is resolved from the connectors store.
const CONNECTOR_LABELS: Record<string, string> = {
  DRIVE: 'Google Drive',
  'DRIVE WORKSPACE': 'Google Drive',
  GMAIL: 'Gmail',
  'GMAIL WORKSPACE': 'Gmail',
  CALENDAR: 'Google Calendar',
  ONEDRIVE: 'OneDrive',
  'SHAREPOINT ONLINE': 'SharePoint',
  OUTLOOK: 'Outlook',
  'MICROSOFT TEAMS': 'Microsoft Teams',
  NOTION: 'Notion',
  SLACK: 'Slack',
  KB: 'Knowledge Base',
  CONFLUENCE: 'Confluence',
  JIRA: 'Jira',
  BOX: 'Box',
  DROPBOX: 'Dropbox',
  WEB: 'Web',
  GITHUB: 'GitHub',
  GITLAB: 'GitLab',
  SERVICENOW: 'ServiceNow',
  SALESFORCE: 'Salesforce',
  S3: 'Amazon S3',
  'AZURE BLOB': 'Azure Blob',
  LINEAR: 'Linear',
  ZOOM: 'Zoom',
};

function prettyConnectorName(raw: string): string {
  if (!raw) return 'Unknown source';
  const key = raw.toUpperCase();
  if (CONNECTOR_LABELS[key]) return CONNECTOR_LABELS[key];
  if (/knowledge.?base|(^|_)kb(_|$)/i.test(raw)) return 'Knowledge Base';
  if (/[0-9a-f-]{16,}/i.test(raw) && /\d/.test(raw)) return 'Connector';
  return raw
    .replace(/[_-]+/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function formatEta(seconds: number | null): string {
  if (seconds === null) return 'Estimating…';
  if (seconds <= 0) return 'Done';
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `~${m}m${s ? ` ${s}s` : ''}`;
  const h = Math.floor(m / 60);
  return `~${h}h ${m % 60}m`;
}

function clampPct(p: number): number {
  return Math.min(100, Math.max(0, p));
}

const KEYFRAMES =
  '@keyframes pipes-spin{to{transform:rotate(360deg)}}' +
  '@keyframes pipes-shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(220%)}}' +
  '@keyframes pipes-in{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}';

/** Circular progress ring; adds a spinning indeterminate arc while active. */
function Ring({
  percentage,
  size,
  stroke,
  color,
  spinning,
}: {
  percentage: number;
  size: number;
  stroke: number;
  color: string;
  spinning: boolean;
}) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - clampPct(percentage) / 100);
  return (
    <Box style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--gray-a4)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={circ}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      {spinning && (
        <svg
          width={size}
          height={size}
          style={{ position: 'absolute', inset: 0, animation: 'pipes-spin 0.9s linear infinite' }}
        >
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="var(--accent-8)"
            strokeWidth={stroke}
            strokeDasharray={`${circ * 0.18} ${circ}`}
            strokeLinecap="round"
            opacity={0.75}
          />
        </svg>
      )}
    </Box>
  );
}

function ProgressBar({
  percentage,
  animated,
  height = 10,
}: {
  percentage: number;
  animated: boolean;
  height?: number;
}) {
  const pct = clampPct(percentage);
  return (
    <Box
      style={{
        position: 'relative',
        width: '100%',
        height,
        borderRadius: 'var(--radius-full)',
        background: 'var(--gray-a4)',
        overflow: 'hidden',
      }}
    >
      <Box
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          bottom: 0,
          width: `${pct}%`,
          borderRadius: 'var(--radius-full)',
          background: 'linear-gradient(90deg, var(--accent-9), var(--accent-10))',
          transition: 'width 0.6s cubic-bezier(0.4,0,0.2,1)',
          overflow: 'hidden',
        }}
      >
        {animated && pct > 0 && (
          <Box
            style={{
              position: 'absolute',
              inset: 0,
              background:
                'linear-gradient(90deg, transparent 20%, rgba(255,255,255,0.4) 50%, transparent 80%)',
              animation: 'pipes-shimmer 1.8s ease-in-out infinite',
            }}
          />
        )}
      </Box>
    </Box>
  );
}

function Stat({ dot, label, value }: { dot: string; label: string; value: number }) {
  return (
    <Flex
      align="center"
      gap="2"
      style={{ padding: '4px 10px', borderRadius: 'var(--radius-full)', background: 'var(--gray-a3)' }}
    >
      <Box style={{ width: 7, height: 7, borderRadius: '50%', background: dot }} />
      <Text size="1" weight="bold" style={{ fontVariantNumeric: 'tabular-nums' }}>
        {value.toLocaleString()}
      </Text>
      <Text size="1" color="gray">
        {label}
      </Text>
    </Flex>
  );
}

function MiniTag({ color, bg, children }: { color: string; bg: string; children: React.ReactNode }) {
  return (
    <Text
      size="1"
      style={{ padding: '1px 8px', borderRadius: 'var(--radius-full)', background: bg, color, fontWeight: 600, fontSize: 11 }}
    >
      {children}
    </Text>
  );
}

function ConnectorRow({ c, name, last }: { c: ConnectorProgress; name: string; last: boolean }) {
  const processed = Math.max(0, c.total - c.pending);
  const dot = c.pending > 0 ? 'var(--blue-9)' : c.failed > 0 ? 'var(--red-9)' : 'var(--accent-9)';
  return (
    <Flex
      direction="column"
      gap="2"
      style={{ padding: '10px 2px', borderBottom: last ? 'none' : '1px solid var(--gray-a3)' }}
    >
      <Flex justify="between" align="center" gap="3">
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <Box style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0 }} />
          <Text size="2" weight="medium" truncate>
            {name}
          </Text>
        </Flex>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <Text size="1" color="gray" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {processed.toLocaleString()}/{c.total.toLocaleString()}
          </Text>
          <Text size="1" weight="bold" style={{ fontVariantNumeric: 'tabular-nums', minWidth: 40, textAlign: 'right' }}>
            {c.percentage}%
          </Text>
        </Flex>
      </Flex>
      <ProgressBar percentage={c.percentage} animated={false} height={5} />
      {(c.failed > 0 || c.skipped > 0) && (
        <Flex gap="2">
          {c.failed > 0 && (
            <MiniTag color="var(--red-11)" bg="var(--red-a3)">
              {c.failed} failed
            </MiniTag>
          )}
          {c.skipped > 0 && (
            <MiniTag color="var(--gray-11)" bg="var(--gray-a3)">
              {c.skipped} skipped
            </MiniTag>
          )}
        </Flex>
      )}
    </Flex>
  );
}

function stateOf(snapshot: ProgressSnapshot) {
  const complete = snapshot.phase === 'complete';
  const paused = snapshot.status === 'paused';
  if (complete) return { complete, paused, icon: 'check_circle', short: 'Indexed' };
  if (paused) return { complete, paused, icon: 'pause_circle', short: 'Paused' };
  return { complete, paused, icon: 'sync', short: 'Indexing' };
}

/** Collapsed pill the user clicks to open the panel. */
function Trigger({
  snapshot,
  open,
  onToggle,
}: {
  snapshot: ProgressSnapshot;
  open: boolean;
  onToggle: () => void;
}) {
  const s = stateOf(snapshot);
  const [hover, setHover] = useState(false);
  const ringColor = s.paused ? 'var(--gray-9)' : 'var(--accent-9)';
  return (
    <button
      type="button"
      onClick={onToggle}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-expanded={open}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 10,
        padding: '7px 14px 7px 8px',
        borderRadius: 'var(--radius-full)',
        border: '1px solid var(--gray-a5)',
        background: 'var(--color-panel-solid)',
        color: 'var(--gray-12)',
        boxShadow: hover ? 'var(--shadow-5)' : 'var(--shadow-3)',
        transform: hover ? 'translateY(-1px)' : 'none',
        transition: 'box-shadow .18s ease, transform .18s ease',
        cursor: 'pointer',
        font: 'inherit',
      }}
    >
      <Ring percentage={snapshot.percentage} size={28} stroke={3} color={ringColor} spinning={!s.complete && !s.paused} />
      <Flex direction="column" align="start" style={{ lineHeight: 1.15 }}>
        <Text
          style={{
            color: 'var(--gray-10)',
            fontWeight: 700,
            letterSpacing: '.04em',
            textTransform: 'uppercase',
            fontSize: 9.5,
          }}
        >
          {s.short}
        </Text>
        <Text size="2" weight="bold" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {snapshot.percentage}%
        </Text>
      </Flex>
      <span className="material-icons-outlined" style={{ fontSize: 18, color: 'var(--gray-9)', marginLeft: 2 }}>
        {open ? 'expand_more' : 'expand_less'}
      </span>
    </button>
  );
}

/** Expanded detail panel. */
function Panel({
  snapshot,
  resolveName,
  onClose,
}: {
  snapshot: ProgressSnapshot;
  resolveName: (c: ConnectorProgress) => string;
  onClose: () => void;
}) {
  const [showBreakdown, setShowBreakdown] = useState(true);
  const s = stateOf(snapshot);
  const processed = snapshot.done + snapshot.failed + snapshot.skipped;

  const title = s.complete ? 'Workspace indexed' : s.paused ? 'Indexing paused' : 'Indexing workspace';
  const etaText = s.paused ? 'Paused' : formatEta(snapshot.etaSeconds);
  const accent = s.paused ? 'var(--gray-11)' : 'var(--accent-11)';
  const iconBg = s.paused ? 'var(--gray-a3)' : 'var(--accent-a3)';

  return (
    <Card
      size="2"
      style={{
        width: '100%',
        boxShadow: 'var(--shadow-5)',
        border: '1px solid var(--gray-a4)',
        animation: 'pipes-in 0.22s cubic-bezier(0.16,1,0.3,1)',
      }}
    >
      <Flex direction="column" gap="4">
        {/* Header */}
        <Flex justify="between" align="start">
          <Flex align="center" gap="3">
            <Flex
              align="center"
              justify="center"
              style={{ width: 34, height: 34, borderRadius: 'var(--radius-3)', background: iconBg, flexShrink: 0 }}
            >
              <span
                className="material-icons-outlined"
                style={{
                  fontSize: 20,
                  color: accent,
                  animation: !s.complete && !s.paused ? 'pipes-spin 1.4s linear infinite' : 'none',
                }}
              >
                {s.icon}
              </span>
            </Flex>
            <Flex direction="column" style={{ lineHeight: 1.25 }}>
              <Heading size="3" style={{ letterSpacing: '-.01em' }}>
                {title}
              </Heading>
              <Text size="1" color="gray">
                {processed.toLocaleString()} of {snapshot.total.toLocaleString()} records
              </Text>
            </Flex>
          </Flex>
          <IconButton variant="ghost" color="gray" radius="full" aria-label="Collapse" onClick={onClose}>
            <span className="material-icons-outlined" style={{ fontSize: 20 }}>
              close
            </span>
          </IconButton>
        </Flex>

        {/* Big % + ETA + bar */}
        <Flex direction="column" gap="3">
          <Flex justify="between" align="end">
            <Text style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-.02em', lineHeight: 1 }}>
              {snapshot.percentage}%
            </Text>
            {/* No time estimate once complete — the title + 100% already say done. */}
            {!s.complete && (
              <Flex direction="column" align="end" style={{ lineHeight: 1.25 }}>
                <Text size="1" color="gray">
                  Est. time remaining
                </Text>
                <Text size="2" weight="bold" style={{ color: accent }}>
                  {etaText}
                </Text>
              </Flex>
            )}
          </Flex>
          <ProgressBar percentage={snapshot.percentage} animated={!s.paused && !s.complete} />
        </Flex>

        {/* Stat chips */}
        <Flex gap="2" wrap="wrap">
          <Stat dot="var(--accent-9)" label="done" value={snapshot.done} />
          {snapshot.pending > 0 && <Stat dot="var(--blue-9)" label="pending" value={snapshot.pending} />}
          {snapshot.failed > 0 && <Stat dot="var(--red-9)" label="failed" value={snapshot.failed} />}
          {snapshot.skipped > 0 && <Stat dot="var(--amber-9)" label="skipped" value={snapshot.skipped} />}
        </Flex>

        {/* Per-connector breakdown */}
        {snapshot.connectors.length > 0 && (
          <Box>
            <button
              type="button"
              onClick={() => setShowBreakdown((v) => !v)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                width: '100%',
                padding: '6px 2px',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                font: 'inherit',
                borderTop: '1px solid var(--gray-a3)',
              }}
            >
              <Text size="1" weight="bold" color="gray" style={{ letterSpacing: '.02em' }}>
                SOURCES · {snapshot.connectors.length}
              </Text>
              <span
                className="material-icons-outlined"
                style={{
                  fontSize: 18,
                  color: 'var(--gray-9)',
                  transform: showBreakdown ? 'rotate(180deg)' : 'none',
                  transition: 'transform .2s ease',
                }}
              >
                expand_more
              </span>
            </button>
            {showBreakdown && (
              <Box className="no-scrollbar" style={{ maxHeight: 240, overflowY: 'auto' }}>
                {snapshot.connectors.map((c, i) => (
                  <ConnectorRow
                    key={c.connectorId}
                    c={c}
                    name={resolveName(c)}
                    last={i === snapshot.connectors.length - 1}
                  />
                ))}
              </Box>
            )}
          </Box>
        )}
      </Flex>
    </Card>
  );
}

/**
 * Org-wide indexing progress for admins — a permanent workspace-status widget.
 * Collapsed pill on the connectors page; click to open the detail panel. Shown
 * whenever the org has records; hidden only when the org has zero records.
 */
export function IndexingProgressWidget() {
  const { snapshot } = useIndexingProgress();
  const [open, setOpen] = useState(false);
  const activeConnectors = useConnectorsStore((state) => state.activeConnectors);

  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of activeConnectors) {
      if (c._key && c.name) m.set(c._key, c.name);
    }
    return m;
  }, [activeConnectors]);

  const resolveName = (c: ConnectorProgress): string => {
    const fromStore = nameById.get(c.connectorId);
    if (fromStore) return fromStore;
    const n = (c.connectorName || '').trim();
    if (n && n !== n.toUpperCase()) return n; // already an instance name
    return prettyConnectorName(n);
  };

  if (!snapshot || snapshot.total <= 0) return null;

  return (
    <Flex direction="column" align="end" gap="2">
      <style>{KEYFRAMES}</style>
      {open ? (
        <Panel snapshot={snapshot} resolveName={resolveName} onClose={() => setOpen(false)} />
      ) : (
        <Trigger snapshot={snapshot} open={open} onToggle={() => setOpen(true)} />
      )}
    </Flex>
  );
}
