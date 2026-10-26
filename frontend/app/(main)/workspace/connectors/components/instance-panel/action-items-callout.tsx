'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Callout, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ConnectorInstance } from '../../types';

/** What the connector's admin has to do, on its overview; each item is worded by the connector. */
export function ActionItemsCallout({
  instance,
  onOpenAuthentication,
}: {
  instance: ConnectorInstance;
  /** Opens the connector's settings on the Authenticate tab. */
  onOpenAuthentication?: () => void;
}) {
  const { t } = useTranslation();
  const items = instance.actionItems ?? [];
  if (items.length === 0) return null;
  const anError = items.some((item) => item.severity === 'error');
  const needsAuthentication = items.some((item) => item.action === 'authenticate');

  return (
    <Callout.Root color={anError ? 'red' : 'amber'} variant="surface" size="1" role="status">
      <Callout.Icon>
        <MaterialIcon name="priority_high" size={16} />
      </Callout.Icon>
      <Flex direction="column" gap="2" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium">
          {t('workspace.connectors.actionItems.title')}
        </Text>
        {items.map((item) => (
          <Flex key={item.code} direction="column" gap="1">
            <Text size="2" weight="medium">
              {item.title}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {item.message}
            </Text>
            {item.docsUrl && /^https:\/\//i.test(item.docsUrl) ? (
              <Text size="1">
                <a
                  href={item.docsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--accent-11)', textDecoration: 'underline' }}
                >
                  {t('workspace.connectors.actionItems.viewSteps')}
                </a>
              </Text>
            ) : null}
          </Flex>
        ))}
        {needsAuthentication && onOpenAuthentication ? (
          <Flex>
            <Button size="2" variant="soft" color={anError ? 'red' : 'amber'} onClick={onOpenAuthentication} style={{ minHeight: 44 }}>
              {t('workspace.connectors.actionItems.openAuthentication')}
            </Button>
          </Flex>
        ) : null}
      </Flex>
    </Callout.Root>
  );
}
