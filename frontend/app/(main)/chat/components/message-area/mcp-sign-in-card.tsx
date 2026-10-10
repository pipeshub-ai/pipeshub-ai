'use client';

import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import {
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
} from '@/app/(main)/workspace/mcp-servers/hooks/use-mcp-oauth-popup';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import type { McpSignInServer } from '../../types';

/** When the server's current sign-in was made (0 for a record from before that was kept), or null without one. */
async function signedInAt(server: McpSignInServer): Promise<number | null> {
  const { instances } = server.agentKey
    ? await McpServersApi.getAgentMcpServers(server.agentKey, false)
    : await McpServersApi.getMyMcpServers(false);
  const entry = instances.find((candidate) => candidate._id === server.instanceId);
  if (!entry?.isAuthenticated) return null;
  return typeof entry.connectedAt === 'number' ? entry.connectedAt : 0;
}

interface McpSignInCardProps {
  servers: McpSignInServer[];
}

/**
 * Under a reply whose MCP servers refused a request for missing permission: signs in to each
 * again, which asks for what was missing, then "Try again" sends a follow-up so the model
 * retries what failed (not the whole question, so what already ran doesn't run twice).
 */
export function McpSignInCard({ servers }: McpSignInCardProps) {
  const { t } = useTranslation();
  const access = useChatStore((s) => s.agentContextAccess);
  // Someone viewing a conversation shared with them can't send in it.
  const canSend = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null)) !== false;
  const [signedIn, setSignedIn] = useState<ReadonlySet<string>>(new Set());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [resent, setResent] = useState(false);
  // The server being signed in to, and when its sign-in before this one was made.
  const targetRef = useRef<{ server: McpSignInServer; before: number | null } | null>(null);

  // An agent's own sign-in is redone by someone who can edit the agent (its route checks too).
  const canSignIn = (server: McpSignInServer) =>
    !server.agentKey || (access?.agentKey === server.agentKey && access.canEdit);

  const setError = (instanceId: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[instanceId] = message;
      else delete next[instanceId];
      return next;
    });

  const { startOAuthPopup } = useMcpOAuthPopup({
    getAuthorizationUrl: async (instanceId) => {
      const target = targetRef.current;
      if (!target) throw new Error('no sign-in target');
      // Read here, after the popup opened and before the provider's page loads: the person was
      // already signed in, so only a sign-in newer than this one means the popup succeeded.
      target.before = (await signedInAt(target.server)) ?? 0;
      return target.server.agentKey
        ? McpServersApi.getAgentOAuthAuthorizationUrl(target.server.agentKey, instanceId)
        : McpServersApi.getOAuthAuthorizationUrl(instanceId, window.location.origin);
    },
    verifyAuthenticated: async () => {
      const target = targetRef.current;
      if (!target || target.before === null) return false;
      const now = await signedInAt(target.server);
      return now !== null && now > target.before;
    },
    onVerified: () => {
      setBusyId(null);
      const id = targetRef.current?.server.instanceId;
      if (id) setSignedIn((prev) => new Set(prev).add(id));
    },
    onFailed: (failure) => {
      setBusyId(null);
      const id = targetRef.current?.server.instanceId;
      if (id) setError(id, mcpOAuthFailureMessage(t, failure));
    },
  });

  const signIn = (server: McpSignInServer) => {
    setError(server.instanceId, null);
    setBusyId(server.instanceId);
    targetRef.current = { server, before: null };
    // Called inside the click: the popup has to open before anything is awaited.
    void startOAuthPopup(server.instanceId);
  };

  const tryAgain = () => {
    const slotId = useChatStore.getState().activeSlotId;
    if (!slotId) return;
    const text = t('chat.mcpSignIn.retryMessage', { servers: servers.map((s) => s.serverName).join(', ') });
    // MCP servers are only in agent mode.
    const request = buildStreamChatRequestForSlot(slotId, text, undefined, undefined, { agentMode: true });
    if (!request) return;
    setResent(true);
    void streamMessageForSlot(slotId, text, request);
  };

  const signable = servers.filter(canSignIn);
  const ready = signable.length > 0 && signable.every((s) => signedIn.has(s.instanceId));
  const agentKey = servers.find((s) => s.agentKey && !canSignIn(s))?.agentKey;

  return (
    <Box
      data-testid="mcp-sign-in-card"
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <Flex align="center" gap="2" mb="2">
        <MaterialIcon name="lock_open" size={16} color="var(--amber-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.mcpSignIn.title')}
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {servers.map((server) => {
          const done = signedIn.has(server.instanceId);
          const error = errors[server.instanceId];
          return (
            <Flex key={server.instanceId} direction="column" gap="1">
              <Flex align="center" justify="between" gap="3">
                <Flex direction="column" style={{ minWidth: 0 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
                    {server.serverName}
                  </Text>
                  {server.scopes.length > 0 && (
                    <Text size="1" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
                      {t('chat.mcpSignIn.needs', { scopes: server.scopes.join(', ') })}
                    </Text>
                  )}
                </Flex>
                {done ? (
                  <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                    <MaterialIcon name="check_circle" size={14} color="var(--green-11)" />
                    <Text size="1" style={{ color: 'var(--green-11)' }}>
                      {t('chat.mcpSignIn.signedIn')}
                    </Text>
                  </Flex>
                ) : canSignIn(server) ? (
                  canSend && (
                    <Button
                      size="1"
                      variant="soft"
                      onClick={() => signIn(server)}
                      disabled={busyId !== null}
                      style={{ flexShrink: 0 }}
                    >
                      {busyId === server.instanceId ? t('chat.mcpSignIn.signingIn') : t('chat.mcpSignIn.signIn')}
                    </Button>
                  )
                ) : (
                  <Text size="1" style={{ color: 'var(--slate-10)', textAlign: 'right' }}>
                    {t('chat.mcpSignIn.askAnEditor')}
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

      {agentKey && (
        <Text as="p" size="1" mt="2">
          <a href={`/agents/edit?agentKey=${encodeURIComponent(agentKey)}`} style={{ color: 'var(--accent-11)' }}>
            {t('chat.mcpConnect.openAgentBuilder')}
          </a>
        </Text>
      )}

      {ready && canSend && !resent && (
        <Flex align="center" justify="between" gap="3" mt="3">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.mcpSignIn.allSignedIn')}
          </Text>
          <Button size="1" onClick={tryAgain}>
            {t('chat.mcpConnect.tryAgain')}
          </Button>
        </Flex>
      )}
    </Box>
  );
}
