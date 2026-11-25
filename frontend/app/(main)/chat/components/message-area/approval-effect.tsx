'use client';

import React, { useId, useMemo, useState } from 'react';
import { Box, Button, Flex, Text, TextArea } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { diffLines, diffTarget, MAX_DENY_REASON_CHARS, type DiffLineKind } from '../../approval-preview';

/** Lines a diff shows before "Show the full change": enough for a typical edit, short enough for a phone. */
export const DIFF_PREVIEW_LINES = 12;

const LINE_STYLE: Record<DiffLineKind, React.CSSProperties> = {
  file: { display: 'none' },
  hunk: { color: 'var(--slate-11)' },
  add: { backgroundColor: 'var(--green-3)', color: 'var(--green-11)' },
  del: { backgroundColor: 'var(--red-3)', color: 'var(--red-11)' },
  ctx: { color: 'var(--slate-11)' },
};

/**
 * "What changes" (UX-03, C3.71): the edit a held call would make, as a coloured unified diff the worker built from
 * the call's own before and after text. Long diffs show their first lines; a diff the worker cut says so.
 */
export function EffectDiff({ diff, truncated }: { diff: string; truncated: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const target = useMemo(() => diffTarget(diff), [diff]);
  const lines = useMemo(() => diffLines(diff).filter((line) => line.kind !== 'file'), [diff]);
  const shown = open ? lines : lines.slice(0, DIFF_PREVIEW_LINES);
  return (
    <Box data-testid="approval-diff">
      <Text as="div" size="1" weight="bold" style={{ marginBottom: 'var(--space-1)' }}>
        {t('approvalCard.effect.whatChanges')}
      </Text>
      <Box
        role="region"
        aria-label={t('approvalCard.effect.diffLabel', { target: target ?? t('approvalCard.effect.diffUntitled') })}
        style={{
          border: '1px solid var(--slate-4)',
          borderRadius: 'var(--radius-2)',
          overflow: 'hidden',
          fontFamily: 'var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, monospace)',
          fontSize: 12,
          lineHeight: 1.5,
        }}
      >
        {target ? (
          <Box
            data-testid="approval-diff-target"
            style={{ backgroundColor: 'var(--slate-3)', color: 'var(--slate-11)', padding: '4px 10px', overflowWrap: 'anywhere' }}
          >
            {target}
          </Box>
        ) : null}
        <Box style={{ maxHeight: open ? 420 : undefined, overflow: 'auto' }}>
          {shown.map((line, index) => (
            <Box
              key={index}
              data-kind={line.kind}
              style={{ ...LINE_STYLE[line.kind], padding: '0 10px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
            >
              {line.text || ' '}
            </Box>
          ))}
        </Box>
      </Box>
      {lines.length > DIFF_PREVIEW_LINES ? (
        <Button
          variant="ghost"
          size="1"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-testid="approval-diff-toggle"
          style={{ marginTop: 'var(--space-2)', minHeight: 32 }}
        >
          {open ? t('approvalCard.effect.showLess') : t('approvalCard.effect.showFull', { count: lines.length })}
        </Button>
      ) : null}
      {truncated ? (
        <Text as="div" size="1" color="gray" data-testid="approval-diff-truncated" style={{ marginTop: 'var(--space-1)' }}>
          {t('approvalCard.effect.truncated')}
        </Text>
      ) : null}
    </Box>
  );
}

/** A declared per-call cost as money: cents for most, more digits for fractions of a cent. */
export function formatCost(usd: number, locale?: string): string {
  return new Intl.NumberFormat(locale || 'en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: usd > 0 && usd < 0.01 ? 4 : 2,
  }).format(usd);
}

/** "Estimated cost: about $0.03" for a tool that declares its cost; nothing is ever guessed (UX-03). */
export function EffectCost({ usd, calls = 1 }: { usd: number; calls?: number }) {
  const { t, i18n } = useTranslation();
  const total = usd * Math.max(1, calls);
  return (
    <Flex align="baseline" gap="2" wrap="wrap" data-testid="approval-cost">
      <Text size="2" weight="bold">
        {t('approvalCard.effect.cost', { amount: formatCost(total, i18n.language) })}
      </Text>
      <Text size="1" color="gray">
        {calls > 1
          ? t('approvalCard.effect.costEach', { amount: formatCost(usd, i18n.language), calls })
          : t('approvalCard.effect.costBasis')}
      </Text>
    </Flex>
  );
}

/**
 * Deny, and optionally say why (AP-05, C3.72): the field opens in place of the card's buttons; Deny again sends the
 * denial with the reason (none when empty), Cancel or Escape go back to the buttons.
 */
export function DenyReasonForm({
  onDeny,
  onCancel,
  disabled = false,
  denyLabel,
  denyText,
}: {
  onDeny: (reason: string) => void;
  onCancel: () => void;
  disabled?: boolean;
  /** The Deny button's accessible name, which names the call. */
  denyLabel: string;
  denyText: string;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const id = useId();
  return (
    <Flex direction="column" gap="1" data-testid="deny-reason">
      <Text as="label" htmlFor={id} size="1" weight="bold">
        {t('approvalCard.denyReason.label')}
      </Text>
      <TextArea
        id={id}
        autoFocus
        value={reason}
        maxLength={MAX_DENY_REASON_CHARS}
        placeholder={t('approvalCard.denyReason.placeholder')}
        onChange={(event) => setReason(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onCancel();
          }
        }}
        disabled={disabled}
        rows={2}
        data-testid="deny-reason-input"
        style={{ minHeight: 56 }}
      />
      <Text size="1" color="gray" align="right" aria-live="polite">
        {t('approvalCard.denyReason.counter', { count: reason.length, max: MAX_DENY_REASON_CHARS })}
      </Text>
      <Flex gap="2" justify="end" wrap="wrap">
        <Button variant="ghost" onClick={onCancel} disabled={disabled} style={{ minHeight: 44 }}>
          {t('approvalCard.denyReason.cancel')}
        </Button>
        <Button
          color="red"
          onClick={() => onDeny(reason.trim())}
          disabled={disabled}
          aria-label={denyLabel}
          data-testid="deny-confirm"
          style={{ minHeight: 44 }}
        >
          {denyText}
        </Button>
      </Flex>
    </Flex>
  );
}
