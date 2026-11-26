'use client';

/**
 * Renders the agent-activity transcript (`MessagePart[]` — see "Parts-Based
 * Agent Message Transcript" / "Cursor-Style Agent Transparency" plans) as a
 * vertical timeline of narration text, collapsible thinking blocks,
 * tool-call cards (individually or grouped), and nested sub-agent groups.
 * Used both while streaming (`ChatSlot.streamingParts`) and after reload
 * (`ConversationMessage.parts`) — same components, same shape, so a message
 * never visually changes when the live stream hands off to the persisted
 * transcript.
 */
import React, { useState, useRef, useMemo, useCallback, useEffect } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { PluggableList } from 'unified';
import { Badge, Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { jobTitle as sharedJobTitle } from '@/lib/harness-jobs/engine-worker';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { markdownUrlTransform } from '@/lib/utils/image-url-policy';
import { TOOL_PROGRESS_STATUS, type MessagePart, type SSEPlanEvent, type SSEPlanStep, type StatusMessage } from '../../types';
import { stripAnswerMarkers } from '../../utils/parse-download-markers';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';
import { createMarkdownComponents } from './answer-content';
import { McpUnavailableNotices, mcpHeldTools, mcpUnusedGroups } from './mcp-unavailable-notices';
import { jobClock } from '@/lib/harness-jobs/jobs';
import { processMarkdownContent } from '../../utils/process-markdown-content';
import {
  extractToolsetLabel,
  isAskUserQuestionTool,
  describeTool,
  isSkillTool,
  skillReadLabel,
  toolActivityLabel as toolActivityLabelUtil,
  toolSubject,
} from '../../utils/tool-display';
import { toolFailureText } from '../../harness-errors';
import { approvalFromPart, budgetRefusalKey } from '../../durable-run-ui';
import { useDurableSession, useFollowsDurableRun } from '../../hooks/use-run-timeline';
import { useRunModelLabel } from '../../hooks/use-run-model-label';
import { toRunModel } from '../../utils/run-model';
import { ApprovalCard } from './approval-card';
import { InteractionReceipt } from './interaction-card';
import { connectReceipt } from './connect-card';
import { ToolUnavailableRows, uniqueUnavailable, unavailableFromSearch } from './tool-unavailable-rows';
import { JobCard, type JobCardVariant } from './job-card/job-card';
import { isJobPart, useIsJobPart } from './job-card/job-parts';
import { withEngineSteps, withoutHelpers, type ChangedFile } from '../../engine-steps';
import { partAnchors } from '../../interactions';
import { waitingSinceTime } from '../../interaction-wording';
import { ToolCallDetails, toolStatus, type ToolStatus } from './tool-result';
import { i18n } from '@/lib/i18n';
import { StepTarget } from './tool-step';
import { CodeTaskDiff } from './code-task-diff';
import { handoffLabel } from '../../handoff';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { EngineRepoRow, repoActivityText } from './engine-repo-row';
import { VirtualRows, useRowState } from '../virtual-rows';
import { useMessageRun } from '../activity-panel/use-activity-run';
import { useActivityPanelStore } from '../activity-panel/activity-panel-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export { formatToolPayload } from './tool-result';

interface AgentActivityTimelineProps {
  parts: MessagePart[];
  /** The chat draws the turn's jobs after the steps (`TurnJobs`, BGUI-2): the root timeline leaves them out. */
  liftJobs?: boolean;
  /** Nested rendering (inside a SubAgentGroup) shows every `text` part,
   * including the delegate's own `isFinal` answer — there's no separate
   * "answer" slot for a sub-agent's turn like there is for the root. */
  isNested?: boolean;
  /** While the root message is still streaming, the trailing (last) `text`
   * part is an in-progress preamble, not settled narration yet — hide it
   * until either it's superseded (a later part arrives) or the run ends
   * (at which point it's either replaced by `isFinal` or just narration). */
  isStreaming?: boolean;
  /** Current SSE status ("Thinking...", "Using Jira Search...") — rendered
   * as the last entry in the timeline (instead of a disconnected element
   * below the answer) so the "thinking -> tool -> thinking" flow reads as
   * one continuous progressive disclosure, Claude-style. Root-level only —
   * nested (sub-agent) timelines never receive this. */
  currentStatus?: StatusMessage | null;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
  /** The run's plan (UXR-B1), shown as a checklist above the steps when it has two or more. Root only. */
  plan?: SSEPlanEvent | null;
  /**
   * What the reply asks of the person (UXINT-1), drawn under the row that raised it: keyed by a tool call's id or
   * `run:<childRunId>` for a sub-agent's row. Given, the transcript's own `approval` rows are left to it.
   */
  anchored?: ReadonlyMap<string, React.ReactNode>;
  /** Interactions whose row is not drawn (yet): after the last row, in order. */
  unanchored?: React.ReactNode;
  /** The anchors (`run:<childRunId>`, a tool call's id) with a card open: those rows wait on the person, not work. */
  waiting?: ReadonlySet<string>;
}

/** True when the transcript contains any tool call, reasoning block, or
 * sub-agent delegation — i.e. this is a multi-step ReAct response, not a
 * simple single-shot answer. Drives whether the completed activity gets a
 * collapsible summary wrapper (`CollapsibleActivitySection`) and whether the
 * live status entry renders inside the timeline vs. below the answer (see
 * `ChatResponse`). */
export function hasMultiStepActivity(parts: MessagePart[] | undefined): boolean {
  if (!parts) return false;
  return parts.some(
    (part) =>
      part.type === 'tool_call' ||
      part.type === 'reasoning' ||
      part.type === 'sub_agent' ||
      part.type === 'user_input' ||
      part.type === 'slash_command' ||
      part.type === 'approval' ||
      part.type === 'engine_task',
  );
}

/**
 * Root-level text filtering:
 * - The one part marked `isFinal` (the actual answer, mirrors Python's
 *   `TranscriptCollector.replace_final_text`) is never shown here —
 *   `AnswerContent` renders it.
 * - Every other root `text` part is narration and shown once `settled`
 *   (`agui-event-handler.ts`'s `LivePartsBuilder.settleLastRootText()`,
 *   called the moment a tool call, reasoning block, or new text turn
 *   proves the text wasn't the final answer).
 * - The still-unsettled trailing text — whether or not the response has
 *   had multi-step activity — stays hidden here and streams live into
 *   `AnswerContent` instead (`ChatResponse` no longer suppresses the
 *   answer for multi-step runs). This is what makes the common case (the
 *   trailing text IS the final answer) render seamlessly in the answer
 *   area as it streams, with no jump when `RUN_FINISHED` lands. If it
 *   later turns out to be narration (a tool call/reasoning block follows),
 *   `settleLastRootText()` moves it here and clears the answer buffer in
 *   the same update.
 */
function filterRootParts(parts: MessagePart[], isStreaming: boolean): MessagePart[] {
  return withEngineSteps(parts, !isStreaming).filter((part) => {
    // Parts that paint no row must not count as activity, or the caller draws a header over an empty list.
    // The answer shows its notices above its steps, never folded away with them (`RunNotices`).
    if (part.type === 'plan' || part.type === 'notice' || part.type === 'context_compacted') return false;
    if (part.type === 'reasoning') return !!part.content?.trim();
    // The reply's sign-in card, drawn under the answer (`McpSignInCard`).
    if (part.type === 'mcp_sign_in') return false;
    if (part.type !== 'text') return true;
    if (!part.content?.trim()) return false;
    if (part.isFinal) return false;
    if (isStreaming && !part.settled) return false;
    return true;
  });
}

/** Root-level visible timeline entries for `parts` — the exact same
 * filtering `AgentActivityTimeline` applies internally for a root (non-
 * nested) render. Exposed so callers (`ChatResponse`) can decide whether to
 * render a separator / collapsible wrapper around the timeline without
 * duplicating (and risking drift from) the filtering rules above. */
export function getVisibleRootParts(parts: MessagePart[], isStreaming: boolean): MessagePart[] {
  return filterRootParts(parts, isStreaming);
}

type RenderItem =
  | { kind: 'part'; part: MessagePart; key: string }
  | { kind: 'toolGroup'; parts: MessagePart[]; key: string };

/** Groups consecutive `tool_call` parts so a burst of activity (e.g. several
 * searches in a row) collapses into one summary row instead of a wall of
 * individual cards. A lone tool call (not adjacent to another) renders
 * directly, without a group wrapper. */
function groupConsecutiveToolCalls(parts: MessagePart[]): RenderItem[] {
  const items: RenderItem[] = [];
  let i = 0;
  while (i < parts.length) {
    const part = parts[i];
    if (part.type === 'tool_call') {
      let j = i;
      while (j < parts.length && parts[j].type === 'tool_call') j += 1;
      const group = parts.slice(i, j);
      if (group.length === 1) {
        items.push({ kind: 'part', part: group[0], key: `part-${group[0].toolCallId ?? i}` });
      } else {
        items.push({ kind: 'toolGroup', parts: group, key: `group-${group[0].toolCallId ?? i}` });
      }
      i = j;
    } else {
      // One run emits several narration parts with the same runId, so the
      // index keeps their keys apart; parts are only ever appended.
      items.push({ kind: 'part', part, key: `part-${part.type}-${part.runId ?? 'root'}-${i}` });
      i += 1;
    }
  }
  return items;
}

/** True when `item` will actually paint something — filters out narration/
 * thinking entries whose `content` is blank before they ever reach a rail
 * row, so the connected line never threads through an empty placeholder
 * (`ThinkingBlock`/`NarrationText`/`LiveNarrationText` would otherwise
 * return `null` from inside an already-committed row). */
/** Enter/Space activates a `role="button"` element the same way a native
 * `<button>` would — every expand/collapse header in this file is a
 * clickable `Flex` (not a real `<button>`, for layout/style reasons), so
 * none of them get free keyboard activation from the browser. */
function handleToggleKeyDown(e: React.KeyboardEvent, toggle: () => void): void {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    toggle();
  }
}

