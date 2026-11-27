'use client';

import React, { useState } from 'react';
import { Box, Button, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { PreviewField } from '../../approval-preview';
import { MarkedText } from './display-safety';

/** One labelled value of a call, masked and display-safe, with "Show all" when it was cut. */
export function PreviewValue({ field }: { field: PreviewField }) {
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
