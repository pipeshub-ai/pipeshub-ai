'use client';

import { useTranslation } from 'react-i18next';
import { Badge, Callout, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { McpServerInstance } from '../types';
import { MCP_CUSTOM_STDIO_FLAG } from '../types';

export function isMcpInstanceDisabled(instance: Pick<McpServerInstance, 'disabledReason'> | null | undefined): boolean {
  return Boolean(instance?.disabledReason);
}

/** Shown in place of the status badge on instances the deployment won't run. */
export function McpDisabledBadge({ instance }: { instance: Pick<McpServerInstance, 'disabledReason'> }) {
  const { t } = useTranslation();
  if (!isMcpInstanceDisabled(instance)) return null;
  return (
    <Tooltip content={t('workspace.mcpServers.stdioPolicy.disabledDescription', { flag: MCP_CUSTOM_STDIO_FLAG })}>
      <Badge color="red" size="1">
        {t('workspace.mcpServers.status.disabled')}
      </Badge>
    </Tooltip>
  );
}

export function McpDisabledCallout({ instance }: { instance: Pick<McpServerInstance, 'disabledReason'> | null | undefined }) {
  const { t } = useTranslation();
  if (!isMcpInstanceDisabled(instance)) return null;
  return (
    <Callout.Root color="red" variant="surface" size="1">
      <Callout.Icon>
        <MaterialIcon name="block" size={16} />
      </Callout.Icon>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium">
          {t('workspace.mcpServers.stdioPolicy.disabledTitle')}
        </Text>
        <Callout.Text size="1">
          {t('workspace.mcpServers.stdioPolicy.disabledDescription', { flag: MCP_CUSTOM_STDIO_FLAG })}
        </Callout.Text>
      </Flex>
    </Callout.Root>
  );
}
