'use client';

import React, { useId, useMemo } from 'react';
import { VirtualRows, useRowState } from '../virtual-rows';
import { Badge, Box, Flex, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ActivityTool, DurableAgentRunHandle, MessagePart } from '../../types';
import { approvalWhyKey } from '../message-area/approval-card';
import { interactionWhyValues } from '../../interaction-wording';
import {
  EngineCommandRow,
  EngineFileRow,
  formatDuration,
  toolActivityLabel as partActivityLabel,
} from '../message-area/agent-activity';
import { StepLink, StepTarget, stepOutcome, stepTargetText } from '../message-area/tool-step';
import { EngineRepoRow } from '../message-area/engine-repo-row';
import { LinkedText, ToolArguments, ToolResult, toolStatus } from '../message-area/tool-result';
import { toolFailureText } from '../../harness-errors';
import { redactText } from '../../approval-preview';
import { toolResultView } from '../../tool-result-view';
import { withEngineSteps, withoutHelpers } from '../../engine-steps';
import { runStillActive } from '../../durable-run-ui';
import { useRunTimeline } from '../../hooks/use-run-timeline';
import { extractToolsetLabel, toolActivityLabel, toolSubject } from '../../utils/tool-display';
import { completedOutcome, partOfTool, stepItems, stepSubject, toolOfPart, type StepItem } from './activity-steps';
import { useStepDetail } from './use-step-detail';

function Icon({ name, size = 16, color }: { name: string; size?: number; color?: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex' }}>
      <MaterialIcon name={name} size={size} color={color} />
    </span>
  );
}

const TOOL_COLOR: Record<ActivityTool['status'], 'blue' | 'green' | 'red' | 'amber' | 'gray'> = {
  running: 'blue',
  completed: 'green',
  failed: 'red',
  awaiting_approval: 'amber',
  denied: 'red',
  blocked: 'red',
};

const STATUS_ICON: Record<ActivityTool['status'], { name: string; color: string }> = {
  running: { name: 'sync', color: 'var(--blue-11)' },
  completed: { name: 'check_circle', color: 'var(--green-11)' },
  failed: { name: 'warning', color: 'var(--red-11)' },
  awaiting_approval: { name: 'pending_actions', color: 'var(--amber-11)' },
  denied: { name: 'block', color: 'var(--red-11)' },
  blocked: { name: 'block', color: 'var(--amber-11)' },
};

function toolStatusText(t: TFunction, status: ActivityTool['status']): string {
  switch (status) {
    case 'running':
      return t('activityPanel.tool.running');
    case 'completed':
      return t('activityPanel.tool.completed');
    case 'failed':
      return t('activityPanel.tool.failed');
    case 'awaiting_approval':
      return t('activityPanel.tool.awaiting');
    case 'denied':
      return t('activityPanel.tool.denied');
    default:
      return t('activityPanel.tool.blocked');
  }
}

/** The worker's refusal of a page fetch to an address the run was not given, after it read private content. */
const WEB_ADDRESS_RULE = 'web_address';

/** Why a call waits or was refused, in the approval card's words (UX1/UX2); null when there is nothing to say. */
function toolReason(t: TFunction, tool: ActivityTool): string | null {
  const rule = tool.refusal?.rule ?? tool.refusal?.code;
  if ((tool.status === 'denied' || tool.status === 'blocked') && rule === WEB_ADDRESS_RULE) {
    return t('activityPanel.tool.webAddressRefused');
  }
  switch (tool.status) {
    case 'awaiting_approval':
      // The approval card's longer explanation is in the step's body (`approvalWhy`): the outcome stays one line.
      return `${t('activityPanel.tool.waitingGeneric')} ${t('activityPanel.tool.decideInChat')}`;
    case 'denied':
      return rule ? t('activityPanel.tool.refusedBy', { rule }) : t('activityPanel.tool.deniedGeneric');
    case 'blocked':
      return rule ? t('activityPanel.tool.blockedBy', { rule }) : t('activityPanel.tool.blockedGeneric');
    default:
      return null;
  }
}

