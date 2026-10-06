'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Checkbox, Flex, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { KnowledgeBaseForBuilder } from '@/app/(main)/agents/types';
import type { ToolsetReference } from '@/app/(main)/agents/agent-builder/types';
import { toolsetPayloadName } from '@/app/(main)/agents/agent-builder/extract-agent-config';
import {
  handleServerError,
  normalizeHandleInput,
  validateAgentHandle,
  type AgentHandleServerError,
} from '@/app/(main)/agents/agent-builder/agent-handle-utils';
import { openFreshAgentChat } from '@/chat/build-chat-url';
import { toast } from '@/lib/store/toast-store';
import type { AgentDraft } from '../../types';
import { draftBoxStyle } from './agent-draft-redacted';
import { useHandleAvailability } from './use-handle-availability';

export interface AgentDraftCardProps {
  draft: AgentDraft;
  conversationId: string | null;
  /** The stored draft row's id; null while the draft is still streaming. */
  messageId: string | null;
  builderEnabled: boolean;
}

interface ToolOption {
  name: string;
  /** The toolset instance that offers this tool to the user; undefined when none is set up for them. */
  source?: { toolset: BuilderSidebarToolset; tool: { name: string; fullName: string; description: string } };
}

interface Rejected {
  handle?: AgentHandleServerError;
  knowledge: string[];
  toolsets: string[];
  generic: boolean;
}

const CREATED_TOAST_MS = 15_000;

/** `jira__create_issue` reads as "Jira: create issue"; the raw name stays the value that is matched and sent. */
function toolLabel(name: string): string {
  const [app, ...rest] = name.split(/_{2,}/);
  if (rest.length === 0) return name.replace(/_/g, ' ');
  return `${app.charAt(0).toUpperCase()}${app.slice(1)}: ${rest.join(' ').replace(/_/g, ' ')}`;
}

const NO_REJECTION: Rejected = { knowledge: [], toolsets: [], generic: false };

const shortId = (id: string): string => (id.length > 12 ? `${id.slice(0, 8)}…` : id);

/** The model names tools as it sees them, `jira__create_issue`; the toolsets API says `jira.create_issue`. */
const toolKey = (name: string): string => name.toLowerCase().replace(/_{2,}/, '.');

function resolveTools(names: string[], toolsets: BuilderSidebarToolset[] | null): ToolOption[] {
  return names.map((name) => {
    const wanted = toolKey(name);
    for (const toolset of toolsets ?? []) {
      if (!toolset.instanceId || !toolset.isConfigured || !toolset.isAuthenticated) continue;
      const tool = toolset.tools.find(
        (x) => x.name.toLowerCase() === wanted || toolKey(x.fullName) === wanted ||
          `${(toolset.normalized_name || toolset.name).toLowerCase()}.${x.name.toLowerCase()}` === wanted,
      );
      if (tool) return { name, source: { toolset, tool } };
    }
    return { name };
  });
}

function toolsetReferences(options: ToolOption[], ticked: ReadonlySet<string>): ToolsetReference[] {
  const byInstance = new Map<string, ToolsetReference>();
  for (const option of options) {
    if (!ticked.has(option.name) || !option.source) continue;
    const { toolset, tool } = option.source;
    const key = toolset.instanceId as string;
    const entry = byInstance.get(key) ?? {
      id: key,
      instanceId: key,
      ...(toolset.instanceName ? { instanceName: toolset.instanceName } : {}),
      name: toolsetPayloadName(toolset.normalized_name || toolset.name),
      displayName: toolset.displayName,
      type: toolset.category || 'app',
      tools: [],
    };
    entry.tools?.push({ name: tool.name, fullName: tool.fullName, description: tool.description });
    byInstance.set(key, entry);
  }
  return [...byInstance.values()];
}

function rejectionFrom(error: unknown, handle: string): Rejected {
  const handleError = handleServerError(error, handle);
  if (handleError) return { ...NO_REJECTION, handle: handleError };
  const processed = error as { code?: unknown; details?: { ids?: unknown } } | null;
  const code = typeof processed?.code === 'string' ? processed.code : '';
  const ids = Array.isArray(processed?.details?.ids)
    ? processed.details.ids.filter((id): id is string => typeof id === 'string')
    : [];
  if (code === 'INVALID_KNOWLEDGE' && ids.length > 0) return { ...NO_REJECTION, knowledge: ids };
  if (code === 'INVALID_TOOLSET' && ids.length > 0) return { ...NO_REJECTION, toolsets: ids };
  return { ...NO_REJECTION, generic: true };
}

