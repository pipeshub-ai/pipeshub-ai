'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Code, Flex, Grid, Switch, Table, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { McpAccessTool } from '../api';
import { kindOf, type BlockReason } from '../tool-groups';

const KIND_BADGE = {
  read: { key: 'workspace.mcpAccess.tools.kinds.readOnly', color: 'gray' },
  write: { key: 'workspace.mcpAccess.tools.writes', color: 'amber' },
  change: { key: 'workspace.mcpAccess.tools.kinds.changes', color: 'red' },
} as const;

function DetailBox({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Flex
      direction="column"
      gap="2"
      style={{ border: '1px solid var(--olive-4)', borderRadius: 'var(--radius-2)', padding: 'var(--space-3)' }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {title}
      </Text>
      {children}
    </Flex>
  );
}

export interface McpToolRowProps {
  tool: McpAccessTool;
  /** The admin-facing description, when this build has one. */
  summary?: string;
  /** When the server offers the tool. */
  availability: string;
  checked: boolean;
  blocked?: BlockReason;
  disabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCheckedChange: (on: boolean) => void;
}

/** One tool: what it is and its switch, and when opened what it needs and takes. */
export function McpToolRow({
  tool,
  summary,
  availability,
  checked,
  blocked,
  disabled,
  open,
  onOpenChange,
  onCheckedChange,
}: McpToolRowProps) {
  const { t } = useTranslation();
  const [showModelText, setShowModelText] = useState(false);
  const kind = KIND_BADGE[kindOf(tool)];
  const args = tool.arguments ?? [];
  const scopes = tool.scopes ?? [];
  const detailsId = `mcp-tool-${tool.name}`;
  const hasDetails = tool.description !== undefined;

  return (
    <Box style={{ borderTop: '1px solid var(--olive-3)' }}>
      <Flex align="center" gap="3" style={{ padding: 'var(--space-2) 0', minHeight: 44 }}>
        {/* Outside the title's wrapping line, so a long title never leaves it on a line of its own. */}
        {hasDetails && (
          <Button
            variant="ghost"
            color="gray"
            size="2"
            aria-expanded={open}
            aria-controls={detailsId}
            aria-label={t(open ? 'workspace.mcpAccess.tools.hideDetails' : 'workspace.mcpAccess.tools.showDetails', { tool: tool.title })}
            onClick={() => onOpenChange(!open)}
            style={{ margin: 0, flexShrink: 0, alignSelf: 'flex-start' }}
          >
            <span aria-hidden="true" style={{ display: 'inline-flex' }}>
              <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={18} color="var(--gray-11)" />
            </span>
          </Button>
        )}
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {tool.title}
            </Text>
            {tool.title !== tool.name && (
              <Code size="1" color="gray" variant="soft" style={{ wordBreak: 'break-all' }}>
                {tool.name}
              </Code>
            )}
            <Badge size="1" color={kind.color} variant="soft">
              {t(kind.key)}
            </Badge>
            {tool.hints?.openWorld && (
              <Badge size="1" color="blue" variant="soft">
                {t('workspace.mcpAccess.tools.kinds.openWorld')}
              </Badge>
            )}
          </Flex>
          {summary && (
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                display: 'block',
                marginTop: 2,
                lineHeight: '16px',
                fontWeight: 300,
                wordBreak: 'break-word',
              }}
            >
              {summary}
            </Text>
          )}
          {/* With MCP access off every row would say so; the switch above already does. */}
          {blocked && blocked !== 'mcpOff' && (
            <Text size="1" style={{ color: 'var(--amber-11)', display: 'block', marginTop: 2 }}>
              {t(`workspace.mcpAccess.tools.blocked.${blocked}`)}
            </Text>
          )}
        </Box>
        <Switch
          size="1"
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
          aria-label={tool.title}
          style={{ flexShrink: 0, cursor: disabled ? 'default' : 'pointer' }}
        />
      </Flex>

      {hasDetails && open && (
        <Flex id={detailsId} direction="column" gap="3" pb="4" pl={{ initial: '0', sm: '6' }}>
          <Grid columns={{ initial: '1', sm: '2' }} gap="3">
            <DetailBox title={t('workspace.mcpAccess.tools.details.scopes')}>
              {scopes.length ? (
                <Flex gap="1" wrap="wrap">
                  {scopes.map((scope) => (
                    <Code key={scope} size="1" variant="soft" color="gray">
                      {scope}
                    </Code>
                  ))}
                </Flex>
              ) : (
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.mcpAccess.tools.details.noScopes')}
                </Text>
              )}
            </DetailBox>
            <DetailBox title={t('workspace.mcpAccess.tools.details.availability')}>
              <Text size="1" style={{ color: 'var(--gray-12)', lineHeight: '17px' }}>
                {availability}
              </Text>
            </DetailBox>
          </Grid>

          <Flex direction="column" gap="2">
            <Text size="1" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.mcpAccess.tools.details.arguments', { n: args.length })}
            </Text>
            {args.length === 0 ? (
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {t('workspace.mcpAccess.tools.details.noArguments')}
              </Text>
            ) : (
              <Box style={{ overflowX: 'auto', border: '1px solid var(--olive-4)', borderRadius: 'var(--radius-2)' }}>
                <Table.Root size="1" style={{ minWidth: 520 }}>
                  <Table.Header>
                    <Table.Row>
                      <Table.ColumnHeaderCell>{t('workspace.mcpAccess.tools.details.columns.name')}</Table.ColumnHeaderCell>
                      <Table.ColumnHeaderCell>{t('workspace.mcpAccess.tools.details.columns.type')}</Table.ColumnHeaderCell>
                      <Table.ColumnHeaderCell>{t('workspace.mcpAccess.tools.details.columns.description')}</Table.ColumnHeaderCell>
                    </Table.Row>
                  </Table.Header>
                  <Table.Body>
                    {args.map((arg) => (
                      <Table.Row key={arg.name}>
                        <Table.RowHeaderCell style={{ whiteSpace: 'nowrap' }}>
                          <Code size="1" variant="ghost">
                            {arg.name}
                          </Code>
                          {arg.required && (
                            <Text size="1" weight="medium" style={{ color: 'var(--red-11)', marginLeft: 6 }}>
                              {t('workspace.mcpAccess.tools.details.required')}
                            </Text>
                          )}
                        </Table.RowHeaderCell>
                        <Table.Cell style={{ whiteSpace: 'nowrap' }}>
                          <Code size="1" variant="ghost" color="gray">
                            {arg.type}
                          </Code>
                        </Table.Cell>
                        <Table.Cell>
                          <Text size="1" style={{ color: 'var(--gray-12)', lineHeight: '17px' }}>
                            {arg.description ?? '—'}
                          </Text>
                          {arg.values?.length ? (
                            <Text size="1" style={{ display: 'block', marginTop: 2, color: 'var(--slate-11)' }}>
                              {t('workspace.mcpAccess.tools.details.oneOf', { values: arg.values.join(' · ') })}
                            </Text>
                          ) : null}
                        </Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Root>
              </Box>
            )}
          </Flex>

          <Flex direction="column" gap="2" align="start">
            <Button
              size="1"
              variant="soft"
              color="gray"
              aria-expanded={showModelText}
              onClick={() => setShowModelText((shown) => !shown)}
            >
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon name="smart_toy" size={14} />
              </span>
              {t(showModelText ? 'workspace.mcpAccess.tools.details.hideModelText' : 'workspace.mcpAccess.tools.details.modelText')}
            </Button>
            {showModelText && (
              <Flex direction="column" gap="1" style={{ width: '100%' }}>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.mcpAccess.tools.details.modelTextHint')}
                </Text>
                <Box
                  asChild
                  style={{
                    margin: 0,
                    padding: 'var(--space-3)',
                    borderRadius: 'var(--radius-2)',
                    background: 'var(--gray-a2)',
                    border: '1px solid var(--olive-4)',
                    fontFamily: 'var(--code-font-family)',
                    fontSize: 12,
                    lineHeight: '18px',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    color: 'var(--gray-12)',
                    // Some tools tell the model several thousand characters; the row stays readable.
                    maxHeight: 360,
                    overflowY: 'auto',
                  }}
                >
                  <pre tabIndex={0}>{tool.description}</pre>
                </Box>
              </Flex>
            )}
          </Flex>
        </Flex>
      )}
    </Box>
  );
}
