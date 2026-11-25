'use client';

import { Suspense } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { GraphExplorer } from './components/graph-explorer';

// Off until it has passed its gates; a build turns it on with NEXT_PUBLIC_GRAPH_EXPLORER_ACTIVE=true.
const ACTIVE = process.env.NEXT_PUBLIC_GRAPH_EXPLORER_ACTIVE === 'true';

/**
 * `/graph?node=block:<id>`: the focus node is in the query (no dynamic segments, the
 * desktop build is a static export). The URL holds ids and filters only.
 */
export default function GraphPage() {
  const { t } = useTranslation();
  if (!ACTIVE) {
    return (
      <Box p="4">
        <Text color="gray">{t('graph.ui.disabled')}</Text>
      </Box>
    );
  }
  return (
    <Suspense fallback={null}>
      <GraphExplorer />
    </Suspense>
  );
}
