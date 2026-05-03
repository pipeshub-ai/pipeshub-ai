'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { AGENT_STRATEGY_ICONS } from '@/chat/components/agent-strategy-dropdown';
import type { AgentStrategy } from '@/chat/types';

export interface AgentStrategyModeSwitcherProps {
  activeStrategy: AgentStrategy;
  modeColors: {
    bg: string;
    fg: string;
    icon: string;
  };
  isPanelOpen: boolean;
  showFullUI: boolean;
  disabled?: boolean;
  onClick: () => void;
}

/**
 * Toolbar pill for agent-scoped chat: same chrome as {@link ModeSwitcher}'s
 * primary (left) control — opens the card-style {@link AgentStrategyModePanel}.
 */
export function AgentStrategyModeSwitcher({
  activeStrategy,
  modeColors,
  isPanelOpen,
  showFullUI,
  disabled,
  onClick,
}: AgentStrategyModeSwitcherProps) {
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
        flexShrink: 0,
        opacity: disabled ? 0.5 : 1,
        pointerEvents: disabled ? 'none' : 'auto',
      }}
    >
      <Flex
        align="center"
        gap="2"
        onClick={disabled ? undefined : onClick}
        style={{
          height: '32px',
          borderRadius: 'var(--radius-2)',
          background: modeColors.bg,
          cursor: disabled ? 'default' : 'pointer',
          paddingLeft: 'var(--space-3)',
          paddingRight: 'var(--space-3)',
          transition: 'background-color 0.15s ease',
        }}
      >
        <MaterialIcon
          name={AGENT_STRATEGY_ICONS[activeStrategy]}
          size={ICON_SIZES.MINIMAL}
          color={modeColors.icon}
        />
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
            {t(`chat.agentStrategy.modes.${activeStrategy}.short`)}
          </Text>
        )}
        <MaterialIcon
          name={isPanelOpen && showFullUI ? 'expand_less' : 'expand_more'}
          size={ICON_SIZES.SMALL}
          color={modeColors.icon}
          style={{ marginLeft: isMobile ? 0 : 'var(--space-1)' }}
        />
      </Flex>
    </Flex>
  );
}
