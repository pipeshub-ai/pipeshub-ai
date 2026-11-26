'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Box, Button, Flex, IconButton, Link, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentTile } from '@/app/components/harness-jobs';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { approvalAskRules } from '@/config';
import { followJob, jobView, useJobsStore, type JobView, type Words } from '../activity-panel/job-data';
import { useChatJobRefs } from '../activity-panel/use-panel-job';
import { openJob } from '../activity-panel/activity-panel-store';
import { decideApproval, decideApprovals } from '../../approvals';
import { interactionTitle } from '../../interaction-wording';
import { useChatStore } from '../../store';
import { useDecisionAuthority } from '../../hooks/use-decision-authority';
import { InteractionAutoFocus, takeAskFocus, usePendingAsk } from '../../hooks/use-interaction-focus';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { ApprovalInteraction } from '../message-area/interaction-card';
import { CardSendNotice } from '../message-area/card-send-notice';
import { approvalKey } from '../../interactions';
import { EngineSignInCard } from '../message-area/engine-signin-card';
import { refusalKey } from '../message-area/approval-card';
import type { PendingApproval, SSEEngineProgressEvent } from '../../types';
import { floatKeys, jobAsksOf, useJobAskFloat, type JobAskItem } from './job-asks';
import { askOffer } from './job-ask-offer';

const NO_APPROVALS: ReadonlyArray<PendingApproval> = [];
const NO_FRAMES: ReadonlyArray<SSEEngineProgressEvent> = [];

/** The full card, opened on a desktop, scrolls past this, so the conversation above stays in view (BEHAVIORUI §14). */
const BODY_HEIGHT = 'min(360px, 40dvh)';

const TOUCH = { minHeight: 44, minWidth: 44 } as const;

/** The jobs an ask names, as their cards read them: title, agent and the step each is stopped on. */
function useAskingJobs(runIds: ReadonlyArray<string>, words: Words): Map<string, JobView> {
  const refs = useChatJobRefs();
  const streams = useJobsStore((s) => s.streams);
  const extras = useJobsStore((s) => s.extras);
  const pending = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.pendingApprovals ?? NO_APPROVALS) : NO_APPROVALS));
  const key = runIds.join('|');
  return useMemo(() => {
    const views = new Map<string, JobView>();
    for (const runId of key ? key.split('|') : []) {
      const ref = refs.find((r) => r.runId === runId) ?? { runId, partStatus: 'running' as const };
      views.set(runId, jobView(ref, { stream: streams[runId], engine: extras[runId]?.engine ?? null }, pending, words));
    }
    return views;
  }, [key, refs, streams, extras, pending, words]);
}

/**
 * A background job's ask, floating over the message box (BGUI-4; compact since BGUI-9, design `needs-you-card.tsx`,
 * shot 01b): the job's tile, which job needs you in one bold line, the action in plain words ("Create ticket in
 * E2Esaas?"), where it stopped ("Fix PAY-1423 · Step 5 of 22: … · See activity"), and the actions that do it: See
 * the job, and the decision named against the safe alternative ("Allow once" / "Keep it out"), or Sign in / Connect /
 * Review for an ask that needs more than a yes or a no. Details opens classic's whole card (in place on a desktop, a
 * bottom sheet on a phone), so every choice it offers stays (What will be sent, Allow for this chat, Deny with a
 * reason, Review each, an ask a rule decides, the sign-in code). Both answer through the same calls and
 * guard. Several asks show one at a time, "1 of N". A new ask is announced politely and never takes focus; Escape
 * returns to the message box.
 */
