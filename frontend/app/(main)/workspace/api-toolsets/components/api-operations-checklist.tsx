'use client';

import { useTranslation } from 'react-i18next';
import { Badge, Checkbox, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ApiOperationKind, ApiOperationSummary } from '../types';

const KIND_COLOR: Record<ApiOperationKind, 'jade' | 'amber' | 'red'> = {
  read: 'jade',
  write: 'amber',
  destructive: 'red',
};

interface ApiOperationsChecklistProps {
  operations: ApiOperationSummary[];
  /** Without it the list is read-only: no checkboxes. */
  onToggle?: (toolName: string, checked: boolean) => void;
  selected?: ReadonlySet<string>;
  /** Flags operations the spec says need credentials, for a toolset that sends none. */
  flagSecured?: boolean;
}

/** One row per operation: method, path, summary, and how much it can change. */
export function ApiOperationsChecklist({ operations, onToggle, selected, flagSecured = false }: ApiOperationsChecklistProps) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      style={{ border: '1px solid var(--olive-4)', borderRadius: 'var(--radius-2)', background: 'var(--color-surface)' }}
    >
      {operations.map((op, index) => {
        const row = (
          <Flex
            align="start"
            gap="3"
            px="3"
            py="2"
            style={{ minHeight: 44, borderTop: index === 0 ? undefined : '1px solid var(--olive-3)' }}
          >
            {onToggle ? (
              <Checkbox
                checked={selected?.has(op.toolName) ?? false}
                onCheckedChange={(checked) => onToggle(op.toolName, checked === true)}
                style={{ marginTop: 3 }}
              />
            ) : null}
            <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
              <Flex align="center" gap="2" wrap="wrap">
                <Text
                  size="1"
                  weight="bold"
                  style={{ fontFamily: 'var(--code-font-family)', color: 'var(--gray-11)', minWidth: 44 }}
                >
                  {op.method.toUpperCase()}
                </Text>
                <Text size="1" style={{ fontFamily: 'var(--code-font-family)', color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
                  {op.path}
                </Text>
              </Flex>
              {op.summary ? (
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {op.summary}
                </Text>
              ) : null}
            </Flex>
            <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
              {flagSecured && op.requiresAuth ? (
                <Tooltip content={t('workspace.apiToolsets.editor.needsCredentialsHint')}>
                  <Flex align="center" gap="1">
                    <MaterialIcon name="lock" size={14} color="var(--amber-11)" />
                    <Text size="1" style={{ color: 'var(--amber-11)' }}>
                      {t('workspace.apiToolsets.editor.needsCredentials')}
                    </Text>
                  </Flex>
                </Tooltip>
              ) : null}
              <Badge size="1" variant="soft" color={KIND_COLOR[op.kind]}>
                {t(`workspace.apiToolsets.kind.${op.kind}`)}
              </Badge>
            </Flex>
          </Flex>
        );
        return onToggle ? (
          <Text as="label" key={op.toolName} size="2" style={{ cursor: 'pointer' }}>
            {row}
          </Text>
        ) : (
          <div key={op.toolName}>{row}</div>
        );
      })}
    </Flex>
  );
}
