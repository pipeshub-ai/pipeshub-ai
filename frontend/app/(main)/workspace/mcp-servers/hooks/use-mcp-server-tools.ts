'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../api';
import { mcpConnectionState, type McpConnectionState } from '../connection-state';
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
  const connectedAt = instance?.connectedAt;
  const isAuthenticated = instance?.isAuthenticated;

  // Another server, or a new sign-in on this one (maybe as someone else): the list is read again.
  useEffect(() => {
    toolsRequest.current += 1;
    setToolsLoad({ status: 'idle' });
  }, [liveId, connectedAt, isAuthenticated]);

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
    const base = mcpConnectionState(instance, { isAdmin });
    if (toolsLoad.status !== 'error' || base === 'blocked') return base;
    // The 409 is newer than the listing, so it wins over a listing that timed out or couldn't
    // connect. Read as the listing's own "unauthorized", it also knows who can fix it: a member
    // waits for an admin to replace a shared credential.
    if (toolsLoad.code === 'reauth') return mcpConnectionState({ ...instance, toolsErrorCode: 'unauthorized' }, { isAdmin });
    return toolsLoad.code === 'unreachable' && base === 'ready' ? 'unreachable' : base;
  }, [instance, isAdmin, toolsLoad]);

  const canListTools =
    connectionState !== 'needs_connect' && connectionState !== 'waiting_for_admin' && connectionState !== 'shared_credential_missing';

  useEffect(() => {
    if (canListTools && toolsLoad.status === 'idle') void loadTools(true);
  }, [canListTools, toolsLoad.status, loadTools]);

  return { toolsLoad, loadTools, connectionState };
}
