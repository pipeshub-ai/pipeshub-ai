'use client';

import React, { useMemo, useRef, useState } from 'react';
import { Box, Button, Card, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { previewPayload } from '../../approval-preview';
import { DisplaySafetyNote, MarkedText } from './display-safety';
import { allowConversationText, grantDestination, interactionTitle, interactionWhy } from '../../interaction-wording';
import { approvalKey, isOpenApproval, isOpenGroup, receiptCounts, type ApprovalGroup } from '../../interactions';
import { CardSendNotice } from './card-send-notice';
import { useInteractionFocus } from '../../hooks/use-interaction-focus';
import { ApprovalAskNote, approvalAskRules } from '@/config';
import { mayApprove } from '../../approval-authority';
import type { ApprovalDecision, ApprovalScope, DecisionAuthority, PendingApproval } from '../../types';
import {
  activeCardStyle,
  ApprovalCard,
  ChildAttribution,
  HEADING_FOCUS,
  offersConversation,
  PausedLine,
  refusalKey,
  TARGET,
  VISUALLY_HIDDEN,
  WhatWillBeSent,
} from './approval-card';
import { UnknownEffectCard } from './unknown-effect-card';
import { ConnectCard, connectReceipt } from './connect-card';
import { isPlanApproval, PlanApprovalCard, planOf, planReceipt } from './run-gate-cards';
import { DenyReasonForm, EffectCost } from './approval-effect';
import { EditedReceipt } from './approval-edit';
import { editedDestination } from '../../approval-edits';

/** Where a card stands in the reply (UXINT-2): the one call to action, waiting its turn, or decided. */
export type InteractionState = 'active' | 'queued' | 'done';

type Tone = 'ok' | 'denied' | 'muted';
const TONE: Record<Tone, { icon: string; color: string }> = {
  ok: { icon: 'check', color: 'var(--accent-11)' },
  denied: { icon: 'close', color: 'var(--red-11)' },
  muted: { icon: 'remove', color: 'var(--slate-11)' },
};

/** One line where the card was: "✓ Allowed once by you", "✕ Denied by you · nothing was sent" (UXINT-2). */
export function InteractionReceipt({ tone, text, status }: { tone: Tone; text: string; status: string }) {
  return (
    <Flex align="center" gap="1" data-testid="interaction-receipt" data-status={status} style={{ marginTop: 'var(--space-1)' }}>
      <MaterialIcon name={TONE[tone].icon} size={14} color={TONE[tone].color} />
      <Text size="1" style={{ color: tone === 'denied' ? 'var(--red-11)' : 'var(--slate-11)' }}>
        {text}
      </Text>
    </Flex>
  );
}

/** A later card while an earlier one waits: one line, no buttons, so only one thing asks at a time. */
export function QueuedInteraction({ title }: { title: string }) {
  const { t } = useTranslation();
  return (
    <Box
      data-testid="interaction-queued"
      style={{
        marginTop: 'var(--space-1)',
        border: '1px dashed var(--slate-7)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-1) var(--space-2)',
      }}
    >
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t('interaction.queued', { title })}
      </Text>
    </Box>
  );
}

/** The receipt of a decided card or group. */
function approvalReceipt(t: ReturnType<typeof useTranslation>['t'], group: ApprovalGroup): { tone: Tone; text: string; status: string } {
  const n = group.items.length;
  const c = receiptCounts(group);
  const allowed = c.approved + c.conversation;
  if (group.items[0]?.kind === 'unknown_effect') return { tone: 'ok', text: t('interaction.receipt.decided'), status: 'decided' };
  // AP-06: the person changed what ran; the receipt says so and shows the change under it.
  if (n === 1 && group.items[0]?.status === 'approved' && group.items[0].editedArgs)
    return { tone: 'ok', text: t('interaction.receipt.approvedWithChanges'), status: 'approved' };
  if (c.conversation > 0 && c.denied === 0 && c.lapsed === 0) {
    // FLOWGRANT: the receipt names what the grant covers, as the button did.
    const destination = grantDestination(group.items);
    return {
      tone: 'ok',
      text: destination
        ? t('interaction.receipt.allowedConversationFor', { destination })
        : t('interaction.receipt.allowedConversation', { count: n }),
      status: 'approved',
    };
  }
  if (allowed === n) return { tone: 'ok', text: t('interaction.receipt.allowed', { count: n }), status: 'approved' };
  if (c.denied === n) {
    // AP-05: the person's own reason, as they gave it, when they gave one.
    const reason = group.items.find((item) => item.denyReason)?.denyReason;
    return reason
      ? { tone: 'denied', text: t('interaction.receipt.deniedBecause', { reason }), status: 'denied' }
      : { tone: 'denied', text: t('interaction.receipt.denied', { count: n }), status: 'denied' };
  }
  if (c.lapsed === n) return { tone: 'muted', text: t('interaction.receipt.lapsed'), status: 'expired' };
  return { tone: 'ok', text: t('interaction.receipt.mixed', { allowed, denied: c.denied + c.lapsed }), status: 'mixed' };
}

