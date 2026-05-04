'use client';

import { useState } from 'react';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { OAuthClient } from '../types';

export interface OAuthApplicationCardProps {
  client: OAuthClient;
  onManage: (client: OAuthClient) => void;
}

export function OAuthApplicationCard({ client, onManage }: OAuthApplicationCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  const scopeCount = client.allowedScopes?.length ?? 0;
  const scopesBadgeText =
    scopeCount === 1
      ? t('workspace.oauth2.scopesBadgeOne', { count: scopeCount })
      : t('workspace.oauth2.scopesBadgeMany', { count: scopeCount });

  const isDcr = client.registeredVia === 'dcr';

  const subtitle =
    typeof client.description === 'string' && client.description.trim().length > 0
      ? client.description.trim()
      : client.clientId?.trim() || '…';

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
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
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
          <MaterialIcon name="settings" size={16} color="var(--gray-9)" />
        </Flex>

        <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
          <Flex direction="column" gap="1" style={{ width: '100%' }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {client.name}
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
              title={subtitle}
            >
              {subtitle}
            </Text>
          </Flex>
          <Flex gap="2" wrap="wrap" style={{ alignSelf: 'flex-start' }}>
            <Badge color="blue" size="1" variant="soft" style={{ flexShrink: 0 }}>
              <Flex align="center" gap="1">
                <MaterialIcon name="check" size={12} color="var(--blue-11)" />
                <span>{scopesBadgeText}</span>
              </Flex>
            </Badge>
            {isDcr && (
              <Badge color="amber" size="1" variant="soft" style={{ flexShrink: 0 }}>
                <Flex align="center" gap="1">
                  <MaterialIcon name="extension" size={12} color="var(--amber-11)" />
                  <span>Third-party MCP client</span>
                </Flex>
              </Badge>
            )}
          </Flex>
        </Flex>
      </Flex>

      <ManageButton
        label={t('workspace.oauth2.manage')}
        onClick={() => onManage(client)}
      />
    </Flex>
  );
}

function ManageButton({ label, onClick }: { label: string; onClick: () => void }) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: '0px solid var(--accent-a6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        width: '100%',
        height: 32,
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--green-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name="settings" size={16} color="var(--accent-11)" />
      <span
        style={{
          fontSize: 14,
          fontWeight: 500,
          lineHeight: '20px',
          color: 'var(--accent-11)',
        }}
      >
        {label}
      </span>
    </button>
  );
}
