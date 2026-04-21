'use client';

import { Suspense, useEffect, useState } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { usePathname, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { RecordViewShell } from './components/record-view-shell';

/**
 * View a record by id.
 *
 * URL: `/record/<recordId>` (canonical).
 *
 * With `output: 'export'` we can't ship a dynamic `[recordId]` segment
 * (`generateStaticParams()` would have to enumerate every id), so the
 * Next.js build emits a single `/record/index.html` shell. The Node.js
 * backend serves that shell for any `/record/:id` URL, and this component
 * recovers the id from `window.location.pathname` on the client. A
 * `?recordId=<id>` query fallback is honored for `next dev` and any
 * legacy callers that still use the query form.
 */
function extractRecordIdFromPath(pathname: string | null): string {
  if (!pathname) return '';
  const match = pathname.match(/^\/record\/([^/?#]+)\/?$/);
  return match?.[1] ? decodeURIComponent(match[1]) : '';
}

function RecordPageContent() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Prefer the path segment; fall back to a query param for dev-mode rewrites
  // and any legacy callers. Resolved once per pathname/search change.
  const [recordId, setRecordId] = useState<string>(() => {
    const fromPath = extractRecordIdFromPath(pathname);
    if (fromPath) return fromPath;
    return searchParams.get('recordId')?.trim() || '';
  });

  useEffect(() => {
    const fromPath = extractRecordIdFromPath(pathname);
    const fromQuery = searchParams.get('recordId')?.trim() || '';
    setRecordId(fromPath || fromQuery);
  }, [pathname, searchParams]);

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
        {t('recordView.loading', 'Loading...')}
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
