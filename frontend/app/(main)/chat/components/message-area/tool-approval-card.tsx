'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Checkbox, Flex, Text, TextArea, Tooltip, VisuallyHidden } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { validateApprovalEdits } from '../../api';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { usePendingApprovals } from '../../pending-approvals-store';
import { useChatStore } from '../../store';
import { approvalProduct, approvalToolLabel, toolApprovalResumeQuery, type SettledToolApproval } from '../../tool-approval';
import type { EditError, ToolApprovalAnswer, ToolApprovalDecision, ToolApprovalDetails } from '../../types';
import { checkArgs } from './approval-form/check-args';
import { DocumentForm } from './approval-form/document-form';
import { DocumentView } from './approval-form/document-view';
import { argumentFields, schemaFields } from './approval-form/schema-fields';

/** Whole minutes left (rounded up), re-read every half minute; 0 once the request has expired. */
function useMinutesLeft(expiresAt: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const left = expiresAt - now;
    if (left <= 0) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(left, 30_000));
    return () => clearTimeout(timer);
  }, [now, expiresAt]);
  return Math.max(0, Math.ceil((expiresAt - now) / 60_000));
}

interface ToolApprovalCardProps {
  approval: ToolApprovalDetails;
  /** The latest reply: an older card was already answered or passed over. */
  isLatest: boolean;
  /** The reply's row: the answer carries it on. */
  rowId?: string;
  /** "composer": a call waiting in another chat, shown above the composer. */
  placement?: 'inline' | 'composer';
  conversationTitle?: string | null;
  /** Sends the answer in the call's own chat instead of the active one. */
  /** `{ failed }` carries the reason the server or stream gave. */
  onAnswer?: (answer: ToolApprovalAnswer) => Promise<AnswerResult>;
  onOpenChat?: () => void;
  /** Composer: how many more calls wait behind this one; `onMore` shows the next. */
  moreCount?: number;
  onMore?: () => void;
  /** Composer: folds the card back to its one-line bar. */
  onHide?: () => void;
  /** Composer: when the call was asked ("Asked 3 min ago"), to tell same-titled chats apart. */
  asked?: string | null;
}

export type AnswerResult = 'sent' | 'busy' | 'stale' | 'failed' | { failed: string };

const ANSWER_PROBLEM = { busy: 'busyElsewhere', stale: 'alreadyAnswered', failed: 'answerFailed' } as const;
type Problem = { kind: keyof typeof ANSWER_PROBLEM; detail?: string };

const MAX_FEEDBACK_CHARS = 2000;

const blank = (v: unknown) => v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
// An optional field the call didn't set stays unchanged while it is left empty.
const same = (value: unknown, asked: unknown) =>
  asked === undefined ? blank(value) : JSON.stringify(value) === JSON.stringify(asked);

/** The top-level field a JSON pointer names (`/labels/0` is `labels`); "" for the whole call. */
function pointerField(path: string): string {
  return (path.split('/')[1] ?? '').replace(/~1/g, '/').replace(/~0/g, '~');
}

/** Asks the person whether a tool call may run. The call itself is saved on the server; the
 * answer carries on this same reply, sending the approval id, the choice and, when the person
 * changed the call or wrote a note, those too. Where it can't be answered (expired, an older
 * reply, a conversation shared with the viewer) it is one line. */
