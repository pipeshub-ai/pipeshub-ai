'use client';

import React, { useState } from 'react';
import { Card, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { formatPasteCount } from '../../utils/paste-attachment';
import { ATTACHMENT_CHIP_HEIGHT, ATTACHMENT_CHIP_MAX_WIDTH } from '../selected-collections';
import { TextPreviewDialog } from '../text-preview-dialog';
import type { QuestionPasteKind } from './question-display';

interface SentPasteChipProps {
  kind: QuestionPasteKind;
  /** Character and line counts, when known; the chip shows them after its label. */
  chars?: number;
  lines?: number;
  onOpen: () => void;
}

/**
 * Pasted material on a sent message: the composer's 28px paste chip without its
 * remove button. Opens the reader.
 */
export function SentPasteChip({ kind, chars, lines, onOpen }: SentPasteChipProps) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const label =
    kind === 'code'
      ? t('chat.question.pastedCode')
      : t('chat.attachments.pastedText', { defaultValue: 'Pasted text' });
  const meta =
    chars !== undefined && lines !== undefined
      ? t('chat.attachments.pasteMeta', { count: lines, chars: formatPasteCount(chars) })
      : undefined;
  const name = meta ? t('chat.attachments.pasteChipLabel', { label, meta }) : label;

  return (
    <Card asChild variant="ghost">
      <button
        type="button"
        aria-label={name}
        onClick={onOpen}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          boxSizing: 'border-box',
          flexShrink: 0,
          height: ATTACHMENT_CHIP_HEIGHT,
          maxWidth: ATTACHMENT_CHIP_MAX_WIDTH,
          minWidth: 0,
          margin: 0,
          paddingBlock: 0,
          paddingLeft: 'calc(var(--space-1) * 1.5)',
          paddingRight: 'calc(var(--space-2) + var(--space-1) / 2)',
          cursor: 'pointer',
          borderRadius: 'var(--radius-4)',
          border: `1px solid ${hovered ? 'var(--accent-a7)' : 'var(--border-strong)'}`,
          background: 'var(--surface-raised)',
          boxShadow: hovered ? 'var(--shadow-2)' : 'none',
          transition:
            'border-color var(--motion-micro) var(--motion-ease), box-shadow var(--motion-micro) var(--motion-ease)',
        }}
      >
        <Flex
          align="center"
          justify="center"
          aria-hidden
          style={{
            width: 'calc(var(--space-4) + var(--space-1) / 2)',
            height: 'calc(var(--space-4) + var(--space-1) / 2)',
            borderRadius: 'var(--radius-2)',
            background: 'var(--fill-muted)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={kind === 'code' ? 'code' : 'content_paste'} size={12} color="var(--text-secondary)" />
        </Flex>
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {label}
        </Text>
        {meta && (
          <Text
            size="1"
            style={{
              color: 'var(--text-secondary)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              minWidth: 0,
            }}
          >
            {meta}
          </Text>
        )}
      </button>
    </Card>
  );
}

/** A paste whose text is already in hand (lifted out of the question), with its reader. */
export function InlinePasteChip({ kind, text }: { kind: QuestionPasteKind; text: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <SentPasteChip kind={kind} chars={text.length} lines={text.split('\n').length} onOpen={() => setOpen(true)} />
      {open && (
        <TextPreviewDialog
          open
          onOpenChange={(next) => {
            if (!next) setOpen(false);
          }}
          title={
            kind === 'code'
              ? t('chat.question.pastedCode')
              : t('chat.attachments.pastedText', { defaultValue: 'Pasted text' })
          }
          loadText={() => Promise.resolve(text)}
        />
      )}
    </>
  );
}
