'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatApi } from '../../api';
import { approvalSendKey } from '../../approvals';
import { cardSendRunning, sendCard } from '../../card-send';
import { CardSendNotice } from './card-send-notice';
import { runStillActive, withRunEnded } from '../../durable-run-ui';
import { withEngineSteps } from '../../engine-steps';
import { useDecisionAuthority } from '../../hooks/use-decision-authority';
import { approvalAskRules } from '@/config';
import type { DurableSessionRoute } from '../../hooks/use-run-timeline';
import { useJobTimeline } from '@/lib/harness-jobs/use-job-timeline';
import type { ApprovalDecision, ChildMergeReport, MessagePart, PendingApproval, RunModelRef } from '../../types';
import { toRunModel } from '../../utils/run-model';
import { ApprovalCard } from './approval-card';
import { RunCost } from './run-cost';
import { EngineSignInCard, OwnPlanChip } from './engine-signin-card';
import { latestSignIn, onOwnPlan } from '../../engine-signin';
import { FloatedAskPointer } from '../job-ask/floated-ask-pointer';
import { signInKey, useFloatedAsks } from '../job-ask/job-asks';

export interface ChildRunPanelProps {
  session: DurableSessionRoute;
  runId: string;
  /** The job's steps as its own stream (or the parent's) carries them: shown until its timeline has more. */
  liveParts?: MessagePart[];
  /** The job ended: its timeline is read again, once, for its answer, merge report and cost. */
  ended: boolean;
  /** The viewer may read the job's timeline (its owner); a shared viewer sees the streamed steps only (D14). */
  readTimeline: boolean;
  /** Draw the job's own approvals; false when the chat already draws them on the card (D3). */
  showAsks: boolean;
  /** Changes when the job raises or settles an ask: its timeline is read again for the ask's card. */
  askKey?: string;
  /** The job's asks as its stream tells them, ahead of the last read of its timeline. */
  streamAsks?: ReadonlyArray<HeldAsk>;
  /** The model the child's own timeline names, once it has loaded: it is nearer the run than its parent's part. */
  onModel?: (model: RunModelRef) => void;
  /** The child's own steps (its parts), rendered by the caller's activity timeline. */
  renderParts: (parts: MessagePart[]) => React.ReactNode;
}

function MergeReport({ merge }: { merge: ChildMergeReport }) {
  const { t } = useTranslation();
  const kind = (value: string): string => {
    switch (value) {
      case 'target_deleted':
        return t('durableRun.merge.kind.targetDeleted');
      case 'fork_deleted':
        return t('durableRun.merge.kind.forkDeleted');
      default:
        return t('durableRun.merge.kind.bothChanged');
    }
  };
  return (
    <Box data-testid="child-merge-report">
      <Text as="div" size="1" weight="bold">
        {t('durableRun.merge.heading')}
      </Text>
      <Text as="div" size="1" color="gray">
        {merge.status === 'up_to_date'
          ? t('durableRun.merge.upToDate')
          : merge.revision === null
            ? t('durableRun.merge.notMerged')
            : t('durableRun.merge.merged', { count: merge.merged.length, revision: merge.revision })}
      </Text>
      {merge.conflicts.length > 0 ? (
        <Box>
          <Text as="div" size="1" weight="bold" style={{ marginTop: 'var(--space-1)' }}>
            {t('durableRun.merge.conflictsHeading')}
          </Text>
          <Box asChild style={{ margin: 0, paddingLeft: 'var(--space-4)' }}>
            <ul>
              {merge.conflicts.map((conflict) => (
                <li key={conflict.path}>
                  <Text size="1">
                    <span style={{ fontFamily: 'monospace' }}>{conflict.path}</span>
                    {` · ${kind(conflict.kind)}`}
                  </Text>
                </li>
              ))}
            </ul>
          </Box>
          <Text as="div" size="1" color="gray">
            {t('durableRun.merge.forkVersion', { revision: merge.forkRevision })}
          </Text>
        </Box>
      ) : null}
      {merge.repositoryConflicts.length > 0 ? (
        <Text as="div" size="1" color="gray">
          {t('durableRun.merge.repositories', { paths: merge.repositoryConflicts.join(', ') })}
        </Text>
      ) : null}
    </Box>
  );
}

