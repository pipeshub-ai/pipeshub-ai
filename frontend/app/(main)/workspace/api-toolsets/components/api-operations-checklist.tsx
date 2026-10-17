'use client';

import { useTranslation } from 'react-i18next';
import { Badge, Checkbox, Flex, Text } from '@radix-ui/themes';
import type { ApiOperationKind, ApiOperationSummary } from '../types';

const KIND_COLOR: Record<ApiOperationKind, 'jade' | 'amber' | 'red'> = {
  read: 'jade',
  write: 'amber',
  destructive: 'red',
};

interface ApiOperationsChecklistProps {
  operations: ApiOperationSummary[];
  selected: ReadonlySet<string>;
  onToggle: (toolName: string, checked: boolean) => void;
  disabled?: boolean;
}

/** One row per operation: checkbox, method, path, summary, and how much it can change. */
export function ApiOperationsChecklist({ operations, selected, onToggle, disabled = false }: ApiOperationsChecklistProps) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1">
      {operations.map((op) => (
        <Text as="label" key={op.toolName} size="2" style={{ cursor: disabled ? 'default' : 'pointer' }}>
          <Flex align="start" gap="2" py="1" style={{ minHeight: 32 }}>
            <Checkbox
              checked={selected.has(op.toolName)}
              disabled={disabled}
              onCheckedChange={(checked) => onToggle(op.toolName, checked === true)}
              style={{ marginTop: 2 }}
            />
            <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
              <Flex align="center" gap="2" wrap="wrap">
                <Badge size="1" variant="soft" color="gray" style={{ fontFamily: 'var(--code-font-family)' }}>
                  {op.method.toUpperCase()}
                </Badge>
                <Text size="1" style={{ fontFamily: 'var(--code-font-family)', overflowWrap: 'anywhere' }}>
                  {op.path}
                </Text>
                <Badge size="1" variant={op.kind === 'destructive' ? 'solid' : 'soft'} color={KIND_COLOR[op.kind]}>
                  {t(`workspace.apiToolsets.kind.${op.kind}`)}
                </Badge>
              </Flex>
              {op.summary ? (
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {op.summary}
                </Text>
              ) : null}
            </Flex>
          </Flex>
        </Text>
      ))}
    </Flex>
  );
}