const hint = { color: 'var(--slate-11)' } as const;
/** A note under a checkbox row lines up with its label: the checkbox plus the row's gap. */
const underLabel = { paddingLeft: 'calc(var(--space-4) + var(--space-2))' } as const;

/** The requester's view of a drafted agent: edit it, tick what it may use, and create it. */
export function AgentDraftCard({ draft, conversationId, messageId, builderEnabled }: AgentDraftCardProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const fromContent = draft.provenance === 'content';

  const [name, setName] = useState(draft.name);
  const [handle, setHandle] = useState(draft.handleSuggestion);
  const [description, setDescription] = useState(draft.description);
  const [instructions, setInstructions] = useState(draft.instructions);
  const [knowledge, setKnowledge] = useState<ReadonlySet<string>>(
    () => new Set(fromContent ? [] : draft.knowledge),
  );
  const [tools, setTools] = useState<ReadonlySet<string>>(() => new Set());
  const [rejected, setRejected] = useState<Rejected>(NO_REJECTION);
  const [created, setCreated] = useState<{ agentKey: string; handle: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const inFlight = useRef(false);

  const [toolsets, setToolsets] = useState<BuilderSidebarToolset[] | null>(null);
  const [knowledgeNames, setKnowledgeNames] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!builderEnabled || created) return;
    let live = true;
    if (draft.suggestedTools.length > 0) {
      ToolsetsApi.getAllMyToolsets({ includeRegistry: false, quiet: true })
        .then((r) => live && setToolsets(r.toolsets))
        .catch(() => live && setToolsets([]));
    }
    if (draft.knowledge.length > 0) {
      Promise.allSettled([
        AgentsApi.getKnowledgeBasesForBuilder({ quiet: true }),
        AgentsApi.getKnowledgeHubAppNodes({ limit: 100, flattened: false, quiet: true }),
      ]).then(([kbs, apps]) => {
        if (!live) return;
        const names: Record<string, string> = {};
        if (apps.status === 'fulfilled') apps.value.nodes.forEach((node) => { names[node.id] = node.name; });
        if (kbs.status === 'fulfilled') {
          kbs.value.knowledgeBases.forEach((kb: KnowledgeBaseForBuilder) => {
            names[kb.connectorId] = kb.name;
            names[kb.id] = kb.name;
          });
        }
        setKnowledgeNames(names);
      });
    }
    return () => {
      live = false;
    };
  }, [builderEnabled, created, draft.suggestedTools.length, draft.knowledge.length]);

  const toolOptions = useMemo(() => resolveTools(draft.suggestedTools, toolsets), [draft.suggestedTools, toolsets]);
  const status = useHandleAvailability(handle, builderEnabled && !created);

  const normalized = normalizeHandleInput(handle);
  const serverHandle = rejected.handle && rejected.handle.handle === normalized ? rejected.handle : null;
  const handleProblem =
    serverHandle?.code === 'HANDLE_TAKEN' ? 'taken'
    : serverHandle ? (serverHandle.code === 'HANDLE_RESERVED' ? 'reserved' : 'invalid')
    : status.state === 'taken' || status.state === 'invalid' || status.state === 'reserved' ? status.state
    : null;
  const suggestion = serverHandle?.suggestion ?? (status.state === 'taken' ? status.suggestion : undefined);

  const toggle = (set: ReadonlySet<string>, id: string, on: boolean): Set<string> => {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  };

  const canCreate =
    builderEnabled && !creating && Boolean(name.trim()) && handleProblem === null && !validateAgentHandle(handle) &&
    Boolean(conversationId) && Boolean(messageId);

  const create = useCallback(async () => {
    if (!canCreate || inFlight.current || !conversationId || !messageId) return;
    inFlight.current = true;
    setCreating(true);
    setRejected(NO_REJECTION);
    const chosen = normalizeHandleInput(handle);
    try {
      const agent = await AgentsApi.createAgent(
        {
          name: name.trim(),
          handle: chosen,
          description: description.trim(),
          instructions: instructions.trim(),
          startMessage: '',
          systemPrompt: '',
          models: [],
          tags: [],
          knowledge: draft.knowledge
            .filter((id) => knowledge.has(id))
            .map((connectorId) => ({ connectorId, filters: { recordGroups: [], records: [] } })),
          toolsets: toolsetReferences(toolOptions, tools),
          draftRef: { conversationId, messageId },
        },
        { suppressErrorToast: true },
      );
      const done = { agentKey: agent._key, handle: agent.handle ?? chosen };
      setCreated(done);
      // An action toast would otherwise stay until dismissed and sit over the composer's send button.
      const toastId = toast.success(t('chat.agentDraft.createdTitle', { handle: done.handle }), {
        duration: CREATED_TOAST_MS,
        action: {
          label: t('chat.agentDraft.openAgentChat'),
          onClick: () => {
            toast.dismiss(toastId);
            openFreshAgentChat(done.agentKey, router);
          },
        },
      });
    } catch (error) {
      const next = rejectionFrom(error, chosen);
      setRejected(next);
      setKnowledge((prev) => new Set([...prev].filter((id) => !next.knowledge.includes(id))));
      setTools((prev) =>
        new Set(
          [...prev].filter((n) => {
            const instance = toolOptions.find((o) => o.name === n)?.source?.toolset.instanceId;
            return !instance || !next.toolsets.includes(instance);
          }),
        ),
      );
    } finally {
      inFlight.current = false;
      setCreating(false);
    }
  }, [canCreate, conversationId, messageId, handle, name, description, instructions, draft.knowledge, knowledge, toolOptions, tools, router, t]);

  if (created) {
    return (
      <Box data-testid="agent-draft-card" data-state="created" data-draft-id={draft.draftId} style={draftBoxStyle}>
        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Flex align="center" gap="2">
            <MaterialIcon name="check_circle" size={18} color="var(--green-11)" />
            <Text size="2" weight="medium" data-testid="agent-draft-created">
              {t('chat.agentDraft.createdTitle', { handle: created.handle })}
            </Text>
          </Flex>
          <Button
            type="button"
            size="1"
            variant="soft"
            data-testid="agent-draft-open-chat"
            onClick={() => openFreshAgentChat(created.agentKey, router)}
          >
            {t('chat.agentDraft.openAgentChat')}
          </Button>
        </Flex>
      </Box>
    );
  }

  const handleMessage =
    handleProblem === 'taken' ? t('chat.agentDraft.handleTaken', { handle: normalized })
    : handleProblem === 'reserved' ? t('chat.agentDraft.handleReserved', { handle: normalized })
    : handleProblem === 'invalid' ? t('chat.agentDraft.handleInvalid')
    : status.state === 'checking' ? t('chat.agentDraft.handleChecking')
    : status.state === 'available' ? t('chat.agentDraft.handleAvailable')
    : null;
  const handleBad = handleProblem !== null;
  const draftId = draft.draftId;
  const readOnly = !builderEnabled;

  return (
    <Box data-testid="agent-draft-card" data-state={readOnly ? 'off' : 'draft'} data-draft-id={draftId} style={draftBoxStyle}>
      <Flex direction="column" gap="3">
        <Text size="1" weight="medium" style={{ ...hint, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {t('chat.agentDraft.label')}
        </Text>

        {fromContent ? (
          <Box role="note" data-testid="agent-draft-content-banner" style={{ background: 'var(--amber-3)', border: '1px solid var(--amber-6)', borderRadius: 'var(--radius-2)', padding: 'var(--space-2)' }}>
            <Text size="2" weight="medium" style={{ color: 'var(--amber-11)' }}>{t('chat.agentDraft.contentBanner')}</Text>
            <Text as="p" size="1" style={{ color: 'var(--amber-11)' }}>{t('chat.agentDraft.contentBannerBody')}</Text>
          </Box>
        ) : null}

        <Flex direction="column" gap="1">
          <Text as="label" size="1" htmlFor={`${draftId}-name`} style={hint}>{t('chat.agentDraft.name')}</Text>
          <TextField.Root id={`${draftId}-name`} data-testid="agent-draft-name" value={name} disabled={readOnly} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </Flex>

        <Flex direction="column" gap="1">
          <Text as="label" size="1" htmlFor={`${draftId}-handle`} style={hint}>{t('chat.agentDraft.handle')}</Text>
          <TextField.Root
            id={`${draftId}-handle`}
            data-testid="agent-draft-handle"
            value={handle}
            disabled={readOnly}
            aria-invalid={handleBad || undefined}
            aria-describedby={handleMessage ? `${draftId}-handle-msg` : undefined}
            color={handleBad ? 'red' : undefined}
            onChange={(e) => setHandle(e.target.value)}
          >
            <TextField.Slot side="left"><Text size="1" style={hint}>@</Text></TextField.Slot>
          </TextField.Root>
          {handleMessage ? (
            <Flex align="center" gap="2" wrap="wrap">
              <Text
                id={`${draftId}-handle-msg`}
                size="1"
                data-testid="agent-draft-handle-status"
                role={handleBad ? 'alert' : 'status'}
                style={{ color: handleBad ? 'var(--red-11)' : status.state === 'available' ? 'var(--green-12)' : 'var(--slate-11)' }}
              >
                {handleMessage}
              </Text>
              {handleProblem === 'taken' && suggestion ? (
                <Button type="button" size="1" variant="soft" data-testid="agent-draft-use-suggestion" onClick={() => setHandle(suggestion)}>
                  {t('chat.agentDraft.useSuggestion', { suggestion })}
                </Button>
              ) : null}
            </Flex>
          ) : null}
        </Flex>

        <Flex direction="column" gap="1">
          <Text as="label" size="1" htmlFor={`${draftId}-description`} style={hint}>{t('chat.agentDraft.description')}</Text>
          <TextArea id={`${draftId}-description`} data-testid="agent-draft-description" value={description} disabled={readOnly} rows={2} onChange={(e) => setDescription(e.target.value)} />
        </Flex>

        <Flex direction="column" gap="1">
          <Text as="label" size="1" htmlFor={`${draftId}-instructions`} style={hint}>{t('chat.agentDraft.instructions')}</Text>
          <TextArea id={`${draftId}-instructions`} data-testid="agent-draft-instructions" value={instructions} disabled={readOnly} rows={4} onChange={(e) => setInstructions(e.target.value)} />
        </Flex>

        {draft.knowledge.length > 0 ? (
          <Flex direction="column" gap="1" role="group" aria-label={t('chat.agentDraft.knowledge')}>
            <Text size="1" style={hint}>{t('chat.agentDraft.knowledge')}</Text>
            {draft.knowledge.map((id) => {
              const gone = rejected.knowledge.includes(id);
              return (
                <Flex key={id} direction="column">
                  <Text as="label" size="2">
                    <Flex align="center" gap="2">
                      <Checkbox
                        data-testid={`agent-draft-knowledge-${id}`}
                        checked={knowledge.has(id)}
                        disabled={readOnly || gone}
                        onCheckedChange={(on) => setKnowledge((prev) => toggle(prev, id, on === true))}
                      />
                      {knowledgeNames[id] ?? t('chat.agentDraft.knowledgeFallback', { id: shortId(id) })}
                    </Flex>
                  </Text>
                  {gone ? <Text size="1" role="alert" style={{ ...underLabel, color: 'var(--red-11)' }}>{t('chat.agentDraft.unavailable')}</Text> : null}
                </Flex>
              );
            })}
          </Flex>
        ) : null}

        {toolOptions.length > 0 ? (
          <Flex direction="column" gap="1" role="group" aria-label={t('chat.agentDraft.tools')}>
            <Text size="1" style={hint}>{t('chat.agentDraft.tools')}</Text>
            <Text size="1" style={hint}>{t('chat.agentDraft.toolsHint')}</Text>
            {toolOptions.map((option) => {
              const instance = option.source?.toolset.instanceId;
              const gone = Boolean(instance && rejected.toolsets.includes(instance));
              const missing = toolsets !== null && !option.source;
              return (
                <Flex key={option.name} direction="column">
                  <Text as="label" size="2">
                    <Flex align="center" gap="2">
                      <Checkbox
                        data-testid={`agent-draft-tool-${option.name}`}
                        checked={tools.has(option.name)}
                        disabled={readOnly || !option.source || gone}
                        onCheckedChange={(on) => setTools((prev) => toggle(prev, option.name, on === true))}
                      />
                      {toolLabel(option.name)}
                    </Flex>
                  </Text>
                  {missing ? <Text size="1" style={{ ...hint, ...underLabel }}>{t('chat.agentDraft.toolNotSetUp')}</Text> : null}
                  {gone ? <Text size="1" role="alert" style={{ ...underLabel, color: 'var(--red-11)' }}>{t('chat.agentDraft.unavailable')}</Text> : null}
                </Flex>
              );
            })}
          </Flex>
        ) : null}

        {rejected.generic ? (
          <Text size="2" role="alert" data-testid="agent-draft-error" style={{ color: 'var(--red-11)' }}>
            {t('chat.agentDraft.errorGeneric')}
          </Text>
        ) : null}

        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Text size="1" style={hint}>
            {readOnly ? t('chat.agentDraft.builderOff') : messageId ? t('chat.agentDraft.private') : t('chat.agentDraft.savingDraft')}
          </Text>
          {readOnly ? null : (
            <Button type="button" data-testid="agent-draft-create" disabled={!canCreate} loading={creating} onClick={() => void create()}>
              {t('chat.agentDraft.create')}
            </Button>
          )}
        </Flex>
      </Flex>
    </Box>
  );
}
