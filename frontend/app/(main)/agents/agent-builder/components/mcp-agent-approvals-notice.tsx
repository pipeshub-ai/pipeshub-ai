'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EnterpriseBadge } from '@/app/components/ui/enterprise-badge';

/** Per-agent tool approvals are an Enterprise Edition feature; each person's own apply here. */
export function McpAgentApprovalsNotice() {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" mt="3" data-testid="mcp-agent-approvals-ee">
      <MaterialIcon name="rule" size={14} color="var(--agent-flow-text-muted)" />
      <Text size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
        {t('agentBuilder.mcpAgentApprovals')}
      </Text>
      <EnterpriseBadge tooltip={t('agentBuilder.mcpAgentApprovalsEnterprise')} />
    </Flex>
  );
}