function hasRenderableContent(item: RenderItem): boolean {
  if (item.kind === 'toolGroup') return true;
  const { part } = item;
  if (part.type === 'reasoning' || part.type === 'text') return !!part.content?.trim();
  return true;
}

type TimelineIconSpec =
  | { kind: 'icon'; node: React.ReactNode; bg: string; border: string }
  | { kind: 'dot'; color: string }
  | { kind: 'lottie'; node: React.ReactNode };


function timelineIconForStatus(status: ToolStatus, iconName: string): TimelineIconSpec {
  return {
    kind: 'icon',
    node: (
      <MaterialIcon
        name={iconName}
        size={13}
        color={TOOL_STATUS_COLOR[status]}
        style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
      />
    ),
    bg: 'var(--slate-a3)',
    border: 'var(--slate-6)',
  };
}

/** One consistent glyph per activity type for the rail node — Claude-style,
 * so the "thinking -> search -> thinking -> ..." order reads at a glance
 * from the icons alone, without opening any card. Skill tool calls
 * (`load_skill`/`skill_search`/...) get the skill icon FIRST — checked
 * ahead of the search heuristic below so `skill_search` reads as a skill
 * lookup, not a web/knowledge search. Search-flavored tool calls
 * (individual or grouped) get the magnifying-glass icon instead of the
 * generic status icon, mirroring how search steps read visually distinct
 * from other tool use. */
function getTimelineIcon(item: RenderItem): TimelineIconSpec {
  if (item.kind === 'toolGroup') {
    const allSkills = item.parts.every((p) => isSkillTool(p.toolName) || !!p.skills);
    const allSearches = !allSkills && item.parts.every((p) => isSearchLike(p.toolName));
    // The most pressing state in the group shows: a call waiting for the person, then one still
    // running, one that failed, one that was stopped.
    const statuses = item.parts.map((p) => p.status ?? 'running');
    const status: ToolStatus =
      (['awaiting_approval', 'running', 'failed', 'blocked'] as const).find((s) => statuses.includes(s)) ?? 'completed';
    const iconName = allSkills ? 'auto_stories' : allSearches ? 'travel_explore' : TOOL_STATUS_ICON[status];
    return timelineIconForStatus(status, iconName);
  }
  const { part } = item;
  switch (part.type) {
    case 'reasoning':
      return {
        kind: 'icon',
        node: <MaterialIcon name="psychology" size={13} color="var(--slate-11)" />,
        bg: 'var(--slate-a3)',
        border: 'var(--slate-6)',
      };
    case 'tool_call': {
      const status = toolStatus(part.status, 'running');
      const iconName = isSkillTool(part.toolName) || part.skills
        ? 'auto_stories'
        : isSearchLike(part.toolName)
          ? 'travel_explore'
          : TOOL_STATUS_ICON[status];
      return timelineIconForStatus(status, iconName);
    }
    case 'sub_agent':
      return timelineIconForStatus(toolStatus(part.status, 'completed'), 'smart_toy');
    case 'engine_command':
      return timelineIconForStatus(toolStatus(part.status, 'running'), part.skills ? 'auto_stories' : 'terminal');
    case 'engine_repo':
      return timelineIconForStatus(
        part.status === 'failed' ? 'failed' : 'completed',
        part.repo?.action === 'pushed' ? 'upload' : part.status === 'failed' ? 'block' : 'call_split',
      );
    case 'engine_file':
      return timelineIconForStatus('completed', part.file?.change === 'deleted' ? 'delete' : part.file?.change === 'added' ? 'note_add' : 'edit_document');
    case 'approval':
      return {
        kind: 'icon',
        node: <MaterialIcon name="front_hand" size={13} color="var(--slate-11)" />,
        bg: 'var(--slate-a3)',
        border: 'var(--slate-6)',
      };
    case 'user_input':
      return {
        kind: 'icon',
        node: <MaterialIcon name="person" size={13} color="var(--slate-11)" />,
        bg: 'var(--slate-a3)',
        border: 'var(--slate-6)',
      };
    case 'slash_command':
      return {
        kind: 'icon',
        node: <MaterialIcon name="bolt" size={13} color="var(--accent-11)" />,
        bg: 'var(--accent-a3)',
        border: 'var(--accent-6)',
      };
    default:
      // Narration (settled or live) — a plain waypoint dot rather than a
      // literal glyph, so free-flowing prose doesn't compete visually with
      // the more "actioned" steps (thinking / tools / sub-agents).
      return { kind: 'dot', color: 'var(--accent-9)' };
  }
}

/** One row of the connected activity timeline: a small icon "node" on a
 * continuous vertical line (rail) to the left, with the actual content
 * (thinking block, tool card, narration, ...) to the right — mirrors how
 * Claude visually threads its reasoning steps together instead of
 * rendering disconnected floating cards, so the read order of a
 * multi-step response is unambiguous at a glance without opening anything.
 * The line is drawn per-row (icon-to-icon-of-the-next-row) rather than as
 * one absolutely-positioned strip, so it naturally keeps pace with rows
 * that grow, shrink, or get inserted while streaming — no height
 * measurement needed. */
