'use client';

import React, { useRef, useState } from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ResponseTab } from '@/chat/types';

interface ResponseTabsProps {
  activeTab: ResponseTab;
  onTabChange: (tab: ResponseTab) => void;
  sourcesCount?: number;
  citationCount?: number;
  /** Unique per answer: names each tab and the panel it controls (`responseTabIds`). */
  idBase: string;
}

/** The ids a tab and its panel share, so the answer's content can say which tab labels it. */
export const responseTabIds = (idBase: string, tab: ResponseTab) => ({
  tab: `${idBase}-tab-${tab}`,
  panel: `${idBase}-panel`,
});

interface TabItemProps {
  id: string;
  panelId: string;
  label: string;
  count?: number;
  isActive: boolean;
  isDisabled?: boolean;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  buttonRef: (node: HTMLButtonElement | null) => void;
}

const RESET: React.CSSProperties = { border: 0, background: 'none', font: 'inherit', margin: 0 };

function TabItem({ id, panelId, label, count, isActive, isDisabled, onClick, onKeyDown, buttonRef }: TabItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      ref={buttonRef}
      type="button"
      role="tab"
      id={id}
      aria-selected={isActive}
      aria-controls={isActive ? panelId : undefined}
      aria-disabled={isDisabled || undefined}
      tabIndex={isActive ? 0 : -1}
      onClick={isDisabled ? undefined : onClick}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        ...RESET,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
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
    </button>
  );
}

/**
 * An answer's Answer / Sources / Citation tabs (QA-CLASSIC-FU-1): a WAI-ARIA tab list. Each tab is a button with
 * `aria-selected`; one tab stop, the arrow keys, Home and End move among the tabs that have something to show.
 */
export function ResponseTabs({
  activeTab,
  onTabChange,
  sourcesCount,
  citationCount,
  idBase,
}: ResponseTabsProps) {
  const { t } = useTranslation();
  const buttons = useRef<Partial<Record<ResponseTab, HTMLButtonElement | null>>>({});
  const tabs: Array<{ tab: ResponseTab; label: string; count?: number; disabled: boolean }> = [
    { tab: 'answer', label: t('chat.answer'), disabled: false },
    { tab: 'sources', label: t('chat.sources'), count: sourcesCount, disabled: !sourcesCount },
    { tab: 'citation', label: t('chat.citation'), count: citationCount, disabled: !citationCount },
  ];
  const enabled = tabs.filter((entry) => !entry.disabled).map((entry) => entry.tab);

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const at = Math.max(0, enabled.indexOf(activeTab));
    const next =
      event.key === 'ArrowRight'
        ? enabled[(at + 1) % enabled.length]
        : event.key === 'ArrowLeft'
          ? enabled[(at - 1 + enabled.length) % enabled.length]
          : event.key === 'Home'
            ? enabled[0]
            : event.key === 'End'
              ? enabled[enabled.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    onTabChange(next);
    buttons.current[next]?.focus();
  };

  return (
    <Flex
      align="center"
      role="tablist"
      aria-label={t('chat.responseTabs')}
      className="response-tabs-scroll"
      style={{
        borderBottom: '1px solid var(--slate-a6)',
        overflowX: 'auto',
      }}
    >
      {tabs.map((entry) => {
        const ids = responseTabIds(idBase, entry.tab);
        return (
          <TabItem
            key={entry.tab}
            id={ids.tab}
            panelId={ids.panel}
            label={entry.label}
            count={entry.count}
            isActive={activeTab === entry.tab}
            isDisabled={entry.disabled}
            onClick={() => onTabChange(entry.tab)}
            onKeyDown={onKeyDown}
            buttonRef={(node) => {
              buttons.current[entry.tab] = node;
            }}
          />
        );
      })}
    </Flex>
  );
}