/** The step's outcome in one line, and the test id the line carries. */
function outcomeLine(t: TFunction, tool: ActivityTool, part: MessagePart): { text: string; testId: string; color?: 'red' } {
  const reason = toolReason(t, tool);
  if (reason) return { text: reason, testId: 'activity-tool-reason' };
  // G-03: a dry run recorded the call as what it would have done; nothing was changed or sent.
  if (tool.dryRun) {
    return {
      text: tool.dryRun.wouldAsk ? t('activityPanel.tool.dryRunWouldAsk') : t('activityPanel.tool.dryRun'),
      testId: 'activity-tool-dry-run',
    };
  }
  if (tool.status === 'failed') {
    return {
      text: toolFailureText({ errorCode: tool.failure?.code }, toolSubject(tool.name, tool.displayName)),
      testId: 'activity-tool-failure',
      color: 'red',
    };
  }
  if (tool.status === 'running') return { text: t('activityPanel.steps.running'), testId: 'activity-tool-outcome' };
  // A step's own count, worded as the chat's row words it (WP-115).
  const counted = tool.status === 'completed' && tool.step ? stepOutcome(tool.step, t) : null;
  if (counted) return { text: counted, testId: 'activity-tool-outcome' };
  // The server's line; a child run's step, which comes with its result, is worded from it here.
  const view = tool.outcome ? null : toolResultView(part.resultView);
  const outcome = tool.outcome ?? completedOutcome(tool, view?.kind === 'records' ? view.total : null);
  switch (outcome.kind) {
    case 'summary':
      return { text: redactText(outcome.text).text, testId: 'activity-tool-outcome' };
    case 'found':
      return { text: t('activityPanel.steps.found', { count: outcome.count }), testId: 'activity-tool-outcome' };
    case 'empty':
      return { text: t('activityPanel.steps.noResults'), testId: 'activity-tool-outcome' };
    default:
      return { text: t('activityPanel.steps.done'), testId: 'activity-tool-outcome' };
  }
}

const HEAD_STYLE: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: 'var(--space-2)',
  width: '100%',
  minHeight: 44,
  padding: 'var(--space-2)',
  background: 'none',
  border: 'none',
  borderRadius: 'var(--radius-2)',
  color: 'var(--gray-12)',
  font: 'inherit',
  textAlign: 'left',
};

const CARD_STYLE: React.CSSProperties = { border: '1px solid var(--gray-a5)', borderRadius: 'var(--radius-2)' };

function SecondaryToggle({ open, onToggle, controls, label }: { open: boolean; onToggle: () => void; controls: string; label: string }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        minHeight: 32,
        padding: 0,
        background: 'none',
        border: 'none',
        cursor: 'pointer',
        color: 'var(--gray-11)',
        font: 'inherit',
        fontSize: 'var(--font-size-1)',
      }}
    >
      <Icon name={open ? 'expand_more' : 'chevron_right'} size={14} color="var(--gray-11)" />
      {label}
    </button>
  );
}