/**
 * One line per call of a group, never the raw JSON: the first field whose value differs across the group (the title
 * when every call shares a body), else the first field (the prompt, the query).
 */
function itemLines(items: PendingApproval[]): Map<string, string> {
  const fields = items.map((item) => previewPayload(item.args, 120));
  const key = (fields[0] ?? []).find((f) => fields.some((other) => other.find((o) => o.key === f.key)?.full !== f.full))?.key;
  return new Map(
    items.map((item, i) => {
      const field = fields[i].find((f) => f.key === key) ?? fields[i][0];
      return [item.interactionId, field?.value ?? item.argsSummary ?? item.toolName];
    }),
  );
}

const SHOWN_ON_PHONE = 3;

/**
 * A batch's cost (UX-03), only when every call declares one: "about $0.03" for 3 calls at $0.0079 each, or the sum
 * when the calls cost differently. One call without a declared cost means no estimate, never a guess.
 */
function groupCost(items: PendingApproval[]): { usd: number; calls: number } | null {
  const costs = items.map((item) => item.effect?.costEstimate?.usd);
  if (costs.length === 0 || costs.some((usd) => usd === undefined)) return null;
  const each = costs as number[];
  return each.every((usd) => usd === each[0]) ? { usd: each[0], calls: each.length } : { usd: each.reduce((a, b) => a + b, 0), calls: 1 };
}

interface DecideProps {
  onDecide: (item: PendingApproval, decision: ApprovalDecision, scope?: ApprovalScope, reason?: string) => void;
  /** "Allow all N" / "Deny all": the batch's open calls in one request (DECIDE409); a denial may carry a reason. */
  onDecideAll: (
    items: PendingApproval[],
    decision: Extract<ApprovalDecision, 'approved' | 'denied'>,
    scope?: ApprovalScope,
    reason?: string,
  ) => void;
  authority?: DecisionAuthority;
  /** The conversation's own chat may offer "Allow for this conversation" (UX1), a sub-agent's card too (FLOWGRANT). */
  conversationChoice: boolean;
  /**
   * A sub-agent's card: its grant reaches the whole conversation (FLOWGRANT), but "Don't ask again" (a rule saved
   * from the run the card belongs to) and editing stay with the conversation's own run.
   */
  subAgent?: boolean;
  paused: boolean;
  /** AP-06: approve one call with the person's changes. Absent: the card offers no Edit. */
  onApproveEdited?: (item: PendingApproval, edits: Record<string, unknown>) => void;
  /** The viewer is the person the task runs for (a connect card is theirs to connect); unknown: treated as them. */
  isOwner?: boolean;
}

/**
 * Calls of one batch to the same tool under the same rule (D2): one card, "Generate 7 images?", the list of what
 * each would do, and Allow all / Review each / Deny. "Allow all" is one request for every open call (DECIDE409:
 * a request per call raced on the run), journaled and audited per call; "Review each" opens a row per call.
 */
