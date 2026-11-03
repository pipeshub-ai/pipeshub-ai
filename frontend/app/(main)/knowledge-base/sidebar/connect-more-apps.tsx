'use client';

import React from 'react';
import { Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { Link } from '@/lib/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import type { MoreConnectorLink } from '../types';

/** Tiles lean like a hand of cards, fixed per slot so they rest the same way every load. */
const FAN = [-10, -4, 0, 4, 8];

interface ConnectMoreAppsProps {
  connectors: MoreConnectorLink[];
  connectorsHref: string;
  buildConnectorHref: (connectorTypeParam: string) => string;
  onNavigateToConnectors: () => void;
  onNavigateToConnector: (connectorTypeParam: string) => void;
}

/** Pinned under the tree so a long source list cannot push the offer out of reach. */
export function ConnectMoreApps({
  connectors,
  connectorsHref,
  buildConnectorHref,
  onNavigateToConnectors,
  onNavigateToConnector,
}: ConnectMoreAppsProps) {
  const { t } = useTranslation();

  const follow = (e: React.MouseEvent, go: () => void) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    go();
  };

  return (
    <section
      aria-labelledby="kb-connect-more-apps"
      style={{
        position: 'relative',
        isolation: 'isolate',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: 'var(--space-2) var(--space-3) var(--space-3)',
        borderTop: '1px solid var(--olive-3)',
      }}
    >
      {/* Light from behind the top-right corner rather than a card around the block. */}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          top: -164,
          right: -104,
          width: 290,
          height: 290,
          borderRadius: '50%',
          background:
            'radial-gradient(closest-side, var(--emerald-a4) 0%, var(--emerald-a3) 38%, transparent 100%)',
          pointerEvents: 'none',
          zIndex: -1,
        }}
      />
      <Flex align="center" justify="between" gap="2" style={{ minWidth: 0 }}>
        <Text
          id="kb-connect-more-apps"
          size="2"
          weight="medium"
          style={{
            color: 'var(--neutral-12)',
            lineHeight: '20px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
        >
          {t('sidebar.connectMoreApps')}
        </Text>
        <Tooltip content={t('sidebar.seeMoreConnectors')} side="top">
          <IconButton
            asChild
            size="1"
            variant="ghost"
            color="gray"
            style={{ width: 24, height: 24, margin: 0, flexShrink: 0, cursor: 'pointer' }}
          >
            <Link
              href={connectorsHref}
              aria-label={t('sidebar.seeMoreConnectors')}
              onClick={(e: React.MouseEvent) => follow(e, onNavigateToConnectors)}
            >
              <MaterialIcon name="add" size={16} color="var(--neutral-11)" />
            </Link>
          </IconButton>
        </Tooltip>
      </Flex>
      <Text as="p" size="1" style={{ margin: 0, color: 'var(--neutral-11)', lineHeight: '16px' }}>
        {t('sidebar.connectMoreAppsHint')}
      </Text>
      <Flex align="center" style={{ marginTop: 6, paddingLeft: 4, height: 32 }}>
        {connectors.slice(0, FAN.length).map((connector, i) => (
          <Tooltip key={connector.id} content={connector.name} side="top">
            <Link
              href={buildConnectorHref(connector.connectorTypeParam)}
              aria-label={connector.name}
              onClick={(e: React.MouseEvent) =>
                follow(e, () => onNavigateToConnector(connector.connectorTypeParam))
              }
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 28,
                height: 28,
                marginLeft: i === 0 ? 0 : -4,
                backgroundColor: 'var(--olive-3)',
                borderRadius: 'var(--radius-3)',
                // Ring in the rail's own ground, so overlapping tiles get a clean cut.
                boxShadow: '0 0 0 2px var(--olive-1), 0 2px 6px var(--black-a4)',
                zIndex: i + 1,
                flexShrink: 0,
                transform: `rotate(${FAN[i]}deg)`,
              }}
            >
              <ConnectorIcon type={connector.type} size={16} />
            </Link>
          </Tooltip>
        ))}
      </Flex>
    </section>
  );
}
