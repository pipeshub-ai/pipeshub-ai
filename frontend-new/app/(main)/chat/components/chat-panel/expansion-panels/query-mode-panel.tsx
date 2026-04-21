'use client';

import React, { useState } from 'react';
import Image from 'next/image';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import type { QueryMode, QueryModeConfig } from '@/chat/types';
import { QUERY_MODES } from '@/chat/constants';

interface QueryModePanelProps {
  /** Currently active query mode */
  activeMode: QueryMode;
  /** Called when a mode is selected */
  onSelect: (mode: QueryMode) => void;
  /** Hide the heading when embedded in a container that provides its own header */
  hideHeader?: boolean;
}

/**
 * Panel content for the query mode selector.
 * Renders inside the ChatInput expansion area as card-style items
 * matching the Figma "Different Modes of Query" design.
 */
export function QueryModePanel({ activeMode, onSelect, hideHeader = false }: QueryModePanelProps) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="4">
      {/* Heading */}
      {!hideHeader && (
        <Text
          size="1"
          weight="medium"
          style={{ color: 'var(--slate-12)' }}
        >
          {t('chat.differentModesOfQuery')}
        </Text>
      )}

      {/* Mode items */}
      <Flex direction="column" gap="2">
        {QUERY_MODES.map((mode) => (
          <QueryModeItem
            key={mode.id}
            mode={mode}
            isActive={activeMode === mode.id}
            onSelect={onSelect}
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ─── Mode icon renderer ──────────────────────────────────────────────

function ModeIconDisplay({
  mode,
  size,
  isActive,
}: {
  mode: QueryModeConfig;
  size: number;
  isActive: boolean;
}) {
  if (mode.iconType === 'component') {
    return (
      <ChatStarIcon
        size={size}
        color={isActive ? mode.colors.icon : 'var(--slate-11)'}
      />
    );
  }
  if (mode.iconType === 'svg') {
    return (
      <Image
        src={mode.icon}
        alt={mode.label}
        width={size}
        height={size}
      />
    );
  }
  return (
    <MaterialIcon
      name={mode.icon}
      size={size}
      color={isActive ? mode.colors.icon : 'var(--slate-11)'}
    />
  );
}

// ─── Individual mode item (card style per Figma) ─────────────────────

interface QueryModeItemProps {
  mode: QueryModeConfig;
  isActive: boolean;
  onSelect: (mode: QueryMode) => void;
}

function QueryModeItem({ mode, isActive, onSelect }: QueryModeItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const { t } = useTranslation();
  const isDisabled = !mode.enabled;

  return (
    <Flex
      align="center"
      justify="between"
      onClick={() => {
        if (!isDisabled) onSelect(mode.id);
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: isHovered && !isDisabled
          ? 'var(--olive-3)'
          : 'var(--olive-2)',
        cursor: isDisabled ? 'default' : 'pointer',
        opacity: isDisabled ? 0.5 : 1,
        transition: 'background-color 0.12s ease',
      }}
    >
      {/* Left: icon + label row, description row */}
      <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
        <Flex align="center" gap="2">
          <ModeIconDisplay mode={mode} size={20} isActive={isActive || true} />
          <Text
            size="2"
            weight="medium"
            style={{ color: isActive ? mode.colors.fg : mode.colors.fg }}
          >
            {t(`chat.queryModes.${mode.id}.label`, { defaultValue: mode.label })}
          </Text>
        </Flex>
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t(`chat.queryModes.${mode.id}.description`, { defaultValue: mode.description })}
        </Text>
      </Flex>

      {/* Right: Radio indicator */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: '16px',
          height: '16px',
          borderRadius: '50%',
          border: isActive
            ? '5px solid var(--accent-9)'
            : '1px solid var(--slate-7)',
          flexShrink: 0,
          marginLeft: 'var(--space-3)',
        }}
      >
        {isActive ? (
          <Box
            style={{
              width: '100%',
              height: '100%',
              borderRadius: '50%',
              backgroundColor: 'white',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          />
        ) :
          <Box
            style={{
              width: '100%',
              height: '100%',
              borderRadius: '50%',
              backgroundColor: 'var(--white-to-dark)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          />}
      </Flex>
    </Flex>
  );
}