function ApprovalGroupCard({ group, active, onDecide, onDecideAll, authority, conversationChoice, paused }: { group: ApprovalGroup; active: boolean } & DecideProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const open = group.items.filter(isOpenApproval);
  // WP-113: an open card lists, counts and allows only the calls still waiting; decided ones have their receipts.
  const items = open.length > 0 ? open : group.items;
  const [first] = items;
  const n = items.length;
  const [reviewing, setReviewing] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [denying, setDenying] = useState(false);
  const headingRef = useRef<HTMLDivElement>(null);
  const sending = open.some((item) => item.status === 'sending');
  // "Allow all" is refused whole when one call is not the viewer's to approve (an ask a rule decides).
  const canApprove = items.every((item) => mayApprove(authority, item.reason));
  const canDeny = authority?.deny ?? true;
  const ruleReason = items.find((item) => approvalAskRules.holds(item.reason))?.reason;
  const waitsOnRule = !canApprove && ruleReason !== undefined;
  // ADR-GRANT-01 D9: allowing calls for the whole chat is the run's own person's decision, never another participant's.
  const offerConversation = conversationChoice && offersConversation(first.reason) && canApprove && authority?.edit !== false;
  const title = interactionTitle(t, first, n);
  const why = interactionWhy(t, first, n);
  const ids = `group-${group.key}`.replace(/[^\w-]/g, '-');
  const decideAll = (decision: 'approved' | 'denied', scope?: ApprovalScope, reason?: string) => {
    const pending = open.filter((item) => item.status === 'pending');
    if (pending.length === 0) return;
    if (reason) onDecideAll(pending, decision, scope, reason);
    else onDecideAll(pending, decision, scope);
  };
  const cost = groupCost(items);
  const isActive = active && open.length > 0;
  const askIds = useMemo(() => items.map((item) => item.interactionId), [items]);
  const { onKeyDown } = useInteractionFocus(isActive, headingRef, canApprove && !sending && !reviewing && !denying ? () => decideAll('approved') : null, askIds);
  const errors = items.map((item) => item.errorCode).filter((code): code is string => Boolean(code));
  // Reviewing each call, the person sees the ones they just decided beside the rest, each with its receipt.
  const listed = reviewing ? group.items : items;
  const shown = isMobile && !showAll && !reviewing ? listed.slice(0, SHOWN_ON_PHONE) : listed;
  const lines = useMemo(() => itemLines(listed), [listed]);
  const itemLine = (item: PendingApproval) => lines.get(item.interactionId) ?? item.toolName;
  // SEC-B-04: "Allow all" covers every call, so the note reads every call's fields, not only the rows shown.
  const shownTexts = useMemo(
    () => [
      title,
      ...items.flatMap((item) => previewPayload(item.args, Number.MAX_SAFE_INTEGER).map((field) => `${field.label} ${field.full}`)),
    ],
    [title, items],
  );

  return (
    <Card
      role="region"
      aria-labelledby={`${ids}-title`}
      aria-describedby={[why ? `${ids}-why` : null, isActive ? `${ids}-keys` : null].filter(Boolean).join(' ') || undefined}
      data-testid="approval-group"
      data-count={n}
      data-status={open.length > 0 ? 'pending' : 'decided'}
      data-interaction-active={isActive ? 'true' : undefined}
      onKeyDown={onKeyDown}
      style={{ marginTop: 'var(--space-2)', ...activeCardStyle(isActive) }}
    >
      <Flex direction="column" gap="3">
        {first.childRunId ? <ChildAttribution approval={first} /> : null}
        <Flex align="center" gap="2">
          <MaterialIcon name={first.reason?.rule === 'flow_external_write' ? 'shield' : 'front_hand'} size={18} color="var(--accent-11)" />
          <Text as="div" id={`${ids}-title`} ref={headingRef} tabIndex={-1} size="2" weight="bold" data-testid="interaction-heading" style={HEADING_FOCUS}>
            <MarkedText text={title} />
          </Text>
        </Flex>
        {paused ? <PausedLine /> : null}
        {why ? (
          <Text id={`${ids}-why`} size="2" data-testid="approval-why">
            {why}
          </Text>
        ) : null}
        <DisplaySafetyNote texts={shownTexts} />
        <Box
          style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--olive-3)', maxHeight: 360, overflow: 'auto' }}
        >
          <Text as="div" size="1" weight="bold" style={{ marginBottom: 'var(--space-1)' }}>
            {t('approvalCard.flow.willSend')}
          </Text>
          <ol data-testid="approval-group-items" style={{ margin: 0, paddingLeft: 'var(--space-5)' }}>
            {shown.map((item) => (
              <li key={item.interactionId} data-testid="approval-group-item" data-status={item.status} style={{ marginBottom: 'var(--space-1)' }}>
                <Flex align="center" justify="between" gap="2" wrap="wrap">
                  <Text size="2" style={{ wordBreak: 'break-word', minWidth: 0, flex: '1 1 12rem' }}>
                    <MarkedText text={itemLine(item)} />
                  </Text>
                  {reviewing ? (
                    isOpenApproval(item) ? (
                      <Flex gap="2">
                        {canDeny ? (
                          <Button size="1" variant="soft" color="gray" disabled={item.status === 'sending'} style={TARGET} onClick={() => onDecide(item, 'denied')} aria-label={t('interaction.denyItem', { item: itemLine(item) })}>
                            {t('approvalCard.flow.deny')}
                          </Button>
                        ) : null}
                        {mayApprove(authority, item.reason) ? (
                          <Button size="1" variant="soft" disabled={item.status === 'sending'} style={TARGET} onClick={() => onDecide(item, 'approved')} aria-label={t('interaction.allowItem', { item: itemLine(item) })}>
                            {t('approvalCard.flow.allowOnce')}
                          </Button>
                        ) : null}
                      </Flex>
                    ) : (
                      <InteractionReceipt {...approvalReceipt(t, { key: item.interactionId, items: [item] })} />
                    )
                  ) : null}
                </Flex>
              </li>
            ))}
          </ol>
          {shown.length < listed.length ? (
            <Button variant="ghost" size="1" onClick={() => setShowAll(true)} style={{ ...TARGET, marginTop: 'var(--space-1)' }}>
              {t('interaction.moreItems', { count: listed.length - shown.length })}
            </Button>
          ) : null}
        </Box>
        {cost ? <EffectCost usd={cost.usd} calls={cost.calls} /> : null}
        {errors.length > 0 ? (
          <Text size="2" color="red" role="alert">
            {t(refusalKey(errors[0]) ?? 'approvalCard.failed')}
          </Text>
        ) : null}
        {open.length > 0 ? (
          <ApprovalAskNote
            reason={ruleReason}
            canApprove={canApprove}
            {...(authority !== undefined ? { authority } : {})}
          />
        ) : null}
        {!canApprove && !canDeny ? (
          waitsOnRule ? null : (
            <Text size="2" color="gray">
              {t('approvalCard.noAuthority')}
            </Text>
          )
        ) : denying && canDeny ? (
          <DenyReasonForm
            disabled={sending}
            onCancel={() => setDenying(false)}
            onDeny={(reason) => decideAll('denied', undefined, reason)}
            denyLabel={t('interaction.denyAllLabel', { title })}
            denyText={t('interaction.denyAll', { count: open.length })}
          />
        ) : (
          <Flex gap="2" justify="end" wrap="wrap" direction={isMobile ? 'column-reverse' : 'row'}>
            <Button variant="ghost" onClick={() => setReviewing((v) => !v)} aria-expanded={reviewing} style={TARGET} data-testid="review-each">
              {reviewing ? t('interaction.hideEach') : t('interaction.reviewEach')}
            </Button>
            {canDeny && !reviewing ? (
              <Button variant="soft" color="gray" disabled={sending} onClick={() => setDenying(true)} style={TARGET}>
                {t('interaction.denyAll', { count: open.length })}
              </Button>
            ) : null}
            {offerConversation && !reviewing ? (
              <Button variant="soft" disabled={sending} onClick={() => decideAll('approved', 'conversation')} style={TARGET} data-testid="allow-conversation">
                {allowConversationText(t, grantDestination(items))}
              </Button>
            ) : null}
            {canApprove && !reviewing ? (
              <Button disabled={sending} onClick={() => decideAll('approved')} style={TARGET} data-testid="allow-all">
                {sending ? t('approvalCard.sending') : t('interaction.allowAll', { count: open.length })}
              </Button>
            ) : null}
          </Flex>
        )}
        {isActive ? (
          <span id={`${ids}-keys`} style={VISUALLY_HIDDEN}>
            {t('interaction.keysHintAll', { count: open.length })}
          </span>
        ) : null}
      </Flex>
    </Card>
  );
}

