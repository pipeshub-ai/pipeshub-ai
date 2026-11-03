'use client';

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { McpAuthDialog } from '@/app/(main)/workspace/mcp-servers/components';
import {
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
} from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import type { McpBlockedServer, McpConfigMissingDetails } from '../../stream-error';

/** Whether the person in the chat can fix this server themselves, right here. */
function connectableHere(server: McpBlockedServer, details: McpConfigMissingDetails): boolean {
  return (
    server.problem === 'not_connected' &&
    !details.serviceAccount &&
    !server.sharedCredential &&
    (server.authMode === 'oauth' || server.authMode === 'api_token' || server.authMode === 'headers')
  );
}

async function findMyEntry(instanceId: string): Promise<McpMyServerEntry | null> {
  const { instances } = await McpServersApi.getMyMcpServers(false);
  return instances.find((entry) => entry._id === instanceId) ?? null;
}

interface McpConnectRequiredCardProps {
  details: McpConfigMissingDetails;
  /** The question that was stopped, sent again by "Try again". */
  question: string;
}

/** Shown instead of the error text when an agent chat stopped because MCP servers aren't connected. */
export function McpConnectRequiredCard({ details, question }: McpConnectRequiredCardProps) {
  const { t } = useTranslation();
  const [connected, setConnected] = useState<ReadonlySet<string>>(new Set());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tokenEntry, setTokenEntry] = useState<McpMyServerEntry | null>(null);
  const [resent, setResent] = useState(false);
  const oauthTargetRef = useRef<string | null>(null);

  const markConnected = (instanceId: string) => setConnected((prev) => new Set(prev).add(instanceId));
  const setError = (instanceId: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[instanceId] = message;
      else delete next[instanceId];
      return next;
    });

  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: async () => {
      const instanceId = oauthTargetRef.current;
      return instanceId ? Boolean((await findMyEntry(instanceId))?.isAuthenticated) : false;
    },
    onVerified: () => {
      setBusyId(null);
      if (oauthTargetRef.current) markConnected(oauthTargetRef.current);
    },
    onFailed: (failure) => {
      setBusyId(null);
      if (oauthTargetRef.current) setError(oauthTargetRef.current, mcpOAuthFailureMessage(t, failure));
    },
  });

  const connect = (server: McpBlockedServer) => {
    setError(server.instanceId, null);
    setBusyId(server.instanceId);
    if (server.authMode === 'oauth') {
      oauthTargetRef.current = server.instanceId;
      // Called inside the click: the popup has to open before anything is awaited.
      void startOAuthPopup(server.instanceId);
      return;
    }
    void findMyEntry(server.instanceId)
      .then((entry) => {
        if (entry) setTokenEntry(entry);
        else setError(server.instanceId, t('chat.mcpConnect.unavailable'));
      })
      .catch(() => setError(server.instanceId, t('chat.mcpConnect.unavailable')))
      .finally(() => setBusyId(null));
  };

  const tryAgain = () => {
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId) return;
    const request = buildStreamChatRequestForSlot(slotId, question);
    if (!request) return;
    setResent(true);
    void streamMessageForSlot(slotId, question, request);
  };

  const fixable = details.servers.filter((server) => connectableHere(server, details));
  const everyoneElse = details.servers.length - fixable.length;
  const ready = fixable.length > 0 && everyoneElse === 0 && fixable.every((s) => connected.has(s.instanceId));
  const tokenEntryId = tokenEntry?._id;

  return (
    <Box
      data-testid="mcp-connect-required"
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <Flex align="center" gap="2" mb="2">
        <MaterialIcon name="link_off" size={16} color="var(--amber-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.mcpConnect.title')}
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {details.servers.map((server) => {
          const isConnected = connected.has(server.instanceId);
          const error = errors[server.instanceId];
          return (
            <Flex key={server.instanceId} direction="column" gap="1">
              <Flex align="center" justify="between" gap="3">
                <Text size="2" style={{ color: 'var(--slate-12)', minWidth: 0, overflowWrap: 'anywhere' }}>
                  {server.name}
                </Text>
                {isConnected ? (
                  <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                    <MaterialIcon name="check_circle" size={14} color="var(--green-11)" />
                    <Text size="1" style={{ color: 'var(--green-11)' }}>
                      {t('chat.mcpConnect.connected')}
                    </Text>
                  </Flex>
                ) : connectableHere(server, details) ? (
                  <Button
                    size="1"
                    variant="soft"
                    onClick={() => connect(server)}
                    disabled={busyId !== null}
                    style={{ flexShrink: 0 }}
                  >
                    {busyId === server.instanceId ? t('chat.mcpConnect.connecting') : t('chat.mcpConnect.connect')}
                  </Button>
                ) : (
                  <Text size="1" style={{ color: 'var(--slate-10)', textAlign: 'right' }}>
                    {server.problem === 'not_found'
                      ? t('chat.mcpConnect.removed')
                      : server.sharedCredential
                        ? t('chat.mcpConnect.waitingForAdmin')
                        : details.serviceAccount
                          ? t('chat.mcpConnect.setUpInBuilder')
                          : t('chat.mcpConnect.unavailable')}
                  </Text>
                )}
              </Flex>
              {error && (
                <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
                  {error}
                </Text>
              )}
            </Flex>
          );
        })}
      </Flex>

      {details.serviceAccount && (
        <Flex direction="column" gap="1" mt="3">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.mcpConnect.serviceAccount')}
          </Text>
          {details.agentId && (
            <Text size="1">
              <a
                href={`/agents/edit?agentKey=${encodeURIComponent(details.agentId)}`}
                style={{ color: 'var(--accent-11)' }}
              >
                {t('chat.mcpConnect.openAgentBuilder')}
              </a>
            </Text>
          )}
        </Flex>
      )}

      {ready && !resent && (
        <Flex align="center" justify="between" gap="3" mt="3">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.mcpConnect.allConnected')}
          </Text>
          <Button size="1" onClick={tryAgain}>
            {t('chat.mcpConnect.tryAgain')}
          </Button>
        </Flex>
      )}

      <McpAuthDialog
        instance={tokenEntry}
        open={tokenEntry !== null}
        onOpenChange={(open) => {
          if (!open) setTokenEntry(null);
        }}
        onAuthenticated={() => {
          if (tokenEntryId) markConnected(tokenEntryId);
        }}
      />
    </Box>
  );
}
