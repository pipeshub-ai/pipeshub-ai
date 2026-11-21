'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Tabs, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCopyText } from '@/lib/hooks/use-copy-text';

export const MCP_CLIENTS = ['claude', 'chatgpt', 'cursor', 'vscode', 'claudeCode'] as const;
export type McpClient = (typeof MCP_CLIENTS)[number];

/** Single-quotes a value for a POSIX shell, so `?`, `[` or `]` in a URL are not read as a glob (zsh stops on one). */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/** What to paste into each client's own config, for the clients configured by file or command. */
export function clientSnippet(client: McpClient, url: string): string | undefined {
  switch (client) {
    case 'cursor':
      return JSON.stringify({ mcpServers: { pipeshub: { url } } }, null, 2);
    case 'vscode':
      return JSON.stringify({ servers: { pipeshub: { type: 'http', url } } }, null, 2);
    case 'claudeCode':
      return `claude mcp add --transport http pipeshub ${shellQuote(url)}`;
    default:
      return undefined;
  }
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const { t } = useTranslation();
  const { copied, copy } = useCopyText();
  return (
    <Button
      size="2"
      variant="soft"
      color="gray"
      className="touch-size"
      onClick={() => void copy(text)}
      aria-label={label}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
        <MaterialIcon name={copied ? 'check' : 'content_copy'} size={16} />
      </span>
      {copied ? t('workspace.mcpAccess.connect.copied') : t('workspace.mcpAccess.connect.copy')}
    </Button>
  );
}

/** The endpoint and how each client connects to it. Users sign in themselves, so no secret is shown. */
export function McpConnectCard({ url }: { url: string }) {
  const { t } = useTranslation();
  const [client, setClient] = useState<McpClient>('claude');
  const snippet = clientSnippet(client, url);
  const tipKey = `workspace.mcpAccess.connect.clients.${client}.tip`;
  const tip = t(tipKey);

  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        background: 'var(--olive-2)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.mcpAccess.connect.title')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
          {t('workspace.mcpAccess.connect.description')}
        </Text>
      </Flex>

      <Flex gap="2" align="end" wrap="wrap">
        <Flex direction="column" gap="1" style={{ flex: '1 1 280px', minWidth: 0 }}>
          <Text as="label" htmlFor="mcp-endpoint" size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.mcpAccess.connect.endpointLabel')}
          </Text>
          <TextField.Root
            id="mcp-endpoint"
            size="2"
            readOnly
            value={url}
            style={{ fontFamily: 'var(--code-font-family)' }}
          />
        </Flex>
        <CopyButton text={url} label={t('workspace.mcpAccess.connect.copyEndpoint')} />
      </Flex>

      <Tabs.Root value={client} onValueChange={(value) => setClient(value as McpClient)}>
        <Tabs.List size="1" aria-label={t('workspace.mcpAccess.connect.clientsLabel')} style={{ flexWrap: 'wrap' }}>
          {MCP_CLIENTS.map((id) => (
            <Tabs.Trigger key={id} value={id} className="touch-size">
              {t(`workspace.mcpAccess.connect.clients.${id}.label`)}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value={client}>
          <Flex direction="column" gap="2" pt="3">
            <Text size="2" style={{ color: 'var(--gray-12)', lineHeight: '20px' }}>
              {t(`workspace.mcpAccess.connect.clients.${client}.steps`)}
            </Text>
            {snippet && (
              <Flex gap="2" align="start" wrap="wrap">
                <Box
                  asChild
                  style={{
                    flex: '1 1 320px',
                    minWidth: 0,
                    margin: 0,
                    padding: 'var(--space-3)',
                    borderRadius: 'var(--radius-2)',
                    background: 'var(--gray-a3)',
                    fontFamily: 'var(--code-font-family)',
                    fontSize: 12,
                    lineHeight: '18px',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    color: 'var(--gray-12)',
                  }}
                >
                  <pre>{snippet}</pre>
                </Box>
                {/* Keyed by the snippet, so "Copied" does not carry over to another client's configuration. */}
                <CopyButton key={snippet} text={snippet} label={t('workspace.mcpAccess.connect.copySnippet')} />
              </Flex>
            )}
            {tip !== tipKey && (
              <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
                {tip}
              </Text>
            )}
          </Flex>
        </Tabs.Content>
      </Tabs.Root>
    </Flex>
  );
}
