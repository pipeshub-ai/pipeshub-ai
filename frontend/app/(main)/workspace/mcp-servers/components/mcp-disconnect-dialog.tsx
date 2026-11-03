'use client';

import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConfirmationDialog } from '../../components';
import { usesSharedCredential } from '../connection-state';
import type { McpMyServerEntry } from '../types';

interface McpDisconnectDialogProps {
  instance: McpMyServerEntry | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isLoading?: boolean;
  /** Portal host, to stack above a workspace drawer. */
  container?: HTMLElement | null;
}

/** Asks before signing out of a server: what stops, what is kept, what is deleted. A shared
 * credential is everyone's, so removing it stops the server for the whole organization. */
export function McpDisconnectDialog({ instance, onOpenChange, onConfirm, isLoading, container }: McpDisconnectDialogProps) {
  const { t } = useTranslation();
  const shared = instance ? usesSharedCredential(instance) : false;
  const count = instance?.tools?.length ?? 0;
  const who = shared ? 'shared' : 'yours';
  const effect = count > 0 ? t(`workspace.mcpServers.disconnectDialog.${who}`, { count }) : t(`workspace.mcpServers.disconnectDialog.${who}Unknown`);

  const line = (icon: string, color: string, text: string) => (
    <Flex align="center" gap="2">
      <MaterialIcon name={icon} size={16} color={color} />
      <Text size="2">{text}</Text>
    </Flex>
  );

  return (
    <ConfirmationDialog
      open={instance !== null}
      onOpenChange={onOpenChange}
      title={t('workspace.mcpServers.disconnectDialog.title', { name: instance?.name ?? '' })}
      message={
        <Flex direction="column" gap="3">
          <Text size="2">{effect}</Text>
          <Flex direction="column" gap="1">
            {line('check', 'var(--green-11)', t('workspace.mcpServers.disconnectDialog.keeps'))}
            {line('check', 'var(--green-11)', t('workspace.mcpServers.disconnectDialog.reconnect'))}
            {line(
              'close',
              'var(--red-11)',
              t(shared ? 'workspace.mcpServers.disconnectDialog.deletesShared' : 'workspace.mcpServers.disconnectDialog.deletesYours')
            )}
          </Flex>
        </Flex>
      }
      confirmLabel={t('workspace.mcpServers.cta.disconnect')}
      confirmVariant="danger"
      isLoading={isLoading}
      confirmLoadingLabel={t('workspace.mcpServers.disconnectDialog.disconnecting')}
      onConfirm={onConfirm}
      container={container}
    />
  );
}
