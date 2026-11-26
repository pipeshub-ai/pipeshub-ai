'use client';

import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Heading, SegmentedControl, Text } from '@radix-ui/themes';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useFeatureFlagGuard } from '@/lib/hooks/use-feature-flag-guard';
import {
  selectAgentHarnessEnabled,
  selectAgentHarnessOn,
  useFeatureFlagsStore,
} from '@/lib/store/feature-flags-store';
import { LoadState } from '../../ai-accounts/components/load-state';
import { SettingsSection } from '../../components/settings-section';
import type { MemoryFilter } from '../api';
import { useMemoriesStore } from '../store';
import { MemoryRow } from './memory-row';
import { RememberSwitch } from './remember-switch';

const FILTERS: readonly MemoryFilter[] = ['all', 'review', 'accepted'];

/**
 * "Memories" (P7.3): what the agent remembers about the reader, which only
 * they see; they edit or delete it, and accept or reject what waits for
 * review. Memory applies to durable runs only, but what is stored stays the
 * reader's to see and delete while new runs are stopped (UX3, R1 F17): the
 * page follows the org's harness (ADR-OPS-08), and says when nothing is saved.
 */
export function MyMemories() {
  const { t } = useTranslation();
  const { ready } = useFeatureFlagGuard(selectAgentHarnessOn);
  const saving = useFeatureFlagsStore(selectAgentHarnessEnabled);
  const { own, filter, loadOwn, loadRemember, editOwn, reviewOwn, removeOwn } = useMemoriesStore();

  useEffect(() => {
    if (!ready) return;
    void loadOwn();
    void loadRemember();
  }, [ready, loadOwn, loadRemember]);

  if (!ready) return null;

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px var(--workspace-gutter, 100px)', paddingBottom: 80, maxWidth: 1100 }}>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Flex align="center" gap="2">
            <Heading as="h1" size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('memories.title')}
            </Heading>
            <BetaBadge />
          </Flex>
          <Text size="2" as="p" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-1)' }}>
            {t('memories.subtitle')}
          </Text>
          {!saving && (
            <Text size="2" as="p" role="note" style={{ color: 'var(--amber-11)', marginTop: 'var(--space-2)' }}>
              {t('memories.notSaving')}
            </Text>
          )}
        </Box>
        <Box style={{ marginBottom: 'var(--space-4)' }}>
          <RememberSwitch />
        </Box>
        <SettingsSection
          title={t('memories.listTitle')}
          description={t('memories.listDescription')}
          rightAction={
            <SegmentedControl.Root
              value={filter}
              aria-label={t('memories.filterLabel')}
              onValueChange={(value) => void loadOwn(value as MemoryFilter)}
            >
              {FILTERS.map((value) => (
                <SegmentedControl.Item key={value} value={value}>
                  {t(`memories.filter.${value}`)}
                </SegmentedControl.Item>
              ))}
            </SegmentedControl.Root>
          }
        >
          <LoadState load={own} unavailableKey="memories.unavailable" onRetry={() => void loadOwn()}>
            {(memories) =>
              memories.length === 0 ? (
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t(filter === 'review' ? 'memories.emptyReview' : 'memories.empty')}
                </Text>
              ) : (
                <Flex direction="column" gap="3">
                  {memories.map((memory) => (
                    <MemoryRow
                      key={memory.memoryId}
                      memory={memory}
                      actions={{
                        edit: (content) => editOwn(memory.memoryId, content),
                        review: (decision) => reviewOwn(memory.memoryId, decision),
                        remove: () => removeOwn(memory.memoryId),
                      }}
                    />
                  ))}
                </Flex>
              )
            }
          </LoadState>
        </SettingsSection>
      </Box>
    </Box>
  );
}
