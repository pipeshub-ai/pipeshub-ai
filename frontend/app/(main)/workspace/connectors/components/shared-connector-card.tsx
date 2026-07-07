'use client';

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Avatar, Flex, Text, IconButton } from '@radix-ui/themes';
import { ConnectorIcon, MaterialIcon } from '@/app/components/ui';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { ConnectorInstance } from '../types';

// ─────────────────────────────────────────────────────────────────────────────

interface SharedConnectorCardProps {
  instance: ConnectorInstance;
  onChevronClick?: (instance: ConnectorInstance) => void;
}

export function SharedConnectorCard({
  instance,
  onChevronClick,
}: SharedConnectorCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  // sharedBy.name is populated by the backend graph owner lookup.
  // Fall back to the users API if it's absent (e.g. graph lookup missed due to stale data).
  const [sharerName, setSharerName] = useState<string | null>(instance.sharedBy?.name ?? null);

  useEffect(() => {
    const name = instance.sharedBy?.name ?? null;
    if (name) { setSharerName(name); return; }

    const userId = instance.sharedBy?.userId ?? null;
    if (!userId) return;

    let cancelled = false;
    UsersApi.getUser(userId)
      .then((user) => { if (!cancelled) setSharerName(user.name ?? user.email ?? null); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [instance.sharedBy?.name, instance.sharedBy?.userId]);

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 24,
        cursor: 'default',
        transition: 'background-color 150ms ease',
      }}
    >
      {/* ── Top section: icon row + name + description (mirrors ConnectorCard) ── */}
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        {/* Icon + chevron row */}
        <Flex align="center" justify="between">
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: 8,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            <ConnectorIcon type={instance.type} size={16} />
          </Flex>

          {onChevronClick && (
            <IconButton
              variant="outline"
              color="gray"
              size="1"
              onClick={(e) => { e.stopPropagation(); onChevronClick(instance); }}
              style={{ cursor: 'pointer', borderRadius: 'var(--radius-2)', width: 28, height: 28, flexShrink: 0 }}
            >
              <MaterialIcon name="chevron_right" size={16} color="var(--gray-11)" />
            </IconButton>
          )}
        </Flex>

        {/* Name + description */}
        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {instance.name?.trim() || instance.type}
          </Text>
          <Text
            size="2"
            style={{
              color: 'var(--gray-11)',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {instance.appDescription || instance.type}
          </Text>
        </Flex>
      </Flex>

      {/* ── Bottom: shared-by row ── */}
      <Flex align="center" gap="2">
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--gray-10)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            flexShrink: 0,
          }}
        >
          {t('workspace.connectors.sharedCard.sharedBy')}
        </Text>
        {sharerName ? (
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
            <Avatar
              size="1"
              radius="small"
              fallback={sharerName[0] ?? '?'}
              style={{ width: 20, height: 20, flexShrink: 0 }}
            />
            <Text
              size="2"
              style={{
                color: 'var(--gray-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {sharerName}
            </Text>
          </Flex>
        ) : (
          <Text size="2" style={{ color: 'var(--gray-10)' }}>—</Text>
        )}
      </Flex>
    </Flex>
  );
}