/** One step: what was done and its outcome in two lines; its result one click away, its arguments one more. */
export function StepRow({ tool: line, run, nested = false }: { tool: ActivityTool; run: DurableAgentRunHandle; nested?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useRowState(`step.open:${line.toolCallId}`, false);
  const [details, setDetails] = useRowState(`step.details:${line.toolCallId}`, false);
  const bodyId = useId();
  const detailsId = useId();
  const { tool: read, state: detailState } = useStepDetail(run, line, open);
  const tool = read;
  const part = useMemo(() => partOfTool(tool), [tool]);
  const status = toolStatus(part.status, 'running');
  const app = extractToolsetLabel(tool.name, tool.displayName);
  // A step with facts reads as the chat's row: its action, then the page (linked), query or file (WP-115).
  const label = part.step || part.skills ? partActivityLabel(part) : toolActivityLabel(tool.name, tool.displayName);
  const subject = part.step ? undefined : stepSubject(tool.argsSummary ? redactText(tool.argsSummary).text : undefined);
  const outcome = outcomeLine(t, tool, part);
  const duration = formatDuration(tool.durationMs, t);
  const icon = STATUS_ICON[tool.status];
  const result =
    detailState === 'loading' ? (
      <Flex align="center" gap="2" data-testid="activity-tool-loading">
        <Spinner size="1" />
        <Text size="1" color="gray">
          {t('activityPanel.steps.resultLoading')}
        </Text>
      </Flex>
    ) : detailState === 'failed' ? (
      <Text size="1" color="gray" data-testid="activity-tool-unavailable">
        {t('activityPanel.steps.resultUnavailable')}
      </Text>
    ) : (
      <ToolResult part={part} status={status} rawBehindSummary />
    );
  const hasArgs = Boolean(tool.argsSummary || (tool.args && tool.args.trim() !== '{}'));
  const why = tool.reason ? approvalWhyKey(tool.reason) : null;
  return (
    <Box data-testid="activity-tool" style={nested ? undefined : CARD_STYLE}>
      <h4 style={{ margin: 0, fontSize: 'inherit', fontWeight: 'inherit' }}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((v) => !v)}
          data-testid="activity-tool-toggle"
          style={{ ...HEAD_STYLE, cursor: 'pointer' }}
        >
          <Icon name={open ? 'expand_more' : 'chevron_right'} size={16} color="var(--gray-11)" />
          {tool.status === 'running' ? <Spinner size="1" style={{ marginTop: 2 }} /> : <Icon name={icon.name} size={16} color={icon.color} />}
          <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
            <Text size="2" style={{ overflowWrap: 'anywhere' }}>
              {/* The raw id stays on the element for debugging, never shown: a model-made id is not redacted (TOOLNAMES). */}
              <Text weight="medium" data-tool-id={tool.name} data-testid="activity-tool-name">
                {app ? `${app} · ${label}` : label}
              </Text>
              {part.step ? (
                <Text color="gray" data-testid="activity-tool-subject">
                  <StepTarget step={part.step} withOutcome={false} linked={false} />
                </Text>
              ) : subject ? (
                <Text color="gray" data-testid="activity-tool-subject">
                  {` · “${subject}”`}
                </Text>
              ) : null}
            </Text>
            <Text size="1" color={outcome.color ?? 'gray'} data-testid={outcome.testId} style={{ overflowWrap: 'anywhere' }}>
              {/* Any other status has its badge; a finished step's is only its icon. */}
              {tool.status === 'completed' ? <VisuallyHidden>{`${toolStatusText(t, tool.status)}: `}</VisuallyHidden> : null}
              {outcome.text}
            </Text>
          </Flex>
          <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
            {duration ? (
              <Text size="1" color="gray" data-testid="activity-tool-duration" style={{ whiteSpace: 'nowrap' }}>
                {duration}
              </Text>
            ) : null}
            {tool.approvalAnswer === 'allowed' && tool.status !== 'awaiting_approval' ? (
              <Badge color="green" variant="soft" highContrast>
                {t('chat.toolCard.approved')}
              </Badge>
            ) : null}
            {tool.status !== 'completed' ? (
              // Radix's soft badge text is under 4.5:1 on the panel in the light look; its high-contrast text is not (F5).
              <Badge color={TOOL_COLOR[tool.status]} variant="soft" highContrast>
                {toolStatusText(t, tool.status)}
              </Badge>
            ) : null}
          </Flex>
        </button>
      </h4>
      {open ? (
        <Flex id={bodyId} direction="column" gap="2" data-testid="activity-tool-body" style={{ padding: '0 var(--space-2) var(--space-2) var(--space-6)' }}>
          {why ? (
            <Text size="1" color="gray" data-testid="activity-tool-why">
              {t(why, interactionWhyValues(tool.reason))}
            </Text>
          ) : null}
          {tool.failure?.detail ? (
            <Text
              size="1"
              color="gray"
              data-testid="activity-tool-detail"
              style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            >
              <LinkedText text={t('activityPanel.tool.detail', { detail: tool.failure.detail })} />
            </Text>
          ) : null}
          {part.step?.href ? (
            <Text size="1" style={{ overflowWrap: 'anywhere' }}>
              <StepLink step={part.step} text={stepTargetText(part.step) ?? part.step.href} testId="activity-tool-link" />
            </Text>
          ) : null}
          {tool.child ? <ChildSteps run={run} child={tool.child} /> : null}
          {result}
          {hasArgs ? (
            <Box>
              <SecondaryToggle
                open={details}
                onToggle={() => setDetails((v) => !v)}
                controls={detailsId}
                label={t('activityPanel.steps.details')}
              />
              {details ? (
                <Box id={detailsId}>
                  <ToolArguments part={part} />
                </Box>
              ) : null}
            </Box>
          ) : null}
        </Flex>
      ) : null}
    </Box>
  );
}

