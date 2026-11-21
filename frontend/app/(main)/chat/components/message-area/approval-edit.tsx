'use client';

import React, { useMemo, useState } from 'react';
import { Box, Button, Checkbox, Flex, Link, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { redactText, safeText } from '../../approval-preview';
import { MarkedText } from './display-safety';
import {
  changedKeys,
  editDiff,
  editedArguments,
  invalidKeys,
  type EditDraft,
  type EditField,
} from '../../approval-edits';

const TARGET: React.CSSProperties = { minHeight: 44 };
/** Where a key or password is kept instead of being typed into a call (WP-39's refusal says the same). */
export const CREDENTIALS_PAGE = '/workspace/actions/';

const BOX: React.CSSProperties = {
  padding: 'var(--space-2)',
  borderRadius: 'var(--radius-2)',
  backgroundColor: 'var(--olive-3)',
};
const CHANGED: React.CSSProperties = { boxShadow: '0 0 0 1px var(--accent-9)', borderRadius: 'var(--radius-2)' };

/**
 * "Looks like a password or key": an edit never carries one; the person saves it as a credential instead. Once the
 * server has refused an edit (`refused`), the line also says nothing was approved, in WP-39's words.
 */
export function SecretInEditNotice({ refused = false }: { refused?: boolean }) {
  const { t } = useTranslation();
  return (
    <Text size="2" color="red" role="alert" data-testid="approval-edit-secret">
      {t(refused ? 'approvalCard.editHasSecret' : 'approvalCard.edit.hasSecret')}{' '}
      <Link href={CREDENTIALS_PAGE} size="2">
        {t('approvalCard.edit.openActions')}
      </Link>
    </Text>
  );
}

function FieldEditor({
  field,
  value,
  changed,
  invalid,
  disabled,
  onChange,
}: {
  field: EditField;
  value: string;
  changed: boolean;
  invalid: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const id = `approval-edit-${field.key.replace(/[^\w-]/g, '-')}`;
  const label = (
    <Text as="label" htmlFor={id} size="1" color="gray">
      {field.label}
      {changed ? <Text style={{ color: 'var(--accent-11)' }}>{` · ${t('approvalCard.edit.changed')}`}</Text> : null}
    </Text>
  );
  if (field.kind === 'fixed') {
    return (
      <Flex direction="column" gap="1" data-testid="approval-edit-fixed">
        <Text as="div" size="1" color="gray">
          {field.label}
        </Text>
        <Text as="div" size="2" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          <MarkedText text={field.preview?.value ?? ''} />
        </Text>
        <Text as="div" size="1" color="gray">
          {t('approvalCard.edit.cannotEdit')}
        </Text>
      </Flex>
    );
  }
  return (
    <Flex direction="column" gap="1" data-testid="approval-edit-field" data-changed={changed ? 'true' : undefined}>
      {field.kind === 'boolean' ? (
        <Text as="label" size="2" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minHeight: 44, cursor: 'pointer' }}>
          <Checkbox
            id={id}
            checked={value === 'true'}
            disabled={disabled}
            onCheckedChange={(checked) => onChange(checked === true ? 'true' : 'false')}
          />
          {field.label}
          {changed ? <Text style={{ color: 'var(--accent-11)' }}>{` · ${t('approvalCard.edit.changed')}`}</Text> : null}
        </Text>
      ) : (
        <>
          {label}
          <Box style={changed ? CHANGED : undefined}>
            {field.multiline ? (
              <TextArea
                id={id}
                value={value}
                disabled={disabled}
                rows={4}
                onChange={(event) => onChange(event.target.value)}
                aria-invalid={invalid || undefined}
              />
            ) : (
              <TextField.Root
                id={id}
                value={value}
                disabled={disabled}
                inputMode={field.kind === 'number' ? 'decimal' : undefined}
                onChange={(event) => onChange(event.target.value)}
                aria-invalid={invalid || undefined}
                style={{ minHeight: 44 }}
              />
            )}
          </Box>
        </>
      )}
      {changed ? (
        <Text size="1" color="gray" data-testid="approval-edit-was" style={{ textDecoration: 'line-through', wordBreak: 'break-word' }}>
          {field.kind === 'boolean'
            ? field.original === 'true'
              ? t('approvalCard.edit.yes')
              : t('approvalCard.edit.no')
            : safeText(field.original, 'prose')}
        </Text>
      ) : null}
      {invalid ? (
        <Text size="1" color="red">
          {t('approvalCard.edit.notANumber')}
        </Text>
      ) : null}
    </Flex>
  );
}

