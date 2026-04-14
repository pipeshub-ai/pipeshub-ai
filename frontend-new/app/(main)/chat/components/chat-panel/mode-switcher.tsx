'use client';

import Image from 'next/image';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { QueryModeConfig } from '@/chat/types';

interface ModeSwitcherProps {
  /** Current query mode configuration */
  activeQueryConfig: QueryModeConfig;
  /** Mode colors (bg, fg, icon) */
  modeColors: {
    bg: string;
    fg: string;
    icon: string;
  };
  /** Whether in search mode (changes layout) */
  isSearchMode: boolean;
  /** Whether the mode panel is open (changes caret direction) */
  isModePanelOpen: boolean;
  /** Whether in full UI mode (affects caret behavior) */
  showFullUI: boolean;
  /** Handler for primary button (mode button or return-to-chat) */
  onLeftClick: () => void;
  /** Handler for secondary button (search toggle) */
  onRightClick: () => void;
}

/**
 * Mode switcher pill with left/right buttons.
 * Used by both collapsed widget and full toolbar in ChatInput.
 *
 * Layout adapts based on isSearchMode:
 * - Chat mode: [Mode + Dropdown] [Search Icon]
 * - Search mode: [Mode Icon] [Search + Text]
 */
export function ModeSwitcher({
  activeQueryConfig,
  modeColors,
  isSearchMode,
  isModePanelOpen,
  showFullUI,
  onLeftClick,
  onRightClick,
}: ModeSwitcherProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  return (
    <Flex
      align="center"
      style={{
        background: 'var(--olive-1)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-1)',
        gap: 0,
        // width: '152px',
        flexShrink: 0,
      }}
    >
      {isSearchMode ? (
        <>
          {/* Icon-only mode button (left) */}
          <Flex
            align="center"
            justify="center"
            onClick={onLeftClick}
            style={{
              width: '32px',
              height: '32px',
              borderRadius: 'var(--radius-1)',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <ChatStarIcon
              size={ICON_SIZES.MINIMAL}
              color="var(--mode-chat-icon)"
            />
          </Flex>

          {/* Search button with text (right) */}
          <Flex
            align="center"
            justify="center"
            gap="2"
            onClick={onRightClick}
            style={{
              height: '32px',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--mode-search-bg)',
              cursor: 'pointer',
              paddingLeft: 'var(--space-3)',
              paddingRight: 'var(--space-3)',
              transition: 'background-color 0.15s ease',
            }}
          >
            <MaterialIcon
              name="search"
              size={ICON_SIZES.SECONDARY}
              color="var(--mode-search-fg)"
            />
            {!isMobile && (
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--mode-search-fg)' }}
              >
                {t('form.search')}
              </Text>
            )}
          </Flex>
        </>
      ) : (
        <>
          {/* Mode label + dropdown (left) */}
          <Flex
            align="center"
            gap="2"
            onClick={onLeftClick}
            style={{
              flex: 1,
              height: '32px',
              borderRadius: 'var(--radius-2)',
              background: modeColors.bg,
              cursor: 'pointer',
              paddingLeft: 'var(--space-3)',
              paddingRight: 'var(--space-3)',
              transition: 'background-color 0.15s ease',
            }}
          >
            {activeQueryConfig.iconType === 'component' ? (
              <ChatStarIcon
                size={ICON_SIZES.MINIMAL}
                color={modeColors.icon}
              />
            ) : activeQueryConfig.iconType === 'svg' ? (
              <Image
                src={activeQueryConfig.icon}
                alt={activeQueryConfig.label}
                width={ICON_SIZES.MINIMAL}
                height={ICON_SIZES.MINIMAL}
              />
            ) : (
              <MaterialIcon
                name={activeQueryConfig.icon}
                size={ICON_SIZES.MINIMAL}
                color={modeColors.icon}
              />
            )}
            {/* Hide mode label text on mobile — icon-only */}
            {!isMobile && (
              <Text
                size="2"
                weight="medium"
                style={{
                  color: modeColors.fg,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {t(activeQueryConfig.toolbarLabel)}
              </Text>
            )}
            <MaterialIcon
              name={isModePanelOpen && showFullUI ? 'expand_less' : 'expand_more'}
              size={ICON_SIZES.SMALL}
              color={modeColors.icon}
              style={{ marginLeft: 'var(--space-1)' }}
            />
          </Flex>

          {/* Search icon (right) */}
          <Flex
            align="center"
            justify="center"
            onClick={onRightClick}
            style={{
              width: '32px',
              height: '32px',
              borderRadius: 'var(--radius-1)',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <MaterialIcon
              name="search"
              size={ICON_SIZES.SECONDARY}
              color={modeColors.fg}
            />
          </Flex>
        </>
      )}
    </Flex>
  );
}