function TimelineRow({ icon, isLast, children }: { icon: TimelineIconSpec; isLast: boolean; children: React.ReactNode }) {
  return (
    <Flex className="agent-activity-enter">
      <Flex direction="column" align="center" style={{ width: 24, flexShrink: 0 }}>
        {icon.kind === 'dot' ? (
          <Box style={{ width: 8, height: 8, marginTop: 7, borderRadius: '50%', backgroundColor: icon.color, flexShrink: 0 }} />
        ) : icon.kind === 'lottie' ? (
          <Box style={{ width: 22, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            {icon.node}
          </Box>
        ) : (
          <Flex
            align="center"
            justify="center"
            style={{
              width: 22,
              height: 22,
              borderRadius: '50%',
              backgroundColor: icon.bg,
              border: `1px solid ${icon.border}`,
              flexShrink: 0,
            }}
          >
            {icon.node}
          </Flex>
        )}
        {!isLast && (
          <Box
            style={{
              width: 2,
              flex: 1,
              minHeight: 'var(--space-3)',
              backgroundColor: 'var(--slate-5)',
              marginTop: 'var(--space-1)',
            }}
          />
        )}
      </Flex>
      <Box style={{ flex: 1, minWidth: 0, paddingLeft: 'var(--space-3)', paddingBottom: isLast ? 0 : 'var(--space-3)' }}>
        {children}
      </Box>
    </Flex>
  );
}

/** The cards anchored on a row: each of its calls', in the row's order. */
function anchoredOn(item: RenderItem, anchored: ReadonlyMap<string, React.ReactNode> | undefined): React.ReactNode[] {
  if (!anchored) return [];
  const parts = item.kind === 'toolGroup' ? item.parts : [item.part];
  return parts.flatMap(partAnchors).flatMap((id) => (anchored.has(id) ? [<React.Fragment key={id}>{anchored.get(id)}</React.Fragment>] : []));
}

/** The hand-off's own call, when its code-task card is shown: the card is the task, its steps and its answer. */
function withoutHandedOffCalls(parts: MessagePart[]): MessagePart[] {
  const cards = parts.filter((part) => part.type === 'sub_agent' && (part.codeTaskGoal || part.codeTaskHandoff)).length;
  if (cards === 0) return parts;
  let hidden = 0;
  return parts.filter((part) => {
    if (part.type !== 'tool_call' || part.toolName !== 'code_task' || hidden >= cards) return true;
    hidden += 1;
    return false;
  });
}

const timelineItemKey = (item: RenderItem): string => item.key;
/** Collapsed, most rows are one line (a tool card's header, a short narration); measured once rendered. */
const estimateTimelineRow = (item: RenderItem): number => (item.kind === 'part' && item.part.type === 'text' ? 72 : 44);
const groupPartKey = (part: MessagePart, index: number): string => part.toolCallId ?? `call-${index}`;
const estimateToolCard = (): number => 40;

export function AgentActivityTimeline({ parts, liftJobs = false, isNested = false, isStreaming = false, currentStatus = null, citationMaps, citationCallbacks, plan = null, anchored, unanchored = null, waiting }: AgentActivityTimelineProps) {
  const checklist = !isNested ? shownPlan(plan) : null;
  const isJob = useIsJobPart();
  const visible = useMemo(() => {
    // The hand-off's own call goes with its job, so it is left out before the job is (one card hides one call).
    const handedOff = withoutHandedOffCalls(isNested ? withEngineSteps(parts, !isStreaming) : getVisibleRootParts(parts, isStreaming));
    const shown = liftJobs && !isNested ? handedOff.filter((part) => !isJob(part)) : handedOff;
    // UXINT-1: a saved approval is drawn at its tool row by the caller, as the live card is.
    return anchored ? shown.filter((part) => part.type !== 'approval') : shown;
  }, [parts, isNested, isStreaming, anchored, liftJobs, isJob]);
  // Don't show status alongside live text with content (the text itself
  // signals progress), or when the last visible entry already covers the
  // status visually (a reasoning block = "Thinking", a running tool call
  // = the same activity the status describes). A tool's own progress note
  // ("waiting in queue") is not on its row, so it is always shown.
  const hasLiveTextWithContent = isStreaming && visible.some(
    (p) => p.type === 'text' && !p.settled && !p.isFinal && !!p.content?.trim(),
  );
  const items = groupConsecutiveToolCalls(visible).filter(hasRenderableContent);
  const lastItem = items.length > 0 ? items[items.length - 1] : null;
  const lastItemCoversStatus = lastItem !== null && currentStatus?.status !== TOOL_PROGRESS_STATUS && (
    (lastItem.kind === 'part' && lastItem.part.type === 'reasoning') ||
    (lastItem.kind === 'part' && lastItem.part.type === 'tool_call' && (lastItem.part.status ?? 'running') === 'running') ||
    (lastItem.kind === 'toolGroup' && lastItem.parts.some((p) => (p.status ?? 'running') === 'running'))
  );
  // While a card waits, nothing moves until the person decides: a "Delegating…" line would say otherwise.
  const showStatus = !isNested && isStreaming && !!currentStatus && !hasLiveTextWithContent && !lastItemCoversStatus && !waiting?.size;
  if (items.length === 0 && !showStatus && !checklist && !unanchored) return null;
  const rowCount = items.length + (unanchored ? 1 : 0) + (showStatus ? 1 : 0);

  return (
    <Box style={{ marginBottom: isNested ? 'var(--space-2)' : 'var(--space-3)' }}>
      {checklist ? <PlanChecklist plan={checklist} /> : null}
      <Flex direction="column">
        <VirtualRows
          items={items}
          getKey={timelineItemKey}
          estimateSize={estimateTimelineRow}
          data-testid="agent-activity-rows"
          renderRow={(item, idx) => (
            <TimelineRow icon={getTimelineIcon(item)} isLast={idx === rowCount - 1}>
              {item.kind === 'toolGroup' ? (
                <ToolCallGroup parts={item.parts} />
              ) : (
                <AgentActivityPart
                  part={item.part}
                  isStreaming={isStreaming}
                  citationMaps={citationMaps}
                  citationCallbacks={citationCallbacks}
                  waiting={waiting}
                  {...(item.part.type === 'sub_agent' ? { asks: anchoredOn(item, anchored) } : {})}
                />
              )}
              {item.kind === 'part' && item.part.type === 'sub_agent' ? null : anchoredOn(item, anchored)}
            </TimelineRow>
          )}
        />
        {unanchored ? (
          <TimelineRow icon={getTimelineIcon({ kind: 'part', part: { type: 'approval' }, key: 'unanchored' })} isLast={rowCount === items.length + 1}>
            {unanchored}
          </TimelineRow>
        ) : null}
        {/* Status indicator as the LAST timeline entry — keeps the
            "thinking -> tool -> thinking -> ..." flow in one place instead
            of a disconnected element below the (possibly-suppressed)
            answer. See ChatResponse for the simple-response fallback. */}
        {showStatus && (
          <TimelineRow icon={{ kind: 'lottie', node: <LottieLoader variant="thinking" size={18} /> }} isLast>
            <StatusTimelineEntry status={currentStatus} />
          </TimelineRow>
        )}
      </Flex>
    </Box>
  );
}

function AgentActivityPart({ part, isStreaming, citationMaps, citationCallbacks, waiting, asks }: { part: MessagePart; isStreaming?: boolean; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks; waiting?: ReadonlySet<string>; asks?: React.ReactNode[] }) {
  const { t } = useTranslation();
  switch (part.type) {
    case 'reasoning':
      return <ThinkingBlock content={part.content ?? ''} />;
    case 'tool_call':
      // Top-level rail row — the icon lives on the rail (see
      // `getTimelineIcon`), not duplicated inline on the card itself.
      return <ToolCallCard part={part} showIcon={false} citationMaps={citationMaps} />;
    case 'sub_agent':
      return (
        <SubAgentGroup
          part={part}
          citationMaps={citationMaps}
          citationCallbacks={citationCallbacks}
          waiting={!!part.runId && !!waiting?.has(`run:${part.runId}`)}
          asks={asks}
        />
      );
    case 'user_input':
      return <SteeringMessage part={part} />;
    case 'slash_command':
      return (
        <Text size="1" style={{ color: 'var(--slate-11)' }} data-testid="slash-command-step">
          {part.commandVersion
            ? t('agentActivity.slashCommand.ranVersion', { name: part.commandName, version: part.commandVersion })
            : t('agentActivity.slashCommand.ran', { name: part.commandName })}
        </Text>
      );
    case 'engine_command':
      return <EngineCommandRow part={part} />;
    case 'engine_file':
      return part.file ? <EngineFileRow file={part.file} /> : null;
    case 'engine_repo':
      return part.repo ? <EngineRepoRow repo={part.repo} /> : null;
    case 'approval': {
      const approval = approvalFromPart(part);
      if (approval?.kind === 'connect') return <InteractionReceipt {...connectReceipt(t, approval)} />;
      return approval ? <ApprovalCard approval={approval} /> : null;
    }
    case 'text': {
      // Unsettled + still streaming: this text is being written right now
      // (a multi-step response's live "candidate" turn) — render it with a
      // typing cursor instead of the plain `NarrationText` treatment used
      // for already-settled narration. Blank content renders nothing —
      // `hasRenderableContent` filters the part out of the timeline and
      // `StatusTimelineEntry` signals progress until the first delta lands.
      const isLive = isStreaming && !part.settled && !part.isFinal;
      if (isLive) {
        return <LiveNarrationText content={part.content ?? ''} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />;
      }
      return part.content ? <NarrationText content={part.content} citationMaps={citationMaps} citationCallbacks={citationCallbacks} /> : null;
    }
    default:
      return null;
  }
}

/** How much of a command's output its opened row shows before "Show the whole output". */
const COMMAND_TAIL_LINES = 20;

const OUTPUT_STYLE: React.CSSProperties = {
  margin: 0,
  padding: 'var(--space-2)',
  borderRadius: 'var(--radius-2)',
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-5)',
  maxHeight: '240px',
  overflow: 'auto',
  fontFamily: 'monospace',
  fontSize: '12px',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
};

const LINK_BUTTON_STYLE: React.CSSProperties = {
  appearance: 'none',
  background: 'none',
  border: 'none',
  padding: 0,
  minHeight: 32,
  cursor: 'pointer',
  color: 'var(--accent-11)',
  fontSize: 'var(--font-size-1)',
};

/** One command of an engine run: "Ran `pytest -q`", how it exited, and what it printed once opened (its last lines,
 * then all of it on request). */
export function EngineCommandRow({ part }: { part: MessagePart }) {
  const { t } = useTranslation();
  // C2.32: a long output's whole log is the activity drawer's (bounded, with Download); there is no live terminal.
  const run = useMessageRun(part.runId, false);
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const [whole, setWhole] = useState(false);
  const output = part.content?.replace(/\n$/, '') ?? '';
  const lines = output ? output.split('\n') : [];
  const cut = lines.length > COMMAND_TAIL_LINES;
  const shown = whole || !cut ? output : lines.slice(-COMMAND_TAIL_LINES).join('\n');
  const failed = part.status === 'failed';
  // FIX-SKILLSHOW: a skill read is its own plain step; the command it ran (often chained) is behind it.
  const skill = part.skills ? skillReadLabel(part.skills, part.status === 'running') : null;
  const opens = !!output || skill !== null;
  const toggle = () => setOpen((value) => !value);
  return (
    <Box data-testid="engine-command" data-skill={part.skills?.join(' ')}>
      <Flex
        align="center"
        gap="2"
        role={opens ? 'button' : undefined}
        tabIndex={opens ? 0 : undefined}
        aria-expanded={opens ? open : undefined}
        onClick={opens ? toggle : undefined}
        onKeyDown={opens ? (e) => handleToggleKeyDown(e, toggle) : undefined}
        style={{ cursor: opens ? 'pointer' : 'default', minWidth: 0, minHeight: 24 }}
      >
        {skill !== null ? (
          <Text size="1" data-testid="engine-command-skill" style={{ color: 'var(--slate-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
            {skill}
          </Text>
        ) : (
          <>
            <Text size="1" style={{ color: 'var(--slate-11)', flexShrink: 0 }}>
              {part.status === 'running' ? t('agentActivity.command.running') : t('agentActivity.command.ran')}
            </Text>
            <Text size="1" asChild style={{ fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
              <code>{part.command}</code>
            </Text>
          </>
        )}
        {failed && part.exitCode !== undefined ? (
          <Text size="1" data-testid="engine-command-exit" style={{ color: 'var(--red-11)', flexShrink: 0 }}>
            {t('agentActivity.command.exit', { code: part.exitCode })}
          </Text>
        ) : null}
        {part.status === 'completed' ? <MaterialIcon name="check" size={13} color="var(--accent-11)" /> : null}
        {opens ? <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={14} color="var(--slate-11)" /> : null}
      </Flex>
      {open && skill !== null ? (
        <Text as="p" size="1" style={{ margin: 'var(--space-1) 0 0', color: 'var(--slate-11)', minWidth: 0, wordBreak: 'break-all' }}>
          {t('agentActivity.command.ran')}{' '}
          <code data-testid="engine-command-line" style={{ fontFamily: 'monospace' }}>{part.command}</code>
        </Text>
      ) : null}
      {open && output ? (
        <Box style={{ marginTop: 'var(--space-1)' }}>
          <Box asChild style={OUTPUT_STYLE} onWheel={(e) => e.stopPropagation()}>
            <pre data-testid="engine-command-output">{shown}</pre>
          </Box>
          {cut ? (
            <button type="button" style={LINK_BUTTON_STYLE} onClick={() => setWhole((value) => !value)}>
              {whole ? t('agentActivity.command.showTail') : t('agentActivity.command.showWhole')}
            </button>
          ) : null}
          {(cut || part.truncated) && run !== null ? (
            <button
              type="button"
              data-testid="engine-command-full-log"
              style={{ ...LINK_BUTTON_STYLE, marginLeft: 'var(--space-3)' }}
              onClick={() => useActivityPanelStore.getState().openRun(run, isMobile, 'commands')}
            >
              {t('agentActivity.command.fullLogInActivity')}
            </button>
          ) : null}
        </Box>
      ) : null}
    </Box>
  );
}

const FILE_VERB: Record<NonNullable<MessagePart['file']>['change'], string> = {
  added: 'agentActivity.fileStep.added',
  modified: 'agentActivity.fileStep.modified',
  deleted: 'agentActivity.fileStep.deleted',
};

/** A file an engine run created, changed or deleted: one line, its diff once opened. */
export function EngineFileRow({ file }: { file: NonNullable<MessagePart['file']> }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((value) => !value);
  return (
    <Box data-testid="engine-file">
      <Flex
        align="center"
        gap="2"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => handleToggleKeyDown(e, toggle)}
        style={{ cursor: 'pointer', minWidth: 0, minHeight: 24 }}
      >
        <Text size="1" style={{ color: 'var(--slate-11)', flexShrink: 0 }}>
          {t(FILE_VERB[file.change])}
        </Text>
        <Text size="1" asChild style={{ fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
          <code>{file.path}</code>
        </Text>
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={14} color="var(--slate-11)" />
      </Flex>
      {open ? (
        <Box style={{ marginTop: 'var(--space-1)' }}>
          <CodeTaskDiff file={file} />
        </Box>
      ) : null}
    </Box>
  );
}

/** What a running child is doing now, in one line: its latest step. Null before its first. */
export function childNow(parts: MessagePart[], t: TFunction): string | null {
  const steps = withoutHelpers(withEngineSteps(parts, false)).filter(
    (part) => part.type === 'engine_command' || part.type === 'engine_file' || part.type === 'engine_repo' || part.type === 'tool_call',
  );
  const last = steps[steps.length - 1];
  if (!last) return null;
  if (last.type === 'engine_command') {
    if (last.skills) return skillReadLabel(last.skills, last.status === 'running');
    const verb = last.status === 'running' ? t('agentActivity.command.running') : t('agentActivity.command.ran');
    return `${verb} ${last.command ?? ''}`.trim();
  }
  if (last.type === 'engine_repo' && last.repo) return repoActivityText(t, last.repo);
  if (last.type === 'engine_file' && last.file) return `${t(FILE_VERB[last.file.change])} ${last.file.path}`;
  return toolActivityLabel(last);
}

/** A message the user sent while the run worked, shown where the run took it. */
/** Notice codes this build words; an unknown one shows nothing. */
const NOTICE_KEYS: Record<string, string> = {
  mcp_unavailable: 'agentActivity.notice.mcpUnavailable',
  // A refused code_task hand-off (CTL-6) reads as the admission refusal it used to be.
  account_missing: 'chatStream.admission.accountMissing',
  model_access: 'chatStream.admission.modelAccess',
  engine_profile: 'chatStream.admission.engineProfile',
  engine_unavailable: 'chatStream.admission.engineUnavailable',
  // SIGNGAP: the person's own Claude account could not be carried here; the task ran on the org's account.
  signin_unavailable: 'agentActivity.notice.signinUnavailable',
  // CH-01: an attempt died under the task (a sandbox or server restart) and the next one carried it on.
  resumed: 'agentActivity.notice.resumed',
  // G-03: nothing the answer says was done was actually done.
  dry_run: 'agentActivity.notice.dryRun',
  // G-05: the run's checks passed when it said it was done.
  verified: 'agentActivity.notice.verified',
  // ADR-OPS-01 D6: an admin retired the task's engine version, so it is paused; then a compatible version took it on.
  engine_retired: 'agentActivity.notice.engineRetired',
  engine_continued: 'agentActivity.notice.engineContinued',
  // G-48: an engine run waited in the queue for a free sandbox before it ran.
  sandbox_queued: 'agentActivity.notice.sandboxQueued',
};

/** G-48: why a turn ran without code tools, one sentence per reason; an unknown reason reads as not available. */
const CODE_TOOLS_WITHHELD_KEYS: Record<string, string> = {
  not_available: 'agentActivity.notice.ranWithoutCodeTools.notAvailable',
  paused_by_admin: 'agentActivity.notice.ranWithoutCodeTools.pausedByAdmin',
  not_allowed: 'agentActivity.notice.ranWithoutCodeTools.notAllowed',
  isolation_unavailable: 'agentActivity.notice.ranWithoutCodeTools.isolationUnavailable',
  engine_off: 'agentActivity.notice.ranWithoutCodeTools.engineOff',
};

/** A time of day in the person's own locale and time zone ("17:20"). */
function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** Notices a later one makes stale: once a retired task carries on, "it is paused" is no longer true, and back. */
const SUPERSEDES: Readonly<Record<string, string>> = {
  engine_continued: 'agentActivity.notice.engineRetired',
  engine_retired: 'agentActivity.notice.engineContinued',
};

/** Where a person connects an item a run went without (WS-09): the pages the resources panel links to. */
const CONNECT_PATHS: Record<'toolset' | 'mcp', string> = {
  toolset: '/workspace/actions',
  mcp: '/workspace/mcp-servers/personal',
};

interface RunNotice {
  text: string;
  connect?: { href: string; label: string };
}

/**
 * What the run tells the person beside its steps (CTL-5b), e.g. that their MCP tools are not available: shown on the
 * answer, above its steps, each once; a code this build does not word shows nothing.
 */
export function RunNotices({ parts }: { parts: MessagePart[] | undefined }) {
  const { t } = useTranslation();
  const notices = new Map<string, RunNotice>();
  for (const part of parts ?? []) {
    if (part.type !== 'notice' || part.code === undefined) continue;
    // MCPNOTE-1: a server named with why it was left out has its own words and action (`McpUnavailableNotices`).
    if ((part.code === 'mcp_unavailable' && part.item) || part.code === 'mcp_tool_held') continue;
    if (part.code === 'credential_missing' && part.item) {
      // QA23-F1: the item the run went on without, and where the person connects it.
      const kind = part.kind === 'mcp' ? 'mcp' : 'toolset';
      const key = kind === 'mcp' ? 'agentActivity.notice.credentialMissingMcp' : 'agentActivity.notice.credentialMissingToolset';
      notices.set(`${key}:${part.item}`, {
        text: t(key, { item: part.item }),
        connect: { href: CONNECT_PATHS[kind], label: t('agentActivity.notice.connectItem', { item: part.item }) },
      });
      continue;
    }
    if (part.code === 'time_plan' && part.writeBy && part.finishBy) {
      // ADR-RUN-01 D4: the run's time plan in plain words; the latest one stands.
      notices.set('agentActivity.notice.timePlan', {
        text: t('agentActivity.notice.timePlan', {
          writeBy: clockTime(part.writeBy),
          finishBy: clockTime(part.finishBy),
        }),
      });
      continue;
    }
    if (part.code === 'ran_without_code_tools') {
      // G-48: the turn answered without code tools; the reason the engine was withheld, in plain words.
      const key = CODE_TOOLS_WITHHELD_KEYS[part.item ?? ''] ?? CODE_TOOLS_WITHHELD_KEYS.not_available;
      notices.set('ran_without_code_tools', { text: t(key) });
      continue;
    }
    if (part.code === 'engine_switched' && part.item && part.engine) {
      // WP-111: the chosen coding engine is off; the answer names the one that really ran, never the one that is off.
      notices.set('engine_switched', {
        text: t('agentActivity.notice.engineSwitched', {
          off: engineName(part.item),
          engine: engineName(part.engine),
        }),
      });
      continue;
    }
    if (part.code === 'model_switched' && part.item) {
      // G-48: the run's own model's provider was down; the alternate the agent allows answered, named.
      notices.set('model_switched', { text: t('agentActivity.notice.modelSwitched', { model: part.item }) });
      continue;
    }
    // G-05: a verification with its checks has the answer's own panel (`VerificationPanel`).
    if ((part.code === 'verified' || part.code === 'unverified') && (part.checks?.length ?? 0) > 0) continue;
    if (part.code === 'unverified') {
      // G-05: the first check that failed, in the gate's own plain words.
      const key = part.item ? 'agentActivity.notice.unverifiedBecause' : 'agentActivity.notice.unverified';
      notices.set('unverified', { text: t(key, { reason: part.item }) });
      continue;
    }
    const key = NOTICE_KEYS[part.code];
    if (key === undefined) continue;
    const stale = SUPERSEDES[part.code];
    if (stale !== undefined) {
      notices.delete(stale);
      notices.delete(key);
    }
    notices.set(key, { text: t(key) });
  }
  const namedMcp = mcpUnusedGroups(parts).length > 0 || mcpHeldTools(parts).length > 0;
  if (notices.size === 0 && !namedMcp) return null;
  return (
    <Flex direction="column" gap="1" style={{ marginBottom: 'var(--space-3)' }}>
      {namedMcp && <McpUnavailableNotices parts={parts} />}
      {[...notices].map(([id, notice]) => (
        <Text key={id} as="p" size="1" role="note" data-testid="run-notice" style={{ color: 'var(--amber-11)', margin: 0 }}>
          {notice.text}
          {notice.connect && (
            <>
              {' '}
              <NextLink href={notice.connect.href} style={{ color: 'var(--accent-11)', textDecoration: 'underline' }}>
                {notice.connect.label}
              </NextLink>
            </>
          )}
        </Text>
      ))}
    </Flex>
  );
}

export function SteeringMessage({ part, queuedNote }: { part: MessagePart; queuedNote?: string }) {
  const { t } = useTranslation();
  return (
    <Box
      data-testid={queuedNote ? 'steering-queued' : 'steering-message'}
      className="agent-activity-enter"
      style={{
        backgroundColor: 'var(--slate-a2)',
        border: '1px solid var(--slate-a4)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-2) var(--space-3)',
      }}
    >
      <Text as="div" size="1" style={{ color: 'var(--slate-11)', marginBottom: 2 }}>
        {t('chat.steering.you')}
      </Text>
      <Text as="div" size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {part.content ?? ''}
      </Text>
      {part.attachments && part.attachments.length > 0 && (
        <Flex gap="2" wrap="wrap" style={{ marginTop: 4 }} data-testid="steering-attachments">
          {part.attachments.map((att) => (
            <Flex key={att.virtualRecordId || att.recordId} align="center" gap="1" style={{ maxWidth: 200 }} title={att.recordName}>
              <FileIcon
                extension={getMimeTypeExtension(att.mimeType) || att.extension || undefined}
                filename={att.recordName}
                size={14}
                fallbackIcon="insert_drive_file"
              />
              <Text size="1" style={{ color: 'var(--slate-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {att.recordName}
              </Text>
            </Flex>
          ))}
        </Flex>
      )}
      {part.consumed === false && (
        <Text as="div" size="1" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {t('chat.steering.notTaken')}
        </Text>
      )}
      {queuedNote ? (
        <Text as="div" size="1" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {queuedNote}
        </Text>
      ) : null}
    </Box>
  );
}

// Backend appends "---\nConfidence: High" to raw LLM output then strips it
// via normalizedAnswer, but narration parts (flushed before a tool call) keep
// the raw text. Strip the trailer here so it doesn't render as prose.
const CONFIDENCE_TRAILER_RE = /(?:\n*-{3,}\s*\n)?\s*Confidence:\s*(?:Very High|High|Medium|Low)\s*$/i;

// Hoisted so react-markdown sees a stable `remarkPlugins` reference across
// renders instead of a fresh array literal each time (see the equivalent
// constant in `answer-content.tsx`).
const NARRATION_REMARK_PLUGINS: PluggableList = [remarkGfm];

function NarrationTextImpl({ content, citationMaps, citationCallbacks }: { content: string; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  const withoutConfidence = stripAnswerMarkers(content).replace(CONFIDENCE_TRAILER_RE, '');

  // After stripping markers, lines like `- ::artifact[...]{...}` become
  // bare `- ` or `- \n`. Remove empty list items / headings so they don't
  // render as blank bullets or orphaned headers.
  const withoutEmptyLines = withoutConfidence
    .replace(/^[ \t]*[-*+][ \t]*$/gm, '')
    .replace(/^#{1,6}\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n');

  const cleanContent = processMarkdownContent(withoutEmptyLines);
  if (!cleanContent.trim()) return null;

  const citMapsRef = useRef(citationMaps);
  citMapsRef.current = citationMaps;
  const citCbRef = useRef(citationCallbacks);
  citCbRef.current = citationCallbacks;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(() => createMarkdownComponents(citMapsRef, citCbRef), []);

  return (
    <Box className="narration-text agent-activity-enter" style={{ color: 'var(--slate-12)', fontSize: 'var(--font-size-2)', lineHeight: 1.6 }}>
      <ReactMarkdown remarkPlugins={NARRATION_REMARK_PLUGINS} urlTransform={markdownUrlTransform} components={components}>
        {cleanContent}
      </ReactMarkdown>
    </Box>
  );
}

/**
 * Settled narration text stops changing once its part is marked settled —
 * memoizing avoids re-parsing it every time an unrelated sibling part (a
 * live tool call, the still-growing live narration below) re-renders.
 */
const NarrationText = React.memo(NarrationTextImpl);

/** The still-unsettled trailing text of a multi-step response, rendered
 * live in the timeline (instead of `AnswerContent`) while its fate —
 * narration vs. the final answer — is still undetermined. The Lottie rail
 * animation (see `TimelineRow`) signals "in progress" instead of an
 * in-text cursor, so this doesn't need its own accent bar. */
function LiveNarrationTextImpl({ content, citationMaps, citationCallbacks }: { content: string; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks }) {
  const cleanContent = processMarkdownContent(stripAnswerMarkers(content));

  const citMapsRef = useRef(citationMaps);
  citMapsRef.current = citationMaps;
  const citCbRef = useRef(citationCallbacks);
  citCbRef.current = citationCallbacks;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(() => createMarkdownComponents(citMapsRef, citCbRef), []);

  if (!cleanContent.trim()) {
    // No content yet — the StatusTimelineEntry already signals progress,
    // rendering an empty cursor here causes a redundant indicator.
    return null;
  }

  return (
    <Box
      className="live-narration-text agent-activity-enter"
      style={{
        color: 'var(--slate-12)',
        fontSize: 'var(--font-size-2)',
        lineHeight: 1.6,
      }}
    >
      <ReactMarkdown remarkPlugins={NARRATION_REMARK_PLUGINS} urlTransform={markdownUrlTransform} components={components}>
        {cleanContent}
      </ReactMarkdown>
    </Box>
  );
}

const LiveNarrationText = React.memo(LiveNarrationTextImpl);

/** Status indicator ("Thinking...", "Using Jira Search...") rendered as a
 * timeline entry rather than a standalone element below the answer — see
 * `AgentActivityTimelineProps.currentStatus`. Its Lottie animation is the
 * rail's icon node (see the `showStatus` branch in `AgentActivityTimeline`)
 * so this only needs to render the shimmer text itself. */
function StatusTimelineEntry({ status }: { status: StatusMessage }) {
  return (
    <Text
      size="1"
      weight="medium"
      className="generating-shimmer"
      style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word', minWidth: 0 }}
    >
      <span className="generating-shimmer-base">{status.message}</span>
      <span className="generating-shimmer-overlay" aria-hidden="true">{status.message}</span>
    </Text>
  );
}

/** Collapsible chain-of-thought block — collapsed by default, same
 * disclosure pattern `AskUserQuestionCard` uses for its answers list. */
export function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useRowState('thinking.expanded', false);
  if (!content.trim()) return null;

  return (
    <Box
      className="agent-activity-enter"
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
          Thinking
        </Text>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-11)"
        />
      </Flex>
      {expanded && (
        <Box
          style={{
            padding: '0 var(--space-3) var(--space-3)',
            color: 'var(--slate-11)',
            fontSize: 'var(--font-size-1)',
            whiteSpace: 'pre-wrap',
          }}
        >
          {content}
        </Box>
      )}
    </Box>
  );
}

const TOOL_STATUS_ICON: Record<ToolStatus, string> = {
  running: 'sync',
  completed: 'check_circle',
  failed: 'warning',
  blocked: 'block',
  awaiting_approval: 'pending_actions',
};

// A failed call is red: in grey it read like a completed one.
const TOOL_STATUS_COLOR: Record<ToolStatus, string> = {
  running: 'var(--blue-11)',
  completed: 'var(--green-11)',
  failed: 'var(--red-11)',
  blocked: 'var(--amber-11)',
  awaiting_approval: 'var(--amber-11)',
};


export function toolActivityLabel(part: Pick<MessagePart, 'toolName' | 'displayName' | 'step' | 'skills'>): string {
  if (part.skills) return skillReadLabel(part.skills);
  // A step with typed facts names its action, whatever the engine called the tool (WP-112).
  if (part.step) return i18n.t(`toolNames.${part.step.action}.past`);
  return toolActivityLabelUtil(part.toolName, part.displayName);
}

function extractSourcesFromSummary(summary: string | undefined): string | undefined {
  if (!summary) return undefined;
  const match = summary.match(/^Sources:\s*(.+)$/m);
  return match?.[1] || undefined;
}

function isSearchLike(toolName: string | undefined): boolean {
  return !!toolName && /search/i.test(toolName);
}


/** One tool call: activity label, status icon, and (when expanded) the
 * server summary plus the raw args / bounded result preview — never the full
 * external tool payload, see MessagePart. `showIcon` is false when this renders as a top-level
 * timeline row (the rail node already carries the status icon — see
 * `getTimelineIcon`); nested usage inside an expanded `ToolCallGroup` has
 * no rail of its own, so it keeps its own inline icon (the default). */
export function ToolCallCard({
  part,
  showIcon = true,
  showUnavailable = true,
  citationMaps,
}: {
  part: MessagePart;
  showIcon?: boolean;
  /** A group shows its calls' unavailable tools once, above its folded calls. */
  showUnavailable?: boolean;
  /** The answer's citations, so a step names the [n] its result produced. */
  citationMaps?: CitationMaps;
}) {
  const [expanded, setExpanded] = useRowState(`tool.expanded:${part.toolCallId ?? ''}`, false);
  const unavailable = useMemo(() => unavailableFromSearch(part), [part]);
  const status = toolStatus(part.status, 'running');
  const toolsetLabel = extractToolsetLabel(part.toolName, part.displayName);
  const sourceInfo = extractSourcesFromSummary(part.argsSummary);

  return (
    <Box
      className="agent-activity-enter"
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {showIcon && (
          <MaterialIcon
            name={TOOL_STATUS_ICON[status]}
            size={ICON_SIZES.PRIMARY}
            color={TOOL_STATUS_COLOR[status]}
            style={status === 'running' ? { animation: 'spin 1s linear infinite' } : undefined}
          />
        )}
        {toolsetLabel && (
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-a3)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-2)',
              fontSize: '11px',
              lineHeight: '16px',
              whiteSpace: 'nowrap',
            }}
          >
            {toolsetLabel}
          </Text>
        )}
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-11)',
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {toolActivityLabel(part)}
          {part.step && <StepTarget step={part.step} citationMaps={citationMaps} />}
          {!part.step && sourceInfo && (
            <span style={{ color: 'var(--slate-11)', fontWeight: 400 }}>
              {' · '}{sourceInfo}
            </span>
          )}
        </Text>
        <StepDuration ms={part.durationMs} />
        <ApprovalChip part={part} />
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-11)"
        />
      </Flex>
      {status === 'failed' && (
        <Text
          as="p"
          size="1"
          data-testid="tool-failure"
          style={{ color: 'var(--red-11)', padding: '0 var(--space-3) var(--space-2)', margin: 0 }}
        >
          {toolFailureText(part, toolSubject(part.toolName, part.displayName))}
        </Text>
      )}
      {showUnavailable && unavailable.length > 0 && (
        <Box style={{ padding: '0 var(--space-3) var(--space-2)' }}>
          <ToolUnavailableRows tools={unavailable} />
        </Box>
      )}
      {expanded && (
        <Box
          style={{
            padding: '0 var(--space-3) var(--space-3)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-2)',
          }}
        >
          <ToolCallDetails part={part} status={status} />
        </Box>
      )}
    </Box>
  );
}


const APPROVAL_CHIP: Record<'awaiting_approval' | NonNullable<MessagePart['approvalAnswer']>, { key: string; color: 'amber' | 'green' | 'gray' }> = {
  awaiting_approval: { key: 'chat.toolCard.needsApproval', color: 'amber' },
  allowed: { key: 'chat.toolCard.approved', color: 'green' },
  declined: { key: 'chat.toolCard.declined', color: 'gray' },
  expired: { key: 'chat.toolCard.expired', color: 'gray' },
};

/** A call that went through an approval card says how it was answered. */
function ApprovalChip({ part }: { part: MessagePart }) {
  const { t } = useTranslation();
  const chip = part.approvalAnswer
    ? APPROVAL_CHIP[part.approvalAnswer]
    : part.status === 'awaiting_approval' ? APPROVAL_CHIP.awaiting_approval : null;
  if (!chip) return null;
  return (
    <Badge size="1" variant="soft" color={chip.color} style={{ flexShrink: 0 }}>
      {t(chip.key)}
    </Badge>
  );
}

/** Collapsed-by-default summary row for a burst of >= 2 consecutive tool
 * calls — e.g. "Explored 3 searches" or "Used 2 tools". Expanding reveals the
 * individual `ToolCallCard`s (which stay collapsed themselves). */
function ToolCallGroup({ parts }: { parts: MessagePart[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useRowState('group.expanded', false);
  // Skill-tool check takes precedence over the search heuristic — a burst
  // of `skill_search`/`load_skill` calls reads as "Used N skills", not
  // "Explored N searches" (see `getTimelineIcon`'s icon precedence above).
  const allSkills = parts.every((part) => isSkillTool(part.toolName));
  const allSearches = !allSkills && parts.every((part) => isSearchLike(part.toolName));
  const count = countToolCalls(parts);
  const label = allSkills
    ? `Used ${count} skills`
    : allSearches
      ? `Explored ${count} searches`
      : t('agentActivity.summary.tools', { count });

  const toolsetLabels = [...new Set(parts.map((p) => extractToolsetLabel(p.toolName, p.displayName)).filter(Boolean))] as string[];
  const groupToolsetLabel = toolsetLabels.length === 1 ? toolsetLabels[0] : undefined;
  const unavailable = useMemo(() => uniqueUnavailable(parts.flatMap(unavailableFromSearch)), [parts]);

  return (
    <Box
      className="agent-activity-enter"
      style={{
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--slate-a2)',
        overflow: 'hidden',
      }}
    >
      <Flex
        align="center"
        gap="2"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
        style={{
          padding: 'var(--space-2) var(--space-3)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {groupToolsetLabel && (
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-a3)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-2)',
              fontSize: '11px',
              lineHeight: '16px',
              whiteSpace: 'nowrap',
            }}
          >
            {groupToolsetLabel}
          </Text>
        )}
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
          {label}
        </Text>
        <MaterialIcon
          name={expanded ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.PRIMARY}
          color="var(--slate-11)"
        />
      </Flex>
      {/* UX-06: a tool the person can connect stays in sight while the group is folded. */}
      {unavailable.length > 0 && (
        <Box style={{ padding: '0 var(--space-3) var(--space-2)' }}>
          <ToolUnavailableRows tools={unavailable} />
        </Box>
      )}
      {expanded && (
        <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>
          <VirtualRows
            items={parts}
            getKey={groupPartKey}
            estimateSize={estimateToolCard}
            gap="var(--space-2)"
            renderRow={(part) => <ToolCallCard part={part} showUnavailable={false} />}
          />
        </Box>
      )}
    </Box>
  );
}

