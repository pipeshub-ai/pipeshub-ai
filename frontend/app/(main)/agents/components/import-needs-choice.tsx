'use client';

import React from 'react';
import { Badge, Box, Button, Callout, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { modelLabelDisplayName, type ConfiguredModel, type ImportResult, type Rebind, type RebindKind } from '../config-transfer';

/** The kinds the builder's palette sets: "Choose" opens it. Models are chosen on the agent itself. */
const IN_PALETTE: ReadonlySet<RebindKind> = new Set(['toolset', 'mcpServer', 'knowledge', 'skill']);

const isModel = (kind: string): boolean => kind === 'model' || kind === 'roleModel';

function reasonText(t: (key: string, options?: Record<string, unknown>) => string, item: Rebind, name: string): string {
  switch (item.reason) {
    case 'ambiguous':
      return t('agentTransfer.needsChoice.reason.ambiguous', { name });
    case 'limited':
      return t('agentTransfer.needsChoice.reason.limited');
    case 'not_reasoning':
      return t('agentTransfer.needsChoice.reason.notReasoning');
    default:
      return t(`agentTransfer.needsChoice.reason.notFound.${item.kind}`, { name });
  }
}

/**
 * Pinned at the top of the builder after an import (C4.08):
 * what the import could not bind here, each with a way to choose it, and
 * what was never carried and must be set again. Closing it forgets the list.
 */
export function ImportNeedsChoice({
  result,
  configuredModels = [],
  onChoose,
  onClose,
}: {
  result: ImportResult;
  /** The importer's configured models, so a model reads by its display name. */
  configuredModels?: readonly ConfiguredModel[];
  /** Opens the builder's palette, where actions, MCP servers, knowledge and skills are chosen. */
  onChoose: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const nameOf = (kind: string, name: string): string =>
    isModel(kind) ? modelLabelDisplayName(name, configuredModels) : name;
  const matched = result.bound.map((b) => nameOf(b.kind, b.name));
  const excluded = result.excluded.map((item) => t(`agentTransfer.excluded.${item}`));
  // A section this build does not offer reads by its name: it has no words of its own here.
  const notImported = result.notImported.map(({ section }) => t('agentTransfer.notImported.other', { section }));
  return (
    <Box
      role="region"
      aria-label={t('agentTransfer.needsChoice.aria')}
      data-testid="import-needs-choice"
      style={{
        border: '1px solid var(--amber-6)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--color-panel-solid)',
        padding: 'var(--space-3) var(--space-4)',
      }}
    >
      <Flex align="start" justify="between" gap="3">
        <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {result.rebind.length > 0
              ? t('agentTransfer.needsChoice.title', { count: result.rebind.length })
              : t('agentTransfer.needsChoice.ready', { name: result.name })}
          </Text>
          {matched.length > 0 ? (
            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('agentTransfer.needsChoice.matched', { names: matched.join(', ') })}
            </Text>
          ) : null}
        </Flex>
        <Button type="button" variant="ghost" color="gray" size="1" onClick={onClose} style={{ minHeight: 32, flexShrink: 0 }}>
          {t('agentTransfer.needsChoice.close')}
        </Button>
      </Flex>

      {result.rebind.length > 0 ? (
        <Flex direction="column" mt="2" role="list">
          {result.rebind.map((item, index) => (
            <Flex
              key={`${item.kind}:${item.name}:${String(index)}`}
              role="listitem"
              align={{ initial: 'start', sm: 'center' }}
              direction={{ initial: 'column', sm: 'row' }}
              gap="2"
              style={{ padding: 'var(--space-2) 0', borderTop: '1px solid var(--gray-4)' }}
            >
              <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
                <Badge color="amber" variant="soft">
                  {t(`agentTransfer.kind.${item.kind}`)}
                </Badge>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {nameOf(item.kind, item.name)}
                </Text>
              </Flex>
              <Text size="1" style={{ color: 'var(--slate-11)', flexGrow: 1, minWidth: 0 }}>
                {reasonText(t, item, nameOf(item.kind, item.name))}
              </Text>
              {IN_PALETTE.has(item.kind) ? (
                <Button type="button" size="1" variant="soft" onClick={onChoose} style={{ minHeight: 32, flexShrink: 0 }}>
                  {t('agentTransfer.needsChoice.choose')}
                </Button>
              ) : null}
            </Flex>
          ))}
        </Flex>
      ) : null}

      {notImported.length > 0 ? (
        <Callout.Root color="gray" variant="surface" size="1" mt="2" data-testid="import-not-imported">
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Flex direction="column" gap="1">
            {notImported.map((line) => (
              <Callout.Text key={line}>{line}</Callout.Text>
            ))}
          </Flex>
        </Callout.Root>
      ) : null}

      {excluded.length > 0 ? (
        <Callout.Root color="gray" variant="surface" size="1" mt="2">
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Callout.Text>{t('agentTransfer.needsChoice.setAgain', { items: excluded.join(', ') })}</Callout.Text>
        </Callout.Root>
      ) : null}
    </Box>
  );
}
