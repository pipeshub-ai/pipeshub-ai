'use client';

import React, { useCallback, useContext, useRef, useState } from 'react';
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
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { UserToolsetConfigDialog } from '@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { connectableHere, type UnavailableTool } from '../../unavailable-tools';
import { AnswerSlotContext, answerSlotIdNow } from './answer-slot-scope';

async function findMyMcpEntry(instanceId: string): Promise<McpMyServerEntry | null> {
  const { instances } = await McpServersApi.getMyMcpServers(false);
  return instances.find((entry) => entry._id === instanceId) ?? null;
}

const keyOf = (item: UnavailableTool) => `${item.kind}:${item.instanceId}`;

export interface ConnectToolsCardProps {
  items: UnavailableTool[];
  agentId?: string;
  /** The agent runs with its own credentials: only an admin can connect them. */
  serviceAccount: boolean;
  /** The question asked again by "Try again". */
  question: string;
  /** The run stopped on these tools; otherwise the answer went on without them. */
  blocked: boolean;
  testId?: string;
}

/** Lists the tools a run could not use, with a Connect button for each one the person can connect here. */
export function ConnectToolsCard({ items, agentId, serviceAccount, question, blocked, testId }: ConnectToolsCardProps) {
  const { t } = useTranslation();
  const [connected, setConnected] = useState<ReadonlySet<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tokenEntry, setTokenEntry] = useState<McpMyServerEntry | null>(null);
  const [toolsetDialog, setToolsetDialog] = useState<{ toolset: BuilderSidebarToolset; item: UnavailableTool } | null>(
    null,
  );
  const [resent, setResent] = useState(false);
  const mcpTargetRef = useRef<string | null>(null);
  const toolsetTargetRef = useRef<UnavailableTool | null>(null);
  const scopedSlotId = useContext(AnswerSlotContext);

  const markConnected = useCallback(
    (key: string) => setConnected((prev) => new Set(prev).add(key)),
    [],
  );
  const setError = useCallback(
    (key: string, message: string | null) =>
      setErrors((prev) => {
        const next = { ...prev };
        if (message) next[key] = message;
        else delete next[key];
        return next;
      }),
    [],
  );

  const { startOAuthPopup } = useMcpOAuthPopup({
    verifyAuthenticated: async () => {
      const instanceId = mcpTargetRef.current;
      return instanceId ? Boolean((await findMyMcpEntry(instanceId))?.isAuthenticated) : false;
    },
    onVerified: () => {
      setBusyKey(null);
      if (mcpTargetRef.current) markConnected(`mcp:${mcpTargetRef.current}`);
    },
    onFailed: (failure) => {
      setBusyKey(null);
      if (mcpTargetRef.current) setError(`mcp:${mcpTargetRef.current}`, mcpOAuthFailureMessage(t, failure));
    },
  });

  const toolsetFailed = useCallback(
    (message?: string) => {
      setBusyKey(null);
      const target = toolsetTargetRef.current;
      if (target) setError(keyOf(target), message || t('chat.connectTools.toolsetUnavailable'));
    },
    [setError, t],
  );

  const connectMcp = (item: UnavailableTool) => {
    if (item.authMode === 'oauth') {
      mcpTargetRef.current = item.instanceId;
      // Called inside the click: the popup has to open before anything is awaited.
      void startOAuthPopup(item.instanceId);
      return;
    }
    void findMyMcpEntry(item.instanceId)
      .then((entry) => {
        if (entry) setTokenEntry(entry);
        else setError(keyOf(item), t('chat.mcpConnect.unavailable'));
      })
      .catch(() => setError(keyOf(item), t('chat.mcpConnect.unavailable')))
      .finally(() => setBusyKey(null));
  };

  const connectToolset = async (item: UnavailableTool) => {
    toolsetTargetRef.current = item;
    let toolset: BuilderSidebarToolset | undefined;
    try {
      toolset = await ToolsetsApi.findMyToolsetByInstanceId(item.instanceId);
    } catch {
      toolset = undefined;
    }
    if (!toolset) {
      toolsetFailed();
      return;
    }
    // The sign-in dialog runs every auth type, OAuth included, with the same popup handling as the agent builder.
    setBusyKey(null);
    setToolsetDialog({ toolset, item });
  };

  const connect = (item: UnavailableTool) => {
    setError(keyOf(item), null);
    setBusyKey(keyOf(item));
    if (item.kind === 'mcp') connectMcp(item);
    else void connectToolset(item);
  };

  const tryAgain = () => {
    const slotId = answerSlotIdNow(scopedSlotId);
    if (!slotId) return;
    const request = buildStreamChatRequestForSlot(slotId, question);
    if (!request) return;
    setResent(true);
    void streamMessageForSlot(slotId, question, request);
  };

  const fixable = items.filter((item) => connectableHere(item, serviceAccount));
  const allFixableConnected = fixable.length > 0 && fixable.every((item) => connected.has(keyOf(item)));
  // A stopped run would stop again on anything still missing; an answer that went on is worth asking again.
  const ready = allFixableConnected && (!blocked || fixable.length === items.length);
  const allMcp = items.every((item) => item.kind === 'mcp');
  const title = serviceAccount
    ? t('chat.connectTools.serviceAccountTitle')
    : blocked
      ? allMcp
        ? t('chat.mcpConnect.title')
        : t('chat.connectTools.blockedTitle')
      : t('chat.connectTools.noticeTitle');
  const tokenEntryId = tokenEntry?._id;

  const statusText = (item: UnavailableTool): string => {
    if (item.problem === 'not_found') return t('chat.mcpConnect.removed');
    if (item.sharedCredential && !serviceAccount) return t('chat.mcpConnect.waitingForAdmin');
    return t('chat.connectTools.askAdmin');
  };

  return (
    <Box
      data-testid={testId ?? 'connect-tools'}
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
        backgroundColor: 'var(--slate-2)',
        marginTop: blocked ? undefined : 'var(--space-3)',
      }}
    >
      <Flex align="center" gap="2" mb="2">
        <MaterialIcon name="link_off" size={16} color="var(--amber-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {title}
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {items.map((item) => {
          const key = keyOf(item);
          const error = errors[key];
          return (
            <Flex key={key} direction="column" gap="1">
              <Flex align="center" justify="between" gap="3">
                <Text size="2" style={{ color: 'var(--slate-12)', minWidth: 0, overflowWrap: 'anywhere' }}>
                  {item.displayName}
                </Text>
                {connected.has(key) ? (
                  <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                    <MaterialIcon name="check_circle" size={14} color="var(--green-11)" />
                    <Text size="1" style={{ color: 'var(--green-11)' }}>
                      {t('chat.mcpConnect.connected')}
                    </Text>
                  </Flex>
                ) : connectableHere(item, serviceAccount) ? (
                  <Button
                    size="1"
                    variant="soft"
                    onClick={() => connect(item)}
                    disabled={busyKey !== null}
                    style={{ flexShrink: 0, minHeight: 32 }}
                  >
                    {busyKey === key ? t('chat.mcpConnect.connecting') : t('chat.mcpConnect.connect')}
                  </Button>
                ) : (
                  <Text size="1" style={{ color: 'var(--slate-10)', textAlign: 'right' }}>
                    {statusText(item)}
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

      {serviceAccount && (
        <Flex direction="column" gap="1" mt="3">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('chat.connectTools.serviceAccount', { tools: items.map((item) => item.displayName).join(', ') })}
          </Text>
          {agentId && (
            <Text size="1">
              <a href={`/agents/edit?agentKey=${encodeURIComponent(agentId)}`} style={{ color: 'var(--accent-11)' }}>
                {t('chat.mcpConnect.openAgentBuilder')}
              </a>
            </Text>
          )}
        </Flex>
      )}

      {ready && !resent && (
        <Flex align="center" justify="between" gap="3" mt="3">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {blocked ? t('chat.mcpConnect.allConnected') : t('chat.connectTools.connectedAskAgain')}
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
          if (tokenEntryId) markConnected(`mcp:${tokenEntryId}`);
        }}
      />

      {toolsetDialog && (
        <UserToolsetConfigDialog
          toolset={toolsetDialog.toolset}
          instanceId={toolsetDialog.item.instanceId}
          onClose={() => setToolsetDialog(null)}
          onSuccess={() => markConnected(keyOf(toolsetDialog.item))}
        />
      )}
    </Box>
  );
}