/** The same tool called again and again: one row, its calls inside, in order. */
function GroupRow({ tools, run }: { tools: ActivityTool[]; run: DurableAgentRunHandle }) {
  const { t } = useTranslation();
  const [open, setOpen] = useRowState(`group.open:${tools[0]?.toolCallId ?? ''}`, false);
  const bodyId = useId();
  const first = tools[0]!;
  const app = extractToolsetLabel(first.name, first.displayName);
  const label = toolActivityLabel(first.name, first.displayName);
  const failed = tools.filter((tool) => tool.status === 'failed' || tool.status === 'denied' || tool.status === 'blocked').length;
  const running = tools.filter((tool) => tool.status === 'running').length;
  const finished = tools.length - failed - running;
  const outcome = [
    finished ? t('activityPanel.steps.groupFinished', { count: finished }) : null,
    running ? t('activityPanel.steps.groupRunning', { count: running }) : null,
    failed ? t('activityPanel.steps.groupFailed', { count: failed }) : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const total = tools.reduce((sum, tool) => sum + (tool.durationMs ?? 0), 0);
  const duration = formatDuration(total || undefined, t);
  const icon = running ? null : failed ? STATUS_ICON.failed : STATUS_ICON.completed;
  return (
    <Box data-testid="activity-step-group" style={CARD_STYLE}>
      <h4 style={{ margin: 0, fontSize: 'inherit', fontWeight: 'inherit' }}>
        <button type="button" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)} style={{ ...HEAD_STYLE, cursor: 'pointer' }}>
          <Icon name={open ? 'expand_more' : 'chevron_right'} size={16} color="var(--gray-11)" />
          {icon ? <Icon name={icon.name} size={16} color={icon.color} /> : <Spinner size="1" style={{ marginTop: 2 }} />}
          <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
            <Text size="2" weight="medium" data-testid="activity-step-group-name" style={{ overflowWrap: 'anywhere' }}>
              {t('activityPanel.steps.groupTitle', { label: app ? `${app} · ${label}` : label, count: tools.length })}
            </Text>
            <Text size="1" color={failed ? 'red' : 'gray'}>
              {outcome}
            </Text>
          </Flex>
          {duration ? (
            <Text size="1" color="gray" style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
              {duration}
            </Text>
          ) : null}
        </button>
      </h4>
      {open ? (
        <Box id={bodyId} style={{ padding: '0 var(--space-2) var(--space-2) var(--space-6)' }}>
          <VirtualRows
            items={tools}
            getKey={toolKey}
            estimateSize={estimateStep}
            gap="var(--space-1)"
            renderRow={(tool) => (
              <Box style={{ borderTop: '1px solid var(--gray-a4)' }}>
                <StepRow tool={tool} run={run} nested />
              </Box>
            )}
          />
        </Box>
      ) : null}
    </Box>
  );
}

function QuietRow({ lookup }: { lookup: 'tools' | 'skills' }) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" data-testid="activity-step-quiet" style={{ padding: '0 var(--space-2)' }}>
      <Icon name="manage_search" size={14} color="var(--gray-11)" />
      <Text size="1" color="gray">
        {lookup === 'skills' ? t('activityPanel.steps.lookedUpSkills') : t('activityPanel.steps.lookedUpTools')}
      </Text>
    </Flex>
  );
}

function StepItemRow({ item, run }: { item: StepItem; run: DurableAgentRunHandle }) {
  switch (item.kind) {
    case 'quiet':
      return <QuietRow lookup={item.lookup} />;
    case 'group':
      return <GroupRow tools={item.tools} run={run} />;
    default:
      return <StepRow tool={item.tool} run={run} />;
  }
}

