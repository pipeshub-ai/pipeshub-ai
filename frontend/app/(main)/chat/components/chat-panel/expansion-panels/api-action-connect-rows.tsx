'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Text } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { ApiActionConnectionPanel } from '@/app/(main)/workspace/api-toolsets/components';
import { connectionMode } from '@/app/(main)/workspace/api-toolsets/toolset-form';
import type { ApiToolset } from '@/app/(main)/workspace/api-toolsets/types';
import { ConnectRow } from './connect-row';

/** The person's API actions a missing credential keeps out of the chat. */
export function apiActionsToConnect(actions: ApiToolset[]): ApiToolset[] {
  return actions.filter((action) => !action.isConnected && action.authType !== 'none');
}

interface ApiActionConnectRowsProps {
  actions: ApiToolset[];
  isAdmin: boolean;
  rowStyle: React.CSSProperties;
  /** Off when the rows follow other "not connected" rows that already carry the heading. */
  showHeading: boolean;
  /** After a credential is saved: reload the list and turn the action's operations on. */
  onConnected: (instanceId: string) => void;
}

/** "Not connected" rows for API actions, each connecting where it is. */
export function ApiActionConnectRows({ actions, isAdmin, rowStyle, showHeading, onConnected }: ApiActionConnectRowsProps) {
  const { t } = useTranslation();
  const [dialogFor, setDialogFor] = useState<ApiToolset | null>(null);

  if (actions.length === 0) return null;

  return (
    <Flex direction="column" gap="1" data-testid="api-action-connect-rows">
      {showHeading ? (
        <Text size="1" style={{ color: 'var(--gray-11)', marginTop: 'var(--space-1)' }}>
          {t('chat.agentResources.notConnected')}
        </Text>
      ) : null}
      {actions.map((action) => (
        <ConnectRow
          key={action.id}
          testId="api-action-connect-row"
          rowStyle={rowStyle}
          icon={<ConnectorIcon type="api" size={18} />}
          name={action.name}
          action={
            // Only an admin can set a shared credential.
            connectionMode(action, isAdmin) === 'managed' ? (
              <Badge size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
                {t('workspace.mcpServers.status.waitingForAdmin')}
              </Badge>
            ) : (
              <Button
                type="button"
                size="1"
                variant="soft"
                onClick={() => setDialogFor(action)}
                style={{ flexShrink: 0 }}
              >
                {t('chat.agentResources.connect')}
              </Button>
            )
          }
        />
      ))}
      <ApiActionConnectionPanel
        toolset={dialogFor}
        isAdmin={isAdmin}
        onOpenChange={(open) => {
          if (!open) setDialogFor(null);
        }}
        onChanged={() => {
          if (!dialogFor) return;
          setDialogFor(null);
          onConnected(dialogFor.id);
        }}
      />
    </Flex>
  );
}
