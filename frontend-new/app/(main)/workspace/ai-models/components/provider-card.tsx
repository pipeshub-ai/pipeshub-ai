'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { useTranslation } from 'react-i18next';
import type { AIModelProvider } from '../types';
import { isRegistryBadgeCapability } from '../types';
import { aiModelsCapabilityBadge } from '../capability-i18n';
import styles from './provider-card.module.css';

const BADGE_STYLE: Record<
  string,
  { border: string; color: string; bg: string }
> = {
  text_generation: {
    border: '1px solid var(--purple-9)',
    color: 'var(--purple-11)',
    bg: 'color-mix(in srgb, var(--purple-3) 40%, transparent)',
  },
  reasoning: {
    border: '1px solid var(--orange-9)',
    color: 'var(--orange-11)',
    bg: 'color-mix(in srgb, var(--orange-3) 40%, transparent)',
  },
  video: {
    border: '1px solid var(--cyan-9)',
    color: 'var(--cyan-11)',
    bg: 'color-mix(in srgb, var(--cyan-3) 40%, transparent)',
  },
  embedding: {
    border: '1px solid var(--blue-9)',
    color: 'var(--blue-11)',
    bg: 'color-mix(in srgb, var(--blue-3) 40%, transparent)',
  },
  image_generation: {
    border: '1px solid var(--pink-9)',
    color: 'var(--pink-11)',
    bg: 'color-mix(in srgb, var(--pink-3) 40%, transparent)',
  },
};

const DEFAULT_BADGE_STYLE = {
  border: '1px solid var(--gray-8)',
  color: 'var(--gray-11)',
  bg: 'var(--gray-a3)',
};

interface ProviderRowProps {
  provider: AIModelProvider;
  onConfigure: () => void;
}

/**
 * Horizontal provider row for the Model Providers grid (+ Configure uses active capability tab).
 */
export function ProviderRow({ provider, onConfigure }: ProviderRowProps) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);

  const badgeCaps = provider.capabilities.filter((c) => isRegistryBadgeCapability(c));

  return (
    <Flex
      direction={{ initial: 'column', sm: 'row' }}
      align={{ initial: 'stretch', sm: 'center' }}
      justify={{ initial: 'start', sm: 'between' }}
      gap="4"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: '100%',
        minWidth: 0,
        minHeight: 88,
        padding: '16px 20px',
        backgroundColor: hover ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        transition: 'background-color 150ms ease',
        boxSizing: 'border-box',
      }}
    >
      <Flex align="center" gap="4" style={{ minWidth: 0, flex: 1, width: '100%' }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 44,
            height: 44,
            padding: 6,
            backgroundColor: 'var(--gray-a2)',
            borderRadius: 'var(--radius-2)',
            flexShrink: 0,
          }}
        >
          <ThemeableAssetIcon
            src={provider.iconPath}
            size={28}
            color="var(--gray-12)"
            variant="flat"
          />
        </Flex>

        <Flex direction="column" gap="2" style={{ minWidth: 0, flex: 1 }}>
          <Flex direction="column" gap="0">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {provider.name}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.aiModels.modelProviderKind')}
            </Text>
          </Flex>

          <Flex gap="2" wrap="wrap" style={{ marginTop: 2 }}>
            {badgeCaps.map((cap) => {
              const label = aiModelsCapabilityBadge(t, cap);
              if (!label) return null;
              const st = BADGE_STYLE[cap] ?? DEFAULT_BADGE_STYLE;
              return (
                <span
                  key={cap}
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    lineHeight: '18px',
                    padding: '2px 10px',
                    borderRadius: "2px",
                    border: st.border,
                    color: st.color,
                    backgroundColor: st.bg,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {label}
                </span>
              );
            })}
          </Flex>
        </Flex>
      </Flex>

      <button
        type="button"
        className={styles.configureButton}
        onClick={(e) => {
          e.stopPropagation();
          onConfigure();
        }}
        style={{
          appearance: 'none',
          margin: 0,
          font: 'inherit',
          flexShrink: 0,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
          padding: '8px 16px',
          borderRadius: 'var(--radius-2)',
          border: '1px solid var(--gray-a6)',
          backgroundColor: 'var(--gray-a3)',
          color: 'var(--gray-12)',
          cursor: 'pointer',
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        <MaterialIcon name="add" size={16} color="var(--gray-11)" />
        {t('workspace.aiModels.configure')}
      </button>
    </Flex>
  );
}
