'use client';

import React, { useState } from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ResponseTab } from '@/chat/types';

interface ResponseTabsProps {
  activeTab: ResponseTab;
  onTabChange: (tab: ResponseTab) => void;
  sourcesCount?: number;
  citationCount?: number;
}

interface TabItemProps {
  label: string;
  count?: number;
  isActive: boolean;
  isDisabled?: boolean;
  onClick: () => void;
}

function TabItem({ label, count, isActive, isDisabled, onClick }: TabItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      justify="center"
      onClick={isDisabled ? undefined : onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: 'relative',
        padding: 'var(--space-4) var(--space-4)',
        height: 'var(--space-7)',
        cursor: isDisabled ? 'not-allowed' : 'pointer',
        opacity: isDisabled ? 0.5 : 1,
      }}
    >
      <Flex align="center" gap="2">
        <Text
          size="2"
          weight={isActive ? 'medium' : 'regular'}
          style={{
            color: isActive
              ? 'var(--slate-12)'
              : isDisabled
                ? 'var(--slate-8)'
                : isHovered
                  ? 'var(--slate-11)'
                  : 'var(--slate-9)',
            transition: 'color 0.15s ease',
          }}
        >
          {label}
        </Text>
        {count !== undefined && count > 0 && (
          <Text
            size="1"
            style={{
              color: 'var(--accent-11)',
              backgroundColor: 'var(--accent-3)',
              padding: '0 6px',
              borderRadius: 'var(--radius-2)',
              fontWeight: 500,
            }}
          >
            {count}
          </Text>
        )}
      </Flex>

      {/* Active indicator */}
      {isActive && (
        <Box
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            height: '2px',
            backgroundColor: 'var(--accent-10)',
          }}
        />
      )}
    </Flex>
  );
}

export function ResponseTabs({
  activeTab,
  onTabChange,
  sourcesCount,
  citationCount,
}: ResponseTabsProps) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      style={{
        borderBottom: '1px solid var(--slate-a6)',
      }}
    >
      <TabItem
        label={t('chat.answer')}
        isActive={activeTab === 'answer'}
        onClick={() => onTabChange('answer')}
      />
      <TabItem
        label={t('chat.sources')}
        count={sourcesCount}
        isActive={activeTab === 'sources'}
        isDisabled={!sourcesCount || sourcesCount === 0}
        onClick={() => onTabChange('sources')}
      />
      <TabItem
        label={t('chat.citation')}
        count={citationCount}
        isActive={activeTab === 'citation'}
        isDisabled={!citationCount || citationCount === 0}
        onClick={() => onTabChange('citation')}
      />
    </Flex>
  );
}