const toolKey = (tool: ActivityTool): string => tool.toolCallId;
const stepItemKey = (item: StepItem): string => item.key;
/** A folded step is two lines; a quiet row one. */
const estimateStep = (): number => 52;
const estimateStepItem = (item: StepItem): number => (item.kind === 'quiet' ? 24 : 56);

/** A run's steps, in order. */
export function StepList({ tools, run }: { tools: readonly ActivityTool[]; run: DurableAgentRunHandle }) {
  const items = useMemo(() => stepItems(tools), [tools]);
  return (
    <VirtualRows
      items={items}
      getKey={stepItemKey}
      estimateSize={estimateStepItem}
      gap="var(--space-2)"
      data-testid="activity-steps"
      renderRow={(item) => <StepItemRow item={item} run={run} />}
    />
  );
}

/**
 * A run's steps from its own timeline, folded as the chat folds them (`withEngineSteps`): the one fold behind a
 * child's steps under the call that started it and an engine run's own steps when the panel shows that run.
 */
export function useRunSteps(
  run: DurableAgentRunHandle,
  runId: string | undefined,
  options: { enabled: boolean; follow: boolean; refreshKey: unknown },
) {
  const { timeline, failed } = useRunTimeline(run, runId, options);
  const parts = useMemo(
    () => (timeline ? withoutHelpers(withEngineSteps(timeline.parts, !runStillActive(timeline.status))) : []),
    [timeline],
  );
  return { timeline, failed, parts };
}

/** What a child run (a coding task, a sub-agent) did, read from its own timeline while the step is open. */
function ChildSteps({ run, child }: { run: DurableAgentRunHandle; child: NonNullable<ActivityTool['child']> }) {
  const { t } = useTranslation();
  const { timeline, failed, parts } = useRunSteps(run, child.runId, {
    enabled: true,
    follow: child.status === 'running',
    refreshKey: child.status,
  });
  const title = child.codeTaskGoal ? t('agentActivity.codeTask', { goal: child.codeTaskGoal }) : (child.goal ?? child.role);
  const rows = parts.filter(
    (part) => part.type === 'tool_call' || part.type === 'engine_command' || part.type === 'engine_file' || part.type === 'engine_repo',
  );
  return (
    <Flex direction="column" gap="1" data-testid="activity-child-steps" style={{ borderLeft: '2px solid var(--gray-a6)', paddingLeft: 'var(--space-2)' }}>
      {title ? (
        <Text size="1" weight="medium" color="gray">
          {title}
        </Text>
      ) : null}
      {failed && !timeline ? (
        <Text size="1" color="gray">
          {t('activityPanel.steps.childUnavailable')}
        </Text>
      ) : !timeline ? (
        <Flex align="center" gap="2">
          <Spinner size="1" />
          <Text size="1" color="gray">
            {t('activityPanel.steps.childLoading')}
          </Text>
        </Flex>
      ) : rows.length === 0 ? (
        <Text size="1" color="gray">
          {t('activityPanel.steps.childEmpty')}
        </Text>
      ) : (
        rows.map((part, index) => {
          // The chat's own rows: a command's output and a file's diff read the same in both places.
          if (part.type === 'engine_command') {
            return (
              <Box key={part.toolCallId ?? index} data-testid="activity-child-command" style={{ padding: 'var(--space-1) var(--space-2)' }}>
                <EngineCommandRow part={part} />
              </Box>
            );
          }
          if (part.type === 'engine_repo') {
            return part.repo ? (
              <Box key={part.toolCallId ?? index} style={{ padding: 'var(--space-1) var(--space-2)' }}>
                <EngineRepoRow repo={part.repo} />
              </Box>
            ) : null;
          }
          if (part.type === 'engine_file') {
            return part.file ? (
              <Box key={part.toolCallId ?? index} style={{ padding: 'var(--space-1) var(--space-2)' }}>
                <EngineFileRow file={part.file} />
              </Box>
            ) : null;
          }
          const tool = toolOfPart(part);
          return tool ? <StepRow key={tool.toolCallId} tool={tool} run={run} nested /> : null;
        })
      )}
    </Flex>
  );
}
