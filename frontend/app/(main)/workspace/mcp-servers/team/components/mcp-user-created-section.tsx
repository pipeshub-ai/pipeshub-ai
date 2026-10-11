'use client';

import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EntityRowActionMenu } from '../../../components';
import type { McpPersonalInstanceSummary } from '../../types';
import { MCP_AUTH_MODE_LABELS, MCP_TRANSPORT_LABELS } from '../../types';

interface McpUserCreatedSectionProps {
  instances: McpPersonalInstanceSummary[];
  /** createdBy → display name; ids missing here are shown as-is. */
  ownerNames: Record<string, string>;
  onDelete: (instance: McpPersonalInstanceSummary) => void;
}

/** Users' own servers, for an administrator to review. Only the creator can use one, so the
 * only action here is removing it, and nothing shown says how to reach it. */
export function McpUserCreatedSection({ instances, ownerNames, onDelete }: McpUserCreatedSectionProps) {
  const { t } = useTranslation();
  if (instances.length === 0) return null;

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.mcpServers.team.userCreatedTitle')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.mcpServers.team.userCreatedDescription')}
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {instances.map((instance) => (
          <Flex
            key={instance._id}
            align="center"
            justify="between"
            gap="3"
            style={{
              padding: 'var(--space-3)',
              backgroundColor: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-1)',
            }}
          >
            <Flex align="center" gap="3" style={{ minWidth: 0 }}>
              <MaterialIcon name="person" size={16} color="var(--gray-10)" />
              <Flex direction="column" gap="0" style={{ minWidth: 0 }}>
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                  {instance.name}
                </Text>
                <Text
                  size="1"
                  style={{ color: 'var(--gray-10)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                  {t('workspace.mcpServers.team.userCreatedOwner', {
                    name: ownerNames[instance.createdBy] ?? instance.createdBy,
                  })}
                  {' · '}
                  {MCP_TRANSPORT_LABELS[instance.transport]}
                  {' · '}
                  {MCP_AUTH_MODE_LABELS[instance.authMode]}
                </Text>
              </Flex>
            </Flex>
            <EntityRowActionMenu
              actions={[
                {
                  icon: 'delete',
                  label: t('workspace.mcpServers.cta.delete'),
                  variant: 'danger',
                  onClick: () => onDelete(instance),
                },
              ]}
            />
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
}
