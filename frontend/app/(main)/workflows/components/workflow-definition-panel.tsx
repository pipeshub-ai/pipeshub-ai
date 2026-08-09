'use client';

import React from 'react';
import { Badge, Box, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Workflow } from '../types';

interface WorkflowDefinitionPanelProps {
  workflow: Workflow;
}

/**
 * Displays the natural-language definition of an agent_task workflow:
 * goal/description, allowed tools, connectors, and knowledge bases.
 * Shown in WorkflowDetailView for agent_task workflows that have no
 * generated code yet.
 */
export function WorkflowDefinitionPanel({ workflow }: WorkflowDefinitionPanelProps) {
  const { t } = useTranslation();

  const hasTools = workflow.toolNames && workflow.toolNames.length > 0;
  const hasConnectors = workflow.connectorIds && workflow.connectorIds.length > 0;
  const hasCollections = workflow.collectionIds && workflow.collectionIds.length > 0;
  const hasMeta = hasTools || hasConnectors || hasCollections || workflow.maxTurns != null || workflow.timeoutSeconds != null;

  return (
    <Flex direction="column" gap="3">
      <Flex align="center" gap="2">
        <MaterialIcon name="smart_toy" size={16} color="var(--violet-9)" />
        <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workflowsPage.definitionPanel.title', 'Workflow Definition')}
        </Heading>
        <Badge variant="soft" color="violet" size="1">
          {t('workflowsPage.definitionPanel.agentTask', 'Agent Task')}
        </Badge>
      </Flex>

      <Box
        style={{
          padding: 'var(--space-4)',
          background: 'var(--violet-a2)',
          borderRadius: 'var(--radius-3)',
          border: '1px solid var(--violet-a4)',
        }}
      >
        <Flex direction="column" gap="3">
          {workflow.description ? (
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                {t('workflowsPage.definitionPanel.goal', 'Goal')}
              </Text>
              <Text
                size="2"
                style={{
                  color: 'var(--slate-12)',
                  lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {workflow.description}
              </Text>
            </Flex>
          ) : (
            <Text size="2" style={{ color: 'var(--slate-9)', fontStyle: 'italic' }}>
              {t('workflowsPage.definitionPanel.noDescription', 'No description provided.')}
            </Text>
          )}

          {hasMeta && (
            <Flex direction="column" gap="2" style={{ borderTop: '1px solid var(--violet-a4)', paddingTop: 'var(--space-3)' }}>
              {hasTools && (
                <Flex align="start" gap="2">
                  <MaterialIcon name="build" size={14} color="var(--jade-9)" style={{ marginTop: 2 }} />
                  <Flex direction="column" gap="1">
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                      {t('workflowsPage.definitionPanel.tools', 'Allowed Tools')}
                    </Text>
                    <Flex gap="1" wrap="wrap">
                      {workflow.toolNames!.map((name) => (
                        <Badge key={name} variant="outline" color="jade" size="1">
                          {name}
                        </Badge>
                      ))}
                    </Flex>
                  </Flex>
                </Flex>
              )}

              {hasConnectors && (
                <Flex align="start" gap="2">
                  <MaterialIcon name="hub" size={14} color="var(--cyan-9)" style={{ marginTop: 2 }} />
                  <Flex direction="column" gap="1">
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                      {t('workflowsPage.definitionPanel.connectors', 'Connectors')}
                    </Text>
                    <Flex gap="1" wrap="wrap">
                      {workflow.connectorIds!.map((id) => (
                        <Badge key={id} variant="outline" color="cyan" size="1">
                          {id}
                        </Badge>
                      ))}
                    </Flex>
                  </Flex>
                </Flex>
              )}

              {hasCollections && (
                <Flex align="start" gap="2">
                  <MaterialIcon name="library_books" size={14} color="var(--amber-9)" style={{ marginTop: 2 }} />
                  <Flex direction="column" gap="1">
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                      {t('workflowsPage.definitionPanel.collections', 'Knowledge Bases')}
                    </Text>
                    <Flex gap="1" wrap="wrap">
                      {workflow.collectionIds!.map((id) => (
                        <Badge key={id} variant="outline" color="amber" size="1">
                          {id}
                        </Badge>
                      ))}
                    </Flex>
                  </Flex>
                </Flex>
              )}

              {(workflow.maxTurns != null || workflow.timeoutSeconds != null) && (
                <Flex gap="4" wrap="wrap">
                  {workflow.maxTurns != null && (
                    <Flex align="center" gap="1">
                      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                        {t('workflowsPage.definitionPanel.maxTurns', 'Max turns:')}
                      </Text>
                      <Text size="1" style={{ color: 'var(--slate-12)' }}>
                        {workflow.maxTurns}
                      </Text>
                    </Flex>
                  )}
                  {workflow.timeoutSeconds != null && (
                    <Flex align="center" gap="1">
                      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                        {t('workflowsPage.definitionPanel.timeout', 'Timeout:')}
                      </Text>
                      <Text size="1" style={{ color: 'var(--slate-12)' }}>
                        {workflow.timeoutSeconds}s
                      </Text>
                    </Flex>
                  )}
                </Flex>
              )}
            </Flex>
          )}
        </Flex>
      </Box>
    </Flex>
  );
}
