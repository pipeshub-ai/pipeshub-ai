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
  /** `chips` is the compact toggle row used under an answer in a collaborative timeline. */
  variant?: 'tabs' | 'chips';
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
        flexShrink: 0,
        whiteSpace: 'nowrap',
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
                  : 'var(--slate-a11)',
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
              padding: '0 var(--space-1)', /* was: 0 6px, delta: -2px side */
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
  variant = 'tabs',
}: ResponseTabsProps) {
  const { t } = useTranslation();
  if (variant === 'chips') {
    const chips: Array<{ tab: ResponseTab; label: string; count?: number; disabled: boolean }> = [
      { tab: 'answer', label: t('chat.answer'), disabled: false },
      { tab: 'sources', label: t('chat.sources'), count: sourcesCount, disabled: !sourcesCount },
      { tab: 'citation', label: t('chat.citation'), count: citationCount, disabled: !citationCount },
    ];
    return (
      <Flex
        align="center"
        gap="2"
        wrap="wrap"
        role="group"
        aria-label={t('chat.collab.timeline.answerViews')}
        data-testid="response-chips"
        style={{ marginTop: 'var(--space-1)' }}
      >
        {chips.map((chip) => {
          const active = activeTab === chip.tab;
          return (
            <button
              key={chip.tab}
              type="button"
              aria-pressed={active}
              disabled={chip.disabled}
              onClick={() => onTabChange(chip.tab)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--space-1)',
                padding: '2px var(--space-2)',
                borderRadius: 'var(--radius-full)',
                border: `1px solid ${active ? 'var(--accent-a7)' : 'var(--slate-a6)'}`,
                background: active ? 'var(--accent-a3)' : 'transparent',
                color: active ? 'var(--accent-11)' : 'var(--slate-11)',
                fontFamily: 'inherit',
                fontSize: 'var(--font-size-1)',
                lineHeight: 'var(--line-height-1)',
                cursor: chip.disabled ? 'not-allowed' : 'pointer',
                opacity: chip.disabled ? 0.5 : 1,
              }}
            >
              {chip.label}
              {chip.count ? <span style={{ fontWeight: 500 }}>{chip.count}</span> : null}
            </button>
          );
        })}
      </Flex>
    );
  }
  return (
    <Flex
      align="center"
      className="response-tabs-scroll"
      style={{
        borderBottom: '1px solid var(--slate-a6)',
        overflowX: 'auto',
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
