'use client';

import { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { BotType } from '../types';

// ========================================
// Icon mapping
// ========================================

const BOT_ICON_MAP: Record<BotType, string> = {
  slack: '/icons/connectors/slack.svg',
  discord: '/icons/connectors/discord.svg',
  telegram: '/icons/connectors/telegram.svg',
  github: '/icons/connectors/github.svg',
};

const BOT_TYPE_LABEL: Record<BotType, string> = {
  slack: 'Slack Bot',
  discord: 'Discord Bot',
  telegram: 'Telegram Bot',
  github: 'GitHub Bot',
};

// ========================================
// Props
// ========================================

interface BotCardProps {
  name: string;
  botType: BotType;
  agentName?: string;
  onManage: () => void;
}

// ========================================
// BotCard
// ========================================

export function BotCard({ name, botType, agentName, onManage }: BotCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [iconError, setIconError] = useState(false);

  const iconSrc = BOT_ICON_MAP[botType];

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
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      {/* ── Top section: icon + text ── */}
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        {/* Icon container */}
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
          {iconError ? (
            <MaterialIcon name="smart_toy" size={16} color="var(--gray-9)" />
          ) : (
            <img
              src={iconSrc}
              alt={BOT_TYPE_LABEL[botType]}
              width={16}
              height={16}
              onError={() => setIconError(true)}
              style={{ display: 'block', objectFit: 'contain' }}
            />
          )}
        </Flex>

        {/* Name + type + agent */}
        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {name}
          </Text>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {BOT_TYPE_LABEL[botType]}
          </Text>
          <Text
            size="1"
            style={{
              color: 'var(--gray-10)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {agentName || 'Default Assistant'}
          </Text>
        </Flex>
      </Flex>

      {/* ── Bottom action: Manage button ── */}
      <ManageButton onClick={onManage} />
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function ManageButton({ onClick }: { onClick: () => void }) {
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
        border: '1px solid var(--accent-a6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        width: '100%',
        height: 32,
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--accent-a4)' : 'var(--accent-a3)',
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
        Manage
      </span>
    </button>
  );
}
