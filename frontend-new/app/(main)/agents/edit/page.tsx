'use client';

import { Suspense } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { AgentBuilder } from '@/app/(main)/agents/agent-builder/agent-builder';

/**
 * Edit an existing agent. Uses query param because `output: 'export'` disallows
 * dynamic `[agentKey]` segments without a fixed `generateStaticParams` list.
 *
 * URL: `/agents/edit?agentKey=<uuid>`
 */
function EditAgentContent() {
  const { t } = useTranslation();
  const searchParams = useSearchParams();
  const agentKey = searchParams.get('agentKey')?.trim() || '';

  if (!agentKey) {
    return (
      <Box p="4">
        <Text size="2" color="gray">
          {t('agents.missingAgentKey')}
        </Text>
      </Box>
    );
  }

  return <AgentBuilder agentKey={agentKey} />;
}

function EditPageSuspenseFallback() {
  const { t } = useTranslation();
  return (
    <Box p="4">
      <Text size="2" color="gray">
        {t('agents.loading')}
      </Text>
    </Box>
  );
}

export default function EditAgentPage() {
  return (
    <Box style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <Suspense fallback={<EditPageSuspenseFallback />}>
        <EditAgentContent />
      </Suspense>
    </Box>
  );
}
