'use client';

import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { approvalValueText, clipValue } from '../../../tool-approval';
import { documentLayout } from './document-layout';
import type { FieldSpec } from './schema-fields';

const blank = (v: unknown) => v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);

/** The call read as the thing it makes, in the same regions as the edit form: who it goes to, its
 * title, one line of settings, its text, and ids and nested values folded under Details. */
export function DocumentView({ fields, values }: { fields: FieldSpec[]; values: Record<string, unknown> }) {
  const { t } = useTranslation();
  const detailsId = useId();
  const [full, setFull] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const layout = documentLayout(fields, values);
  const set = (f: FieldSpec) => !blank(values[f.name]);
  const text = (f: FieldSpec) => {
    const value = values[f.name];
    return f.control === 'switch' && typeof value === 'boolean' ? t(value ? 'common.yes' : 'common.no') : approvalValueText(f.name, value);
  };
  const pair = (f: FieldSpec) => t('chat.toolApproval.pill', { label: f.label, value: text(f) });
  const meta = [...layout.pills, ...layout.lists].filter(set);
  const body = layout.body.filter(set).map((f) => ({ field: f, ...clipValue(text(f), full) }));
  const headline = layout.headline && set(layout.headline) ? clipValue(text(layout.headline), full) : null;
  const details = layout.more.filter(set);
  const recipients = layout.recipients.filter(set);
  const anyCut = Boolean(headline?.cut) || body.some((b) => b.cut);

  if (!recipients.length && !headline && !meta.length && !body.length && !details.length) {
    return <Text size="1" style={{ color: 'var(--gray-11)' }}>{t('chat.toolApproval.noArguments')}</Text>;
  }

  return (
    <Flex direction="column" gap="2">
      {recipients.map((f) => (
        <Text key={f.name} as="p" size="2" style={{ color: 'var(--gray-11)', overflowWrap: 'anywhere' }}>{pair(f)}</Text>
      ))}
      {headline && (
        <Text as="p" size="3" weight="medium" style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>{headline.text}</Text>
      )}
      {meta.length > 0 && (
        <Text as="p" size="1" style={{ color: 'var(--gray-11)', overflowWrap: 'anywhere' }}>{meta.map(pair).join(' · ')}</Text>
      )}
      {body.map((b) => (
        <Text key={b.field.name} as="p" size="2" aria-label={b.field.label} style={{ color: 'var(--gray-12)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {b.text}
        </Text>
      ))}
      {(anyCut || full) && (
        <Box>
          <Button size="1" variant="ghost" aria-expanded={full} onClick={() => setFull((value) => !value)}>
            {full ? t('chat.toolApproval.showLess') : t('chat.toolApproval.showAll')}
          </Button>
        </Box>
      )}
      {details.length > 0 && (
        <Box>
          <Button size="1" variant="ghost" color="gray" aria-expanded={detailsOpen} aria-controls={detailsId} onClick={() => setDetailsOpen((open) => !open)}>
            {t('chat.toolApproval.details')}
            <MaterialIcon name={detailsOpen ? 'expand_less' : 'expand_more'} size={16} />
          </Button>
          {detailsOpen && (
            <Box id={detailsId} mt="2" style={{ display: 'grid', gridTemplateColumns: 'max-content minmax(0, 1fr)', columnGap: 'var(--space-3)', rowGap: 'var(--space-1)' }}>
              {details.map((f) => (
                <React.Fragment key={f.name}>
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>{f.label}</Text>
                  <Text size="1" style={{ color: 'var(--gray-12)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'var(--code-font-family)' }}>
                    {text(f)}
                  </Text>
                </React.Fragment>
              ))}
            </Box>
          )}
        </Box>
      )}
    </Flex>
  );
}