export function JobAskFloat() {
  const { t } = useTranslation();
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const isMobile = useIsMobile();
  const slotId = useChatStore((s) => s.activeSlotId);
  const isStreaming = useChatStore((s) => (s.activeSlotId ? Boolean(s.slots[s.activeSlotId]?.isStreaming) : false));
  const isOwner = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner !== false : false));
  const convId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.convId ?? null) : null));
  const agentId = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.threadAgentId ?? null) : null));
  const pendingApprovals = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.pendingApprovals ?? NO_APPROVALS) : NO_APPROVALS));
  const durableRun = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.durableRun ?? null) : null));
  const paused = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.interactionWaiting?.paused === true : false));

  // A live job's engine asks for its sign-in on its own stream: followed here too (shared and counted with its card).
  const refs = useChatJobRefs();
  const liveJobs = useMemo(() => refs.filter((r) => r.partStatus === 'running').map((r) => r.runId), [refs]);
  const liveKey = liveJobs.join('|');
  useEffect(() => {
    if (!convId || !isOwner || !liveKey) return undefined;
    const session = { ...(agentId ? { agentId } : {}), sessionId: convId };
    const stops = liveKey.split('|').map((runId) => followJob(session, runId, { settle: true }));
    return () => stops.forEach((stop) => stop());
  }, [convId, agentId, isOwner, liveKey]);
  const streams = useJobsStore((s) => s.streams);
  const jobs = useMemo(
    () => liveJobs.map((runId) => ({ runId, frames: streams[runId]?.ended ? NO_FRAMES : (streams[runId]?.frames ?? NO_FRAMES) })),
    [liveJobs, streams],
  );

  const items = useMemo(
    () => (isOwner ? jobAsksOf({ isStreaming, pendingApprovals, jobs }) : []),
    [isOwner, isStreaming, pendingApprovals, jobs],
  );
  const keys = useMemo(() => floatKeys(items), [items]);

  // The transcript draws a pointer for what is listed here, and the whole card again once this is gone.
  useEffect(() => {
    useJobAskFloat.getState().setFloated(keys);
  }, [keys]);
  useEffect(() => () => useJobAskFloat.getState().setFloated([]), []);

  const [index, setIndex] = useState(0);
  const at = Math.min(index, Math.max(0, items.length - 1));
  const item: JobAskItem | undefined = items[at];
  const headingRef = useRef<HTMLDivElement>(null);

  // A pointer in the transcript, or a Needs you link (`&ask=`), names an ask: turn to it. The pointer's focuses the
  // card's heading; a link's is taken by the ask's own card, which focuses its own heading.
  const wanted = useJobAskFloat((s) => s.wanted);
  const pendingAsk = usePendingAsk();
  useEffect(() => {
    if (!wanted) return;
    const found = items.findIndex((it) => floatKeys([it]).includes(wanted));
    if (found < 0) return;
    useJobAskFloat.getState().show(null);
    setIndex(found);
    requestAnimationFrame(() => headingRef.current?.focus());
  }, [wanted, items]);
  useEffect(() => {
    if (!pendingAsk) return;
    const found = items.findIndex((it) => it.kind === 'approval' && it.group.items.some((a) => a.interactionId === pendingAsk));
    if (found >= 0) setIndex(found);
  }, [pendingAsk, items]);

  const views = useAskingJobs(useMemo(() => [...new Set(items.map((it) => it.runId))], [items]), words);

  // Each new ask is said once, politely; the page's own announcer says the root run's asks.
  const [said, setSaid] = useState('');
  const heard = useRef<Set<string>>(new Set());
  useEffect(() => {
    const fresh = items.find((it) => !heard.current.has(it.key));
    for (const it of items) heard.current.add(it.key);
    if (!fresh) return;
    const job = views.get(fresh.runId)?.title ?? t('jobCard.untitled');
    setSaid(
      fresh.kind === 'signin'
        ? t('jobAsk.signIn', { job })
        : t('jobAsk.announce', { job, title: interactionTitle(t, fresh.group.items[0], fresh.group.items.length).replace(/[.?!]+$/, '') }),
    );
  }, [items, views, t]);

  const authority = useDecisionAuthority(
    durableRun,
    isStreaming && pendingApprovals.some((a) => a.childRunId && a.status === 'pending'),
    pendingApprovals.some((a) => a.childRunId && a.status === 'pending' && approvalAskRules.holds(a.reason)),
  );
  const onDecide = useCallback<Parameters<typeof ApprovalInteraction>[0]['onDecide']>(
    (card, decision, scope, reason) => {
      if (slotId) void decideApproval(slotId, card, decision, scope ?? 'once', reason);
    },
    [slotId],
  );
  const onDecideAll = useCallback<Parameters<typeof ApprovalInteraction>[0]['onDecideAll']>(
    (cards, decision, scope, reason) => {
      if (slotId) void decideApprovals(slotId, cards, decision, scope ?? 'once', reason);
    },
    [slotId],
  );

  const offer = useMemo(() => (item ? askOffer(t, item, authority) : null), [t, item, authority]);
  // The full card opens for one ask: answered (or turned away from), it closes by itself.
  const [detailsFor, setDetailsFor] = useState<string | null>(null);
  const detailsOpen = item !== undefined && detailsFor === item.key;
  const detailsToggleRef = useRef<HTMLButtonElement>(null);
  const detailsRef = useRef<HTMLDivElement>(null);
  const itemKey = item?.key ?? null;
  useEffect(() => {
    if (detailsFor === null || detailsFor === itemKey) return;
    setDetailsFor(null);
    // The full card held the focus and is gone: it goes to what is asked next, or back to the message box.
    const focus = headingRef.current ?? document.querySelector<HTMLTextAreaElement>(COMPOSER);
    requestAnimationFrame(() => focus?.focus({ preventScroll: true }));
  }, [detailsFor, itemKey]);
  const openDetails = useCallback(() => {
    if (!itemKey) return;
    setDetailsFor(itemKey);
    if (isMobile) return; // the sheet focuses its own first control
    requestAnimationFrame(() => {
      const box = detailsRef.current;
      (box?.querySelector<HTMLElement>('[data-testid="interaction-heading"][tabindex]') ?? box)?.focus({ preventScroll: true });
    });
  }, [itemKey, isMobile]);
  const closeDetails = useCallback(() => {
    setDetailsFor(null);
    requestAnimationFrame(() => detailsToggleRef.current?.focus({ preventScroll: true }));
  }, []);

  // A Needs you link (`&ask=`) names this ask: the compact card takes it, as the full card would.
  const askIds = useMemo(() => (item?.kind === 'approval' ? item.group.items.map((a) => a.interactionId) : []), [item]);
  useEffect(() => {
    if (pendingAsk && askIds.length > 0) takeAskFocus(askIds, headingRef.current);
  }, [pendingAsk, askIds]);

  // The compact card's answer is the full card's: the same calls, the same guard against a second answer.
  const decideHere = (decision: 'approved' | 'denied') => {
    if (!slotId || offer?.kind !== 'decide' || offer.cards.length === 0) return;
    if (offer.cards.length === 1) void decideApproval(slotId, offer.cards[0], decision);
    else void decideApprovals(slotId, offer.cards, decision);
  };

  const announcer = (
    <div aria-live="polite" role="status" data-testid="job-ask-announcer" style={HIDDEN}>
      {said}
    </div>
  );
  if (!item || !offer) return announcer;

  const job = views.get(item.runId);
  const title = job?.title || t('jobCard.untitled');
  const first = item.kind === 'approval' ? item.group.items[0] : null;
  const headline = paused
    ? t('jobAsk.paused', { job: title })
    : item.kind === 'signin'
      ? t('jobAsk.signIn', { job: title })
      : first?.kind === 'connect' && first.connect
        ? t('jobAsk.connect', { job: title, item: first.connect.item })
        : t('jobAsk.asking', { job: title });
  const where = job?.asking
    ? t('jobAsk.where', { job: title, n: job.asking.index + 1, total: job.asking.total, doing: job.asking.doing })
    : title;
  const canOpen = Boolean(convId);
  const headingId = 'job-ask-float-heading';
  const actionId = 'job-ask-float-action';
  const detailsId = 'job-ask-float-details';
  const touch = isMobile ? TOUCH : undefined;
  const button: CSSProperties = isMobile ? { ...TOUCH, flex: '1 1 0' } : {};
  const linkButton: CSSProperties = { flexShrink: 0, cursor: 'pointer', background: 'none', border: 'none', padding: 0, ...(isMobile ? { minHeight: 44, minWidth: 44 } : {}) };
  const decisionsShown = isMobile || !detailsOpen;

  const full = (
    <InteractionAutoFocus.Provider value={false}>
      {item.kind === 'approval' ? (
        <ApprovalInteraction
          key={item.key}
          group={item.group}
          state="active"
          onDecide={onDecide}
          onDecideAll={onDecideAll}
          authority={authority}
          conversationChoice
          subAgent
          paused={paused}
          isOwner={isOwner}
        />
      ) : (
        <EngineSignInCard key={item.key} line={item.line} active />
      )}
    </InteractionAutoFocus.Provider>
  );

  const pager =
    items.length > 1 ? (
      <Flex align="center" gap="1" data-testid="job-ask-pager" style={{ flexShrink: 0 }}>
        <IconButton size="1" variant="ghost" color="gray" aria-label={t('jobAsk.previous')} disabled={at === 0} onClick={() => setIndex(at - 1)} style={touch}>
          <MaterialIcon name="chevron_left" size={16} />
        </IconButton>
        <Text size="1" data-testid="job-ask-position" style={{ color: 'var(--gray-11)', fontVariantNumeric: 'tabular-nums' }}>
          {t('jobAsk.position', { n: at + 1, total: items.length })}
        </Text>
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          aria-label={t('jobAsk.next')}
          disabled={at >= items.length - 1}
          onClick={() => setIndex(at + 1)}
          style={touch}
        >
          <MaterialIcon name="chevron_right" size={16} />
        </IconButton>
      </Flex>
    ) : null;

  const text = (
    <Flex direction="column" style={{ flex: 1, minWidth: 0, gap: 2 }}>
      <Text
        as="div"
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        size="2"
        weight="medium"
        data-testid="job-ask-headline"
        style={{ color: 'var(--gray-12)', outline: 'none', ...CLAMP }}
      >
        {headline}
      </Text>
      <Text as="div" id={actionId} size="2" data-testid="job-ask-action" style={{ color: 'var(--gray-11)', ...CLAMP }}>
        {offer.action}
      </Text>
      {offer.errorCode ? (
        <Text size="1" color="red" role="alert" data-testid="job-ask-refused">
          {t(refusalKey(offer.errorCode) ?? 'approvalCard.failed')}
        </Text>
      ) : null}
      {/* CARDFAIL: the compact card's answer is sent as the full card's; it says the same while it is not sent (the
          full card, once open in Details, draws its own). */}
      {item.kind === 'approval' && !detailsOpen ? <CardSendNotice sendKey={item.group.items.map((a) => approvalKey(a))} /> : null}
      <Flex align="center" gap="1" style={{ minWidth: 0 }}>
        <Text size="1" data-testid="job-ask-where" style={{ minWidth: 0, color: 'var(--gray-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {where}
        </Text>
        {canOpen ? (
          <>
            <Text size="1" aria-hidden="true" style={{ color: 'var(--gray-11)', flexShrink: 0 }}>
              ·
            </Text>
            <Link asChild size="1" color="gray" underline="always">
              <button type="button" data-testid="job-ask-see-activity" onClick={() => openJob(item.runId, 'activity')} style={linkButton}>
                {t('jobAsk.seeActivity')}
              </button>
            </Link>
          </>
        ) : null}
        <Text size="1" aria-hidden="true" style={{ color: 'var(--gray-11)', flexShrink: 0 }}>
          ·
        </Text>
        <Link asChild size="1" color="gray" underline="always">
          <button
            type="button"
            ref={detailsToggleRef}
            data-testid="job-ask-details-toggle"
            aria-expanded={detailsOpen}
            aria-controls={!isMobile && detailsOpen ? detailsId : undefined}
            aria-haspopup={isMobile ? 'dialog' : undefined}
            onClick={() => (detailsOpen ? closeDetails() : openDetails())}
            style={linkButton}
          >
            {detailsOpen && !isMobile ? t('jobAsk.hideDetails') : t('jobAsk.details')}
          </button>
        </Link>
      </Flex>
    </Flex>
  );

  const actions = (
    <Flex align="center" gap="2" wrap={isMobile ? 'wrap' : 'nowrap'} style={isMobile ? { width: '100%' } : { flexShrink: 0 }}>
      {canOpen ? (
        <Button size={isMobile ? '2' : '1'} variant="soft" color="gray" data-testid="job-ask-see-job" onClick={() => openJob(item.runId)} style={button}>
          {t('jobAsk.seeJob')}
        </Button>
      ) : null}
      {!decisionsShown ? null : offer.kind === 'decide' ? (
        <>
          <Button
            size={isMobile ? '2' : '1'}
            variant="soft"
            color="gray"
            data-testid="job-ask-keep"
            disabled={offer.sending}
            aria-label={offer.keep.name}
            onClick={() => decideHere('denied')}
            style={button}
          >
            {offer.keep.text}
          </Button>
          <Button
            size={isMobile ? '2' : '1'}
            data-testid="job-ask-allow"
            disabled={offer.sending}
            aria-label={offer.allow.name}
            onClick={() => decideHere('approved')}
            style={button}
          >
            {offer.sending ? t('approvalCard.sending') : offer.allow.text}
          </Button>
        </>
      ) : (
        <Button size={isMobile ? '2' : '1'} data-testid="job-ask-open" aria-haspopup={isMobile ? 'dialog' : undefined} onClick={openDetails} style={button}>
          {offer.primary}
        </Button>
      )}
    </Flex>
  );

  return (
    <>
      {announcer}
      <Box
        role="region"
        aria-labelledby={`${headingId} ${actionId}`}
        data-testid="job-ask-float"
        data-kind={item.kind}
        data-offer={offer.kind}
        data-ask-key={item.key}
        onKeyDown={(e) => {
          if (e.key !== 'Escape') return;
          e.preventDefault();
          if (detailsOpen && !isMobile) {
            closeDetails();
            return;
          }
          document.querySelector<HTMLTextAreaElement>(COMPOSER)?.focus();
        }}
        style={{
          marginBottom: 'var(--space-2)',
          padding: 'var(--space-3)',
          background: 'var(--color-panel-solid)',
          border: '1px solid var(--olive-6)',
          borderRadius: 'var(--radius-3)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        {isMobile ? (
          <Flex direction="column" gap="2">
            <Flex align="start" gap="3">
              <AgentTile agent={job?.agent ?? 'Background agent'} size={28} status="needsYou" />
              {text}
              {pager}
            </Flex>
            {actions}
          </Flex>
        ) : (
          <Flex align="center" gap="3">
            <AgentTile agent={job?.agent ?? 'Background agent'} size={28} status="needsYou" />
            {text}
            {pager}
            {actions}
          </Flex>
        )}
        {detailsOpen && !isMobile ? (
          <Box
            ref={detailsRef}
            id={detailsId}
            tabIndex={-1}
            data-testid="job-ask-details"
            style={{ maxHeight: BODY_HEIGHT, overflowY: 'auto', marginTop: 'var(--space-2)', outline: 'none' }}
          >
            {full}
          </Box>
        ) : null}
      </Box>
      {isMobile ? (
        <MobileBottomSheet open={detailsOpen} onOpenChange={(open) => (open ? undefined : closeDetails())} title={headline} maxHeight="85dvh">
          <Box data-testid="job-ask-details">{full}</Box>
        </MobileBottomSheet>
      ) : null}
    </>
  );
}

const COMPOSER = 'textarea.chat-input-textarea';

/** Two lines at most: the full words are one click down, in Details. */
const CLAMP: CSSProperties = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' };

const HIDDEN: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};
