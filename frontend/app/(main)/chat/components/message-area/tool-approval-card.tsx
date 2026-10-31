'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Checkbox, Flex, Text, TextArea } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { validateApprovalEdits } from '../../api';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import { approvalArgumentLines, approvalToolLabel, toolApprovalResumeQuery, type SettledToolApproval } from '../../tool-approval';
import type { EditError, ToolApprovalDecision, ToolApprovalDetails } from '../../types';
import { checkArgs } from './approval-form/check-args';
import { SchemaForm } from './approval-form/schema-form';
import { schemaFields } from './approval-form/schema-fields';

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
}

const MAX_FEEDBACK_CHARS = 2000;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The top-level field a JSON pointer names (`/labels/0` is `labels`); "" for the whole call. */
function pointerField(path: string): string {
  return (path.split('/')[1] ?? '').replace(/~1/g, '/').replace(/~0/g, '~');
}

/** Asks the person whether a tool call may run. The call itself is saved on the server; the
 * answer carries on this same reply, sending the approval id, the choice and, when the person
 * changed the call or wrote a note, those too. Where it can't be answered (expired, an older
 * reply, a conversation shared with the viewer) it is one line. */
export function ToolApprovalCard({ approval, isLatest, rowId }: ToolApprovalCardProps) {
  const { t } = useTranslation();
  const minutesLeft = useMinutesLeft(approval.expiresAt);
  const original = approval.arguments ?? null;
  const [remember, setRemember] = useState(false);
  const [forConversation, setForConversation] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...(original ?? {}) }));
  const [errors, setErrors] = useState<EditError[]>(() => approval.editErrors ?? []);
  const [editing, setEditing] = useState(() => Boolean(approval.editErrors?.length));
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const cardRef = useRef<HTMLDivElement>(null);
  // Someone viewing a conversation shared with them can't send in it.
  const canSend = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null)) !== false;

  const canEdit = approval.editable === true && Boolean(approval.inputSchema) && original !== null;
  const fields = useMemo(() => (canEdit && approval.inputSchema ? schemaFields(approval.inputSchema) : []), [canEdit, approval.inputSchema]);
  const changed = useMemo(
    () => new Set(Object.keys({ ...original, ...values }).filter((name) => !same(values[name], original?.[name]))),
    [values, original],
  );
  const edited = changed.size > 0;
  const showForm = editing && canEdit;

  useEffect(() => {
    if (focusTick) cardRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [focusTick]);

  const tool = approvalToolLabel(approval);
  const server = approval.serverName;
  const expired = minutesLeft === 0;

  if (expired || !isLatest || !canSend) {
    const notice = expired ? 'expired' : canSend ? 'passed' : 'waiting';
    return (
      <Flex data-testid="tool-approval-card" align="start" gap="2" style={{ color: 'var(--slate-11)' }}>
        <MaterialIcon name={expired ? 'timer_off' : 'pending_actions'} size={16} color="var(--slate-9)" style={{ marginTop: 3 }} />
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

  const lines = original ? approvalArgumentLines(values, { full: showAll }) : [];
  const anyCut = !showAll && lines.some((line) => line.cut);

  const answer = (decision: ToolApprovalDecision, extra: { arguments?: Record<string, unknown>; feedback?: string } = {}) => {
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId || useChatStore.getState().slots[slotId]?.isStreaming) return;
    const query = toolApprovalResumeQuery(decision);
    // An edit or a note is about this one call, so it never sets a lasting rule.
    const plain = !extra.arguments && !extra.feedback;
    const request = buildStreamChatRequestForSlot(slotId, query, undefined, {
      approvalId: approval.approvalId,
      decision,
      ...(approval.argsHash ? { argsHash: approval.argsHash } : {}),
      ...extra,
      ...(plain && remember && approval.canRemember !== false ? { remember: true } : {}),
      ...(plain && forConversation && decision === 'allow' ? { rememberForConversation: true } : {}),
    });
    if (!request) return;
    void streamMessageForSlot(slotId, query, request, { reuseRowId: rowId });
  };

  const refuse = (found: EditError[]) => {
    setErrors(found);
    setEditing(true);
    setFocusTick((n) => n + 1);
  };

  const allow = async () => {
    if (!edited) {
      answer('allow');
      return;
    }
    const local = checkArgs(fields, values);
    if (local.length) {
      refuse(local);
      return;
    }
    const args = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== undefined));
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
    answer('allow', { arguments: args });
  };

  const onFormKeyDown = (e: React.KeyboardEvent) => {
    // A Select or other layer closing on Esc has already handled it.
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    e.stopPropagation();
    setEditing(false);
  };

  const onNoteKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    e.stopPropagation();
    setNoteOpen(false);
  };

  return (
    <Box
      ref={cardRef}
      data-testid="tool-approval-card"
      role="group"
      aria-label={t('chat.toolApproval.needsApproval')}
      style={{
        border: '1px solid var(--amber-a6)',
        borderRadius: 'var(--radius-4)',
        backgroundColor: 'var(--amber-a2)',
        padding: 'var(--space-3) var(--space-4) var(--space-4)',
      }}
    >
      <Flex gap="3" align="start">
        <Flex
          align="center"
          justify="center"
          style={{ width: 32, height: 32, borderRadius: 'var(--radius-3)', backgroundColor: 'var(--amber-a4)', flexShrink: 0 }}
        >
          <ConnectorIcon type={server} size={18} color="var(--amber-11)" />
        </Flex>
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <Text size="3" weight="medium" title={approval.toolName} style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
              {tool}
            </Text>
            <Badge size="1" variant="soft" color="gray">{server}</Badge>
            {approval.kind === 'destructive' && (
              <Badge size="1" variant="soft" color="red">
                {t('chat.toolApproval.cantBeUndone')}
              </Badge>
            )}
          </Flex>
          {approval.argsSummary && (
            <Text as="p" size="2" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
              {approval.argsSummary}
            </Text>
          )}
          <Text as="p" size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.toolApproval.needsApproval')}
            {' · '}
            {minutesLeft <= 1 ? t('chat.toolApproval.expiresSoon') : t('chat.toolApproval.expiresIn', { count: minutesLeft })}
          </Text>
        </Box>
      </Flex>

      <Box
        mt="3"
        className="no-scrollbar"
        onKeyDown={showForm ? onFormKeyDown : undefined}
        style={{
          border: '1px solid var(--slate-a5)',
          borderRadius: 'var(--radius-3)',
          backgroundColor: 'var(--color-panel-solid)',
          padding: showForm ? 'var(--space-3)' : 'var(--space-2) var(--space-3)',
          maxHeight: showAll || showForm ? 360 : 220,
          overflowY: 'auto',
        }}
      >
        {showForm ? (
          <SchemaForm
            fields={fields}
            values={values}
            errors={fieldErrors}
            changed={changed}
            onChange={(name, value) => {
              setValues((prev) => ({ ...prev, [name]: value }));
              setErrors((prev) => prev.filter((e) => pointerField(e.path) !== name));
            }}
          />
        ) : approval.argumentsPreview ? (
          <>
            <Text as="p" size="1" style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {approval.argumentsPreview}
            </Text>
            <Text as="p" size="1" mt="1" style={{ color: 'var(--slate-10)' }}>
              {t('chat.toolApproval.partial')}
            </Text>
          </>
        ) : lines.length === 0 ? (
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('chat.toolApproval.noArguments')}
          </Text>
        ) : (
          <Box style={{ display: 'grid', gridTemplateColumns: 'minmax(72px, max-content) 1fr', columnGap: 'var(--space-4)', rowGap: 'var(--space-1)' }}>
            {lines.map((line) => (
              <React.Fragment key={line.name}>
                <Text size="1" style={{ color: 'var(--slate-10)', fontFamily: 'var(--code-font-family)', lineHeight: '20px' }}>
                  {line.name}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: '20px' }}>
                  {line.value}
                </Text>
              </React.Fragment>
            ))}
          </Box>
        )}
      </Box>
      {!showForm && (anyCut || showAll) && (
        <Button size="1" variant="ghost" mt="2" onClick={() => setShowAll((value) => !value)}>
          {showAll ? t('chat.toolApproval.showLess') : t('chat.toolApproval.showAll')}
        </Button>
      )}
      {(hasFieldErrors || cardErrors.length > 0) && (
        <Flex direction="column" gap="1" mt="2" role="alert">
          {hasFieldErrors && <Text size="1" style={{ color: 'var(--red-11)' }}>{t('chat.toolApproval.editRejected')}</Text>}
          {cardErrors.map((message) => (
            <Text key={message} size="1" style={{ color: 'var(--red-11)' }}>{message}</Text>
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
            aria-label={t('chat.toolApproval.changeSomethingElse')}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Flex gap="2">
            <Button size="2" disabled={!note.trim()} onClick={() => answer('deny', { feedback: note.trim() })}>
              {t('chat.toolApproval.sendToAgent')}
            </Button>
          </Flex>
        </Flex>
      )}

      <Flex mt="3" align="center" gap="2" wrap="wrap">
        <Button size="2" color={approval.kind === 'destructive' ? 'red' : undefined} loading={sending} onClick={() => void allow()}>
          {t('chat.toolApproval.allow')}
        </Button>
        {canEdit && (
          <Button size="2" variant={showForm ? 'soft' : 'outline'} aria-pressed={showForm} onClick={() => setEditing((value) => !value)}>
            {t('chat.toolApproval.edit')}
          </Button>
        )}
        {showForm && (
          <Button
            size="2"
            variant="ghost"
            color="gray"
            disabled={!edited}
            onClick={() => {
              setValues({ ...(original ?? {}) });
              setErrors([]);
            }}
          >
            {t('chat.toolApproval.reset')}
          </Button>
        )}
        <Button size="2" variant="soft" color="gray" onClick={() => answer('deny')}>
          {t('chat.toolApproval.deny')}
        </Button>
        <Button size="2" variant="ghost" color="gray" aria-expanded={noteOpen} onClick={() => setNoteOpen((value) => !value)}>
          {t('chat.toolApproval.changeSomethingElse')}
        </Button>
      </Flex>
      {!edited && (
        <Flex mt="3" direction="column" gap="2">
          {approval.canRemember !== false && (
            <Text as="label" size="1" style={{ color: 'var(--slate-11)', cursor: 'pointer' }}>
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
          )}
          <Box>
            <Text as="label" size="1" style={{ color: 'var(--slate-11)', cursor: 'pointer' }}>
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
            <Text as="p" id={`${approval.approvalId}-conversation-hint`} size="1" style={{ color: 'var(--slate-10)', paddingLeft: 'var(--space-5)' }}>
              {t('chat.toolApproval.allowForConversationHint')}
            </Text>
          </Box>
        </Flex>
      )}
    </Box>
  );
}

const RECEIPT_ICON: Record<SettledToolApproval['answer'], { name: string; color: string }> = {
  allowed: { name: 'check_circle', color: 'var(--green-9)' },
  allowedNotRun: { name: 'block', color: 'var(--amber-9)' },
  declined: { name: 'block', color: 'var(--slate-9)' },
  expired: { name: 'timer_off', color: 'var(--slate-9)' },
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
    ? t('chat.toolApproval.receipt.declinedWithNote', { note: item.note })
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
          <Text size="2" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>{text}</Text>
        )}
        {item.server && <Badge size="1" variant="soft" color="gray">{item.server}</Badge>}
      </Flex>
      {open && item.changedFields && (
        <Text as="p" size="1" style={{ color: 'var(--slate-10)', paddingLeft: 'var(--space-5)' }}>
          {t('chat.toolApproval.receipt.changedFields', { fields: item.changedFields.join(', ') })}
        </Text>
      )}
    </Box>
  );
}
