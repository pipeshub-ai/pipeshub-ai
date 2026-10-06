'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Checkbox, Flex, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ToolsetsApi, type BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { KnowledgeBaseForBuilder } from '@/app/(main)/agents/types';
import {
  handleServerError,
  normalizeHandleInput,
  validateAgentHandle,
  type AgentHandleServerError,
} from '@/app/(main)/agents/agent-builder/agent-handle-utils';
import { openFreshAgentChat } from '@/chat/build-chat-url';
import { toast } from '@/lib/store/toast-store';
import type { AgentDraft, DraftKnowledge, DraftToolset } from '../../types';
import { draftBoxStyle } from './agent-draft-redacted';
import { useHandleAvailability } from './use-handle-availability';
import {
  initialKnowledge,
  legacyToolLabel,
  mergeToolsets,
  resolveLegacyTools,
  toolDisplayName,
  toolsetReferences,
  toolTickKey,
  webSearchPayload,
  type SelectedTool,
} from './agent-draft-model';
import { AgentDraftPicker, type PickerMode } from './agent-draft-picker';
import {
  ActionsSection,
  AddButton,
  KnowledgeSection,
  SectionHeader,
  UnresolvedSection,
  WebSearchRow,
  underLabel,
} from './agent-draft-sections';

export interface AgentDraftCardProps {
  draft: AgentDraft;
  conversationId: string | null;
  /** The stored draft row's id; null while the draft is still streaming. */
  messageId: string | null;
  builderEnabled: boolean;
  /** A later draft in the chat revises this one, so it collapses. */
  superseded?: boolean;
}

interface Rejected {
  handle?: AgentHandleServerError;
  knowledge: string[];
  toolsets: string[];
  generic: boolean;
  alreadyCreated?: { agentKey: string; handle: string };
}

const CREATED_TOAST_MS = 15_000;
const LONG_INSTRUCTIONS = 240;

const NO_REJECTION: Rejected = { knowledge: [], toolsets: [], generic: false };

function rejectionFrom(error: unknown, handle: string): Rejected {
  const handleError = handleServerError(error, handle);
  if (handleError) return { ...NO_REJECTION, handle: handleError };
  const processed = error as { code?: unknown; details?: { ids?: unknown; agentKey?: unknown; handle?: unknown } } | null;
  const code = typeof processed?.code === 'string' ? processed.code : '';
  if (code === 'AGENT_DRAFT_ALREADY_CREATED' && typeof processed?.details?.agentKey === 'string') {
    const existing = typeof processed.details.handle === 'string' ? processed.details.handle : handle;
    return { ...NO_REJECTION, alreadyCreated: { agentKey: processed.details.agentKey, handle: existing } };
  }
  const ids = Array.isArray(processed?.details?.ids)
    ? processed.details.ids.filter((id): id is string => typeof id === 'string')
    : [];
  if (code === 'INVALID_KNOWLEDGE' && ids.length > 0) return { ...NO_REJECTION, knowledge: ids };
  if (code === 'INVALID_TOOLSET' && ids.length > 0) return { ...NO_REJECTION, toolsets: ids };
  return { ...NO_REJECTION, generic: true };
}

const hint = { color: 'var(--slate-11)' } as const;

const toggled = (set: ReadonlySet<string>, ids: readonly string[], on: boolean): Set<string> => {
  const next = new Set(set);
  ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
  return next;
};

interface Attached {
  knowledge: string[];
  actions: string[];
  webSearch: string | null;
}

