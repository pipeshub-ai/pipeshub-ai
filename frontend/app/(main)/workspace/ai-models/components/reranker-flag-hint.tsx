'use client';

import Link from 'next/link';
import { Callout, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  selectFeatureFlagsLoaded,
  selectRerankerEnabled,
  useFeatureFlagsStore,
} from '@/lib/store/feature-flags-store';

/** A configured reranker does nothing until the Labs flag is on; say so where it is configured. */
export function RerankerFlagHint() {
  const { t } = useTranslation();
  const loaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const enabled = useFeatureFlagsStore(selectRerankerEnabled);
  if (!loaded || enabled) return null;

  return (
    <Callout.Root color="amber" size="1" variant="surface" data-testid="reranker-flag-hint">
      <Callout.Icon>
        <MaterialIcon name="info" size={16} />
      </Callout.Icon>
      <Callout.Text>
        <Text size="2" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.aiModels.rerankerOffHint')}{' '}
          <Link href="/workspace/labs" style={{ color: 'var(--accent-11)' }}>
            {t('workspace.aiModels.rerankerOffHintLink')}
          </Link>
        </Text>
      </Callout.Text>
    </Callout.Root>
  );
}
