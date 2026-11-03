'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { AIModelProvider } from '../types';
import { isRegistryBadgeCapability } from '../types';
import { aiModelsCapabilityBadge } from '../capability-i18n';
import { MODEL_ROW_ICON_CONTAINER_STYLE, modelRowCardStyle } from './model-row-layout';

/** One neutral tag style; colour is kept for state (Default, errors), not for categories. */
const CAPABILITY_TAG_STYLE: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 500,
  lineHeight: '18px',
  padding: '1px 8px',
  borderRadius: '2px',
  color: 'var(--gray-11)',
  backgroundColor: 'var(--gray-a3)',
  whiteSpace: 'nowrap',
};

interface ProviderRowProps {
  provider: AIModelProvider;
  onConfigure: () => void;
  /** When true, hide the capability chips under the provider name. */
  hideCapabilityBadges?: boolean;
}

/**
 * Horizontal provider row for the Model Providers grid (+ Configure uses active capability tab).
 */
export function ProviderRow({ provider, onConfigure, hideCapabilityBadges = false }: ProviderRowProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [hover, setHover] = useState(false);

  const badgeCaps = provider.capabilities.filter((c) => isRegistryBadgeCapability(c));

  return (
    <Flex
      data-testid={`ai-provider-${provider.providerId}`}
      direction={{ initial: 'column', sm: 'row' }}
      align={{ initial: 'stretch', sm: 'center' }}
      justify={{ initial: 'start', sm: 'between' }}
      gap="4"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={modelRowCardStyle(hover)}
    >
      <Flex align="center" gap="4" style={{ minWidth: 0, flex: 1, width: '100%' }}>
        <Flex align="center" justify="center" style={MODEL_ROW_ICON_CONTAINER_STYLE}>
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

          {hideCapabilityBadges ? null : (
            <Flex gap="1" wrap="wrap" style={{ marginTop: 4 }}>
              {badgeCaps.map((cap) => {
                const label = aiModelsCapabilityBadge(t, cap);
                if (!label) return null;
                return (
                  <span key={cap} style={CAPABILITY_TAG_STYLE}>
                    {label}
                  </span>
                );
              })}
            </Flex>
          )}
        </Flex>
      </Flex>

      <button
        type="button"
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
          width: isMobile ? '100%' : 'auto',
          boxSizing: 'border-box',
          alignSelf: isMobile ? 'stretch' : 'center',
        }}
      >
        <MaterialIcon name="add" size={16} color="var(--gray-11)" />
        {t('workspace.aiModels.configure')}
      </button>
    </Flex>
  );
}
