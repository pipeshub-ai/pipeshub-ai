'use client';

import React from 'react';
import { Badge, Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { workflowCardStyle } from './workflow-format';

export interface PrereqIssue {
  kind: string;
  id: string;
  reason: string;
  blocking: boolean;
}

export interface PrereqCheckCardPayload {
  name: 'prerequisite_check_result';
  ok: boolean;
  issues: PrereqIssue[];
  summary: string;
}

export interface PrereqCheckCardProps {
  payload: PrereqCheckCardPayload;
}

const KIND_LABEL_DEFAULTS: Record<string, string> = {
  connector: 'Connector',
  collection: 'Knowledge Base',
  mcp_server: 'MCP Server',
  toolset: 'Toolset',
  tool: 'Tool',
  schedule: 'Schedule',
};

function IssueRow({ issue }: { issue: PrereqIssue }) {
  const { t } = useTranslation();
  const color = issue.blocking ? 'var(--red-9)' : 'var(--amber-9)';
  const iconName = issue.blocking ? 'block' : 'warning';
  const badgeColor = issue.blocking ? ('red' as const) : ('amber' as const);

  return (
    <Flex align="start" gap="2" style={{ padding: 'var(--space-1) 0' }}>
      <MaterialIcon name={iconName} size={16} color={color} />
      <Flex direction="column" gap="1" style={{ flex: 1 }}>
        <Flex align="center" gap="1" wrap="wrap">
          <Badge size="1" variant="soft" color={badgeColor}>
            {t(`prereqCheckCard.kind.${issue.kind}`, KIND_LABEL_DEFAULTS[issue.kind] ?? issue.kind)}
          </Badge>
          <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {issue.id}
          </Text>
        </Flex>
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {issue.reason}
        </Text>
      </Flex>
    </Flex>
  );
}

/**
 * In-chat card emitted as a CUSTOM SSE event when the agent calls
 * `workflow_manage(action="validate")`. Shows per-resource pass/fail
 * state (blocking issues in red, non-blocking warnings in amber).
 */
export function PrereqCheckCard({ payload }: PrereqCheckCardProps) {
  const { t } = useTranslation();
  const ok = payload.ok;
  const blockingIssues = payload.issues.filter((i) => i.blocking);
  const warnIssues = payload.issues.filter((i) => !i.blocking);

  const headerIcon = ok ? 'check_circle' : 'error';
  const headerColor = ok ? 'var(--green-9)' : 'var(--red-9)';
  const headerText = ok
    ? t('prereqCheckCard.passed', 'All prerequisites satisfied')
    : t('prereqCheckCard.failed', 'Prerequisites check failed');

  return (
    <Box style={{ ...workflowCardStyle, marginTop: 'var(--space-3)' }}>
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2">
          <MaterialIcon name={headerIcon} size={16} color={headerColor} />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {headerText}
          </Text>
          <Badge size="1" color={ok ? 'green' : 'red'} variant="soft">
            {ok ? t('prereqCheckCard.pass', 'PASS') : t('prereqCheckCard.fail', 'FAIL')}
          </Badge>
        </Flex>

        {blockingIssues.length > 0 && (
          <Flex direction="column" gap="0">
            <Text size="1" weight="medium" style={{ color: 'var(--red-11)', marginBottom: 'var(--space-1)' }}>
              {t('prereqCheckCard.blockingHeading', 'Blocking issues — must resolve before creating workflow:')}
            </Text>
            {blockingIssues.map((issue, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <IssueRow key={`${issue.kind}-${issue.id}-${i}`} issue={issue} />
            ))}
          </Flex>
        )}

        {warnIssues.length > 0 && (
          <Flex direction="column" gap="0">
            <Text size="1" weight="medium" style={{ color: 'var(--amber-11)', marginBottom: 'var(--space-1)' }}>
              {t('prereqCheckCard.warningsHeading', 'Warnings (non-blocking):')}
            </Text>
            {warnIssues.map((issue, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <IssueRow key={`${issue.kind}-${issue.id}-${i}`} issue={issue} />
            ))}
          </Flex>
        )}

        {payload.issues.length === 0 && (
          <Text size="1" style={{ color: 'var(--green-11)' }}>
            {t(
              'prereqCheckCard.allClear',
              'All connectors authenticated, collections accessible, and schedule is valid.',
            )}
          </Text>
        )}
      </Flex>
    </Box>
  );
}