export function ToolApprovalCard({ approval, isLatest, rowId, placement = 'inline', conversationTitle, onAnswer, onOpenChat, moreCount = 0, onMore, onHide, asked }: ToolApprovalCardProps) {
  const { t } = useTranslation();
  const minutesLeft = useMinutesLeft(approval.expiresAt);
  const original = approval.arguments ?? null;
  const [remember, setRemember] = useState(false);
  const [forConversation, setForConversation] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...(approval.editedArguments ?? original ?? {}) }));
  const [errors, setErrors] = useState<EditError[]>(() => approval.editErrors ?? []);
  const [editing, setEditing] = useState(() => Boolean(approval.editErrors?.length));
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const [problem, setProblem] = useState<Problem | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const actionsRef = useRef<HTMLDivElement>(null);
  // Leaving the editor or the note box unmounts the focused field: focus goes back to its opener.
  const editRef = useRef<HTMLButtonElement>(null);
  const suggestRef = useRef<HTMLButtonElement>(null);
  // Someone viewing a conversation shared with them can't send in it; onAnswer sends in the call's own chat.
  const ownsActive = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null)) !== false;
  const canSend = Boolean(onAnswer) || ownsActive;

  const canEdit = approval.editable === true && Boolean(approval.inputSchema) && original !== null;
  const fields = useMemo(() => (canEdit && approval.inputSchema ? schemaFields(approval.inputSchema, original ?? {}) : []), [canEdit, approval.inputSchema, original]);
  const changed = useMemo(
    () => new Set(Object.keys({ ...original, ...values }).filter((name) => !same(values[name], original?.[name]))),
    [values, original],
  );
  const edited = changed.size > 0;
  const showForm = editing && canEdit;

  // The form grows the card under the composer; keep its actions where the person can reach them.
  useEffect(() => {
    if (!showForm) return;
    // After the next frame, so the form's fields have their final height.
    const frame = requestAnimationFrame(() => actionsRef.current?.scrollIntoView?.({ block: 'nearest' }));
    return () => cancelAnimationFrame(frame);
  }, [showForm]);

  useEffect(() => {
    const invalid = focusTick ? cardRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]') : null;
    // A list field marks its group; the input inside takes the focus, not a chip's remove button.
    const target = invalid?.matches('input, textarea, button')
      ? invalid
      : invalid?.querySelector<HTMLElement>('input, textarea') ?? invalid?.querySelector<HTMLElement>('button');
    if (!target) return;
    // The errors grew the card under the composer: bring back the actions, which sit below every field.
    // One scroll only: a second call would cancel the scroller's smooth scroll.
    target.focus({ preventScroll: true });
    actionsRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [focusTick]);

  const tool = approvalToolLabel(approval);
  // The product the call acts in ("Jira"); the server it goes through ("Atlassian Rovo") is the subline.
  const server = approvalProduct(approval);
  const title = tool.toLowerCase().includes(server.toLowerCase()) ? tool : t('chat.toolApproval.titleIn', { tool, server });
  const via = server === approval.serverName ? null : approval.serverName;
  const expired = minutesLeft === 0;
  const answerable = !expired && isLatest && canSend;

  // The composer's list of other chats' calls folds away while this chat asks its own.
  useEffect(() => {
    if (placement !== 'inline' || !answerable) return;
    usePendingApprovals.getState().markInline(1);
    return () => usePendingApprovals.getState().markInline(-1);
  }, [placement, answerable]);

  if (!answerable) {
    const notice = expired ? 'expired' : canSend ? 'passed' : 'waiting';
    return (
      <Flex data-testid="tool-approval-card" align="start" gap="2" style={{ color: 'var(--gray-11)' }}>
        <MaterialIcon name={expired ? 'timer_off' : 'pending_actions'} size={16} color="var(--gray-9)" style={{ marginTop: 'calc(var(--space-1) / 2)' }} />
        <Text size="2">{t(`chat.toolApproval.notice.${notice}`, { tool, server })}</Text>
      </Flex>
    );
  }

  const errorText = (e: EditError) =>
    e.code ? t(`chat.toolApproval.errors.${e.code}`, { ...e.params, context: e.code === 'type' ? String(e.params?.type ?? '') : undefined }) : e.message;
  const fieldNames = new Set(fields.map((f) => f.name));
  const fieldErrors: Record<string, string> = {};
  const cardErrors: string[] = [];
  for (const e of errors) {
    const name = pointerField(e.path);
    if (fieldNames.has(name) && !fieldErrors[name]) fieldErrors[name] = errorText(e);
    else if (!fieldNames.has(name)) cardErrors.push(errorText(e));
  }
  const hasFieldErrors = Object.keys(fieldErrors).length > 0;

  const viewFields = original ? argumentFields(approval.inputSchema, values) : [];
  const changeCount = t('chat.toolApproval.changeCount', { count: changed.size });
  const changedLabels = [...changed].map((name) => fields.find((f) => f.name === name)?.label ?? name).join(', ');

  const answer = async (decision: ToolApprovalDecision, extra: { arguments?: Record<string, unknown>; feedback?: string } = {}) => {
    // An edit or a note is about this one call, so it never sets a lasting rule.
    const plain = !edited && !extra.arguments && !extra.feedback;
    const payload: ToolApprovalAnswer = {
      approvalId: approval.approvalId,
      decision,
      ...(approval.argsHash ? { argsHash: approval.argsHash } : {}),
      ...extra,
      ...(plain && remember && approval.canRemember !== false ? { remember: true } : {}),
      ...(plain && forConversation && decision === 'allow' && approval.canRemember !== false ? { rememberForConversation: true } : {}),
    };
    if (onAnswer) {
      setProblem(null);
      setSending(true);
      let result: AnswerResult = 'failed';
      try {
        result = await onAnswer(payload);
      } catch {
        // Treated as 'failed': the card stays so the person can try again.
      } finally {
        setSending(false);
      }
      if (typeof result === 'object') setProblem({ kind: 'failed', detail: result.failed });
      else if (result !== 'sent') setProblem({ kind: result });
      return;
    }
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId || useChatStore.getState().slots[slotId]?.isStreaming) return;
    const query = toolApprovalResumeQuery(decision);
    const request = buildStreamChatRequestForSlot(slotId, query, undefined, payload);
    if (!request) return;
    // Answered here: the list above the composer must not offer it once this chat is left.
    usePendingApprovals.getState().drop(approval.approvalId);
    void usePendingApprovals.getState().refresh();
    void streamMessageForSlot(slotId, query, request, { reuseRowId: rowId });
  };

  const refuse = (found: EditError[]) => {
    setErrors(found);
    setEditing(true);
    setFocusTick((n) => n + 1);
  };

  const allow = async () => {
    if (!edited) {
      await answer('allow');
      return;
    }
    const local = checkArgs(fields, values);
    if (local.length) {
      refuse(local);
      return;
    }
    // A field the call didn't set and the person left empty stays out of the call.
    const args = Object.fromEntries(Object.entries(values).filter(([k, v]) => v !== undefined && !(original?.[k] === undefined && blank(v))));
    setSending(true);
    let found: EditError[] = [];
    try {
      found = await validateApprovalEdits(approval.approvalId, approval.argsHash ?? '', args);
    } catch {
      // The check couldn't run; the server checks the edit again when it gets the answer.
    }
    setSending(false);
    if (found.length) {
      refuse(found);
      return;
    }
    setErrors([]);
    await answer('allow', { arguments: args });
  };

  const onFormKeyDown = (e: React.KeyboardEvent) => {
    // A Select or other layer closing on Esc has already handled it.
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    e.stopPropagation();
    setEditing(false);
    editRef.current?.focus();
  };

  const onNoteKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    e.stopPropagation();
    setNoteOpen(false);
    suggestRef.current?.focus();
  };

  const soon = minutesLeft <= 1;
  const expiry = placement === 'composer' && conversationTitle
    ? soon
      ? t('chat.toolApproval.inChatExpiresSoon', { title: conversationTitle })
      : t('chat.toolApproval.inChatExpiresIn', { title: conversationTitle, count: minutesLeft })
    : soon
      ? t('chat.toolApproval.expiresSoon')
      : t('chat.toolApproval.expiresIn', { count: minutesLeft });
  const detail = [via, expiry, asked].filter(Boolean).join(' · ');

  return (
    <Box
      ref={cardRef}
      className={placement === 'composer' ? 'tool-approval-card no-scrollbar' : 'tool-approval-card'}
      data-testid="tool-approval-card"
      role="group"
      aria-label={t('chat.toolApproval.needsApprovalFor', { title })}
      // On the whole card, so Esc leaves edit mode from Done, Reset or Allow too.
      onKeyDown={showForm ? onFormKeyDown : undefined}
      p="3"
      style={{
        border: '1px solid var(--gray-5)',
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--gray-2)',
        // Its height cap is in globals.css (.composer-approval): it depends on the composer's width.
        ...(placement === 'composer' ? { boxShadow: 'var(--shadow-4)', overflowY: 'auto' } : {}),
      }}
    >
      <Flex gap="3" align="start">
        <Flex
          align="center"
          justify="center"
          style={{
            width: 'calc(var(--space-5) + var(--space-1) / 2)',
            height: 'calc(var(--space-5) + var(--space-1) / 2)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--gray-4)',
            flexShrink: 0,
          }}
        >
          <ConnectorIcon type={server} size={15} />
        </Flex>
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Flex align="baseline" gap="2">
            <Text size="2" weight="medium" truncate title={approval.toolName} style={{ color: 'var(--gray-12)', minWidth: 0 }}>
              {title}
            </Text>
            {/* One status label for the whole edit, like a document's save status; the tooltip names the fields. */}
            {edited && (
              <Tooltip content={t('chat.toolApproval.receipt.changedFields', { fields: changedLabels })}>
                <Text size="1" tabIndex={0} style={{ color: 'var(--gray-11)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                  {changeCount}
                </Text>
              </Tooltip>
            )}
            {approval.kind === 'destructive' && (
              <Badge size="1" variant="soft" color="red" style={{ flexShrink: 0 }}>
                {t('chat.toolApproval.cantBeUndone')}
              </Badge>
            )}
          </Flex>
          {canEdit && (
            <VisuallyHidden role="status" aria-live="polite">
              {edited ? changeCount : ''}
            </VisuallyHidden>
          )}
          {/* The call's own fields say it below; the summary stands in only when they're too large to send. */}
          {approval.argsSummary && !original && (
            <Text as="p" size="2" truncate style={{ color: 'var(--gray-11)' }}>
              {approval.argsSummary}
            </Text>
          )}
          <Text as="p" size="1" style={{ color: 'var(--gray-11)' }}>
            {detail}
          </Text>
        </Box>
        {moreCount > 0 && onMore && (
          <Button
            data-approval-more=""
            size="1"
            variant="ghost"
            color="gray"
            style={{ flexShrink: 0 }}
            aria-label={t('chat.toolApproval.moreWaitingLabel', { count: moreCount })}
            onClick={onMore}
          >
            {t('chat.toolApproval.moreWaiting', { count: moreCount })}
          </Button>
        )}
        {onHide && (
          <Button data-approval-hide="" size="1" variant="ghost" color="gray" style={{ flexShrink: 0 }} onClick={onHide}>
            {t('chat.toolApproval.hide')}
          </Button>
        )}
      </Flex>

      <Box
        mt="3"
        className={showForm ? 'no-scrollbar tool-approval-editor' : 'no-scrollbar'}
        style={{
          border: '1px solid var(--gray-5)',
          borderRadius: 'var(--radius-3)',
          backgroundColor: 'var(--color-panel-solid)',
          padding: showForm ? 'var(--space-3)' : 'var(--space-2) var(--space-3)',
          maxHeight: showForm ? 360 : undefined,
          overflowY: 'auto',
        }}
      >
        {showForm ? (
          <DocumentForm
            fields={fields}
            values={values}
            errors={fieldErrors}
            onChange={(name, value) => {
              setValues((prev) => ({ ...prev, [name]: value }));
              setRemember(false);
              setForConversation(false);
              setErrors((prev) => prev.filter((e) => pointerField(e.path) !== name));
            }}
          />
        ) : approval.argumentsPreview ? (
          <>
            <Text as="p" size="1" style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {approval.argumentsPreview}
            </Text>
            <Text as="p" size="1" mt="1" style={{ color: 'var(--gray-10)' }}>
              {t('chat.toolApproval.partial')}
            </Text>
          </>
        ) : (
          <DocumentView fields={viewFields} values={values} />
        )}
      </Box>
      {(hasFieldErrors || cardErrors.length > 0) && (
        <Flex direction="column" gap="1" mt="2" role="alert">
          {hasFieldErrors && <Text size="1" style={{ color: 'var(--red-11)' }}>{t('chat.toolApproval.editRejected')}</Text>}
          {cardErrors.map((message, i) => (
            <Text key={i} size="1" style={{ color: 'var(--red-11)' }}>{message}</Text>
          ))}
        </Flex>
      )}

      {noteOpen && (
        <Flex mt="3" direction="column" gap="2" onKeyDown={onNoteKeyDown}>
          <TextArea
            autoFocus
            size="2"
            rows={3}
            maxLength={MAX_FEEDBACK_CHARS}
            placeholder={t('chat.toolApproval.feedbackPlaceholder')}
            aria-label={t('chat.toolApproval.suggestChange')}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Flex gap="2">
            <Button size="2" disabled={!note.trim()} loading={sending} onClick={() => void answer('deny', { feedback: note.trim() })}>
              {t('chat.toolApproval.sendSuggestion')}
            </Button>
          </Flex>
        </Flex>
      )}

      {problem && (
        <Text as="p" size="1" mt="2" role="status" style={{ color: problem.kind === 'failed' ? 'var(--red-11)' : 'var(--gray-11)' }}>
          {problem.detail || t(`chat.toolApproval.${ANSWER_PROBLEM[problem.kind]}`)}
        </Text>
      )}

      {/* The margin clears the sticky composer and the ghost buttons, which reach past the row. */}
      <Flex
        ref={actionsRef}
        data-testid="tool-approval-actions"
        mt="3"
        align="center"
        justify="between"
        gap="3"
        wrap="wrap"
        style={{ scrollMarginBottom: 'calc(var(--composer-dock-height, 0px) + var(--space-4))' }}
      >
        <Flex className="tool-approval-card-group tool-approval-card-lead" align="center" gapX="4" gapY="2" wrap="wrap">
          {canEdit && (
            <Button ref={editRef} size="2" variant="ghost" color="gray" onClick={() => setEditing((value) => !value)}>
              {showForm ? t('chat.toolApproval.done') : t('chat.toolApproval.edit')}
            </Button>
          )}
          {showForm && edited && (
            <Button
              size="2"
              variant="ghost"
              color="gray"
              onClick={() => {
                setValues({ ...(original ?? {}) });
                setErrors([]);
                // Reset leaves with the changes; Done is next to it.
                editRef.current?.focus();
              }}
            >
              {t('chat.toolApproval.reset')}
            </Button>
          )}
          <Button ref={suggestRef} size="2" variant="ghost" color="gray" aria-expanded={noteOpen} onClick={() => setNoteOpen((value) => !value)}>
            {t('chat.toolApproval.suggestChange')}
          </Button>
          {placement === 'composer' && onOpenChat && (
            <Button size="2" variant="ghost" color="gray" onClick={onOpenChat}>
              {t('chat.toolApproval.openChat')}
            </Button>
          )}
        </Flex>
        <Flex className="tool-approval-card-group tool-approval-card-answer" align="center" gap="2">
          {/* A dialog footer: a bordered secondary beside the house primary, never a soft one. */}
          <Button size="2" variant="outline" color="gray" disabled={sending} onClick={() => void answer('deny')}>
            {t('chat.toolApproval.deny')}
          </Button>
          <Button size="2" color={approval.kind === 'destructive' ? 'red' : undefined} loading={sending} onClick={() => void allow()}>
            {edited ? t('chat.toolApproval.allowWithChanges') : t('chat.toolApproval.allow')}
          </Button>
        </Flex>
      </Flex>
      {/* An admin owns the rule: neither choice is the person's to save. */}
      {!edited && approval.canRemember !== false && (
        <Flex mt="3" direction="column" gap="2">
          <Text as="label" size="1" style={{ color: 'var(--gray-11)', cursor: 'pointer' }}>
            <Flex gap="2" align="center">
              <Checkbox
                size="1"
                checked={remember}
                onCheckedChange={(value) => {
                  setRemember(value === true);
                  if (value === true) setForConversation(false);
                }}
              />
              {t('chat.toolApproval.dontAskAgain')}
            </Flex>
          </Text>
          <Box>
            <Text as="label" size="1" style={{ color: 'var(--gray-11)', cursor: 'pointer' }}>
              <Flex gap="2" align="center">
                <Checkbox
                  size="1"
                  checked={forConversation}
                  aria-describedby={`${approval.approvalId}-conversation-hint`}
                  onCheckedChange={(value) => {
                    setForConversation(value === true);
                    if (value === true) setRemember(false);
                  }}
                />
                {t('chat.toolApproval.allowForConversation')}
              </Flex>
            </Text>
            <Text as="p" id={`${approval.approvalId}-conversation-hint`} size="1" style={{ color: 'var(--gray-10)', paddingLeft: 'var(--space-5)' }}>
              {t('chat.toolApproval.allowForConversationHint')}
            </Text>
          </Box>
        </Flex>
      )}
    </Box>
  );
}

