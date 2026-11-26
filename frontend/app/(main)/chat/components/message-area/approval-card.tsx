'use client';

import React, { useMemo, useRef, useState } from 'react';
import { Badge, Box, Button, Card, Checkbox, Flex, Link, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { maskedReason, previewPayload, safeText, sourceHref, type PreviewField } from '../../approval-preview';
import { DisplaySafetyNote, MarkedText } from './display-safety';
import {
  allowConversationText,
  childAttribution,
  cloneDetails,
  EXTERNAL_POLICY_RULE,
  interactionAction,
  interactionTitle,
  interactionWhy,
  isClone,
  isSiteUpload,
  SITE_UPLOAD_RULE,
} from '../../interaction-wording';
import { useInteractionFocus } from '../../hooks/use-interaction-focus';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { HoldRule, RuleLayer } from '@/lib/contracts/harness/generated/core/enums';
import type {
  ApprovalDecision,
  ApprovalReason,
  ApprovalScope,
  DecisionAuthority,
  PendingApproval,
} from '../../types';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';
import { DenyReasonForm, EffectCost, EffectDiff } from './approval-effect';
import { ApprovalEditForm, EditedReceipt, SecretInEditNotice } from './approval-edit';
import { editedDestination, editFields } from '../../approval-edits';
import { ApprovalAskNote, approvalAskRules } from '@/config';
import { mayApproveCard } from '../../approval-authority';

export interface ApprovalCardProps {
  approval: PendingApproval;
  /** Absent: the card only shows what became of the request (a saved answer). A denial may carry a reason (AP-05). */
  onDecide?: (decision: ApprovalDecision, scope?: ApprovalScope, reason?: string) => void;
  /** What the viewer may decide; unknown: both buttons, and the server decides. */
  authority?: DecisionAuthority;
  /** The arguments are withheld from this viewer (an admin who may only deny, P2.11e's queue). */
  argsHidden?: boolean;
  /**
   * "Allow for this conversation" may be shown when the run offers it (UX1). Only
   * the conversation's own chat sets this, a sub-agent's card in it too (FLOWGRANT):
   * an approver deciding from the queue, or a child run's panel, decides one call at a time.
   */
  conversationChoice?: boolean;
  /** Main's "Don't ask again" may be offered (BUGS-B); unset, wherever the conversation's choice is. */
  ruleChoice?: boolean;
  /** The reply's one call to action (UXINT-2): it takes focus and answers Enter and Escape. */
  active?: boolean;
  /** The run's hold ran out (UXINT-5 D5): it waits, paused, and a decision still resumes it. */
  paused?: boolean;
  /** `authority` is this call's own (the approvals queue), not the run's. */
  authorityPerCall?: boolean;
  /**
   * AP-06: the viewer is the person the run acts for, in the conversation's own chat, so a call that offers
   * editing (its reason's `editable`) shows Edit. An approver (the queue, a link) never edits.
   */
  editAllowed?: boolean;
  /** AP-06: approve with the person's changes; the card shows Edit only when this is set. */
  onApproveEdited?: (edits: Record<string, unknown>) => void;
}

export const REFUSAL_KEYS: Record<string, string> = {
  [harnessHttpCode('APPROVER_REQUIRED')]: 'approvalCard.refusedApprover',
  [harnessHttpCode('NOT_ALLOWED_TO_DECIDE')]: 'approvalCard.refusedNotAllowed',
  [harnessHttpCode('CONNECT_OWNER_REQUIRED')]: 'connectCard.refusedOwner',
  [harnessHttpCode('INTERACTION_NOT_OPEN')]: 'approvalCard.refusedClosed',
  [harnessHttpCode('RUN_CANCELLING')]: 'approvalCard.refusedClosed',
  [harnessHttpCode('RUN_ENDED')]: 'approvalCard.refusedEnded',
  [harnessHttpCode('CONVERSATION_GRANT_NOT_OFFERED')]: 'approvalCard.flow.refusedConversation',
  // SEC-CR-07 x AP-06: an edit with a pasted secret is refused, never masked into what the call sends.
  [harnessHttpCode('EDIT_HAS_SECRET')]: 'approvalCard.editHasSecret',
  [harnessHttpCode('EDIT_NOT_ALLOWED')]: 'approvalCard.edit.refusedNotAllowed',
  [harnessHttpCode('EDIT_NOT_OFFERED')]: 'approvalCard.edit.refusedNotOffered',
};

/** The text key for a refusal's error code, this build's or a rule's (`ApprovalAskRules.refusalKeys`); undefined: none. */
export function refusalKey(code: string): string | undefined {
  return REFUSAL_KEYS[code] ?? approvalAskRules.refusalKeys[code];
}

/** WP-39's refusal of an edit that carries a pasted key or password. */
export const EDIT_HAS_SECRET = harnessHttpCode('EDIT_HAS_SECRET');

/** The refusal line under a card: a secret in an edit points at the Actions page, anything else is one sentence. */
export function RefusalLine({ code }: { code: string }) {
  const { t } = useTranslation();
  if (code === EDIT_HAS_SECRET) return <SecretInEditNotice refused />;
  return (
    <Text size="2" color="red" role="alert">
      {t(refusalKey(code) ?? 'approvalCard.failed')}
    </Text>
  );
}

/**
 * Rules whose reason says what the run read: the flow guard (UX1) and an engine run's "ask before a write that
 * leaves your own space" (UX2, UX4), and a layer's own rule when the run's context came with it (QA6-F6). The card
 * lists those sources.
 */
const FLOW_RULE = 'flow_external_write';
const ENGINE_RULE = 'engine_writes';
// WP-63: the upload hold says why it asks with the flow guard's private-read sources, as other flow asks do.
const EXPLAINED_RULES: ReadonlySet<string> = new Set([FLOW_RULE, ENGINE_RULE, SITE_UPLOAD_RULE]);
const POLICY_RULES: ReadonlySet<string> = new Set<HoldRule>(['writes', 'effect_class', 'tool']);
const POLICY_WHY: Readonly<Partial<Record<RuleLayer, string>>> = {
  deployment: 'approvalCard.policy.whyDeployment',
  org: 'approvalCard.policy.whyOrg',
  agent: 'approvalCard.policy.whyAgent',
  engine: 'approvalCard.policy.whyEngine',
};

/** The reason says what the run read, so the card can list where that came from. */
const describesRun = (reason: ApprovalReason): boolean =>
  EXPLAINED_RULES.has(reason.rule) || (POLICY_RULES.has(reason.rule) && reason.sensitivity !== undefined);

/** "Allow for this conversation" is offered for a rule that read the run's context and grants it (UX1). */
export const offersConversation = (reason: ApprovalReason | undefined): boolean =>
  reason !== undefined &&
  reason.rule !== EXTERNAL_POLICY_RULE &&
  !approvalAskRules.ownCard(reason) &&
  // The upload hold is lifted for the run only, never the conversation (WP-63).
  reason.rule !== SITE_UPLOAD_RULE &&
  describesRun(reason) &&
  reason.conversationGrant === true;

/** The sentence explaining why a call waits (UX1, UX2, QA6-F6), or null for a rule the card does not explain. */
export function approvalWhyKey(reason: ApprovalReason | undefined): string | null {
  if (!reason) return null;
  if (POLICY_RULES.has(reason.rule)) return (reason.layer && POLICY_WHY[reason.layer]) ?? 'approvalCard.policy.why';
  if (reason.rule === ENGINE_RULE) {
    return reason.sensitivity === 'public' ? 'approvalCard.engine.why' : 'approvalCard.engine.whyRead';
  }
  if (reason.rule === FLOW_RULE) {
    return reason.sensitivity === 'unknown' ? 'approvalCard.flow.whyUnknown' : 'approvalCard.flow.whyPrivate';
  }
  // G-04: the run proposed its plan; nothing that changes anything runs until it is approved.
  if (reason.rule === 'plan_approval') return 'approvalCard.plan.why';
  if (reason.rule === EXTERNAL_POLICY_RULE) return 'approvalCard.externalPolicy.why';
  return approvalAskRules.whyKey(reason) ?? null;
}

/** Whose request it is when a sub-agent asks in the main thread: "Codex wants to:" (UXINT-3). */
export function ChildAttribution({ approval }: { approval: Pick<PendingApproval, 'childEngine' | 'childRole'> }) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="1" data-testid="child-attribution">
      <MaterialIcon name="account_tree" size={14} color="var(--gray-11)" />
      <Text size="1" color="gray">
        {childAttribution(t, approval)}
      </Text>
    </Flex>
  );
}

