'use client';

import { Suspense, useEffect, useState } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
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
 *
 * Deep-link sub-paths such as `/record/<recordId>/preview` are treated as
 * aliases: the component redirects to the canonical form so the URL bar
 * always shows `/record/<recordId>`.
 */
function extractRecordIdFromPath(pathname: string | null): string {
  if (!pathname) return '';
  // Allow an optional sub-path (e.g. /preview) after the record id so that
  // deep-link URLs like /record/<id>/preview can still extract the id before
  // the client-side redirect fires.
  const match = pathname.match(/^\/record\/([^/?#]+)(?:\/.*)?$/);
  return match?.[1] ? decodeURIComponent(match[1]) : '';
}

function RecordPageContent() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();

  // If the path has a sub-segment after the record id (e.g. /record/<id>/preview),
  // redirect to the canonical form /record/<id> so the URL bar stays clean.
  useEffect(() => {
    if (!pathname) return;
    if (/^\/record\/[^/?#]+\/.+/.test(pathname)) {
      const id = extractRecordIdFromPath(pathname);
      if (id) {
        router.replace(`/record/${id}/`);
      }
    }
  }, [pathname, router]);
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