/**
 * An approval card or group at its anchor: active is the full card, queued one line, decided one receipt line.
 * The full card stays in "View activity".
 */
/**
 * One approval card of any kind, with what its send says (CARDFAIL): every kind (a call, a batch, a connect, a plan,
 * an unknown effect, a job's ask) sends through `sendCard`, so each shows the same notice under it.
 */
export function ApprovalInteraction(props: { group: ApprovalGroup; state: InteractionState } & DecideProps) {
  const keys = useMemo(() => props.group.items.map((item) => approvalKey(item)), [props.group.items]);
  const open = isOpenGroup(props.group);
  return (
    <>
      <ApprovalInteractionCard {...props} />
      {open && props.state !== 'queued' ? (
        <Box mt="2">
          <CardSendNotice sendKey={keys} />
        </Box>
      ) : null}
    </>
  );
}

function ApprovalInteractionCard({ group, state, ...decide }: { group: ApprovalGroup; state: InteractionState } & DecideProps) {
  const { t } = useTranslation();
  const [first] = group.items;
  const title = useMemo(() => interactionTitle(t, editedDestination(first), group.items.length).replace(/\?$/, ''), [t, first, group.items.length]);
  if (first.kind === 'connect') {
    // G-12: its own card, never batched; its receipt names the item, not a decision on a call.
    if (!isOpenGroup(group)) return <InteractionReceipt {...connectReceipt(t, first)} />;
    if (state === 'queued') return <QueuedInteraction title={t('connectCard.title', { item: first.connect?.item ?? '' })} />;
    return (
      <ConnectCard
        card={first}
        isOwner={decide.isOwner !== false}
        active={state === 'active'}
        paused={decide.paused}
        onDecide={(decision) => decide.onDecide(first, decision, 'once')}
      />
    );
  }
  if (group.items.length === 1 && isPlanApproval(first) && planOf(first.args)) {
    // G-04: the run's plan reads as a plan (summary, numbered steps), decided with Approve plan / Decline.
    if (!isOpenGroup(group)) return <InteractionReceipt {...planReceipt(t, first)} />;
    if (state === 'queued') return <QueuedInteraction title={t('runGates.plan.title')} />;
    return (
      <PlanApprovalCard
        approval={first}
        canApprove={mayApprove(decide.authority, first.reason)}
        canDeny={decide.authority?.deny ?? true}
        active={state === 'active'}
        paused={decide.paused}
        onDecide={(decision, reason) =>
          reason ? decide.onDecide(first, decision, 'once', reason) : decide.onDecide(first, decision, 'once')
        }
      />
    );
  }
  if (!isOpenGroup(group)) {
    // A row can hold several calls (consecutive calls fold into one row): the receipt names which one it was.
    const receipt = approvalReceipt(t, group);
    const line = <InteractionReceipt {...receipt} text={t('interaction.receipt.named', { title, decision: receipt.text })} />;
    if (group.items.length !== 1 || first.status !== 'approved' || !first.editedArgs) return line;
    return (
      <Box>
        {line}
        <Box style={{ marginTop: 'var(--space-1)', paddingLeft: 'var(--space-4)' }}>
          <EditedReceipt
            args={first.args}
            editedArgs={first.editedArgs}
            renderAll={(args) => <WhatWillBeSent args={args} />}
          />
        </Box>
      </Box>
    );
  }
  if (state === 'queued') return <QueuedInteraction title={title} />;
  // UXINT-0 D2: calls that waited together stay one card until the last is decided, in any order ("Review each");
  // the card lists and counts only what still waits (WP-113), and grouping keeps apart asks that did not wait together.
  if (group.items.length > 1) return <ApprovalGroupCard group={group} active={state === 'active'} {...decide} />;
  const only = first;
  if (only.kind === 'unknown_effect') {
    return <UnknownEffectCard effect={only} onDecide={(decision) => decide.onDecide(only, decision, 'once')} />;
  }
  return (
    <ApprovalCard
      approval={only}
      authority={decide.authority}
      conversationChoice={decide.conversationChoice}
      ruleChoice={decide.conversationChoice && decide.subAgent !== true}
      active={state === 'active'}
      paused={decide.paused}
      editAllowed={decide.conversationChoice && decide.subAgent !== true && decide.authority?.edit === true}
      {...(decide.onApproveEdited ? { onApproveEdited: (edits: Record<string, unknown>) => decide.onApproveEdited?.(only, edits) } : {})}
      onDecide={(decision, scope, reason) =>
        reason ? decide.onDecide(only, decision, scope, reason) : decide.onDecide(only, decision, scope)
      }
    />
  );
}