/** "Paused, waiting for you": the run stopped holding and resumes when the person decides (D5). */
export function PausedLine() {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="1" data-testid="interaction-paused">
      <MaterialIcon name="pause_circle" size={16} color="var(--amber-11)" />
      <Text size="2" weight="medium" style={{ color: 'var(--amber-11)' }}>
        {t('interaction.paused')}
      </Text>
      <Text size="1" color="gray">
        {t('interaction.pausedHint')}
      </Text>
    </Flex>
  );
}

/** The active card's frame: a warm outline, so the one call to action reads at a glance. */
export const activeCardStyle = (active: boolean): React.CSSProperties =>
  active ? { boxShadow: '0 0 0 2px var(--amber-7)', backgroundColor: 'var(--color-panel-solid)' } : { backgroundColor: 'var(--olive-2)' };

/** The heading the active card focuses: a soft accent ring, not the browser's black box. */
export const HEADING_FOCUS: React.CSSProperties = { outlineOffset: 2, outlineColor: 'var(--accent-8)', borderRadius: 4 };

/** Buttons a person taps: 44 px (frontend guide), the primary one full width on a phone. */
export const TARGET: React.CSSProperties = { minHeight: 44 };

function PreviewValue({ field }: { field: PreviewField }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Box>
      {field.label ? (
        <Text as="div" size="1" color="gray" style={{ marginBottom: 2 }}>
          {field.label}
        </Text>
      ) : null}
      <Text
        as="div"
        size="2"
        data-testid="approval-preview-value"
        style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
      >
        <MarkedText text={open ? field.full : field.value} />
      </Text>
      {field.redacted ? (
        <Text as="div" size="1" color="gray">
          {t('approvalCard.flow.redacted')}
        </Text>
      ) : null}
      {field.truncated ? (
        <Button variant="ghost" size="1" onClick={() => setOpen((v) => !v)} aria-expanded={open} style={{ marginTop: 'var(--space-1)' }}>
          {open ? t('approvalCard.flow.showLess') : t('approvalCard.flow.showAll')}
        </Button>
      ) : null}
    </Box>
  );
}