/** A sub-agent's row. A durable child is a job card (CLASSIC-JOBS D2); any other sub-agent, an engine's own helper
 * included, keeps its nested timeline, collapsed by default so its header's state is the primary signal. */
export function SubAgentGroup({ part, citationMaps, citationCallbacks, waiting = false, asks, variant = 'card' }: { part: MessagePart; citationMaps?: CitationMaps; citationCallbacks?: CitationCallbacks; waiting?: boolean; asks?: React.ReactNode[]; variant?: JobCardVariant }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useRowState(`subAgent.expanded:${part.runId ?? ''}`, false);
  const nested = part.parts ?? [];
  const status = toolStatus(part.status, 'completed');
  // A durable child keeps its own timeline on its own stream (U3): saved parts
  // name the parent, and a live durable stream is Node's projection.
  const session = useDurableSession();
  const followsDurable = useFollowsDurableRun();
  const durableChild = isJobPart(part, session, followsDurable);
  const partModel = useMemo(() => toRunModel(part.model), [part.model]);
  const modelLabel = useRunModelLabel(partModel);
  const nowOf = useCallback((parts: MessagePart[]) => childNow(parts, t), [t]);
  const renderSteps = useCallback(
    (steps: MessagePart[]) => <AgentActivityTimeline parts={steps} isNested citationMaps={citationMaps} citationCallbacks={citationCallbacks} />,
    [citationMaps, citationCallbacks],
  );
  // DEEPROUTE: a hand-off the run made on its own says why; the goal stays in the engine's own steps.
  const role = part.engineHelper
    ? part.engineHelper.task
      ? t('agentActivity.engineHelperTask', { task: part.engineHelper.task })
      : t('agentActivity.engineHelper')
    : durableChild
      ? // A job is named by what it was asked, as the Jobs page names it; a role id is not words (F10). Why it was
        // handed off is the line before the jobs (`TurnJobs`), as the design's hand-off line is.
        sharedJobTitle(part.codeTaskGoal || part.goal, () => t('jobCard.untitled'))
      : part.codeTaskHandoff
        ? handoffLabel(t, part.codeTaskHandoff)
        : part.codeTaskGoal
          ? t('agentActivity.codeTask', { goal: part.codeTaskGoal })
          : (part.roleName ?? '');

  if (durableChild && session !== null) {
    return (
      <JobCard
        part={part}
        session={session}
        title={role || t('jobCard.untitled')}
        variant={variant}
        waiting={waiting}
        asks={asks}
        nowOf={nowOf}
        renderSteps={renderSteps}
      />
    );
  }

  return (
    <>
      <Box
        className="agent-activity-enter"
        style={{
          border: `1px solid ${status === 'running' ? 'var(--blue-a5)' : 'var(--accent-a5)'}`,
          borderRadius: 'var(--radius-3)',
          backgroundColor: status === 'running' ? 'var(--blue-a2)' : 'var(--accent-a2)',
          overflow: 'hidden',
        }}
      >
        <Flex
          align="center"
          gap="2"
          role="button"
          tabIndex={0}
          aria-expanded={expanded}
          onClick={() => setExpanded((prev) => !prev)}
          onKeyDown={(e) => handleToggleKeyDown(e, () => setExpanded((prev) => !prev))}
          style={{
            padding: 'var(--space-2) var(--space-3)',
            cursor: 'pointer',
            userSelect: 'none',
          }}
        >
          <Text size="1" weight="medium" style={{ color: 'var(--accent-11)', flex: 1, minWidth: 0 }}>
            {role || 'Sub-agent'}
            {modelLabel ? (
              <Text as="span" size="1" weight="regular" color="gray" data-testid="sub-agent-model">
                {` · ${modelLabel}`}
              </Text>
            ) : null}
            {part.codeTaskHandoff && part.codeTaskGoal ? (
              <Text
                as="span"
                size="1"
                weight="regular"
                color="gray"
                data-testid="sub-agent-handoff-goal"
                title={part.codeTaskGoal}
                style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
                {part.codeTaskGoal}
              </Text>
            ) : null}
          </Text>
          {part.engineHelper ? (
            <Text size="1" color={status === 'failed' ? 'red' : 'gray'} data-testid="sub-agent-status">
              {status === 'running'
                ? t('durableRun.child.running')
                : status === 'failed'
                  ? t('durableRun.child.failed')
                  : t('durableRun.child.completed')}
            </Text>
          ) : null}
          <MaterialIcon
            name={expanded ? 'expand_less' : 'expand_more'}
            size={ICON_SIZES.PRIMARY}
            color="var(--accent-11)"
          />
        </Flex>
        {budgetRefusalKey(part.errorType) ? (
          <Text as="div" size="1" color="red" style={{ padding: '0 var(--space-3) var(--space-2)' }}>
            {t(budgetRefusalKey(part.errorType) as string)}
          </Text>
        ) : null}
        {expanded ? (
          <Box style={{ padding: '0 var(--space-3) var(--space-3)' }}>
            {nested.length > 0 ? (
              <AgentActivityTimeline parts={nested} isNested isStreaming={status === 'running'} citationMaps={citationMaps} citationCallbacks={citationCallbacks} />
            ) : (
              <Text size="1" color="gray" data-testid="sub-agent-empty">
                {status === 'running' ? t('durableRun.child.noStepsYet') : t('durableRun.child.noSteps')}
              </Text>
            )}
          </Box>
        ) : null}
      </Box>
      {asks}
    </>
  );
}