/** The child's steps (its parts, an engine's progress as rows) and its answer, apart. */
export function stepsOf(parts: MessagePart[], ended: boolean): { steps: MessagePart[]; answer: MessagePart | null } {
  const rows = withEngineSteps(parts, ended).filter(
    (part) => part.type !== 'approval' && part.type !== 'plan' && part.type !== 'notice' && !(part.type === 'text' && !part.content?.trim()),
  );
  const answer = rows.find((part) => part.type === 'text' && part.isFinal) ?? null;
  return { steps: rows.filter((part) => part !== answer), answer };
}

/** An ask as the job's stream or timeline last told it: what the step it holds reads as. */
export type HeldAsk = Pick<PendingApproval, 'interactionId'> & { status: string };

/**
 * A call that waits on its ask reads as waiting on it, not running; a refused one as blocked (FUQUICK-FU-2). The
 * Activity panel folds the ask into its step the same way (`engineRunTools`); here the ask's card stays where it is.
 */
export function withHeldAsks(steps: MessagePart[], asks: ReadonlyArray<HeldAsk>): MessagePart[] {
  if (asks.length === 0) return steps;
  const status = new Map(asks.map((ask) => [ask.interactionId, ask.status] as const));
  return steps.map((step) => {
    if (step.type !== 'tool_call' || !step.interactionId || step.status !== 'running') return step;
    const ask = status.get(step.interactionId);
    if (ask === 'pending' || ask === 'sending') return { ...step, status: 'awaiting_approval' };
    if (ask === 'denied') return { ...step, status: 'blocked' };
    return step;
  });
}

/**
 * A job's inline body (CLASSIC-JOBS D2, D13): its steps, its asks and, once it ended, its answer, merge report and
 * cost. Live steps come from the job's stream through the card; the timeline is read when the body opens, when an
 * ask comes or goes, and once after the job ended, never on a timer.
 */