const RECEIPT_ICON: Record<SettledToolApproval['answer'], { name: string; color: string }> = {
  allowed: { name: 'check_circle', color: 'var(--jade-9)' },
  // A person stopped it.
  allowedNotRun: { name: 'block', color: 'var(--orange-11)' },
  declined: { name: 'block', color: 'var(--gray-9)' },
  expired: { name: 'timer_off', color: 'var(--gray-9)' },
};

/** What was answered on this reply's cards, one line each, so the answer reads in order even
 * with the activity folded away. */
export function ToolApprovalReceipts({ items }: { items: SettledToolApproval[] }) {
  return (
    <Flex direction="column" gap="1" data-testid="tool-approval-receipts">
      {items.map((item) => <Receipt key={item.key} item={item} />)}
    </Flex>
  );
}

function Receipt({ item }: { item: SettledToolApproval }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const icon = RECEIPT_ICON[item.answer];
  const text = item.note
    ? t('chat.toolApproval.receipt.declinedWithNote', { tool: item.tool, note: item.note })
    : item.changedFields
      ? t('chat.toolApproval.receipt.allowedWithChanges', { tool: item.tool })
      : t(`chat.toolApproval.receipt.${item.answer}`, { tool: item.tool });
  return (
    <Box>
      <Flex align="center" gap="2">
        <MaterialIcon name={icon.name} size={16} color={icon.color} />
        {item.changedFields ? (
          <Button size="1" variant="ghost" color="gray" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {text}
            <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={16} />
          </Button>
        ) : (
          <Text size="2" style={{ color: 'var(--gray-11)', overflowWrap: 'anywhere' }}>{text}</Text>
        )}
        {item.server && <Badge size="1" variant="soft" color="gray">{item.server}</Badge>}
      </Flex>
      {open && item.changedFields && (
        <Text as="p" size="1" style={{ color: 'var(--gray-10)', paddingLeft: 'var(--space-5)' }}>
          {t('chat.toolApproval.receipt.changedFields', { fields: item.changedFields.join(', ') })}
        </Text>
      )}
    </Box>
  );
}