/** The plan a saved durable answer kept (its `plan` part, CTL-3b); null when it kept none. */
export function planFromParts(parts: MessagePart[] | undefined): SSEPlanEvent | null {
  const part = parts?.find((p) => p.type === 'plan' && Array.isArray(p.steps));
  return part?.steps ? { runId: part.runId ?? '', steps: part.steps, currentTaskId: null } : null;
}

/** A plan worth a checklist: two or more steps (one step is just the task). */
function shownPlan(plan: SSEPlanEvent | null | undefined): SSEPlanEvent | null {
  return plan && plan.steps.length >= 2 ? plan : null;
}

/** How long a step or run took, as a person reads it ("3s", "2m 5s"); nothing under a second (CTL-3b). */
export function formatDuration(ms: number | undefined, t: TFunction): string | null {
  if (ms === undefined || !Number.isFinite(ms) || ms < 1000) return null;
  // Past an hour, the job clock's hours and days (BGUI-11): minutes alone read "54614m 26s".
  if (ms >= 3_600_000) return jobClock(ms, (key, vars) => t(key, vars) as string);
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0
    ? t('agentActivity.duration.minutes', { minutes, seconds })
    : t('agentActivity.duration.seconds', { seconds });
}

function StepDuration({ ms }: { ms: number | undefined }) {
  const { t } = useTranslation();
  const shown = formatDuration(ms, t);
  if (!shown) return null;
  return (
    <Text size="1" data-testid="step-duration" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
      {shown}
    </Text>
  );
}

