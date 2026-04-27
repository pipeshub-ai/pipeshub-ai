'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import {
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import { Flex, Text, Button, Dialog, Callout } from '@radix-ui/themes';
import { AgentsApi } from '../api';
import { extractAgentConfigFromFlow } from './extract-agent-config';
import { useAgentBuilderData } from './hooks/use-agent-builder-data';
import { useAgentBuilderState } from './hooks/use-agent-builder-state';
import { useAgentBuilderNodeTemplates } from './hooks/use-node-templates';
import { useAgentBuilderReconstruction } from './hooks/use-flow-reconstruction';
import { AgentBuilderHeader } from './components/agent-builder-header';
import { AgentBuilderSidebar } from './components/agent-builder-sidebar';
import { AgentBuilderCanvas } from './components/agent-builder-canvas';
import { DeleteAgentDialog } from '@/app/(main)/chat/sidebar/dialogs';
import { ServiceAccountConfirmDialog } from './components/service-account-confirm-dialog';
import { AgentToolsetCredentialsDialog } from './components/agent-toolset-credentials-dialog';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { FlowNodeData } from './types';
import { normalizeDisplayName, formattedProvider } from './display-utils';
import { FLOW_EDGE } from './flow-theme';
import { connectionError } from './connection-rules';
import { buildChatHref } from '@/chat/build-chat-url';
import { invalidateModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getAgentBuilderPermissions } from './agent-builder-permissions';
import { toast } from '@/lib/store/toast-store';
import { normalizeToolsetTypeKey } from './sidebar-toolset-utils';

/** Palette width: comfortable for labels; chrome matches `SecondaryPanel` / chat sidebars. */
const AGENT_BUILDER_SIDEBAR_WIDTH = 332;

const SVC_ACCT_TOOLSET_BLOCK_TOAST_MS = 9000;

