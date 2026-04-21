'use client';

import { Suspense } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { RecordViewShell } from './components/record-view-shell';

/**
 * View a record by id. Uses a query param because `output: 'export'` disallows
 * dynamic `[recordId]` segments without a fixed `generateStaticParams` list.
 *
 * URL: `/record?recordId=<id>`
 */
function RecordPageContent() {
  const { t } = useTranslation();
  const searchParams = useSearchParams();
  const recordId = searchParams.get('recordId')?.trim() || '';

  if (!recordId) {
    return (
      <Box p="4">
        <Text size="2" color="gray">
          {t('recordView.missingRecordId', 'Missing record id')}
        </Text>
      </Box>
    );
  }

  return <RecordViewShell recordId={recordId} />;
}

function RecordPageSuspenseFallback() {
  const { t } = useTranslation();
  return (
    <Box p="4">
      <Text size="2" color="gray">
        {t('recordView.loading')}
      </Text>
    </Box>
  );
}

export default function RecordPage() {
  return (
    <Suspense fallback={<RecordPageSuspenseFallback />}>
      <RecordPageContent />
    </Suspense>
  );
}