/** How many tool calls these parts made, a batch counting as its N calls: the one count the steps header and a
 * group row both show, so they never disagree (QA-FP4: "Used 1 tool" over "Ran 4 tools"). */
export function countToolCalls(parts: readonly MessagePart[]): number {
  return parts.filter((part) => part.type === 'tool_call').length;
}

/** How many kinds of step the summary names before it says how many more there were. */
const NAMED_STEPS = 3;

/**
 * What a tool step is called: what the tool says it did (a built-in's action, its written display name), else the
 * app it used and the tool ("Used Jira: Create issue"), in TOOLNAMES' words; null for a tool with neither, which the
 * summary only counts (CTL-3b).
 */
function stepLabel(part: MessagePart, t: TFunction): string | null {
  if (isAskUserQuestionTool(part.toolName)) return t('agentActivity.summary.askedYou');
  if (!part.toolName) return null;
  if (part.skills) return toolActivityLabel(part);
  const tool = describeTool(part.toolName, part.displayName);
  const written = part.displayName?.trim();
  if (tool.key || (written && !written.includes(' · '))) return toolActivityLabel(part);
  return tool.app ? t('agentActivity.summary.usedApp', { app: tool.app, tool: tool.name }) : null;
}

/**
 * The header of a run's steps, e.g. "Completed 2 of 3 steps · Searched the knowledge base (2) · 42s" (CTL-3b).
 * Counts come from the transcript itself, so it never drifts from what is shown underneath; a cancelled plan step
 * is not one left to do. The time is the saved run's, or `elapsedMs` while it runs.
 */