/** Extract a human-readable message from an unknown API error. */
function extractErrorMessage(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

export function AgentBuilder({ agentKey }: { agentKey: string | null }) {
  const router = useRouter();
  const { t } = useTranslation();
  const editingKey = agentKey;

  const {
    availableTools,
    availableModels,
    availableKnowledgeBases,
    activeAgentConnectors,
    configuredConnectors,
    toolsets,
    loading,
    loadedAgent,
    error,
    setError,
    refreshToolsets,
    refreshAgent,
  } = useAgentBuilderData(editingKey);

  const {
    deleteDialogOpen,
    setDeleteDialogOpen,
    nodeToDelete,
    setNodeToDelete,
    edgeDeleteDialogOpen,
    setEdgeDeleteDialogOpen,
    edgeToDelete,
    setEdgeToDelete,
    sidebarOpen,
    setSidebarOpen,
    agentName,
    setAgentName,
    saving,
    setSaving,
    deleting,
    setDeleting,
    success,
    setSuccess,
  } = useAgentBuilderState(loadedAgent?.name || '');

  const [shareWithOrg, setShareWithOrg] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  const [serviceAccountConfirmOpen, setServiceAccountConfirmOpen] = useState(false);
  const [serviceAccountCreating, setServiceAccountCreating] = useState(false);
  const [serviceAccountError, setServiceAccountError] = useState<string | null>(null);
  const [agentToolsetDialog, setAgentToolsetDialog] = useState<{
    toolset: BuilderSidebarToolset;
    instanceId: string;
  } | null>(null);
  const [agentDeleteDialogOpen, setAgentDeleteDialogOpen] = useState(false);
  const [isDeletingAgent, setIsDeletingAgent] = useState(false);
  const [agentNameError, setAgentNameError] = useState<string | null>(null);
  const agentNameInputRef = useRef<HTMLInputElement>(null);

  const effectiveAgentKey = loadedAgent?._key ?? editingKey ?? null;

  const { nodeTemplates } = useAgentBuilderNodeTemplates(
    availableTools,
    availableModels,
    availableKnowledgeBases,
    configuredConnectors
  );
  const { reconstructFlowFromAgent } = useAgentBuilderReconstruction();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<FlowNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const { canPersist, isAgentStructureLocked, isServiceAccountToolsetOrgLocked, isServiceAccount } =
    useMemo(() => getAgentBuilderPermissions(loadedAgent), [loadedAgent]);

  const paletteDragBlockedMessage = useMemo(() => {
    if (!isAgentStructureLocked) return '';
    return isServiceAccountToolsetOrgLocked
      ? t('agentBuilder.paletteActionBlockedViewOnly')
      : t('agentBuilder.viewerPaletteDragBlocked');
  }, [isAgentStructureLocked, isServiceAccountToolsetOrgLocked, t]);

  useEffect(() => {
    if (loadedAgent) {
      setShareWithOrg(isServiceAccount ? true : Boolean(loadedAgent.shareWithOrg));
    }
  }, [loadedAgent, isServiceAccount]);

  useEffect(() => {
    if (loadedAgent?.name) setAgentName(loadedAgent.name);
  }, [loadedAgent?.name, setAgentName]);

  useEffect(() => {
    if (agentName.trim()) setAgentNameError(null);
  }, [agentName]);

  // Auto-dismiss connection error banner after 3.5 s
  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 3500);
    return () => clearTimeout(t);
  }, [banner]);

  // Auto-dismiss success toast after 4 s
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(null), 4000);
    return () => clearTimeout(t);
  }, [success, setSuccess]);

  const prevAgentKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevAgentKeyRef.current;
    if (editingKey && prev && prev !== editingKey && !loading) {
      setNodes([]);
      setEdges([]);
    }
    prevAgentKeyRef.current = editingKey;
  }, [editingKey, loading, setNodes, setEdges]);

  const initOnce = useRef(false);
  useEffect(() => {
    initOnce.current = false;
  }, [editingKey]);

  useEffect(() => {
    if (loading || availableModels.length === 0 || nodes.length > 0 || initOnce.current) return;

    const agentSrc = loadedAgent || undefined;
    if (agentSrc) {
      const { nodes: n, edges: e } = reconstructFlowFromAgent(
        { ...agentSrc, knowledge: (agentSrc.knowledge as unknown[]) || [] },
        availableModels,
        availableTools,
        availableKnowledgeBases
      );
      setNodes(n);
      setEdges(e);
      initOnce.current = true;
      return;
    }

    const initialModel =
      availableModels.find((m) => m.isReasoning) || availableModels[0];
    if (!initialModel) return;

    const systemPrompt = t('agentBuilder.defaultSystemPrompt');
    const startMessage = t('agentBuilder.defaultStartMessage');

    const initialNodes: Node<FlowNodeData>[] = [
      {
        id: 'chat-input-1',
        type: 'flowNode',
        position: { x: 50, y: 520 },
        data: {
          id: 'chat-input-1',
          type: 'user-input',
          label: t('agentBuilder.nodeLabelChatInput'),
          description: t('agentBuilder.nodeDescUserMessages'),
          icon: 'chat',
          config: { placeholder: t('agentBuilder.chatInputPlaceholder') },
          inputs: [],
          outputs: ['message'],
          isConfigured: true,
        },
      },
      {
        id: 'llm-1',
        type: 'flowNode',
        position: { x: 50, y: 220 },
        data: {
          id: 'llm-1',
          type: `llm-${(initialModel.modelKey || 'default').replace(/[^a-zA-Z0-9]/g, '-').toLowerCase()}`,
          label: initialModel.modelFriendlyName?.trim() || initialModel.modelName || 'Model',
          description: `${formattedProvider(initialModel.provider || 'AI')} model`,
          icon: 'psychology',
          config: {
            modelKey: initialModel.modelKey,
            modelName: initialModel.modelName,
            provider: initialModel.provider || 'azureOpenAI',
            modelType: initialModel.modelType || 'llm',
            isMultimodal: initialModel.isMultimodal,
            isDefault: initialModel.isDefault,
            isReasoning: initialModel.isReasoning,
            modelFriendlyName: initialModel.modelFriendlyName,
          },
          inputs: [],
          outputs: ['response'],
          isConfigured: true,
        },
      },
      {
        id: 'agent-core-1',
        type: 'flowNode',
        position: { x: 420, y: 120 },
        data: {
          id: 'agent-core-1',
          type: 'agent-core',
          label: normalizeDisplayName(t('agentBuilder.coreNodeTitle')),
          description: t('agentBuilder.coreNodeSubtitle'),
          icon: 'auto_awesome',
          config: {
            systemPrompt,
            instructions: '',
            startMessage,
            routing: 'auto',
            allowMultipleLLMs: true,
          },
          inputs: ['input', 'toolsets', 'knowledge', 'llms'],
          outputs: ['response'],
          isConfigured: true,
        },
      },
      {
        id: 'chat-response-1',
        type: 'flowNode',
        position: { x: 820, y: 320 },
        data: {
          id: 'chat-response-1',
          type: 'chat-response',
          label: t('agentBuilder.nodeLabelChatOutput'),
          description: t('agentBuilder.nodeDescChatReply'),
          icon: 'reply',
          config: { format: 'text' },
          inputs: ['response'],
          outputs: [],
          isConfigured: true,
        },
      },
    ];

    const initialEdges: Edge[] = [
      {
        id: 'e-input-agent',
        source: 'chat-input-1',
        target: 'agent-core-1',
        sourceHandle: 'message',
        targetHandle: 'input',
        type: 'smoothstep',
        style: { stroke: FLOW_EDGE.line, strokeWidth: 1.5 },
      },
      {
        id: 'e-llm-agent',
        source: 'llm-1',
        target: 'agent-core-1',
        sourceHandle: 'response',
        targetHandle: 'llms',
        type: 'smoothstep',
        style: { stroke: FLOW_EDGE.line, strokeWidth: 1.5 },
      },
      {
        id: 'e-agent-output',
        source: 'agent-core-1',
        target: 'chat-response-1',
        sourceHandle: 'response',
        targetHandle: 'response',
        type: 'smoothstep',
        style: { stroke: FLOW_EDGE.line, strokeWidth: 1.5 },
      },
    ];

    setNodes(initialNodes);
    setEdges(initialEdges);
    initOnce.current = true;
  }, [
    loading,
    availableModels,
    availableKnowledgeBases,
    availableTools,
    loadedAgent,
    nodes.length,
    reconstructFlowFromAgent,
    setEdges,
    setNodes,
    t,
  ]);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (isAgentStructureLocked) return;
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);
      const msgKey = connectionError(sourceNode, targetNode, connection);
      if (msgKey) {
        setBanner(t(msgKey));
        return;
      }

      const sourceType = sourceNode?.data?.type;
      const targetType = targetNode?.data?.type;

      // Linear chain guardrails: each agent has max one response successor.
      if (sourceType === 'agent-core' && connection.sourceHandle === 'response') {
        const existingOutgoing = edges.some(
          (e) =>
            e.source === connection.source &&
            e.sourceHandle === 'response' &&
            !(e.target === connection.target && e.targetHandle === connection.targetHandle)
        );
        if (existingOutgoing) {
          setBanner(t('agentBuilder.connectionInvalid'));
          return;
        }
      }

      // Linear chain guardrails: each downstream agent input accepts only one predecessor agent.
      if (targetType === 'agent-core' && connection.targetHandle === 'input') {
        const existingIncomingFromAgent = edges.some((e) => {
          if (e.target !== connection.target || e.targetHandle !== 'input') return false;
          const existingSourceType = nodes.find((n) => n.id === e.source)?.data?.type;
          return existingSourceType === 'agent-core';
        });
        if (existingIncomingFromAgent && sourceType === 'agent-core') {
          setBanner(t('agentBuilder.connectionInvalid'));
          return;
        }
      }

      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            id: `e-${connection.source}-${connection.target}-${Date.now()}`,
            type: 'smoothstep',
            style: { stroke: FLOW_EDGE.line, strokeWidth: 1.5 },
          },
          eds
        )
      );
    },
    [isAgentStructureLocked, nodes, setEdges, t]
  );

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      if (isAgentStructureLocked) return;
      setEdgeToDelete(edge.id);
      setEdgeDeleteDialogOpen(true);
    },
    [isAgentStructureLocked, setEdgeDeleteDialogOpen, setEdgeToDelete]
  );

  const hasToolsets = useMemo(
    () => nodes.some((n) => String(n.data?.type ?? '').startsWith('toolset-')),
    [nodes]
  );

  const saWelcomeShownRef = useRef(false);
  useEffect(() => {
    if (saWelcomeShownRef.current || typeof window === 'undefined' || !editingKey) return;
    const q = new URLSearchParams(window.location.search);
    if (q.get('sa') !== '1') return;
    saWelcomeShownRef.current = true;
    setSuccess(t('agentBuilder.serviceAccountCreated'));
    q.delete('sa');
    const path = window.location.pathname;
    const rest = q.toString();
    void router.replace(rest ? `${path}?${rest}` : path);
  }, [editingKey, router, setSuccess]);

  /** Configured toolset instances already on the canvas (same type, different instance = allowed). */
  const activeToolsetInstanceIds = useMemo(
    () =>
      nodes
        .filter((n) => String(n.data?.type ?? '').startsWith('toolset-'))
        .map((n) => String((n.data?.config as Record<string, unknown> | undefined)?.instanceId ?? '').trim())
        .filter(Boolean),
    [nodes]
  );

  /** Legacy / non-instance nodes: block a second palette row that has no instanceId when this type is already placed. */
  const activeToolsetTypeKeysWithoutInstance = useMemo(
    () =>
      nodes
        .filter((n) => {
          if (!String(n.data?.type ?? '').startsWith('toolset-')) return false;
          const id = String((n.data?.config as Record<string, unknown> | undefined)?.instanceId ?? '').trim();
          return !id;
        })
        .map((n) => {
          const cfg = (n.data?.config || {}) as Record<string, unknown>;
          const name =
            (cfg.toolsetName as string) || String(n.data?.type || '').replace(/^toolset-/, '');
          return normalizeToolsetTypeKey(String(name));
        })
        .filter(Boolean),
    [nodes]
  );

  const saveRef = useRef(false);

  const focusAgentNameInput = useCallback(() => {
    queueMicrotask(() => {
      agentNameInputRef.current?.focus();
    });
  }, []);

  /** Inline error + focus — shared by save, service-account entry, and confirm edge cases. */
  const showInlineAgentNameRequired = useCallback(() => {
    setAgentNameError(t('agentBuilder.nameRequired'));
    focusAgentNameInput();
  }, [focusAgentNameInput, t]);

  const notifyServiceAccountToolsetBlocked = useCallback(() => {
    const converting = Boolean(loadedAgent);
    toast.error(t('agentBuilder.svcAcctRemoveToolsetsTitle'), {
      description: t('agentBuilder.svcAcctRemoveToolsetsDesc', {
        action: converting ? t('agentBuilder.svcAcctConvertAction') : t('agentBuilder.svcAcctCreateAction'),
      }),
      duration: SVC_ACCT_TOOLSET_BLOCK_TOAST_MS,
    });
  }, [loadedAgent, t]);

  const handleRequestServiceAccount = useCallback(() => {
    if (!agentName.trim()) {
      toast.error(t('agentBuilder.nameRequired'), {
        description: t('agentBuilder.svcAcctNameRequired'),
      });
      showInlineAgentNameRequired();
      return;
    }
    if (hasToolsets) {
      notifyServiceAccountToolsetBlocked();
      return;
    }
    setServiceAccountConfirmOpen(true);
  }, [agentName, hasToolsets, notifyServiceAccountToolsetBlocked, showInlineAgentNameRequired, t]);

  const handleSave = useCallback(async () => {
    if (!canPersist) return;
    if (saveRef.current) return;
    if (!agentName.trim()) {
      showInlineAgentNameRequired();
      setBanner(null);
      return;
    }
    saveRef.current = true;
    setSaving(true);
    setError(null);
    setBanner(null);
    try {
      const payload = {
        ...extractAgentConfigFromFlow(
          agentName.trim(),
          nodes as Parameters<typeof extractAgentConfigFromFlow>[1],
          edges as Parameters<typeof extractAgentConfigFromFlow>[2],
          loadedAgent,
          shareWithOrg,
          isServiceAccount
        ),
        // Persist the visual layout so positions survive subsequent edits.
        flow: { nodes, edges },
        flowSchemaVersion: 2,
        orchestrationMode: (nodes.some((node) => node.data?.type === 'conditional-check')
          ? 'conditional'
          : 'linear') as 'conditional' | 'linear',
      };

      if (loadedAgent) {
        const updated = await AgentsApi.updateAgent(loadedAgent._key, payload);
        // Agent's model list may have changed — drop the chat-side cache so
        // the next chat view for this agent refetches fresh models.
        invalidateModelsForContext(loadedAgent._key);
        await refreshAgent(loadedAgent._key, { knownAgent: updated });
        setSuccess(t('agentBuilder.agentUpdated'));
      } else {
        const created = await AgentsApi.createAgent(payload);
        invalidateModelsForContext(created._key);
        setSuccess(t('agentBuilder.agentCreated'));
        router.replace(`/agents/edit?agentKey=${encodeURIComponent(created._key)}`);
      }
    } catch (e: unknown) {
      setError(extractErrorMessage(e, t('agentBuilder.saveFailed')));
    } finally {
      setSaving(false);
      saveRef.current = false;
    }
  }, [
    agentName,
    edges,
    canPersist,
    showInlineAgentNameRequired,
    isServiceAccount,
    loadedAgent,
    nodes,
    refreshAgent,
    router,
    setError,
    setSaving,
    setSuccess,
    shareWithOrg,
    t,
  ]);

  const handleConfirmServiceAccount = useCallback(async () => {
    if (loadedAgent && !canPersist) return;
    if (!agentName.trim()) {
      setServiceAccountConfirmOpen(false);
      setServiceAccountError(null);
      toast.error(t('agentBuilder.nameRequired'), {
        description: t('agentBuilder.svcAcctNameRequired'),
      });
      showInlineAgentNameRequired();
      return;
    }
    if (hasToolsets) {
      setServiceAccountConfirmOpen(false);
      setServiceAccountError(null);
      notifyServiceAccountToolsetBlocked();
      return;
    }
    setServiceAccountCreating(true);
    setServiceAccountError(null);
    try {
      const currentAgent = loadedAgent;
      const agentConfig = {
        ...extractAgentConfigFromFlow(
          agentName.trim(),
          nodes as Parameters<typeof extractAgentConfigFromFlow>[1],
          edges as Parameters<typeof extractAgentConfigFromFlow>[2],
          currentAgent ?? null,
          true,
          true
        ),
        flow: { nodes, edges },
        flowSchemaVersion: 2,
        orchestrationMode: (nodes.some((node) => node.data?.type === 'conditional-check')
          ? 'conditional'
          : 'linear') as 'conditional' | 'linear',
      };

      if (currentAgent) {
        const updated = await AgentsApi.updateAgent(currentAgent._key, agentConfig);
        invalidateModelsForContext(currentAgent._key);
        setServiceAccountConfirmOpen(false);
        await refreshAgent(currentAgent._key, { knownAgent: updated });
        setSuccess(t('agentBuilder.serviceAccountConverted'));
      } else {
        const created = await AgentsApi.createAgent(agentConfig);
        invalidateModelsForContext(created._key);
        setServiceAccountConfirmOpen(false);
        router.replace(`/agents/edit?agentKey=${encodeURIComponent(created._key)}&sa=1`);
      }
    } catch (e: unknown) {
      setServiceAccountError(extractErrorMessage(e, t('agentBuilder.svcAcctEnableFailed')));
    } finally {
      setServiceAccountCreating(false);
    }
  }, [
    agentName,
    canPersist,
    edges,
    hasToolsets,
    loadedAgent,
    nodes,
    notifyServiceAccountToolsetBlocked,
    refreshAgent,
    router,
    setSuccess,
    showInlineAgentNameRequired,
    t,
  ]);

  const confirmDelete = useCallback(async () => {
    if (!nodeToDelete || isAgentStructureLocked) return;
    setDeleting(true);
    setNodes((nds) => nds.filter((n) => n.id !== nodeToDelete));
    setEdges((eds) => eds.filter((e) => e.source !== nodeToDelete && e.target !== nodeToDelete));
    setDeleteDialogOpen(false);
    setNodeToDelete(null);
    setDeleting(false);
  }, [
    isAgentStructureLocked,
    nodeToDelete,
    setDeleteDialogOpen,
    setEdges,
    setNodes,
    setDeleting,
  ]);

  const confirmDeleteAgent = useCallback(async () => {
    if (!loadedAgent?._key) return;
    setIsDeletingAgent(true);
    setError(null);
    try {
      await AgentsApi.deleteAgent(loadedAgent._key);
      setAgentDeleteDialogOpen(false);
      router.replace('/chat/');
    } catch (e: unknown) {
      setError(extractErrorMessage(e, t('agentBuilder.deleteAgentFailed')));
    } finally {
      setIsDeletingAgent(false);
    }
  }, [loadedAgent, router, setError, t]);

  // Look up the label of the node pending deletion for the confirmation dialog.
  const nodeToDeleteLabel = useMemo(() => {
    if (!nodeToDelete) return null;
    const node = nodes.find((n) => n.id === nodeToDelete);
    return (node?.data?.label as string) || null;
  }, [nodeToDelete, nodes]);

  return (
    <ReactFlowProvider>
      <Flex direction="column" style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
        <AgentBuilderHeader
          agentName={agentName}
          onAgentNameChange={setAgentName}
          agentNameError={agentNameError}
          agentNameInputRef={agentNameInputRef}
          saving={saving}
          onSave={handleSave}
          shareWithOrg={shareWithOrg}
          onShareWithOrgChange={setShareWithOrg}
          isFlowStructureLocked={isAgentStructureLocked}
          canPersist={canPersist}
          isServiceAccount={isServiceAccount}
          editing={Boolean(loadedAgent)}
          onEnableServiceAccount={canPersist ? handleRequestServiceAccount : undefined}
          canDeleteAgent={Boolean(loadedAgent?.can_delete)}
          onRequestDeleteAgent={() => setAgentDeleteDialogOpen(true)}
        />

        {((loadedAgent && !canPersist) || error || banner || success) && (
          <Flex
            direction="column"
            gap="2"
            px="4"
            py="3"
            style={{
              flexShrink: 0,
              borderBottom: '1px solid var(--olive-3)',
              background: 'var(--olive-1)',
            }}
          >
            {loadedAgent && !canPersist ? (
              <Callout.Root color="blue" variant="surface" size="1">
                <Callout.Text style={{ flex: 1, minWidth: 0 }}>
                  {isServiceAccount
                    ? t('chat.viewAgentTooltipServiceAccount')
                    : t('chat.viewAgentTooltipIndividual')}
                </Callout.Text>
              </Callout.Root>
            ) : null}
            {error ? (
              <Callout.Root color="red" variant="surface" size="1">
                <Flex align="start" justify="between" gap="3" wrap="wrap">
                  <Callout.Text style={{ flex: 1, minWidth: 0 }}>{error}</Callout.Text>
                  <Button variant="soft" color="gray" size="1" onClick={() => setError(null)}>
                    {t('agentBuilder.dismiss')}
                  </Button>
                </Flex>
              </Callout.Root>
            ) : null}
            {banner ? (
              <Callout.Root color="amber" variant="surface" size="1">
                <Flex align="start" justify="between" gap="3" wrap="wrap">
                  <Callout.Text style={{ flex: 1, minWidth: 0 }}>{banner}</Callout.Text>
                  <Button variant="soft" color="gray" size="1" onClick={() => setBanner(null)}>
                    {t('common.ok')}
                  </Button>
                </Flex>
              </Callout.Root>
            ) : null}
            {success ? (
              <Callout.Root color="green" variant="surface" size="1">
                <Flex align="start" justify="between" gap="3" wrap="wrap">
                  <Callout.Text style={{ flex: 1, minWidth: 0 }}>{success}</Callout.Text>
                  <Button variant="soft" color="gray" size="1" onClick={() => setSuccess(null)}>
                    {t('agentBuilder.dismiss')}
                  </Button>
                </Flex>
              </Callout.Root>
            ) : null}
          </Flex>
        )}

        <Flex style={{ flex: 1, minHeight: 0, minWidth: 0 }}>
          <AgentBuilderSidebar
            open={sidebarOpen}
            width={AGENT_BUILDER_SIDEBAR_WIDTH}
            loading={loading}
            nodeTemplates={nodeTemplates}
            configuredConnectors={configuredConnectors}
            toolsets={toolsets}
            activeToolsetInstanceIds={activeToolsetInstanceIds}
            activeToolsetTypeKeysWithoutInstance={activeToolsetTypeKeysWithoutInstance}
            refreshToolsets={refreshToolsets}
            onNotify={setBanner}
            agentKey={effectiveAgentKey}
            isServiceAccount={isServiceAccount}
            paletteStructureLocked={isAgentStructureLocked}
            paletteDragBlockedMessage={paletteDragBlockedMessage}
            toolsetsOrgCredentialLocked={isServiceAccountToolsetOrgLocked}
            onManageAgentToolsetCredentials={
              isServiceAccountToolsetOrgLocked
                ? undefined
                : (ts) => {
                    if (!effectiveAgentKey) {
                      setBanner(t('agentBuilder.saveAsServiceAccountFirst'));
                      return;
                    }
                    if (!ts.instanceId) return;
                    setAgentToolsetDialog({ toolset: ts, instanceId: ts.instanceId });
                  }
            }
          />
          <AgentBuilderCanvas
            sidebarOpen={sidebarOpen}
            sidebarWidth={AGENT_BUILDER_SIDEBAR_WIDTH}
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeClick={onEdgeClick}
            setNodes={setNodes}
            setEdges={setEdges}
            nodeTemplates={nodeTemplates}
            configuredConnectors={configuredConnectors}
            activeAgentConnectors={activeAgentConnectors}
            onNodeDelete={(id) => {
              setNodeToDelete(id);
              setDeleteDialogOpen(true);
            }}
            onError={(m) => setBanner(m)}
            readOnly={isAgentStructureLocked}
          />
        </Flex>

        <Flex
          align="center"
          justify="between"
          px="4"
          py="3"
          gap="3"
          wrap="wrap"
          style={{
            borderTop: '1px solid var(--olive-3)',
            flexShrink: 0,
            background: 'var(--olive-1)',
            fontFamily: 'Manrope, sans-serif',
          }}
        >
          <Button variant="soft" color="gray" onClick={() => setSidebarOpen((s) => !s)}>
            <Flex align="center" gap="2">
              <MaterialIcon
                name={sidebarOpen ? 'arrow_back' : 'arrow_forward'}
                size={18}
                color="var(--slate-11)"
              />
              {sidebarOpen ? t('agentBuilder.hidePalette') : t('agentBuilder.showPalette')}
            </Flex>
          </Button>
          {loadedAgent ? (
            <Button variant="soft" color="green" onClick={() => router.push(buildChatHref({ agentId: loadedAgent._key }))}>
              <Flex align="center" gap="2">
                <MaterialIcon name="chat" size={18} />
                {t('agentBuilder.openInChat')}
              </Flex>
            </Button>
          ) : null}
        </Flex>
      </Flex>

      {/* ── Delete node dialog ── */}
      <Dialog.Root open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>{t('agentBuilder.removeNodeTitle')}</Dialog.Title>
          <Text size="2" mb="3">
            {nodeToDeleteLabel
              ? <>{t('agentBuilder.removeNodeWithName', { name: nodeToDeleteLabel })}</>
              : t('agentBuilder.removeNodeFallback')}
          </Text>
          <Flex gap="2" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray">
                {t('action.cancel')}
              </Button>
            </Dialog.Close>
            <Button color="red" onClick={confirmDelete} disabled={deleting}>
              {t('agentBuilder.remove')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      {/* ── Delete edge dialog ── */}
      <Dialog.Root
        open={edgeDeleteDialogOpen}
        onOpenChange={(o) => {
          setEdgeDeleteDialogOpen(o);
          if (!o) setEdgeToDelete(null);
        }}
      >
        <Dialog.Content style={{ maxWidth: 400 }}>
          <Dialog.Title>{t('agentBuilder.removeConnectionTitle')}</Dialog.Title>
          <Text size="2" mb="3">
            {t('agentBuilder.removeConnectionDesc')}
          </Text>
          <Flex gap="2" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray">
                {t('action.cancel')}
              </Button>
            </Dialog.Close>
            <Button
              color="red"
              onClick={() => {
                if (edgeToDelete) {
                  setEdges((eds) => eds.filter((e) => e.id !== edgeToDelete));
                }
                setEdgeDeleteDialogOpen(false);
                setEdgeToDelete(null);
              }}
            >
              {t('agentBuilder.remove')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      <ServiceAccountConfirmDialog
        open={serviceAccountConfirmOpen}
        agentName={agentName}
        creating={serviceAccountCreating}
        error={serviceAccountError}
        isConverting={Boolean(loadedAgent)}
        onClose={() => {
          setServiceAccountConfirmOpen(false);
          setServiceAccountError(null);
        }}
        onConfirm={handleConfirmServiceAccount}
      />

      {agentToolsetDialog && effectiveAgentKey ? (
        <AgentToolsetCredentialsDialog
          toolset={agentToolsetDialog.toolset}
          instanceId={agentToolsetDialog.instanceId}
          agentKey={effectiveAgentKey}
          onClose={() => {
            setAgentToolsetDialog(null);
            void refreshToolsets(effectiveAgentKey, true);
          }}
          onSuccess={() => {
            void refreshToolsets(effectiveAgentKey, true);
          }}
          onNotify={setBanner}
        />
      ) : null}

      <DeleteAgentDialog
        open={agentDeleteDialogOpen}
        onOpenChange={setAgentDeleteDialogOpen}
        onConfirm={confirmDeleteAgent}
        agentName={agentName.trim() || loadedAgent?.name || ''}
        isDeleting={isDeletingAgent}
      />
    </ReactFlowProvider>
  );
}