/** "What will be sent", as labelled values for every rule: never the raw JSON (UXINT-3). */
export function WhatWillBeSent({ args }: { args: string }) {
  const { t } = useTranslation();
  const fields = useMemo(() => previewPayload(args), [args]);
  return (
    <Box>
      <Text as="div" size="1" weight="bold" style={{ marginBottom: 'var(--space-1)' }}>
        {t('approvalCard.flow.willSend')}
      </Text>
      <Flex
        direction="column"
        gap="2"
        data-testid="approval-preview"
        style={{
          padding: 'var(--space-2)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--olive-3)',
          maxHeight: '320px',
          overflow: 'auto',
        }}
      >
        {fields.length === 0 ? (
          <Text size="2" color="gray">
            {t('approvalCard.flow.nothingToSend')}
          </Text>
        ) : (
          fields.map((field) => <PreviewValue key={field.key || 'value'} field={field} />)
        )}
      </Flex>
    </Box>
  );
}

function Sources({ reason }: { reason: ApprovalReason }) {
  const { t } = useTranslation();
  if (reason.sources.length === 0 && reason.moreSources === 0) return null;
  return (
    <Box>
      <Text as="div" size="1" weight="bold" style={{ marginBottom: 'var(--space-1)' }}>
        {t('approvalCard.flow.sources')}
      </Text>
      <Flex direction="column" gap="1" asChild>
        <ul data-testid="approval-sources" style={{ margin: 0, paddingLeft: 'var(--space-4)' }}>
          {reason.sources.map((source, index) => {
            const href = sourceHref(source);
            const title = source.title ?? t('approvalCard.flow.untitledSource');
            const outside = href !== null && !href.startsWith('/');
            return (
              <li key={`${index}-${title}`}>
                {href ? (
                  <Link size="2" href={href} {...(outside ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
                    <MarkedText text={title} />
                  </Link>
                ) : (
                  <Text size="2">
                    <MarkedText text={title} />
                  </Text>
                )}
              </li>
            );
          })}
          {reason.moreSources > 0 ? (
            <li>
              <Text size="2" color="gray">
                {t('approvalCard.flow.moreSources', { count: reason.moreSources })}
              </Text>
            </li>
          ) : null}
        </ul>
      </Flex>
    </Box>
  );
}

/**
 * What a clone brings (WP-63, the approved mock): public, read-only, its size, and that nothing goes back to the
 * host. After a private read, a warning that the repository's address is sent to its host.
 */
export function CloneSummary({ approval }: { approval: Pick<PendingApproval, 'reason'> }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const clone = cloneDetails(approval.reason?.destination);
  if (!clone) return null;
  const size =
    clone.size === null
      ? null
      : clone.size.kind === 'about'
        ? t('approvalCard.clone.sizeAbout', { mb: clone.size.mb })
        : t('approvalCard.clone.sizeUpTo', { mb: clone.size.mb });
  const read = approval.reason?.sensitivity === 'private' || approval.reason?.sensitivity === 'unknown';
  return (
    <Flex direction="column" gap="2" data-testid="clone-summary">
      <Text size="2">
        {size ? t('approvalCard.clone.whatSized', { size }) : t('approvalCard.clone.what')}
        {isMobile ? null : ` ${t('approvalCard.clone.nothingBack', { host: clone.host })}`}
      </Text>
      {read ? (
        <Flex
          align="start"
          gap="2"
          style={{ padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-2)', background: 'var(--amber-a3)' }}
        >
          <MaterialIcon name="warning_amber" size={16} color="var(--amber-11)" />
          <Text size="2" data-testid="clone-private-read" style={{ color: 'var(--amber-11)' }}>
            {isMobile ? t('approvalCard.clone.privateReadShort') : t('approvalCard.clone.privateRead', { host: clone.host })}
          </Text>
        </Flex>
      ) : null}
    </Flex>
  );
}

/** Visually hidden, read with the card: the keys it answers (UXINT-4). */
export const VISUALLY_HIDDEN: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
};

/**
 * A tool call the durable run holds until a person decides, and later what became of it. The tool and its
 * arguments come from the journaled call and render as text, never markup. Buttons the viewer may not use are not
 * shown. The title says what the call does ("Create ticket in E2Esaas?"), one line says why it asks, and "What will
 * be sent" lists the values; nothing is chosen for the person.
 */
export function ApprovalCard({
  approval,
  onDecide,
  authority,
  argsHidden = false,
  conversationChoice = false,
  ruleChoice,
  active = false,
  paused = false,
  authorityPerCall = false,
  editAllowed = false,
  onApproveEdited,
}: ApprovalCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  // AP-06: an approved edit that changed where the call sends names the new place, as it ran.
  const title = interactionTitle(t, editedDestination(approval));
  const why = interactionWhy(t, approval);
  const open = (approval.status === 'pending' || approval.status === 'sending') && onDecide !== undefined;
  const sending = approval.status === 'sending';
  const canApprove = mayApproveCard(authority, approval, authorityPerCall);
  const canDeny = authority?.deny ?? true;
  const waitsOnRule = approvalAskRules.holds(approval.reason) && !canApprove;
  const ids = `approval-${approval.runId ?? 'run'}-${approval.childRunId ?? 'root'}-${approval.interactionId}`.replace(/[^\w-]/g, '-');
  const headingId = `${ids}-title`;
  const whyId = `${ids}-why`;
  const hintId = `${ids}-keys`;
  const reason = useMemo(
    () => (approval.reason && describesRun(approval.reason) ? maskedReason(approval.reason) : undefined),
    [approval.reason],
  );
  // ADR-GRANT-01 D9: allowing a call for the whole chat is the run's own person's decision, never another participant's.
  const offerConversation = conversationChoice && offersConversation(approval.reason) && canApprove && authority?.edit !== false;
  // Main's "Don't ask again" (BUGS-B): the answer becomes the person's own rule, so only in their conversation.
  const offerRule = (ruleChoice ?? conversationChoice) && approval.reason?.ruleGrant === true;
  const [remember, setRemember] = useState(false);
  const [denying, setDenying] = useState(false);
  const [editing, setEditing] = useState(false);
  const order = approval.reason?.fieldOrder;
  const fields = useMemo(() => editFields(approval.args, order), [approval.args, order]);
  const offerEdit =
    editAllowed &&
    onApproveEdited !== undefined &&
    approval.reason?.editable === true &&
    !argsHidden &&
    !approval.effect?.diff &&
    fields !== null &&
    fields.some((field) => field.kind !== 'fixed') &&
    // A refusal that says editing is not theirs (or not offered) takes the button away.
    approval.errorCode !== harnessHttpCode('EDIT_NOT_ALLOWED') &&
    approval.errorCode !== harnessHttpCode('EDIT_NOT_OFFERED');
  const isEditing = editing && offerEdit && open;
  const edited = approval.status === 'approved' && approval.editedArgs ? approval.editedArgs : null;
  const decide = (decision: ApprovalDecision, reason?: string) => {
    const scope = remember && offerRule ? ('tool' as const) : undefined;
    if (reason) onDecide?.(decision, scope, reason);
    else if (scope) onDecide?.(decision, scope);
    else onDecide?.(decision);
  };
  const effect = approval.effect;
  const headingRef = useRef<HTMLDivElement>(null);
  const isActive = active && open;
  const { onKeyDown } = useInteractionFocus(
    isActive,
    headingRef,
    canApprove && !sending && !denying && !isEditing ? () => decide('approved') : null,
    [approval.interactionId],
  );

  const outcome =
    approval.status === 'approved'
      ? {
          color: 'green' as const,
          label: edited
            ? t('approvalCard.edit.approvedWithChanges')
            : approval.scope === 'conversation'
              ? t('approvalCard.flow.allowedConversation')
              : t('approvalCard.approved'),
        }
      : approval.status === 'denied'
        ? { color: 'red' as const, label: t('approvalCard.denied') }
        : approval.status === 'expired'
          ? { color: 'gray' as const, label: t('approvalCard.expired') }
          : approval.status === 'closed'
            ? { color: 'gray' as const, label: t('approvalCard.closed') }
            : null;
  const tool = interactionAction(t, approval);
  // WP-63: a clone shows what it brings, not its arguments; the upload hold is decided for this run only.
  const clone = isClone(approval) && cloneDetails(approval.reason?.destination) !== null;
  const siteUpload = isSiteUpload(approval);
  const conversationText = clone
    ? isMobile
      ? t('approvalCard.clone.allowConversationShort')
      : t('approvalCard.clone.allowConversation')
    : allowConversationText(t, safeText(approval.reason?.destination ?? ''));
  const allowText = siteUpload ? t('approvalCard.siteUpload.allowRun') : t('approvalCard.flow.allowOnce');
  // AP-04: what "Allow for this conversation" covers is said in words, not only to a screen reader.
  // A clone's grant covers its repository, whatever size the card names.
  const scopeDestination = clone
    ? (cloneDetails(approval.reason?.destination)?.repository ?? '')
    : safeText(approval.reason?.destination ?? '');
  // SEC-B-04: the note names a lookalike host or hidden characters wherever the card shows them.
  const shownTexts = useMemo(
    () => [
      title,
      safeText(approval.reason?.destination ?? ''),
      ...(reason?.sources ?? []).map((source) => source.title ?? ''),
      ...(argsHidden ? [] : previewPayload(approval.args, Number.MAX_SAFE_INTEGER).map((field) => `${field.label} ${field.full}`)),
    ],
    [title, approval.reason?.destination, reason, argsHidden, approval.args],
  );

  return (
    <Card
      role="region"
      aria-labelledby={headingId}
      aria-describedby={[why ? whyId : null, isActive ? hintId : null].filter(Boolean).join(' ') || undefined}
      data-testid="approval-card"
      data-status={approval.status}
      data-interaction-active={isActive ? 'true' : undefined}
      onKeyDown={onKeyDown}
      style={{ marginTop: 'var(--space-2)', ...activeCardStyle(isActive) }}
    >
      <Flex direction="column" gap="3">
        {approval.childRunId ? <ChildAttribution approval={approval} /> : null}
        <Flex align="center" gap="2">
          <MaterialIcon name={reason ? 'shield' : 'front_hand'} size={18} color="var(--accent-11)" />
          <Text
            as="div"
            id={headingId}
            ref={headingRef}
            tabIndex={-1}
            size="2"
            weight="bold"
            data-testid="interaction-heading"
            style={HEADING_FOCUS}
          >
            <MarkedText text={title} />
          </Text>
          {outcome ? (
            <Badge color={outcome.color} variant="soft">
              {outcome.label}
            </Badge>
          ) : null}
        </Flex>
        {paused && open ? <PausedLine /> : null}
        {why ? (
          <Text id={whyId} size="2" data-testid="approval-why">
            {why}
          </Text>
        ) : open ? (
          <Text size="2" color="gray">
            {t('approvalCard.description')}
          </Text>
        ) : null}
        <DisplaySafetyNote texts={shownTexts} />
        {approval.status === 'expired' ? (
          <Text size="2" color="gray">
            {reason ? t('approvalCard.flow.expiredDescription') : t('approvalCard.expiredDescription')}
          </Text>
        ) : null}
        {argsHidden ? (
          <Text size="2" color="gray" data-testid="approval-args-hidden">
            {t('approvalCard.argumentsHidden')}
          </Text>
        ) : isEditing && fields ? (
          <ApprovalEditForm
            args={approval.args}
            fields={fields}
            sending={sending}
            onCancel={() => setEditing(false)}
            onApprove={(edits) => onApproveEdited?.(edits)}
          />
        ) : edited ? (
          <EditedReceipt args={approval.args} editedArgs={edited} renderAll={(args) => <WhatWillBeSent args={args} />} />
        ) : effect?.diff ? (
          // UX-03: an edit shows what it changes; its arguments are the same text before and after.
          <EffectDiff diff={effect.diff} truncated={effect.diffTruncated === true} />
        ) : clone ? (
          <CloneSummary approval={approval} />
        ) : siteUpload ? null : (
          <WhatWillBeSent args={approval.args} />
        )}
        {effect?.costEstimate ? <EffectCost usd={effect.costEstimate.usd} /> : null}
        {!argsHidden && reason ? <Sources reason={reason} /> : null}
        {approval.errorCode ? <RefusalLine code={approval.errorCode} /> : null}
        {open ? (
          <ApprovalAskNote
            reason={approval.reason}
            canApprove={canApprove}
            {...(authority !== undefined ? { authority } : {})}
            {...(approval.approvals !== undefined ? { approvals: approval.approvals } : {})}
            approvedByYou={approval.approvedByYou === true}
          />
        ) : null}
        {open && !canApprove && !canDeny && !waitsOnRule ? (
          <Text size="2" color="gray">
            {t('approvalCard.noAuthority')}
          </Text>
        ) : null}
        {open && offerRule && (canApprove || canDeny) && !isEditing ? (
          <Text as="label" size="2" style={{ color: 'var(--gray-11)', cursor: 'pointer', minHeight: 44, display: 'flex', alignItems: 'center' }}>
            <Flex gap="2" align="center">
              <Checkbox checked={remember} disabled={sending} onCheckedChange={(value) => setRemember(value === true)} />
              {t('chat.toolApproval.dontAskAgain')}
            </Flex>
          </Text>
        ) : null}
        {open && canDeny && denying ? (
          <DenyReasonForm
            disabled={sending}
            onCancel={() => setDenying(false)}
            onDeny={(reason) => decide('denied', reason)}
            denyLabel={t('approvalCard.flow.denyLabel', { tool })}
            denyText={t('approvalCard.flow.deny')}
          />
        ) : null}
        {approval.status === 'approved' && approval.scope === 'conversation' && scopeDestination ? (
          <Text size="2" color="gray" data-testid="approval-conversation-scope">
            {t('approvalCard.flow.allowedConversationScope', { destination: scopeDestination })}
          </Text>
        ) : null}
        {approval.status === 'denied' && approval.denyReason ? (
          <Text size="2" color="gray" data-testid="approval-deny-reason">
            {t('approvalCard.denyReason.given', { reason: approval.denyReason })}
          </Text>
        ) : null}
        {open && (canApprove || canDeny) && !denying && !isEditing ? (
          <Flex gap="2" justify="end" wrap="wrap" direction={isMobile ? 'column-reverse' : 'row'}>
            {canDeny ? (
              <Button
                variant="soft"
                color="gray"
                disabled={sending}
                onClick={() => setDenying(true)}
                aria-label={t('approvalCard.flow.denyLabel', { tool })}
                style={TARGET}
              >
                {t('approvalCard.flow.deny')}
              </Button>
            ) : null}
            {offerEdit && canApprove ? (
              <Button
                variant="soft"
                disabled={sending}
                onClick={() => setEditing(true)}
                aria-label={t('approvalCard.edit.editLabel', { tool })}
                data-testid="approval-edit"
                style={TARGET}
              >
                {t('approvalCard.edit.edit')}
              </Button>
            ) : null}
            {offerConversation ? (
              <Button
                variant="soft"
                disabled={sending}
                onClick={() => onDecide?.('approved', 'conversation')}
                aria-label={
                  clone
                    ? t('approvalCard.clone.allowConversation')
                    : t('approvalCard.flow.buttonName', { action: conversationText, subject: tool })
                }
                style={TARGET}
              >
                {conversationText}
              </Button>
            ) : null}
            {canApprove ? (
              <Button
                disabled={sending}
                onClick={() => decide('approved')}
                // The name starts with the words on the button, so a voice user can say what they see (F5, WCAG 2.5.3).
                aria-label={t('approvalCard.flow.buttonName', { action: allowText, subject: siteUpload ? scopeDestination : tool })}
                style={TARGET}
              >
                {sending ? t('approvalCard.sending') : allowText}
              </Button>
            ) : null}
          </Flex>
        ) : null}
        {open && offerConversation && !denying && !isEditing && scopeDestination ? (
          <Text size="1" color="gray" align="right" data-testid="approval-conversation-scope">
            {t('approvalCard.flow.conversationScope', { destination: scopeDestination })}
          </Text>
        ) : null}
        {isActive ? (
          <span id={hintId} style={VISUALLY_HIDDEN}>
            {t('interaction.keysHint')}
          </span>
        ) : null}
        <Text size="1" color="gray" aria-live="polite" style={VISUALLY_HIDDEN}>
          {sending ? t('approvalCard.sending') : (outcome?.label ?? '')}
        </Text>
      </Flex>
    </Card>
  );
}
