'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../api';
import { mcpConnectionState, usesSharedCredential, type McpConnectionState } from '../connection-state';
import type { McpToolsLoad } from '../team/components/mcp-tools-approvals-tab';
import type { McpMyServerEntry } from '../types';

/** One server's tool list (the cache on open, live on refresh) and its status, corrected by what
 * the list just said. */
export function useMcpServerTools(instance: McpMyServerEntry | null, { isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation();
  const [toolsLoad, setToolsLoad] = useState<McpToolsLoad>({ status: 'idle' });
  // Bumped whenever the list is asked for again or the server changes: an older answer is dropped.
  const toolsRequest = useRef(0);
  const liveId = instance?._id;

  useEffect(() => {
    toolsRequest.current += 1;
    setToolsLoad({ status: 'idle' });
  }, [liveId]);

  const loadTools = useCallback(
    async (cached: boolean) => {
      if (!liveId) return;
      const request = ++toolsRequest.current;
      setToolsLoad({ status: 'loading' });
      try {
        const res = await McpServersApi.getInstanceTools(liveId, { cached });
        if (request !== toolsRequest.current) return;
        setToolsLoad({ status: 'ready', tools: res.tools, syncedAt: res.syncedAt ?? Date.now() });
      } catch (error) {
        if (request !== toolsRequest.current) return;
        const status = isProcessedError(error) ? error.statusCode : undefined;
        setToolsLoad({
          status: 'error',
          code: status === 409 ? 'reauth' : status === 502 ? 'unreachable' : 'error',
          message: isProcessedError(error) ? error.message : t('workspace.mcpServers.toasts.discoveryError'),
        });
      }
    },
    [liveId, t]
  );

  const connectionState: McpConnectionState = useMemo(() => {
    if (!instance) return 'ready';
    if (toolsLoad.status === 'error' && toolsLoad.code === 'reauth' && instance.authMode === 'none') {
      // The 409 says the server asks for a sign-in, which only its auth mode can give it. It is
      // newer than the listing, so it wins over a listing that timed out or couldn't connect.
      return mcpConnectionState({ ...instance, toolsErrorCode: 'unauthorized' }, { isAdmin });
    }
    const base = mcpConnectionState(instance, { isAdmin });
    if (base !== 'ready' || toolsLoad.status !== 'error') return base;
    if (toolsLoad.code === 'reauth') return usesSharedCredential(instance) ? 'shared_credential_missing' : 'needs_reconnect';
    return toolsLoad.code === 'unreachable' ? 'unreachable' : base;
  }, [instance, isAdmin, toolsLoad]);

  const canListTools =
    connectionState !== 'needs_connect' && connectionState !== 'waiting_for_admin' && connectionState !== 'shared_credential_missing';

  useEffect(() => {
    if (canListTools && toolsLoad.status === 'idle') void loadTools(true);
  }, [canListTools, toolsLoad.status, loadTools]);

  // A new sign-in makes a failed list worth reading again.
  const connectedAt = instance?.connectedAt;
  const isAuthenticated = instance?.isAuthenticated;
  useEffect(() => {
    setToolsLoad((prev) => (prev.status === 'error' ? { status: 'idle' } : prev));
  }, [connectedAt, isAuthenticated]);

  return { toolsLoad, loadTools, connectionState };
}