/**
 * AP-06: the card's values, edited in place (no dialog). Changed values are outlined, with the call's own value
 * struck through under them. Escape cancels, Ctrl/Cmd+Enter approves with the changes (UXINT-4 keys).
 */
export function ApprovalEditForm({
  args,
  fields,
  sending,
  onCancel,
  onApprove,
}: {
  args: string;
  fields: EditField[];
  sending: boolean;
  onCancel: () => void;
  onApprove: (edits: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const initial = useMemo(
    () => Object.fromEntries(fields.filter((f) => f.kind !== 'fixed').map((f) => [f.key, f.original])) as EditDraft,
    [fields],
  );
  const [draft, setDraft] = useState<EditDraft>(initial);
  const changed = changedKeys(fields, draft);
  const invalid = invalidKeys(fields, draft);
  const secret = changed.some((key) => redactText(draft[key] ?? '').redacted);
  const ready = changed.length > 0 && invalid.length === 0 && !secret && !sending;
  const approve = () => {
    if (ready) onApprove(editedArguments(args, fields, draft));
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onCancel();
      return;
    }
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      event.stopPropagation();
      approve();
    }
  };
  return (
    <Flex direction="column" gap="3" data-testid="approval-edit-form" onKeyDown={onKeyDown}>
      <Text size="2">{t('approvalCard.edit.intro')}</Text>
      <Flex direction="column" gap="3" style={BOX}>
        {fields.map((field) => (
          <FieldEditor
            key={field.key}
            field={field}
            value={draft[field.key] ?? ''}
            changed={changed.includes(field.key)}
            invalid={invalid.includes(field.key)}
            disabled={sending}
            onChange={(value) => setDraft((prev) => ({ ...prev, [field.key]: value }))}
          />
        ))}
      </Flex>
      {secret ? <SecretInEditNotice /> : null}
      <Flex gap="2" justify="end" wrap="wrap" direction={isMobile ? 'column-reverse' : 'row'}>
        {changed.length > 0 ? (
          <Button variant="ghost" disabled={sending} onClick={() => setDraft(initial)} style={TARGET} data-testid="approval-edit-undo">
            {t('approvalCard.edit.undo')}
          </Button>
        ) : null}
        <Button variant="soft" color="gray" disabled={sending} onClick={onCancel} style={TARGET} data-testid="approval-edit-cancel">
          {t('approvalCard.edit.cancel')}
        </Button>
        <Button disabled={!ready} onClick={approve} style={TARGET} data-testid="approval-edit-approve">
          {sending ? t('approvalCard.sending') : t('approvalCard.edit.approve')}
        </Button>
      </Flex>
      <Text size="1" color="gray">
        {t('approvalCard.edit.keys')}
      </Text>
    </Flex>
  );
}

const LINE: React.CSSProperties = {
  fontFamily: 'var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, monospace)',
  fontSize: 12,
  padding: '2px 6px',
  borderRadius: 4,
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
};

/**
 * AP-06 receipt: what the person changed, old and new per value, and on request everything that was sent. The same
 * live and after a reload (the saved answer carries the approved arguments).
 */
export function EditedReceipt({
  args,
  editedArgs,
  renderAll,
}: {
  args: string;
  editedArgs: string;
  renderAll: (args: string) => React.ReactNode;
}) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const lines = useMemo(() => editDiff(args, editedArgs), [args, editedArgs]);
  return (
    <Flex direction="column" gap="2" data-testid="approval-edited-receipt">
      {lines.length > 0 ? (
        <Flex direction="column" gap="2" style={BOX}>
          {lines.map((line) => (
            <Box key={line.key} data-testid="approval-edited-line">
              <Text as="div" size="1" color="gray" style={{ marginBottom: 2 }}>
                {line.label}
              </Text>
              <Box style={{ ...LINE, backgroundColor: 'var(--red-3)', color: 'var(--red-11)' }} aria-label={t('approvalCard.edit.before')}>
                {line.before || ' '}
              </Box>
              <Box style={{ ...LINE, backgroundColor: 'var(--green-3)', color: 'var(--green-11)', marginTop: 2 }} aria-label={t('approvalCard.edit.after')}>
                {line.after || ' '}
              </Box>
            </Box>
          ))}
        </Flex>
      ) : null}
      <Button
        variant="ghost"
        size="1"
        onClick={() => setAll((v) => !v)}
        aria-expanded={all}
        data-testid="approval-edited-all"
        style={{ alignSelf: 'flex-start', minHeight: 32 }}
      >
        {all ? t('approvalCard.edit.hideAll') : t('approvalCard.edit.showAll')}
      </Button>
      {all ? renderAll(editedArgs) : null}
    </Flex>
  );
}
