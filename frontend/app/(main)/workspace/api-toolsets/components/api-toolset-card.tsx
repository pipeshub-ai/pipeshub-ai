'use client';

import { useTranslation } from 'react-i18next';
import { Badge, Button, Card, Flex, Text } from '@radix-ui/themes';
import { connectionMode } from '../toolset-form';
import type { ApiToolset } from '../types';

interface ApiToolsetCardProps {
  toolset: ApiToolset;
  isAdmin: boolean;
  onConnect: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

export function ApiToolsetCard({ toolset, isAdmin, onConnect, onEdit, onDelete }: ApiToolsetCardProps) {
  const { t } = useTranslation();
  const mode = connectionMode(toolset, isAdmin);
  return (
    <Card size="2">
      <Flex direction="column" gap="3" style={{ height: '100%' }}>
        <Flex direction="column" gap="1">
          <Text size="3" weight="medium" style={{ overflowWrap: 'anywhere' }}>
            {toolset.name}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-11)', overflowWrap: 'anywhere' }}>
            {toolset.specTitle}
          </Text>
          {toolset.description ? (
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {toolset.description}
            </Text>
          ) : null}
        </Flex>

        <Flex gap="2" wrap="wrap">
          <Badge color={toolset.isConnected ? 'jade' : 'gray'}>
            {t(toolset.isConnected ? 'workspace.apiToolsets.status.connected' : 'workspace.apiToolsets.status.notConnected')}
          </Badge>
          <Badge variant="outline" color="gray">
            {t(`workspace.apiToolsets.authType.${toolset.authType}`)}
          </Badge>
          {toolset.useSharedCredential ? (
            <Badge variant="soft" color="blue">
              {t('workspace.apiToolsets.status.shared')}
            </Badge>
          ) : null}
          <Badge variant="soft" color="gray">
            {t('workspace.apiToolsets.operationCount', { count: toolset.operations.length })}
          </Badge>
        </Flex>

        <Flex gap="2" wrap="wrap" mt="auto">
          <Button size="2" variant={toolset.isConnected || mode === 'managed' ? 'soft' : 'solid'} onClick={onConnect}>
            {t(mode === 'own' || mode === 'shared' ? 'workspace.apiToolsets.cta.connection' : 'workspace.apiToolsets.cta.details')}
          </Button>
          {isAdmin ? (
            <>
              <Button size="2" variant="soft" color="gray" onClick={onEdit}>
                {t('workspace.apiToolsets.cta.edit')}
              </Button>
              <Button size="2" variant="soft" color="red" onClick={onDelete}>
                {t('workspace.apiToolsets.cta.delete')}
              </Button>
            </>
          ) : null}
        </Flex>
      </Flex>
    </Card>
  );
}
