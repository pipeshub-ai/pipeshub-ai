'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Badge, Box, Flex, Text } from '@radix-ui/themes';
import { ServiceReason } from './service-reason';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** `agentHarness` in GET /api/v1/health: whether durable agent runs are on, and why not. */
export type AgentHarnessHealth =
  | { status: 'off' }
  | { status: 'available' }
  | { status: 'unavailable'; reason: string; detail: string };

/** The health answer's `agentHarness`, or null when it is absent or not a shape this page knows. */
export function agentHarnessHealthOf(raw: unknown): AgentHarnessHealth | null {
  if (raw === null || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (value.status === 'off' || value.status === 'available') return { status: value.status };
  if (value.status === 'unavailable') {
    return {
      status: 'unavailable',
      reason: typeof value.reason === 'string' ? value.reason : '',
      detail: typeof value.detail === 'string' ? value.detail : '',
    };
  }
  return null;
}

/** Why durable runs are off, in the reader's language; a reason this build does not know shows the server's words. */
export function agentHarnessReason(health: AgentHarnessHealth, t: TFunction): string | null {
  if (health.status !== 'unavailable') return null;
  switch (health.reason) {
    case 'standalone':
      return t('workspace.services.app.agentHarness.reason.standalone');
    case 'arbiter':
      return t('workspace.services.app.agentHarness.reason.arbiter');
    case 'majority_unavailable':
      return t('workspace.services.app.agentHarness.reason.majorityUnavailable');
    case 'probe_failed':
      return t('workspace.services.app.agentHarness.reason.probeFailed');
    case 'preparation_failed':
      return t('workspace.services.app.agentHarness.reason.preparationFailed');
    default:
      return health.detail || t('workspace.services.app.agentHarness.reason.unknown');
  }
}

/** The Services page's agent-runs row; nothing when the deployment has not turned the harness on. */
export function AgentHarnessHealthRow({ health }: { health: AgentHarnessHealth | null }) {
  const { t } = useTranslation();
  if (health === null || health.status === 'off') return null;
  const reason = agentHarnessReason(health, t);

  return (
    <Flex
      align="start"
      gap="3"
      style={{
        padding: '12px 14px',
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-1)',
      }}
    >
      <Flex
        align="center"
        justify="center"
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-3)',
          flexShrink: 0,
        }}
      >
        <MaterialIcon name="smart_toy" size={18} color="var(--slate-11)" />
      </Flex>

      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {t('workspace.services.app.agentHarness.label')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, fontWeight: 300 }}>
          {t('workspace.services.app.agentHarness.description')}
        </Text>
        <ServiceReason text={reason} />
      </Box>

      {health.status === 'available' ? (
        <Badge color="green" variant="soft" size="1" style={{ flexShrink: 0 }}>
          {t('workspace.services.statusHealthy')}
        </Badge>
      ) : (
        <Badge color="amber" variant="soft" size="1" style={{ flexShrink: 0 }}>
          {t('workspace.services.app.agentHarness.statusUnavailable')}
        </Badge>
      )}
    </Flex>
  );
}