export function buildActivitySummary(
  rawParts: MessagePart[],
  t: TFunction,
  plan?: SSEPlanEvent | null,
  elapsedMs?: number,
  files: ChangedFile[] = [],
  cost?: string | null,
): string {
  const nested = withEngineSteps(rawParts, true);
  // An engine helper's steps count as the run's; the helper itself counts as work it delegated.
  const parts = withoutHelpers(nested);
  const labelled = new Map<string, number>();
  for (const part of parts) {
    if (part.type !== 'tool_call') continue;
    const label = stepLabel(part, t);
    if (label !== null) labelled.set(label, (labelled.get(label) ?? 0) + 1);
  }
  const calls = countToolCalls(parts);
  const thinkCount = parts.filter((p) => p.type === 'reasoning').length;
  const subAgentCount = nested.filter((p) => p.type === 'sub_agent').length;
  const steps = shownPlan(plan)?.steps.filter((step) => step.status !== 'cancelled');

  const segments: string[] = [];
  if (steps) {
    const done = steps.filter((step) => step.status === 'completed').length;
    segments.push(t('agentActivity.summary.steps', { done, total: steps.length }));
  }
  if (thinkCount > 0) segments.push(t('agentActivity.summary.thought', { count: thinkCount }));
  const commandCount = parts.filter((p) => p.type === 'engine_command').length;
  if (commandCount > 0) segments.push(t('agentActivity.summary.commands', { count: commandCount }));
  const named = [...labelled.entries()];
  for (const [label, count] of named.slice(0, NAMED_STEPS)) {
    segments.push(count > 1 ? t('agentActivity.summary.repeated', { label, count }) : label);
  }
  if (named.length > NAMED_STEPS) segments.push(t('agentActivity.summary.more', { count: named.length - NAMED_STEPS }));
  // The total, in the rows' count (QA-FP4), whenever the names above do not already account for every call.
  const namedCalls = named.slice(0, NAMED_STEPS).reduce((sum, [, count]) => sum + count, 0);
  if (calls > namedCalls) segments.push(t('agentActivity.summary.tools', { count: calls }));
  if (subAgentCount > 0) segments.push(t('agentActivity.summary.delegated', { count: subAgentCount }));
  const approvalCount = parts.filter((p) => p.type === 'approval').length;
  if (approvalCount > 0) segments.push(t('agentActivity.summary.approvals', { count: approvalCount }));
  const [file] = files;
  if (files.length === 1 && file) {
    const verb = file.change === 'added' ? 'created' : file.change === 'deleted' ? 'deleted' : 'edited';
    segments.push(t(`agentActivity.summary.${verb}`, { name: file.path.slice(file.path.lastIndexOf('/') + 1) }));
  } else if (files.length > 1) segments.push(t('agentActivity.summary.files', { count: files.length }));
  if (segments.length === 0) segments.push(t('agentActivity.summary.fallback'));
  const took = formatDuration(elapsedMs ?? parts.find((p) => p.elapsedMs !== undefined)?.elapsedMs, t);
  if (took) segments.push(took);
  // C10.05: while the run works, what it has spent so far, after its time.
  if (cost) segments.push(t('agentActivity.summary.costSoFar', { cost }));
  return segments.join(' · ');
}

