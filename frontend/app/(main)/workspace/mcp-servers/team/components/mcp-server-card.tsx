'use client';

import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { McpServerIcon } from '../../components/mcp-server-icon';

interface McpServerCardProps {
  title: string;
  description: string;
  iconUrl?: string | null;
  typeId?: string;
  /** How many servers this person already has from this entry; they can still add another. */
  addedCount?: number;
  onSetup: () => void;
}

/** A catalog entry that can be added. */
export function McpServerCard({ title, description, iconUrl, typeId, addedCount = 0, onSetup }: McpServerCardProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-3)',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 28,
            height: 28,
            backgroundColor: 'var(--gray-a3)',
            borderRadius: 'var(--radius-1)',
            flexShrink: 0,
            overflow: 'hidden',
          }}
        >
          {iconUrl ? (
            <img src={iconUrl} alt="" width={16} height={16} />
          ) : (
            <McpServerIcon typeId={typeId} name={title} size={16} color="var(--gray-10)" />
          )}
        </Flex>
        <Text size="2" weight="medium" truncate style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
      </Flex>

      <Text
        size="1"
        style={{
          color: 'var(--gray-11)',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          minHeight: 32,
        }}
      >
        {description}
      </Text>

      <Flex align="center" justify="between" gap="2" wrap="wrap" style={{ marginTop: 'auto' }}>
        <Flex align="center" gap="1">
          {addedCount > 0 && (
            <>
              <MaterialIcon name="check" size={14} color="var(--gray-10)" />
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {t('workspace.mcpServers.catalog.added')}
              </Text>
            </>
          )}
        </Flex>
        <Button
          size={isMobile ? '3' : '2'}
          variant="soft"
          color="gray"
          className="touch-hit"
          onClick={onSetup}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="add" size={16} color="var(--gray-11)" />
          {addedCount > 0 ? t('workspace.mcpServers.catalog.addAnother') : t('workspace.actions.cta.setup')}
        </Button>
      </Flex>
    </Flex>
  );
}