export function ChildRunPanel({ session, runId, liveParts = [], ended, readTimeline, showAsks, askKey = '', streamAsks, onModel, renderParts }: ChildRunPanelProps) {
  const { t } = useTranslation();
  // The job's one timeline, shared with its card's settle read and its panel (D13, ADR-CJ-01); an ask that comes or
  // goes while it runs reads it again, for the ask's card.
  const { timeline, failed, reload } = useJobTimeline(session, runId, { enabled: readTimeline, ended });
  const askSeen = useRef(askKey);
  useEffect(() => {
    if (askSeen.current === askKey) return;
    askSeen.current = askKey;
    if (readTimeline && !ended) void reload();
  }, [askKey, readTimeline, ended, reload]);
  const [decisions, setDecisions] = useState<Record<string, Pick<PendingApproval, 'status' | 'errorCode'>>>({});
  const handle = { ...(session.agentId ? { agentId: session.agentId } : {}), sessionId: session.sessionId, runId };
  const active = !ended && runStillActive(timeline?.status ?? 'running');

  const ownModel = timeline?.model;
  useEffect(() => {
    const model = toRunModel(ownModel);
    if (model) onModel?.(model);
  }, [ownModel, onModel]);

  const approvals = useMemo(() => {
    if (!showAsks) return [];
    const listed: PendingApproval[] = (timeline?.approvals ?? []).map((approval) => ({
      ...approval,
      errorCode: null,
      ...(decisions[approval.interactionId] ?? {}),
    }));
    return active ? listed : withRunEnded(listed);
  }, [timeline, decisions, active, showAsks]);
  const authority = useDecisionAuthority(
    handle,
    approvals.some((a) => a.status === 'pending'),
    approvals.some((a) => a.status === 'pending' && approvalAskRules.holds(a.reason)),
  );

  // CARDFAIL: the same send as the chat's own cards; a transient failure retries, a refusal is said on the card.
  const decide = async (interactionId: string, decision: ApprovalDecision) => {
    const key = approvalSendKey({ interactionId, runId });
    if (cardSendRunning(key)) return;
    setDecisions((all) => ({ ...all, [interactionId]: { status: 'sending', errorCode: null } }));
    const outcome = await sendCard(
      key,
      async () => {
        const sent = await ChatApi.decideDurableInteractionOutcome(handle, interactionId, decision, `${interactionId}:${decision}`);
        return 'refused' in sent ? { failed: sent.failure ?? { retry: false, code: sent.refused } } : { sent };
      },
      { again: () => void decide(interactionId, decision) },
    );
    if ('cancelled' in outcome) return;
    setDecisions((all) => ({
      ...all,
      [interactionId]:
        'sent' in outcome
          ? { status: decision, errorCode: null }
          : { status: 'pending', errorCode: 'refused' in outcome ? (outcome.refused ?? 'HTTP_AGENT_HARNESS_DECISION_FAILED') : null },
    }));
    if ('sent' in outcome) await reload();
  };

  // SIGNIN: the person's own sign-in, as the engine's lines tell it; a plan run shows no dollar cost.
  const engineFrames = useMemo(() => {
    const streamed = liveParts.find((part) => part.type === 'engine_task')?.frames ?? [];
    const read = timeline?.engine?.frames ?? [];
    return streamed.length > read.length ? streamed : read;
  }, [liveParts, timeline]);
  const signIn = useMemo(() => latestSignIn(engineFrames), [engineFrames]);
  const ownPlan = useMemo(() => onOwnPlan(engineFrames), [engineFrames]);
  const floated = useFloatedAsks();
  // The engine's steps come from its full frames (`timeline.engine`), not the part, which may hold fewer (CTL-4).
  const fromTimeline = useMemo(
    () =>
      timeline === null
        ? null
        : stepsOf(
            [
              ...(timeline.engine ? [{ type: 'engine_task' as const, runId, frames: timeline.engine.frames }] : []),
              ...timeline.parts.filter((part) => part.type !== 'engine_task'),
            ],
            !active,
          ),
    [timeline, runId, active],
  );
  const fromStream = useMemo(() => stepsOf(liveParts, false), [liveParts]);
  // The stream can be ahead of the last read of the timeline while the child runs; never behind once it ended.
  const { steps, answer } =
    fromTimeline === null || (active && fromStream.steps.length > fromTimeline.steps.length) ? fromStream : fromTimeline;
  const loading = readTimeline && timeline === null && !failed;
  // Later tellings win: the timeline's read, then the stream's, then this card's own decision.
  const held = useMemo<HeldAsk[]>(
    () => [
      ...(timeline?.approvals ?? []),
      ...(streamAsks ?? []),
      ...Object.entries(decisions).map(([interactionId, decided]) => ({ interactionId, status: decided.status })),
    ],
    [timeline, streamAsks, decisions],
  );
  const shownSteps = useMemo(() => withHeldAsks(steps, held), [steps, held]);

  return (
    <Flex direction="column" gap="2" data-testid="child-run-panel" data-run-id={runId}>
      {failed ? (
        <Text size="1" color="red">
          {t('durableRun.child.loadFailed')}
        </Text>
      ) : loading && steps.length === 0 ? (
        <Text size="1" color="gray">
          {t('durableRun.child.loading')}
        </Text>
      ) : null}
      {signIn !== null && floated.has(signInKey(runId, signIn.attemptId)) ? (
        // BGUI-4: the card over the message box holds this sign-in; one card, so the two never disagree.
        <FloatedAskPointer askKey={signInKey(runId, signIn.attemptId)} />
      ) : signIn !== null && !(ownPlan && signIn.state === 'signed_in') ? (
        <EngineSignInCard line={signIn} active={active} />
      ) : null}
      {ownPlan ? <OwnPlanChip plan={signIn?.plan ?? null} /> : null}
      {timeline?.budget && !ownPlan ? <RunCost budget={timeline.budget} /> : null}
      {approvals.map((approval) => (
        <React.Fragment key={approval.interactionId}>
          <ApprovalCard
            approval={approval}
            authority={authority}
            onDecide={active ? (decision) => void decide(approval.interactionId, decision) : undefined}
          />
          <CardSendNotice sendKey={approvalSendKey({ interactionId: approval.interactionId, runId })} />
        </React.Fragment>
      ))}
      {steps.length > 0 ? (
        renderParts(shownSteps)
      ) : !loading && !failed && answer === null ? (
        <Text size="1" color="gray" data-testid="child-run-empty">
          {active || (readTimeline && timeline === null) ? t('durableRun.child.noStepsYet') : t('durableRun.child.noSteps')}
        </Text>
      ) : null}
      {answer ? (
        <Box data-testid="child-run-answer">
          <Text as="div" size="1" weight="bold">
            {t('durableRun.child.answer')}
          </Text>
          {renderParts([answer])}
        </Box>
      ) : null}
      {timeline?.merge ? <MergeReport merge={timeline.merge} /> : null}
    </Flex>
  );
}