const PLAN_ICON: Record<SSEPlanStep['status'], { name: string; color: string }> = {
  completed: { name: 'check_circle', color: 'var(--accent-11)' },
  in_progress: { name: 'autorenew', color: 'var(--accent-11)' },
  pending: { name: 'radio_button_unchecked', color: 'var(--slate-11)' },
  cancelled: { name: 'do_not_disturb_on', color: 'var(--slate-11)' },
};

/** The run's plan (UXR-B1) as a checklist: each step with its state, the one in progress marked current. */
export function PlanChecklist({ plan }: { plan: SSEPlanEvent }) {
  const { t } = useTranslation();
  return (
    <Box
      as="div"
      style={{ marginBottom: 'var(--space-2)' }}
    >
      <ul aria-label={t('agentActivity.plan.label')} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {plan.steps.map((step) => {
          const icon = PLAN_ICON[step.status];
          return (
            <li
              key={step.taskId}
              aria-current={step.status === 'in_progress' ? 'step' : undefined}
              style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', padding: '2px 0' }}
            >
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon
                  name={icon.name}
                  size={14}
                  color={icon.color}
                  style={step.status === 'in_progress' ? { animation: 'spin 1s linear infinite' } : undefined}
                />
              </span>
              <Text
                size="1"
                style={{
                  color: step.status === 'pending' || step.status === 'cancelled' ? 'var(--slate-11)' : 'var(--slate-12)',
                  textDecoration: step.status === 'cancelled' ? 'line-through' : undefined,
                }}
              >
                {step.title}
              </Text>
              <span style={VISUALLY_HIDDEN}>{t(`agentActivity.plan.status.${step.status}`)}</span>
            </li>
          );
        })}
      </ul>
    </Box>
  );
}

const VISUALLY_HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};

/** Whether the steps around a reader are open: what they hold may wait to load until the person opens them. */
export const ActivitySectionOpen = React.createContext(true);

/** Milliseconds since `since`, ticking each second while it is set; undefined when it is not. */
function useLiveElapsed(since: string | undefined): number | undefined {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === undefined) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [since]);
  if (since === undefined) return undefined;
  const from = Date.parse(since);
  return Number.isFinite(from) ? Math.max(0, now - from) : undefined;
}

/**
 * Wraps a run's steps in a summary line, as Claude does: open while the run works, folded into the summary when
 * it ends. A person who opened or closed it while it ran keeps their choice. `trailing` sits beside the summary
 * (the answer's "View activity").
 */
export function CollapsibleActivitySection({
  parts,
  isStreaming = false,
  plan = null,
  trailing = null,
  startedAt,
  files,
  waiting = 0,
  waitingLine,
  waitingSince,
  paused = false,
  defaultOpen = false,
  cost = null,
  children,
}: {
  parts: MessagePart[];
  /** The files the run made or changed, named in the summary (CTL-4). */
  files?: ChangedFile[];
  isStreaming?: boolean;
  plan?: SSEPlanEvent | null;
  trailing?: React.ReactNode;
  /** When the question was sent: the live summary counts the run's time from it. */
  startedAt?: string;
  /** Cards waiting for the person (UXINT-2): counted in the header, and the steps stay open so they show. */
  waiting?: number;
  /** What the one waiting step waits for, in words ("Waiting for you to reconnect GitHub", WP-64); else the count. */
  waitingLine?: string;
  /** When the longest-waiting card was asked (ISO, WP-97): the count says since when. */
  waitingSince?: string;
  /** The run is paused on them (UXINT-5): the header says so instead of a running time. */
  paused?: boolean;
  /** The person decided in these steps in this tab: they stay open once the run ends (UXINT-1). */
  defaultOpen?: boolean;
  /** C10.05: what the running run has spent so far ("$0.42"); null: nothing to show. */
  cost?: string | null;
  children: React.ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const [collapsed, setCollapsed] = useState(!isStreaming && waiting === 0 && !defaultOpen);
  const since = waitingSince !== undefined ? waitingSinceTime(waitingSince, i18n.language) : undefined;
  const touched = useRef(false);
  const wasStreaming = useRef(isStreaming);
  const liveMs = useLiveElapsed(isStreaming && !paused ? startedAt : undefined);

  // A card that starts waiting opens the steps it sits in, whatever the person chose before; once they decided
  // there, the steps stay open when the run ends, so the receipt stays where they acted (UXINT-1: no jump).
  useEffect(() => {
    if (waiting === 0) return;
    setCollapsed(false);
    touched.current = true;
  }, [waiting]);
  const summary = useMemo(
    () => buildActivitySummary(parts, t, plan, liveMs, files, isStreaming ? cost : null),
    [parts, t, plan, liveMs, files, isStreaming, cost],
  );

  useEffect(() => {
    if (wasStreaming.current && !isStreaming && !touched.current && waiting === 0) setCollapsed(true);
    wasStreaming.current = isStreaming;
  }, [isStreaming]);

  const handleToggle = useCallback(() => {
    touched.current = true;
    setCollapsed((prev) => !prev);
  }, []);

  return (
    <Box style={{ marginBottom: 'var(--space-3)' }}>
      <Flex
        align="center"
        gap="2"
        wrap="wrap"
        style={{ marginBottom: collapsed ? 0 : 'var(--space-2)', transition: 'margin-bottom 200ms ease-out' }}
      >
        <Flex
          align="center"
          gap="1"
          role="button"
          tabIndex={0}
          data-testid="activity-summary"
          aria-expanded={!collapsed}
          onClick={handleToggle}
          onKeyDown={(e) => handleToggleKeyDown(e, handleToggle)}
          style={{ cursor: 'pointer', userSelect: 'none', width: 'fit-content' }}
        >
          <MaterialIcon
            name={collapsed ? 'expand_more' : 'expand_less'}
            size={ICON_SIZES.PRIMARY}
            color="var(--slate-11)"
          />
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {paused ? t('interaction.paused') : summary}
          </Text>
        </Flex>
        {waiting > 0 ? (
          <Text
            size="1"
            weight="medium"
            data-testid="interaction-waiting-count"
            style={{ color: 'var(--amber-11)', backgroundColor: 'var(--amber-a3)', borderRadius: 999, padding: '1px 8px' }}
          >
            {waitingLine ??
              (since !== undefined
                ? t('interaction.waitingCountSince', { count: waiting, time: since })
                : t('interaction.waitingCount', { count: waiting }))}
          </Text>
        ) : null}
        {trailing}
      </Flex>
      {/* 0fr/1fr grid animates to the content's intrinsic height — no fixed
          maxHeight cap that would clip long transcripts. */}
      <div
        style={{
          display: 'grid',
          gridTemplateRows: collapsed ? '0fr' : '1fr',
          transition: 'grid-template-rows 300ms ease-out, opacity 200ms ease-out',
          opacity: collapsed ? 0 : 1,
        }}
      >
        <div
          style={{ overflow: 'hidden', minHeight: 0 }}
          aria-hidden={collapsed || undefined}
          inert={collapsed || undefined}
        >
          <ActivitySectionOpen.Provider value={!collapsed}>{children}</ActivitySectionOpen.Provider>
        </div>
      </div>
    </Box>
  );
}