/** The requester's view of a drafted agent: edit it, tick what it may use, and create it. */
export function AgentDraftCard({ draft, conversationId, messageId, builderEnabled, superseded = false }: AgentDraftCardProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const fromContent = draft.provenance === 'content';

  const [name, setName] = useState(draft.name);
  const [handle, setHandle] = useState(draft.handleSuggestion);
  const [description, setDescription] = useState(draft.description);
  const [instructions, setInstructions] = useState(draft.instructions);
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  const [knowledgeItems, setKnowledgeItems] = useState<DraftKnowledge[]>(() => initialKnowledge(draft));
  const [knowledge, setKnowledge] = useState<ReadonlySet<string>>(
    () => new Set(fromContent ? [] : initialKnowledge(draft).map((k) => k.id)),
  );
  const [groups, setGroups] = useState<DraftToolset[]>(() => draft.actions ?? []);
  const [tools, setTools] = useState<ReadonlySet<string>>(
    () => new Set(fromContent ? [] : (draft.actions ?? []).flatMap((g) => g.tools.map((x) => toolTickKey(g.instanceId, x.fullName)))),
  );
  const [webSearchOn, setWebSearchOn] = useState(!fromContent && Boolean(draft.webSearch));
  const [legacyTools, setLegacyTools] = useState<ReadonlySet<string>>(() => new Set());
  const [rejected, setRejected] = useState<Rejected>(NO_REJECTION);
  const [created, setCreated] = useState<{ agentKey: string; handle: string; attached: Attached | null } | null>(
    () => (draft.createdAgent ? { ...draft.createdAgent, attached: null } : null),
  );
  const [creating, setCreating] = useState(false);
  const [picker, setPicker] = useState<PickerMode | null>(null);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const inFlight = useRef(false);

  const collapsed = superseded && !showSuperseded && !created;
  const legacyNames = draft.suggestedTools.length > 0 && !draft.actions?.length;
  const needsKnowledgeNames = knowledgeItems.some((k) => !k.name);
  const [toolsets, setToolsets] = useState<BuilderSidebarToolset[] | null>(null);
  const [knowledgeNames, setKnowledgeNames] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!builderEnabled || created || collapsed) return;
    let live = true;
    if (legacyNames) {
      ToolsetsApi.getAllMyToolsets({ includeRegistry: false, quiet: true })
        .then((r) => live && setToolsets(r.toolsets))
        .catch(() => live && setToolsets([]));
    }
    if (needsKnowledgeNames) {
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
  }, [builderEnabled, created, collapsed, legacyNames, needsKnowledgeNames]);

  const legacyOptions = useMemo(
    () => (legacyNames ? resolveLegacyTools(draft.suggestedTools, toolsets) : []),
    [legacyNames, draft.suggestedTools, toolsets],
  );
  const status = useHandleAvailability(handle, builderEnabled && !created && !collapsed);

  const normalized = normalizeHandleInput(handle);
  const serverHandle = rejected.handle && rejected.handle.handle === normalized ? rejected.handle : null;
  const handleProblem =
    serverHandle?.code === 'HANDLE_TAKEN' ? 'taken'
    : serverHandle ? (serverHandle.code === 'HANDLE_RESERVED' ? 'reserved' : 'invalid')
    : status.state === 'taken' || status.state === 'invalid' || status.state === 'reserved' ? status.state
    : null;
  const suggestion = serverHandle?.suggestion ?? (status.state === 'taken' ? status.suggestion : undefined);

  const selectedTools = useMemo<SelectedTool[]>(() => {
    const fromGroups = groups.flatMap((toolset) =>
      toolset.tools
        .filter((tool) => tools.has(toolTickKey(toolset.instanceId, tool.fullName)))
        .map((tool) => ({ toolset, tool })),
    );
    const fromLegacy = legacyOptions.flatMap((o) => (legacyTools.has(o.name) && o.source ? [o.source] : []));
    return [...fromGroups, ...fromLegacy];
  }, [groups, tools, legacyOptions, legacyTools]);
  const selectedKnowledge = knowledgeItems.filter((k) => knowledge.has(k.id));
  const webSearch = webSearchOn ? draft.webSearch ?? null : null;

  const summary = useMemo(() => {
    const parts: string[] = [];
    if (selectedKnowledge.length > 0) parts.push(t('chat.agentDraft.summaryKnowledge', { count: selectedKnowledge.length }));
    if (selectedTools.length > 0) parts.push(t('chat.agentDraft.summaryActions', { count: selectedTools.length }));
    if (webSearch) parts.push(t('chat.agentDraft.summaryWebSearch', { provider: webSearch.providerLabel }));
    return parts.length > 0 ? parts.join(' · ') : t('chat.agentDraft.summaryNothing');
  }, [selectedKnowledge.length, selectedTools.length, webSearch, t]);

  const canCreate =
    builderEnabled && !creating && Boolean(name.trim()) && handleProblem === null && !validateAgentHandle(handle) &&
    Boolean(conversationId) && Boolean(messageId);

  const create = useCallback(async () => {
    if (!canCreate || inFlight.current || !conversationId || !messageId) return;
    inFlight.current = true;
    setCreating(true);
    setRejected(NO_REJECTION);
    const chosen = normalizeHandleInput(handle);
    const payloadWebSearch = webSearchPayload(webSearch);
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
          knowledge: selectedKnowledge.map((k) => ({ connectorId: k.id, filters: { recordGroups: [], records: [] } })),
          toolsets: toolsetReferences(selectedTools),
          ...(payloadWebSearch ? { webSearch: payloadWebSearch } : {}),
          draftRef: { conversationId, messageId },
        },
        { suppressErrorToast: true },
      );
      const done = {
        agentKey: agent._key,
        handle: agent.handle ?? chosen,
        attached: {
          knowledge: selectedKnowledge.map((k) => k.name || knowledgeNames[k.id] || k.id),
          actions: selectedTools.map(({ toolset, tool }) => `${toolset.displayName}: ${toolDisplayName(tool.name)}`),
          webSearch: webSearch?.providerLabel ?? null,
        },
      };
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
      if (next.alreadyCreated) {
        setCreated({ ...next.alreadyCreated, attached: null });
        return;
      }
      setRejected(next);
      setKnowledge((prev) => new Set([...prev].filter((id) => !next.knowledge.includes(id))));
      const goneKeys = groups
        .filter((g) => next.toolsets.includes(g.instanceId))
        .flatMap((g) => g.tools.map((x) => toolTickKey(g.instanceId, x.fullName)));
      setTools((prev) => toggled(prev, goneKeys, false));
      setLegacyTools((prev) =>
        new Set([...prev].filter((n) => {
          const instance = legacyOptions.find((o) => o.name === n)?.source?.toolset.instanceId;
          return !instance || !next.toolsets.includes(instance);
        })),
      );
    } finally {
      inFlight.current = false;
      setCreating(false);
    }
  }, [canCreate, conversationId, messageId, handle, name, description, instructions, selectedKnowledge, selectedTools, webSearch, knowledgeNames, groups, legacyOptions, router, t]);

  const addKnowledge = useCallback((items: DraftKnowledge[]) => {
    setKnowledgeItems((prev) => [...prev, ...items.filter((i) => !prev.some((p) => p.id === i.id))]);
    setKnowledge((prev) => toggled(prev, items.map((i) => i.id), true));
  }, []);
  const addActions = useCallback((items: DraftToolset[]) => {
    setGroups((prev) => mergeToolsets(prev, items));
    setTools((prev) => toggled(prev, items.flatMap((g) => g.tools.map((x) => toolTickKey(g.instanceId, x.fullName))), true));
  }, []);
  const existingKnowledge = useMemo(() => new Set(knowledgeItems.map((k) => k.id)), [knowledgeItems]);
  const existingTools = useMemo(
    () => new Set(groups.flatMap((g) => g.tools.map((x) => toolTickKey(g.instanceId, x.fullName)))),
    [groups],
  );

  if (created) {
    const attached = created.attached ?? { knowledge: [], actions: [], webSearch: null };
    const attachedLine = [
      attached.knowledge.length > 0 ? t('chat.agentDraft.summaryKnowledge', { count: attached.knowledge.length }) : null,
      attached.actions.length > 0 ? t('chat.agentDraft.summaryActions', { count: attached.actions.length }) : null,
      attached.webSearch ? t('chat.agentDraft.summaryWebSearch', { provider: attached.webSearch }) : null,
    ].filter(Boolean).join(' · ');
    const details = [...attached.knowledge, ...attached.actions, ...(attached.webSearch ? [t('chat.agentDraft.webSearch')] : [])];
    return (
      <Box data-testid="agent-draft-card" data-state="created" data-draft-id={draft.draftId} style={draftBoxStyle}>
        <Flex direction="column" gap="2">
          <Flex align="center" gap="2">
            <MaterialIcon name="check_circle" size={18} color="var(--green-11)" />
            <Text size="2" weight="medium" data-testid="agent-draft-created">
              {t('chat.agentDraft.createdTitle', { handle: created.handle })}
            </Text>
          </Flex>
          {attachedLine ? (
            <Text size="1" style={hint} data-testid="agent-draft-attached" title={details.join('\n')}>
              {t('chat.agentDraft.attached', { summary: attachedLine })}
            </Text>
          ) : null}
          <Flex gap="2" wrap="wrap">
            <Button type="button" size="2" variant="soft" data-testid="agent-draft-open-chat" style={{ minHeight: 40 }} onClick={() => openFreshAgentChat(created.agentKey, router)}>
              {t('chat.agentDraft.openAgentChat')}
            </Button>
            <Button
              type="button"
              size="2"
              variant="soft"
              color="gray"
              data-testid="agent-draft-edit-agent"
              style={{ minHeight: 40 }}
              onClick={() => router.push(`/agents/edit?agentKey=${encodeURIComponent(created.agentKey)}`)}
            >
              {t('chat.agentDraft.editAgent')}
            </Button>
          </Flex>
        </Flex>
      </Box>
    );
  }

  if (collapsed) {
    return (
      <Box data-testid="agent-draft-superseded" data-draft-id={draft.draftId} style={draftBoxStyle}>
        <Flex align="center" justify="between" gap="3" wrap="wrap">
          <Text size="2" style={hint}>{t('chat.agentDraft.superseded')}</Text>
          <Button type="button" size="2" variant="ghost" color="gray" aria-expanded={false} style={{ minHeight: 40 }} onClick={() => setShowSuperseded(true)}>
            {t('chat.agentDraft.supersededShow')}
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
  const readOnly = !builderEnabled || superseded;
  const longInstructions = instructions.length > LONG_INSTRUCTIONS || instructions.split('\n').length > 4;

  return (
    <Box data-testid="agent-draft-card" data-state={!builderEnabled ? 'off' : superseded ? 'superseded' : 'draft'} data-draft-id={draftId} style={draftBoxStyle}>
      <Flex direction="column" gap="3">
        <Flex align="center" justify="between" gap="2">
          <Text size="1" weight="medium" style={{ ...hint, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            {t('chat.agentDraft.label')}
          </Text>
          {superseded ? (
            <Button type="button" size="2" variant="ghost" color="gray" aria-expanded style={{ minHeight: 40 }} onClick={() => setShowSuperseded(false)}>
              {t('chat.agentDraft.supersededHide')}
            </Button>
          ) : null}
        </Flex>

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
                <Button type="button" size="2" variant="soft" data-testid="agent-draft-use-suggestion" style={{ minHeight: 40 }} onClick={() => setHandle(suggestion)}>
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
          {longInstructions && !instructionsOpen ? (
            <Text
              as="p"
              size="2"
              id={`${draftId}-instructions`}
              data-testid="agent-draft-instructions-preview"
              style={{ margin: 0, overflowWrap: 'anywhere', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
            >
              {instructions}
            </Text>
          ) : (
            <TextArea id={`${draftId}-instructions`} data-testid="agent-draft-instructions" value={instructions} disabled={readOnly} rows={longInstructions ? 8 : 4} onChange={(e) => setInstructions(e.target.value)} />
          )}
          {longInstructions ? (
            <Button
              type="button"
              size="2"
              variant="ghost"
              color="gray"
              aria-expanded={instructionsOpen}
              data-testid="agent-draft-instructions-toggle"
              style={{ alignSelf: 'flex-start', minHeight: 40 }}
              onClick={() => setInstructionsOpen((v) => !v)}
            >
              {instructionsOpen ? t('chat.agentDraft.hideInstructions') : t('chat.agentDraft.showInstructions')}
            </Button>
          ) : null}
        </Flex>

        <KnowledgeSection
          items={knowledgeItems}
          names={knowledgeNames}
          ticked={knowledge}
          rejected={rejected.knowledge}
          readOnly={readOnly}
          onToggle={(id, on) => setKnowledge((prev) => toggled(prev, [id], on))}
        />

        <ActionsSection
          groups={groups}
          ticked={tools}
          rejectedInstances={rejected.toolsets}
          readOnly={readOnly}
          onToggleMany={(keys, on) => setTools((prev) => toggled(prev, keys, on))}
        />

        {legacyOptions.length > 0 ? (
          <Flex direction="column" gap="1" role="group" aria-label={t('chat.agentDraft.tools')}>
            <SectionHeader>{t('chat.agentDraft.tools')}</SectionHeader>
            <Text size="1" style={hint}>{t('chat.agentDraft.toolsHint')}</Text>
            {legacyOptions.map((option) => {
              const instance = option.source?.toolset.instanceId;
              const gone = Boolean(instance && rejected.toolsets.includes(instance));
              const missing = toolsets !== null && !option.source;
              return (
                <Flex key={option.name} direction="column">
                  <Text as="label" size="2" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minHeight: 40 }}>
                    <Checkbox
                      data-testid={`agent-draft-tool-${option.name}`}
                      checked={legacyTools.has(option.name)}
                      disabled={readOnly || !option.source || gone}
                      onCheckedChange={(on) => setLegacyTools((prev) => toggled(prev, [option.name], on === true))}
                    />
                    {legacyToolLabel(option.name)}
                  </Text>
                  {missing ? <Text size="1" style={{ ...hint, ...underLabel }}>{t('chat.agentDraft.toolNotSetUp')}</Text> : null}
                  {gone ? <Text size="1" role="alert" style={{ ...underLabel, color: 'var(--red-11)' }}>{t('chat.agentDraft.unavailable')}</Text> : null}
                </Flex>
              );
            })}
          </Flex>
        ) : null}

        {draft.webSearch ? (
          <WebSearchRow webSearch={draft.webSearch} on={webSearchOn} readOnly={readOnly} onChange={setWebSearchOn} />
        ) : null}

        <UnresolvedSection items={draft.unresolved ?? []} />

        {readOnly ? null : (
          <Flex gap="2" wrap="wrap">
            <AddButton label={t('chat.agentDraft.addKnowledge')} testId="agent-draft-add-knowledge" disabled={creating} onClick={() => setPicker('knowledge')} />
            <AddButton label={t('chat.agentDraft.addActions')} testId="agent-draft-add-actions" disabled={creating} onClick={() => setPicker('actions')} />
          </Flex>
        )}

        {rejected.generic ? (
          <Text size="2" role="alert" data-testid="agent-draft-error" style={{ color: 'var(--red-11)' }}>
            {t('chat.agentDraft.errorGeneric')}
          </Text>
        ) : null}

        <Flex direction="column" gap="2">
          {readOnly ? null : (
            <Text size="2" weight="medium" data-testid="agent-draft-summary" aria-live="polite">{summary}</Text>
          )}
          <Flex align="center" justify="between" gap="3" wrap="wrap">
            <Text size="1" style={hint}>
              {!builderEnabled ? t('chat.agentDraft.builderOff') : superseded ? '' : messageId ? t('chat.agentDraft.private') : t('chat.agentDraft.savingDraft')}
            </Text>
            {readOnly ? null : (
              <Button type="button" size="3" data-testid="agent-draft-create" disabled={!canCreate} loading={creating} onClick={() => void create()}>
                {t('chat.agentDraft.create')}
              </Button>
            )}
          </Flex>
        </Flex>
      </Flex>

      <AgentDraftPicker
        mode={picker}
        onClose={() => setPicker(null)}
        existingKnowledge={existingKnowledge}
        existingTools={existingTools}
        onAddKnowledge={addKnowledge}
        onAddActions={addActions}
      />
    </Box>
  );
}
