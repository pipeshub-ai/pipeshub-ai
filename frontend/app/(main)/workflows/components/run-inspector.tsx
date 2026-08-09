'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Flex, Text, Card, Badge, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useWorkflowRunUpdates } from '@/lib/hooks/use-workflow-run-updates';
import { WorkflowGraph } from './workflow-graph';
import { WorkflowsApi } from '../api';
import type { WorkflowIR, WorkflowRun, WorkflowTraceEntry } from '../types';

interface RunInspectorProps {
  workflowId: string;
  runId: string;
  /** IR of the version this run executed; enables the graph <-> trace pairing. */
  ir?: WorkflowIR | null;
}

const ENTRY_KIND_ICONS: Record<string, string> = {
  step:      'functions',
  tool:      'build',
  agent:     'smart_toy',
  clock:     'schedule',
  random:    'casino',
  wait:      'pause_circle',
  approval:  'thumb_up',
  uuid:      'tag',
  knowledge: 'search',
  state:     'database',
  emit:      'forum',
  sleep:     'bedtime',
};

// Coalesce bursts of `workflowRunUpdate` WS events into at most one
// re-fetch per window -- a chatty run (many step transitions in quick
// succession) would otherwise fire one trace GET per event.
const REFRESH_DEBOUNCE_MS = 800;

function outcomeColor(outcome: string): 'green' | 'red' | 'gray' {
  if (outcome === 'succeeded') return 'green';
  if (outcome === 'failed') return 'red';
  return 'gray';
}

/** What an IR node acts on, in the same vocabulary as `TraceEntry.target`. */
function nodeTarget(metadata: Record<string, unknown>, label: string): string {
  const toolPath = metadata?.tool_path;
  return typeof toolPath === 'string' && toolPath ? toolPath : label;
}

function TraceEntryRow({
  entry,
  highlighted,
}: {
  entry: WorkflowTraceEntry;
  highlighted: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Box>
      <Flex
        align="center"
        gap="2"
        p="2"
        style={{
          cursor: 'pointer',
          borderRadius: 'var(--radius-2)',
          background: highlighted ? 'var(--violet-a3)' : undefined,
        }}
        onClick={() => setExpanded((e) => !e)}
      >
        <MaterialIcon name={ENTRY_KIND_ICONS[entry.kind] ?? 'circle'} size={14} />
        <Text size="1" style={{ fontFamily: 'monospace', flex: 1 }}>
          {entry.label}
        </Text>
        {entry.attempt > 1 && (
          <Badge size="1" color="amber" variant="soft">
            attempt {entry.attempt}
          </Badge>
        )}
        <Badge size="1" color={outcomeColor(entry.outcome)}>
          {entry.outcome}
        </Badge>
        <MaterialIcon name={expanded ? 'expand_less' : 'expand_more'} size={14} />
      </Flex>
      {expanded && (
        <Box
          p="2"
          style={{
            background: 'var(--gray-a2)',
            borderRadius: 'var(--radius-2)',
            fontFamily: 'monospace',
            fontSize: '11px',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {entry.error ? (
            <Text color="red" size="1">
              {entry.error}
            </Text>
          ) : (
            <Text size="1">{JSON.stringify(entry.detail, null, 2)}</Text>
          )}
        </Box>
      )}
    </Box>
  );
}

export function RunInspector({ workflowId, runId, ir = null }: RunInspectorProps) {
  const { t } = useTranslation();
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [traceEntries, setTraceEntries] = useState<WorkflowTraceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    WorkflowsApi.getRunTrace(workflowId, runId)
      .then(({ run: r, traceEntries: entries }) => {
        setRun(r);
        setTraceEntries(entries ?? []);
      })
      .catch((err: unknown) =>
        setError(
          err instanceof Error
            ? err.message
            : t('workflowsPage.detail.traceLoadError', 'Failed to load trace')
        )
      )
      .finally(() => setLoading(false));
  }, [workflowId, runId, t]);

  // Subscribe to live run updates via the notification socket, debounced so a
  // burst of step-transition events collapses into one refetch.
  const handleRunUpdate = useCallback(
    (update: { runId?: string }) => {
      if (update.runId !== runId) return;
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        WorkflowsApi.getRunTrace(workflowId, runId)
          .then(({ run: r, traceEntries: entries }) => {
            if (r) setRun(r);
            setTraceEntries(entries ?? []);
          })
          .catch((err: unknown) => console.error('RunInspector: failed to refresh trace', err));
      }, REFRESH_DEBOUNCE_MS);
    },
    [workflowId, runId]
  );
  useWorkflowRunUpdates(handleRunUpdate);

  useEffect(
    () => () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    },
    []
  );

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      // Clicking the selected node again clears the highlight.
      if (nodeId === selectedNodeId) {
        setSelectedNodeId(null);
        setSelectedTarget(null);
        return;
      }
      const node = ir?.nodes.find((n) => n.node_id === nodeId);
      setSelectedNodeId(nodeId);
      setSelectedTarget(node ? nodeTarget(node.metadata, node.label) : null);
    },
    [ir, selectedNodeId]
  );

  if (loading) {
    return (
      <Text color="gray" size="2">
        {t('workflowsPage.loading', 'Loading...')}
      </Text>
    );
  }
  if (error) {
    return (
      <Text color="red" size="2">
        {error}
      </Text>
    );
  }

  const hasGraph = (ir?.nodes.length ?? 0) > 0;

  return (
    <Flex direction="column" gap="3">
      {run && (
        <Card variant="surface">
          <Flex justify="between" align="center" p="2">
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium">
                {t('workflowsPage.detail.runLabel', 'Run: {{id}}', { id: run.runId.slice(0, 8) })}…
              </Text>
              {run.startedAt && (
                <Text size="1" color="gray">
                  {t('workflowsPage.detail.startedAt', 'Started: {{when}}', {
                    when: new Date(run.startedAt).toLocaleString(),
                  })}
                </Text>
              )}
            </Flex>
            <Badge
              color={
                run.status === 'succeeded'
                  ? 'green'
                  : run.status === 'failed'
                    ? 'red'
                    : run.status === 'running'
                      ? 'blue'
                      : 'gray'
              }
            >
              {run.status}
            </Badge>
          </Flex>
          {run.outputSummary && (
            <Box p="2" pt="0">
              <Text size="1" color="gray">
                {run.outputSummary}
              </Text>
            </Box>
          )}
        </Card>
      )}

      {hasGraph && ir && (
        <Flex direction="column" gap="1">
          <Text size="1" color="gray">
            {t('workflowsPage.detail.graphHint', 'Select a node to highlight the steps it produced.')}
          </Text>
          <WorkflowGraph
            ir={ir}
            height="300px"
            selectedNodeId={selectedNodeId}
            onNodeClick={handleNodeClick}
          />
        </Flex>
      )}

      {traceEntries.length === 0 ? (
        <Text color="gray" size="2">
          {run?.status === 'running'
            ? t('workflowsPage.detail.runInProgress', 'Run in progress…')
            : t('workflowsPage.detail.noTraceEntries', 'No trace entries available.')}
        </Text>
      ) : (
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium">
            {t('workflowsPage.detail.executionTrace', 'Execution trace ({{count}} steps)', {
              count: traceEntries.length,
            })}
          </Text>
          {traceEntries.map((entry) => (
            <TraceEntryRow
              key={`${entry.seq}-${entry.label}`}
              entry={entry}
              highlighted={selectedTarget != null && entry.target === selectedTarget}
            />
          ))}
        </Flex>
      )}
    </Flex>
  );
}
