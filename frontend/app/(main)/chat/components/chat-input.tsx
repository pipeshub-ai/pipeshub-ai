'use client';

import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { Spinner } from '@/app/components/ui/spinner';
import { Flex, Box, Text, IconButton, Tooltip } from '@radix-ui/themes';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatInputExpansionPanel } from '@/chat/components/chat-panel/expansion-panels/chat-input-expansion-panel';
import { ChatInputOverlayPanel } from '@/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel';
import { QueryModePanel } from '@/chat/components/chat-panel/expansion-panels/query-mode-panel';
import { ConnectorsCollectionsPanel } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/connectors-collections-panel';
import { AgentScopedResourcesPanel } from '@/chat/components/chat-panel/expansion-panels/agent-scoped-resources-panel';
import { UniversalAgentResourcesPanel } from '@/chat/components/chat-panel/expansion-panels/universal-agent-resources-panel';
import { MessageActionIndicator } from '@/chat/components/chat-panel/expansion-panels/message-actions';
import { ModelSelectorPanel } from '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import { SelectedCollections } from '@/chat/components/selected-collections';
import { resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import {
  ModeSwitcher,
  AgentStrategyModeSwitcher,
  AgentStrategyModePanel,
} from '@/chat/components/chat-panel';
import { MobileQueryOptionsSheet } from '@/chat/components/chat-panel/expansion-panels/mobile-query-options-sheet';
import { MobileQueryModesSheet } from '@/chat/components/chat-panel/expansion-panels/mobile-query-modes-sheet';
import { AgentStrategyDropdown } from '@/chat/components/agent-strategy-dropdown';
import { getQueryModeConfig } from '@/chat/constants';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useCommandStore } from '@/lib/store/command-store';
import { toast } from '@/lib/store/toast-store';
import { streamRegenerateForSlot, cancelStreamForSlot } from '@/chat/streaming';
import { useTranslation } from 'react-i18next';
import { useChatSpeechRecognition } from '@/lib/hooks/use-chat-speech-recognition';
import type {
  UploadedFile,
  ActiveMessageAction,
  ModelOverride,
  AppliedFilters,
  AttachmentRef,
} from '@/chat/types';
import { CHAT_ATTACHMENT_MAX_BYTES, CHAT_ATTACHMENT_MAX_FILES } from '@/chat/types';

type ChatInputVariant = 'full' | 'widget';

interface ChatInputProps {
  /**
   * Called when the user submits. `attachments` only contains the
   * server-assigned refs of files whose upload finished successfully.
   * Chips still uploading or in error are blocked from submit by `canSubmit`.
   */
  onSend?: (message: string, attachments?: AttachmentRef[]) => void;
  /**
   * Per-file upload. Fired the moment a file is added to the composer
   * (not at send time). Receives an abort signal so the composer can cancel
   * the in-flight request when the user removes the chip mid-upload.
   * Throws on failure — the chip is marked `'error'` and surfaces a retry.
   */
  onUploadFile?: (file: File, signal: AbortSignal) => Promise<AttachmentRef>;
  /**
   * Called fire-and-forget when the user removes a chip whose upload already
   * completed. The chip is removed from state synchronously; this callback
   * should trigger a best-effort server-side delete in the background.
   * Errors MUST be swallowed by the caller — never block the UI on a failed
   * delete.
   */
  onDeleteFile?: (recordId: string) => void;
  placeholder?: string;
  /** Placeholder shown in the collapsed widget pill (parent controls the text) */
  widgetPlaceholder?: string;
  variant?: ChatInputVariant;
  expandable?: boolean;
  /** `?agentId=` agent conversation — query-mode + web search controls are hidden */
  isAgentChat?: boolean;
  /** Agent ID for filtering models to only those configured for the agent */
  agentId?: string | null;
}

// Only PDF and images are supported by the chat attachment upload endpoint.
const SUPPORTED_FILE_TYPES = ['PDF', 'PNG', 'JPEG', 'JPG'];
const ACCEPTED_MIME_TYPES = {
  'application/pdf': 'PDF',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/jpg': 'JPEG',
};
// Extension fallback for files that arrive without a recognisable MIME type
// (e.g. on some Windows setups the file.type may be empty).
const ACCEPTED_EXTENSIONS = ['pdf', 'png', 'jpeg', 'jpg'];

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isFileTypeSupported(file: File): boolean {
  const mimeType = file.type;
  if (Object.keys(ACCEPTED_MIME_TYPES).includes(mimeType)) return true;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return ACCEPTED_EXTENSIONS.includes(ext);
}

interface SpeechInputButtonProps {
  tooltip: string;
  ariaLabel: string;
  isListening: boolean;
  isSupported: boolean;
  isDisabled: boolean;
  isRegenerateMode: boolean;
  activeIconColor: string;
  onToggle: () => void;
  style?: React.CSSProperties;
}

function SpeechInputButton({
  tooltip,
  ariaLabel,
  isListening,
  isSupported,
  isDisabled,
  isRegenerateMode,
  activeIconColor,
  onToggle,
  style,
}: SpeechInputButtonProps) {
  return (
    <Tooltip content={tooltip} side="top">
      <IconButton
        variant={isListening ? 'soft' : 'ghost'}
        color={isListening ? 'red' : 'gray'}
        size="2"
        disabled={isDisabled}
        onClick={onToggle}
        aria-label={ariaLabel}
        style={{
          margin: 0,
          cursor: !isSupported ? 'not-allowed' : isRegenerateMode ? 'default' : 'pointer',
          ...(isListening && { animation: 'pulse 1.5s ease-in-out infinite' }),
          ...style,
        }}
      >
        <MaterialIcon
          name={isListening ? 'mic' : 'mic_none'}
          size={ICON_SIZES.PRIMARY}
          color={
            isDisabled ? 'var(--slate-9)' : isListening ? 'var(--red-11)' : activeIconColor
          }
        />
      </IconButton>
    </Tooltip>
  );
}

export function ChatInput({
  onSend,
  onUploadFile,
  onDeleteFile,
  placeholder,
  widgetPlaceholder,
  variant = 'full',
  expandable = false,
  isAgentChat = false,
  agentId,
}: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [showUploadArea, setShowUploadArea] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isExpanded, setIsExpanded] = useState(variant === 'full');
  const [isAnimatingIn, setIsAnimatingIn] = useState(false);
  const [isModePanelOpen, setIsModePanelOpen] = useState(false);
  const [isAgentStrategyPanelOpen, setIsAgentStrategyPanelOpen] = useState(false);
  const [isCollectionsPanelOpen, setIsCollectionsPanelOpen] = useState(false);
  /** Agent chat: Connectors / Collections / Actions (Figma agent input). */
  const [isAgentResourcesPanelOpen, setIsAgentResourcesPanelOpen] = useState(false);
  const [isModelPanelOpen, setIsModelPanelOpen] = useState(false);
  const [isModelButtonHovered, setIsModelButtonHovered] = useState(false);
  const [isAddFileButtonHovered, setIsAddFileButtonHovered] = useState(false);
  const [isMobileOptionsOpen, setIsMobileOptionsOpen] = useState(false);
  const [isMobileModesOpen, setIsMobileModesOpen] = useState(false);
  const [isInputFocused, setIsInputFocused] = useState(false);
  const isMobile = useIsMobile();
  // ── Message action state (local — NOT in Zustand store) ──
  const [activeMessageAction, setActiveMessageAction] = useState<ActiveMessageAction>(null);
  const [regenModelOverride, setRegenModelOverride] = useState<ModelOverride | null>(null);
  const isRegenerateMode = activeMessageAction?.type === 'regenerate';
  const isEditMode = activeMessageAction?.type === 'editQuery';
  const isActionMode = isRegenerateMode || isEditMode;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const chipsScrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  /**
   * Per-file AbortController keyed by `UploadedFile.id`. Held in a ref (not
   * state) because controllers are imperative — they belong outside the
   * render cycle. Used by `removeFile`/retry/unmount to cancel in-flight
   * uploads cleanly. Entries are deleted in the upload finalizer so the map
   * doesn't leak across long-lived sessions.
   */
  const uploadControllersRef = useRef<Map<string, AbortController>>(new Map());
  const { t, i18n } = useTranslation();
  const resolvedPlaceholder = placeholder ?? t('chat.askAnything');

  const {
    isListening,
    isSupported: isSpeechSupported,
    transcript: speechTranscript,
    interimTranscript,
    toggle: toggleSpeech,
    stop: stopSpeech,
    resetTranscript,
    unavailableReason: speechUnavailableReason,
  } = useChatSpeechRecognition({
    lang: i18n.language,
    onError: (error) => {
      if (error === 'not-allowed') {
        toast.error(t('chat.voiceError'));
      }
    },
  });

  // Sync finalized speech transcript into the message textarea
  useEffect(() => {
    if (speechTranscript) {
      setMessage((prev) => {
        const separator = prev.length > 0 ? ' ' : '';
        return prev + separator + speechTranscript;
      });
      resetTranscript();
    }
  }, [speechTranscript, resetTranscript]);

  // Read all chat settings directly from the shared store
  const settings = useChatStore((s) => s.settings);
  const setMode = useChatStore((s) => s.setMode);
  const setQueryMode = useChatStore((s) => s.setQueryMode);
  const setAgentStrategy = useChatStore((s) => s.setAgentStrategy);
  const setFilters = useChatStore((s) => s.setFilters);
  const setSelectedModelForCtx = useChatStore((s) => s.setSelectedModelForCtx);
  const collectionNamesCache = useChatStore((s) => s.collectionNamesCache);
  const collectionMetaCache = useChatStore((s) => s.collectionMetaCache);
  const agentKnowledgeScope = useChatStore((s) => s.agentKnowledgeScope);
  const agentKnowledgeDefaults = useChatStore((s) => s.agentKnowledgeDefaults);
  const setAgentKnowledgeScope = useChatStore((s) => s.setAgentKnowledgeScope);
  const agentStreamToolsSel = useChatStore((s) => s.agentStreamTools);
  const agentToolCatalogLen = useChatStore((s) => s.agentToolCatalogFullNames.length);
  const agentChatToolGroups = useChatStore((s) => s.agentChatToolGroups);
  const universalAgentStreamTools = useChatStore((s) => s.universalAgentStreamTools);
  const universalAgentToolsLoading = useChatStore((s) => s.universalAgentToolsLoading);
  const universalAgentToolGroups = useChatStore((s) => s.universalAgentToolGroups);

  // Context key for the active (agent-scoped or assistant) chat. All
  // model-related reads/writes below are keyed by this so assistant selections
  // don't leak into agents and vice-versa.
  const modelCtxKey = ctxKeyFromAgent(agentId);
  const contextSelectedModel = settings.selectedModels[modelCtxKey] ?? null;
  const contextDefaultModel = settings.defaultModels[modelCtxKey] ?? null;
  const displayModel = contextSelectedModel ?? contextDefaultModel;
  const displayModelLabel = displayModel
    ? (displayModel.modelFriendlyName || displayModel.modelName)
    : t('chat.aiModelsTooltip');
  const handleModelSelect = useCallback(
    (model: ModelOverride | null) => {
      setSelectedModelForCtx(modelCtxKey, model);
    },
    [setSelectedModelForCtx, modelCtxKey],
  );

  // Expansion panel view mode (inline vs overlay) from store
  const expansionViewMode = useChatStore((s) => s.expansionViewMode);
  const setExpansionViewMode = useChatStore((s) => s.setExpansionViewMode);

  // Active slot ID for regenerate/edit flows
  const activeSlotId = useChatStore((s) => s.activeSlotId);

  // Is the active slot currently streaming?
  const isStreaming = useChatStore((s) =>
    s.activeSlotId ? (s.slots[s.activeSlotId]?.isStreaming ?? false) : false
  );

  const handleStopStream = useCallback(() => {
    const sid = useChatStore.getState().activeSlotId;
    if (sid) cancelStreamForSlot(sid);
    toast.info(t('chat.toasts.stopStreamTitle'), {
      description: t('chat.toasts.stopStreamDescription'),
    });
  }, [t]);

  const handleToggleView = useCallback(() => {
    setExpansionViewMode(expansionViewMode === 'inline' ? 'overlay' : 'inline');
  }, [expansionViewMode, setExpansionViewMode]);

  const showFullUI = variant === 'full' || isExpanded;
  const resolvedWidgetPlaceholder = widgetPlaceholder || resolvedPlaceholder;

  const isSearchMode = settings.mode === 'search' && !isAgentChat;
  const selectedKbCount = (settings.filters?.apps?.length ?? 0) + (settings.filters?.kb?.length ?? 0);
  const agentResourcesCustomized =
    isAgentChat &&
    (agentKnowledgeScope !== null ||
      (agentStreamToolsSel !== null &&
        agentToolCatalogLen > 0 &&
        (agentStreamToolsSel.length === 0 || agentStreamToolsSel.length < agentToolCatalogLen)));

  /** Universal agent mode has an explicit tool selection (not null = "all tools"). */
  const universalAgentResourcesCustomized =
    !isAgentChat && settings.queryMode === 'agent' && universalAgentStreamTools !== null;

  /** True when universal agent tool data is loading (disable send while loading). */
  const isUniversalAgentLoading =
    !isAgentChat && settings.queryMode === 'agent' && universalAgentToolsLoading;
  const activeQueryConfig = getQueryModeConfig(settings.queryMode) ?? getQueryModeConfig('chat')!;
  /** Internal-search / chat modes: `settings.filters` drives the connectors & collections picker. */
  const hubFilterQueryMode =
    !isAgentChat && settings.queryMode !== 'agent' && settings.queryMode !== 'web-search';
  /** Assistant collections overlay is active (web search never uses this chrome). */
  const assistantCollectionsOverlayActive =
    !isAgentChat && isCollectionsPanelOpen && settings.queryMode !== 'web-search';
  const modeColors = activeQueryConfig.colors;
  const agentQueryToolbarConfig = getQueryModeConfig('agent')!;
  const agentStrategyToolbarColors = agentQueryToolbarConfig.colors;
  /** Query-mode, agent-strategy, or agent resources panel — chrome + outside click. */
  const modeChromeOpen = isAgentChat
    ? isAgentStrategyPanelOpen || isAgentResourcesPanelOpen
    : isModePanelOpen;

  const dismissExpansionPanels = useCallback(() => {
    setIsModePanelOpen(false);
    setIsAgentStrategyPanelOpen(false);
    setIsCollectionsPanelOpen(false);
    setIsAgentResourcesPanelOpen(false);
    setIsModelPanelOpen(false);
    setShowUploadArea(false);
  }, []);

  const dismissExpansionPanelsRef = useRef(dismissExpansionPanels);
  useEffect(() => {
    dismissExpansionPanelsRef.current = dismissExpansionPanels;
  }, [dismissExpansionPanels]);

  // Build selected collections from store (roots → apps API; record groups → kb API).
  // Includes connector metadata so pills show the right icon per source type.
  // In agent mode, read from the effective agent knowledge scope instead of settings.filters.
  // In regenerate mode, read from the original message's appliedFilters (locked, non-removable).
  const regenAppliedFilters = isRegenerateMode && activeMessageAction?.type === 'regenerate'
    ? activeMessageAction.appliedFilters
    : undefined;

  const selectedCollections = useMemo(() => {
    // Regenerate mode: derive pills directly from the original message's appliedFilters nodes
    // (they carry name + connector already, so no cache lookup needed).
    if (regenAppliedFilters) {
      return [
        ...regenAppliedFilters.apps.map((node) => ({
          id: node.id,
          name: node.name,
          kind: (node.nodeType === 'app' ? 'connector' : 'collection') as 'connector' | 'collection',
          connectorType: node.connector ? resolveConnectorType(node.connector) : undefined,
        })),
        ...regenAppliedFilters.kb.map((node) => ({
          id: node.id,
          name: node.name,
          kind: 'collection' as const,
          connectorType: node.connector ? resolveConnectorType(node.connector) : undefined,
        })),
      ];
    }

    if (!isAgentChat && settings.queryMode === 'web-search') {
      return [];
    }

    const source = isAgentChat
      ? (agentKnowledgeScope ?? agentKnowledgeDefaults)
      : settings.filters;
    const hubApps = source?.apps ?? [];
    const groups = source?.kb ?? [];
    return [
      ...hubApps.map((id) => {
        const meta = collectionMetaCache[id];
        const isConnector = meta?.nodeType === 'app';
        return {
          id,
          name: collectionNamesCache[id] || meta?.name || 'Collection',
          kind: (isConnector ? 'connector' : 'collection') as 'connector' | 'collection',
          connectorType: meta?.connector ? resolveConnectorType(meta.connector) : undefined,
        };
      }),
      ...groups.map((id) => {
        const meta = collectionMetaCache[id];
        return {
          id,
          name: collectionNamesCache[id] || meta?.name || 'Collection',
          kind: 'collection' as const,
          connectorType: meta?.connector ? resolveConnectorType(meta.connector) : undefined,
        };
      }),
    ];
  }, [
    regenAppliedFilters,
    isAgentChat,
    agentKnowledgeScope,
    agentKnowledgeDefaults,
    settings.filters,
    settings.queryMode,
    collectionNamesCache,
    collectionMetaCache,
  ]);

  const showSelectedCollectionsRow =
    selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen;

  const handleRemoveCollection = useCallback(
    (id: string) => {
      if (isAgentChat) {
        const eff = agentKnowledgeScope ?? agentKnowledgeDefaults;
        const nextApps = eff.apps.filter((aid) => aid !== id);
        const nextKb = eff.kb.filter((gid) => gid !== id);
        // Normalize to null when result matches defaults (no customization applied)
        const appsMatch =
          new Set(nextApps).size === new Set(agentKnowledgeDefaults.apps).size &&
          nextApps.every((x) => agentKnowledgeDefaults.apps.includes(x));
        const kbMatch =
          new Set(nextKb).size === new Set(agentKnowledgeDefaults.kb).size &&
          nextKb.every((x) => agentKnowledgeDefaults.kb.includes(x));
        setAgentKnowledgeScope(appsMatch && kbMatch ? null : { apps: nextApps, kb: nextKb });
      } else {
        const hubApps = settings.filters?.apps ?? [];
        const groups = settings.filters?.kb ?? [];
        if (hubApps.includes(id)) {
          setFilters({
            ...settings.filters,
            apps: hubApps.filter((aid) => aid !== id),
          });
        } else {
          setFilters({
            ...settings.filters,
            kb: groups.filter((gid) => gid !== id),
          });
        }
      }
    },
    [isAgentChat, agentKnowledgeScope, agentKnowledgeDefaults, setAgentKnowledgeScope, settings.filters, setFilters]
  );

  // Toolbar icon color follows the active query mode so it stays consistent with ModeSwitcher.
  const activeIconColor = isSearchMode
    ? 'var(--mode-search-icon)'
    : modeColors.icon;

  const activeToggleColor = isSearchMode
    ? 'var(--mode-search-toggle)'
    : modeColors.toggle;

  // ── Message action command handlers ──────────────────────────────
  // Both handlers are registered on the global command bus (useCommandStore) so
  // ChatResponse / MessageActions can trigger them without prop drilling.

  // Regenerate: closes all panels, sets activeMessageAction, and pre-fills the
  // textarea with the original question text (dispatched from message-actions.tsx
  // as { messageId, text: question }).
  const handleShowRegenBar = useCallback((payload?: unknown) => {
    if (typeof payload !== 'object' || payload === null) return;
    const { messageId, text, appliedFilters } = payload as { messageId: string; text?: string; appliedFilters?: AppliedFilters };
    if (!messageId) return;
    dismissExpansionPanels();
    setRegenModelOverride(null);
    setActiveMessageAction({ type: 'regenerate', messageId, appliedFilters });
    // Pre-fill textarea so user can see what will be regenerated (shown dimmed/disabled)
    setMessage(text ?? '');
  }, [dismissExpansionPanels]);

  // Edit query: same as regenerate but the textarea is editable so the user can
  // amend the question before resending. Also focuses the textarea immediately.
  const handleShowEditQuery = useCallback((payload?: unknown) => {
    if (
      typeof payload !== 'object' ||
      payload === null ||
      typeof (payload as Record<string, unknown>).messageId !== 'string'
    ) return;
    const { messageId, text } = payload as { messageId: string; text: string };
    dismissExpansionPanels();
    setRegenModelOverride(null);
    setActiveMessageAction({ type: 'editQuery', messageId, text });
    // Populate the textarea with the original question so the user can edit it
    setMessage(text ?? '');
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [dismissExpansionPanels]);

  // Dismissing either action clears the pill bar and resets the textarea to empty.
  const handleDismissAction = useCallback(() => {
    setActiveMessageAction(null);
    setRegenModelOverride(null);
    setMessage('');
  }, []);

  // Register showRegenBar / showEditQuery commands
  useEffect(() => {
    const { register, unregister } = useCommandStore.getState();
    register('showRegenBar', handleShowRegenBar);
    register('showEditQuery', handleShowEditQuery);
    return () => {
      unregister('showRegenBar');
      unregister('showEditQuery');
    };
  }, [handleShowRegenBar, handleShowEditQuery]);

  // Dismiss message action on slot switch
  useEffect(() => {
    setActiveMessageAction(null);
    setRegenModelOverride(null);
  }, [activeSlotId]);

  // ── Execute message action (regenerate or edit query) ──
  const executeMessageAction = useCallback((_editedText?: string) => {
    if (!activeMessageAction) return;

    if (activeMessageAction.type === 'regenerate') {
      const modelOverride = regenModelOverride ?? undefined;
      const af = activeMessageAction.appliedFilters;
      const originalFilters = af
        ? { apps: af.apps.map((a) => a.id), kb: af.kb.map((k) => k.id) }
        : undefined;
      setActiveMessageAction(null);
      setRegenModelOverride(null);
      if (activeSlotId) {
        streamRegenerateForSlot(activeSlotId, activeMessageAction.messageId, modelOverride, originalFilters);
      }
      return;
    }

    if (activeMessageAction.type === 'editQuery') {
      toast.info(t('chat.toasts.editComingSoonTitle'), {
        description: t('chat.toasts.editComingSoonDescription'),
      });
      setActiveMessageAction(null);
      setRegenModelOverride(null);
      return;
    }
  }, [activeMessageAction, regenModelOverride, activeSlotId, t]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (isListening) stopSpeech();

    if (isStreaming || isUniversalAgentLoading) return;
    // Block submit while any chip is still uploading — every chip must be
    // either `uploaded` (forwarded as a ref) or removed by the user before
    // we hand off to the runtime.
    if (uploadedFiles.some((f) => f.status === 'uploading')) return;

    // ── Message action intercept ──────────────────────────────
    if (activeMessageAction) {
      executeMessageAction();
      return;
    }

    // ── Agent tool validation ─────────────────────────────────
    const isUrlAgent = Boolean(agentId);
    const isUniversalAgentMode = !agentId && settings.queryMode === 'agent';
    if (isUrlAgent || isUniversalAgentMode) {
      const groups = isUniversalAgentMode ? universalAgentToolGroups : agentChatToolGroups;
      const toolsSel = isUniversalAgentMode ? universalAgentStreamTools : agentStreamToolsSel;

      const stripPrefix = (key: string) => {
        const colon = key.indexOf(':');
        return colon >= 0 ? key.slice(colon + 1) : key;
      };

      // Count resolved (stripped + deduped) tools — mirrors the wire format
      // in runtime.ts where prefixed keys are stripped then deduped via Set.
      const resolvedCount =
        toolsSel === null
          ? new Set(groups.flatMap((g) => g.fullNames).map(stripPrefix)).size
          : new Set(toolsSel.map(stripPrefix)).size;

      if (resolvedCount > 128) {
        toast.error(
          t('chat.toolValidation.tooManyTools', {
            defaultValue:
              'Too many tools selected. Maximum 128 tools are allowed per request due to performance limits.',
          })
        );
        return;
      }

      // Detect multiple selected instances of the same toolset type.
      // Key format differs by mode:
      //   universal agent  → `${instanceId}:${fullName}` (prefixed)
      //   URL-scoped agent → bare `fullName`
      //
      // When toolsSel === null (default "all tools") and the groups list is empty
      // (panel hasn't loaded yet), skip the check — the user hasn't had a chance
      // to curate, and blocking without an actionable path is confusing.
      const instanceCountBySlug = new Map<string, number>();
      if (toolsSel === null) {
        if (groups.length === 0) {
          // Groups not loaded yet — let the request through; the backend will
          // use its own full set and handle any conflicts server-side.
        } else {
          for (const group of groups) {
            instanceCountBySlug.set(
              group.toolsetSlug,
              (instanceCountBySlug.get(group.toolsetSlug) ?? 0) + 1
            );
          }
        }
      } else {
        const selectedKeys = new Set(toolsSel);
        for (const group of groups) {
          const hasSelected = isUniversalAgentMode
            // Universal: keys are `${instanceId}:${fullName}`
            ? group.fullNames.some((fn) => selectedKeys.has(`${group.instanceId ?? ''}:${fn}`))
            // URL-scoped: keys are bare fullNames
            : group.fullNames.some((fn) => selectedKeys.has(fn));
          if (hasSelected) {
            instanceCountBySlug.set(
              group.toolsetSlug,
              (instanceCountBySlug.get(group.toolsetSlug) ?? 0) + 1
            );
          }
        }
      }
      const multiTypes = [...instanceCountBySlug.entries()]
        .filter(([, n]) => n > 1)
        .map(([slug]) => slug);
      if (multiTypes.length > 0) {
        const typeNames = multiTypes.join(', ');
        toast.error(
          t('chat.toolValidation.multipleInstances', {
            types: typeNames,
            defaultValue:
              `Multiple instances of the same action type (${typeNames}) cannot be used together. Open the Actions panel and select only one instance per type.`,
          })
        );
        return;
      }
    }

    // ── Normal send flow ──────────────────────────────────────
    // Only forward chips whose upload completed successfully. Errored
    // chips are dropped silently here — `canSubmit` lets them through
    // (otherwise the send button would be stuck), but the user has
    // already seen a toast per failed upload and the chip exposes a
    // retry icon if they want to recover.
    if ((message.trim() || uploadedFiles.length > 0) && onSend) {
      const refs = uploadedFiles
        .filter((f) => f.status === 'uploaded' && f.ref)
        .map((f) => f.ref!);
      onSend(message, refs.length > 0 ? refs : undefined);
      setMessage('');
      setUploadedFiles([]);
      setShowUploadArea(false);
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Escape cancels whichever action mode is active (edit or regenerate)
    if (e.key === 'Escape' && isActionMode) {
      handleDismissAction();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  /**
   * Fire the upload for a single chip. Called both on initial add and on
   * retry. The chip MUST already exist in `uploadedFiles` — we only flip
   * status and store the ref / error.
   *
   * On abort (user removed the chip mid-flight) we silently swallow the
   * error — the chip is already gone from state, no UX needed.
   */
  const startUpload = useCallback((file: UploadedFile) => {
    if (!onUploadFile) return;

    // Replace any prior controller for this id (e.g. retry after error).
    const prevCtrl = uploadControllersRef.current.get(file.id);
    if (prevCtrl) prevCtrl.abort();
    const controller = new AbortController();
    uploadControllersRef.current.set(file.id, controller);

    setUploadedFiles((prev) =>
      prev.map((f) =>
        f.id === file.id ? { ...f, status: 'uploading', errorMessage: undefined } : f,
      ),
    );

    onUploadFile(file.file, controller.signal)
      .then((ref) => {
        if (controller.signal.aborted) return;
        setUploadedFiles((prev) =>
          prev.map((f) => (f.id === file.id ? { ...f, status: 'uploaded', ref } : f)),
        );
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const errorMessage =
          (err as { message?: string })?.message ??
          t('chat.attachments.uploadFailed', { defaultValue: 'Upload failed' });
        setUploadedFiles((prev) =>
          prev.map((f) =>
            f.id === file.id ? { ...f, status: 'error', errorMessage, ref: undefined } : f,
          ),
        );
        toast.error(
          t('chat.attachments.uploadFailedNamed', {
            defaultValue: `Failed to upload ${file.name}: ${errorMessage}`,
          }),
        );
      })
      .finally(() => {
        if (uploadControllersRef.current.get(file.id) === controller) {
          uploadControllersRef.current.delete(file.id);
        }
      });
  }, [onUploadFile, t]);

  const processFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);

    const typeValid: File[] = [];
    const typeRejected: File[] = [];
    for (const f of fileArray) {
      if (isFileTypeSupported(f)) {
        typeValid.push(f);
      } else {
        typeRejected.push(f);
      }
    }
    if (typeRejected.length > 0) {
      toast.error(
        t('chat.attachments.unsupportedType', {
          defaultValue: `Unsupported file type: ${typeRejected.map((f) => f.name).join(', ')}. Only PDF, JPEG, and PNG files are supported.`,
        })
      );
    }

    const sizeValid: File[] = [];
    const sizeRejected: File[] = [];
    for (const f of typeValid) {
      if (f.size > CHAT_ATTACHMENT_MAX_BYTES) {
        sizeRejected.push(f);
      } else {
        sizeValid.push(f);
      }
    }
    if (sizeRejected.length > 0) {
      toast.error(
        t('chat.attachments.fileTooLarge', {
          defaultValue: `File too large: ${sizeRejected.map((f) => f.name).join(', ')}. Maximum size is ${Math.round(CHAT_ATTACHMENT_MAX_BYTES / (1024 * 1024))} MB per file.`,
        })
      );
    }

    if (sizeValid.length === 0) return;

    // Compute the new chips entirely outside of any setState call so the
    // arrays are stable — React Strict Mode calls updater functions twice
    // to detect impure updaters, which would push into `newFiles` twice and
    // produce duplicate chips and duplicate upload calls.
    const currentCount = uploadedFiles.length;
    const remaining = CHAT_ATTACHMENT_MAX_FILES - currentCount;
    if (remaining <= 0) return;

    const toAdd = sizeValid.slice(0, remaining);
    if (toAdd.length < sizeValid.length) {
      toast.error(
        t('chat.attachments.tooManyFiles', {
          defaultValue: `Maximum ${CHAT_ATTACHMENT_MAX_FILES} attachments per message.`,
        })
      );
    }

    const newFiles: UploadedFile[] = toAdd.map((file) => ({
      id: `${file.name}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
      file,
      name: file.name,
      size: file.size,
      type: file.type,
      status: 'uploading' as const,
    }));

    // Pure state update — no side effects inside the updater.
    setUploadedFiles((prev) => [...prev, ...newFiles]);

    // Kick off uploads after the state update — never inside React's reducer.
    for (const file of newFiles) {
      startUpload(file);
    }

    setShowUploadArea(false);
  }, [t, startUpload, uploadedFiles]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      processFiles(e.target.files);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files);
    }
  };

  const removeFile = useCallback((fileId: string) => {
    // Abort any in-flight upload for this chip so the network call and its
    // `.then`/`.catch` handlers don't write back into state after the chip is gone.
    const ctrl = uploadControllersRef.current.get(fileId);
    if (ctrl) {
      ctrl.abort();
      uploadControllersRef.current.delete(fileId);
    }

    // Capture the record ref before setState — Strict Mode runs updaters twice,
    // so any side effect inside an updater fires twice.
    const fileToDelete = uploadedFiles.find((f) => f.id === fileId);

    // Pure state update — remove the chip.
    setUploadedFiles((prev) => prev.filter((f) => f.id !== fileId));

    // Fire-and-forget server delete outside the updater.
    if (fileToDelete?.status === 'uploaded' && fileToDelete.ref?.recordId && onDeleteFile) {
      onDeleteFile(fileToDelete.ref.recordId);
    }
  }, [uploadedFiles, onDeleteFile]);

  const retryFile = useCallback((fileId: string) => {
    // Find the target outside the updater — Strict Mode runs updaters twice
    // which would trigger two upload calls for the same retry.
    const target = uploadedFiles.find((f) => f.id === fileId);
    if (target) startUpload(target);
  }, [uploadedFiles, startUpload]);

  /**
   * Handle Ctrl+V / paste events.
   *
   * Only clipboard items of kind `'file'` with a supported MIME type are
   * intercepted. Plain-text pastes continue to work normally — we only call
   * `e.preventDefault()` when we actually consume file items so that normal
   * text pasting is never disrupted.
   *
   * File pastes use the same gating as the attach control: enterprise search
   * (`mode === 'search'`) and web search do not accept attachments.
   */
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (isRegenerateMode || isSearchMode || settings.queryMode === 'web-search') {
      return;
    }
    const items = e.clipboardData?.items;
    if (!items) return;

    const fileItems: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file && isFileTypeSupported(file)) {
          // Keep the original filename when the browser provides one.
          // Screenshot/copy-image pastes typically arrive with an empty name
          // or a bare extension-less name — only generate a fallback in those
          // cases so user-copied files preserve their real names.
          const hasRealName = file.name && file.name.trim() !== '' && file.name !== 'image';
          if (hasRealName) {
            fileItems.push(file);
          } else {
            const ext =
              file.type === 'application/pdf'
                ? 'pdf'
                : file.type === 'image/png'
                  ? 'png'
                  : 'jpg';
            const named = new File([file], `pasted-${Date.now()}.${ext}`, {
              type: file.type,
            });
            fileItems.push(named);
          }
        }
      }
    }

    if (fileItems.length > 0) {
      // Stop the event here — without this, bubbling causes the handler to fire
      // once for each ancestor that also has onPaste registered, producing
      // duplicate chips for the same paste action.
      e.stopPropagation();
      // Prevent the browser from trying to render the raw image data as text.
      e.preventDefault();
      processFiles(fileItems);
    }
  }, [processFiles, isRegenerateMode, isSearchMode, settings.queryMode]);

  // Abort any still-pending uploads on unmount so we don't write back into
  // a destroyed component's state when the network finally responds.
  useEffect(() => {
    const controllers = uploadControllersRef.current;
    return () => {
      for (const ctrl of controllers.values()) ctrl.abort();
      controllers.clear();
    };
  }, []);

  // Keep scroll-arrow visibility in sync with the chips container scroll state.
  const updateChipsScrollState = useCallback(() => {
    const el = chipsScrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  // Re-check whenever the file list changes (chips added / removed).
  useEffect(() => {
    updateChipsScrollState();
  }, [uploadedFiles, updateChipsScrollState]);

  const scrollChips = useCallback((direction: 'left' | 'right') => {
    const el = chipsScrollRef.current;
    if (!el) return;
    el.scrollBy({ left: direction === 'left' ? -220 : 220, behavior: 'smooth' });
  }, []);

  const toggleUploadArea = () => {
    const next = !showUploadArea;
    if (next) {
      // Close all other panels before opening the upload area
      setIsModePanelOpen(false);
      setIsAgentStrategyPanelOpen(false);
      setIsCollectionsPanelOpen(false);
      setIsAgentResourcesPanelOpen(false);
      setIsModelPanelOpen(false);
      setExpansionViewMode('inline');
    }
    setShowUploadArea(next);
  };

  const hasContent = message.trim() || uploadedFiles.length > 0 || isListening;
  const hasUploadingAttachments = uploadedFiles.some((f) => f.status === 'uploading');
  const canSubmit =
    (hasContent || activeMessageAction !== null) &&
    !isUniversalAgentLoading &&
    !hasUploadingAttachments;

  // Display value combines committed text with interim speech so users see real-time feedback
  const displayValue = interimTranscript
    ? message + (message.length > 0 ? ' ' : '') + interimTranscript
    : message;

  const speechTooltip =
    speechUnavailableReason === 'stt-not-configured'
      ? t('chat.voiceSttNotConfigured', {
          defaultValue:
            'Configure a Speech-to-Text (STT) model in AI Models settings to enable voice input.',
        })
      : speechUnavailableReason === 'stt-loading'
        ? t('chat.voiceSttLoading', {
            defaultValue: 'Checking speech capabilities…',
          })
        : !isSpeechSupported
          ? t('chat.voiceInputNotSupported')
          : isListening
            ? t('chat.listening')
            : t('chat.micTooltip');
  const isSpeechButtonDisabled = isRegenerateMode || !isSpeechSupported;

  // Close panels on outside click
  useEffect(() => {
    if (!modeChromeOpen && !isCollectionsPanelOpen && !isModelPanelOpen && !showUploadArea) return;
    function handleClickOutside(e: MouseEvent) {
      if (expansionViewMode === 'overlay') return;

      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        dismissExpansionPanelsRef.current();
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [modeChromeOpen, isCollectionsPanelOpen, isModelPanelOpen, showUploadArea, expansionViewMode]);

  const handleExpand = () => {
    if (expandable && !isExpanded) {
      setIsAnimatingIn(true);
      setIsExpanded(true);
    }
  };

  // Auto-focus the textarea when expanding from widget to full
  useEffect(() => {
    if (isExpanded && variant === 'widget' && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isExpanded, variant]);

  useEffect(() => {
    if (isAgentChat) {
      setIsModePanelOpen(false);
    } else {
      setIsAgentStrategyPanelOpen(false);
      setIsAgentResourcesPanelOpen(false);
    }
  }, [isAgentChat]);

  // Dismiss collections chrome when it no longer applies (stale panel / overlay).
  const prevQueryModeRef = useRef(settings.queryMode);
  useEffect(() => {
    const prev = prevQueryModeRef.current;
    prevQueryModeRef.current = settings.queryMode;
    if (!isCollectionsPanelOpen) return;
    if ((prev === 'agent' && settings.queryMode !== 'agent') || settings.queryMode === 'web-search') {
      setIsCollectionsPanelOpen(false);
      setExpansionViewMode('inline');
    }
  }, [settings.queryMode, isCollectionsPanelOpen, setExpansionViewMode]);

  if (!showFullUI) {
    return (
      <Flex
        direction="column"
        gap="4"
        style={{
          background:'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          backdropFilter: 'blur(25px)',
          borderRadius: 'var(--radius-1)',
          padding: 'var(--space-1)',
        }}
      >
        {/* Single row: mode-switcher + input + send */}
        <Flex align="center" justify="between" gap="3">
          {isAgentChat ? (
            <AgentStrategyModeSwitcher
              activeStrategy={settings.agentStrategy}
              modeColors={agentStrategyToolbarColors}
              isPanelOpen={false}
              showFullUI={false}
              onClick={handleExpand}
            />
          ) : (
            <ModeSwitcher
              activeQueryConfig={activeQueryConfig}
              modeColors={modeColors}
              isSearchMode={isSearchMode}
              isModePanelOpen={false}
              showFullUI={false}
              onLeftClick={handleExpand}
              onRightClick={handleExpand}
            />
          )}

          {/* Input field */}
          <input
            type="text"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={handleExpand}
            placeholder={resolvedWidgetPlaceholder}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: 'var(--slate-12)',
              fontSize: 'var(--font-size-2)',
              fontFamily: 'Manrope, sans-serif',
              minWidth: 0,
              textOverflow: 'ellipsis',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
            }}
          />

          {/* Send / Stop — same contract as full composer */}
          {isStreaming ? (
            <IconButton
              variant="solid"
              size="2"
              onClick={handleStopStream}
              style={{
                margin: 0,
                backgroundColor: activeToggleColor,
              }}
            >
              <MaterialIcon name="stop" size={ICON_SIZES.PRIMARY} color="white" />
            </IconButton>
          ) : (
            <IconButton
              variant="solid"
              size="2"
              onClick={handleSubmit}
              disabled={!canSubmit}
              style={{
                margin: 0,
                backgroundColor: canSubmit ? activeToggleColor : 'var(--slate-a3)',
              }}
            >
              <MaterialIcon
                name="arrow_upward"
                size={ICON_SIZES.PRIMARY}
                color={canSubmit ? 'white' : 'var(--slate-a8)'}
              />
            </IconButton>
          )}
        </Flex>
      </Flex>
    );
  }

  return (
    <>
    <Flex
      ref={containerRef}
      direction="column"
      onAnimationEnd={() => setIsAnimatingIn(false)}
      onPaste={handlePaste}
      style={{
        width: isMobile ? '100%' : '50rem',
        fontFamily: 'Manrope, sans-serif',
        ...(isAnimatingIn && {
          animation: 'chatWidgetExpandIn 220ms ease-out',
        }),
      }}
    >
      {/* Selected Collection Cards — shown above the main input, matching Figma spec */}
      {showSelectedCollectionsRow && (
        <Flex
          align="center"
          style={{
            backgroundColor: 'var(--slate-1)',
            borderTop: '1px solid var(--slate-5)',
            borderLeft: '1px solid var(--slate-5)',
            borderRight: '1px solid var(--slate-5)',
            borderTopLeftRadius: 'var(--radius-1)',
            borderTopRightRadius: 'var(--radius-1)',
            padding: 'var(--space-2) var(--space-3)',
          }}
        >
          <SelectedCollections
            collections={selectedCollections}
            removable={!isRegenerateMode}
            onRemove={isRegenerateMode ? undefined : handleRemoveCollection}
          />
        </Flex>
      )}

      {/* Uploaded Files Preview — separate container above the main input, matching Figma spec */}
      {uploadedFiles.length > 0 && (
        <Flex
          align="center"
          style={{
            backgroundColor: 'var(--slate-1)',
            borderTop:
              showSelectedCollectionsRow
                ? 'none'
                : '1px solid var(--slate-5)',
            borderLeft: '1px solid var(--slate-5)',
            borderRight: '1px solid var(--slate-5)',
            borderTopLeftRadius:
              showSelectedCollectionsRow
                ? '0'
                : 'var(--radius-1)',
            borderTopRightRadius:
              showSelectedCollectionsRow
                ? '0'
                : 'var(--radius-1)',
            padding: 'var(--space-3) var(--space-4)',
            gap: 'var(--space-1)',
          }}
        >
          {/* Left scroll arrow */}
          {canScrollLeft && (
            <Box
              onClick={() => scrollChips('left')}
              style={{
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 24,
                height: 24,
                borderRadius: 'var(--radius-round)',
                backgroundColor: 'var(--slate-4)',
                cursor: 'pointer',
              }}
              aria-label="Scroll attachments left"
            >
              <MaterialIcon name="chevron_left" size={16} color="var(--slate-11)" />
            </Box>
          )}

          {/* Scrollable chips row */}
          <Box
            ref={chipsScrollRef}
            onScroll={updateChipsScrollState}
            style={{
              flex: 1,
              overflowX: 'auto',
              overflowY: 'hidden',
              scrollbarWidth: 'none',
            }}
            className="no-scrollbar"
          >
          <Flex gap="2" style={{ minWidth: 'max-content' }}>
            {uploadedFiles.map((file) => (
              <Box
                key={file.id}
                style={{
                  flexShrink: 0,
                  width: '196px',
                  padding: 'var(--space-2)',
                  backgroundColor: 'var(--olive-a2)',
                  border:
                    file.status === 'error'
                      ? '1px solid var(--red-7)'
                      : '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-1)',
                }}
              >
                <Flex direction="column" gap="2">
                  {/* Header: file icon + per-status action affordance.
                      - uploading: spinner replaces the close button.
                      - uploaded:  close button removes the chip (and signals
                        the server orphan via the future cleanup endpoint).
                      - error:     retry + close so the user can recover
                        without losing the other chips' completed uploads. */}
                  <Flex align="center" justify="between">
                    <FileIcon
                      extension={getMimeTypeExtension(file.type) || undefined}
                      filename={file.name}
                      size={16}
                      fallbackIcon="insert_drive_file"
                    />
                    {file.status === 'uploading' ? (
                      <Tooltip
                        content={t('chat.attachments.uploading', { defaultValue: 'Uploading…' })}
                        side="top"
                      >
                        <Box
                          aria-label={`Uploading ${file.name}`}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 20,
                            height: 20,
                            flexShrink: 0,
                          }}
                        >
                          <Spinner
                            size={14}
                            thickness={1.5}
                            color="var(--slate-11)"
                            ariaLabel={`Uploading ${file.name}`}
                          />
                        </Box>
                      </Tooltip>
                    ) : (
                      <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                        {file.status === 'error' && (
                          <Tooltip
                            content={t('chat.attachments.retry', { defaultValue: 'Retry upload' })}
                            side="top"
                          >
                            <IconButton
                              variant="ghost"
                              size="1"
                              onClick={() => retryFile(file.id)}
                              style={{ margin: 0, flexShrink: 0 }}
                              aria-label={`Retry uploading ${file.name}`}
                            >
                              <MaterialIcon
                                name="refresh"
                                size={ICON_SIZES.SECONDARY}
                                color="var(--red-11)"
                              />
                            </IconButton>
                          </Tooltip>
                        )}
                        <IconButton
                          variant="ghost"
                          size="1"
                          onClick={() => removeFile(file.id)}
                          style={{ margin: 0, flexShrink: 0 }}
                          aria-label={`Remove ${file.name}`}
                        >
                          <MaterialIcon
                            name="close"
                            size={ICON_SIZES.SECONDARY}
                            color="var(--slate-11)"
                          />
                        </IconButton>
                      </Flex>
                    )}
                  </Flex>

                  {/* Content: filename + size (or error message in red). */}
                  <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                    <Text
                      size="1"
                      weight="medium"
                      style={{
                        color: 'var(--slate-12)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap'
                      }}
                    >
                      {file.name}
                    </Text>
                    <Text
                      size="1"
                      style={{
                        color: file.status === 'error' ? 'var(--red-11)' : 'var(--slate-11)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {file.status === 'error'
                        ? file.errorMessage ||
                          t('chat.attachments.uploadFailed', { defaultValue: 'Upload failed' })
                        : formatFileSize(file.size)}
                    </Text>
                  </Flex>
                </Flex>
              </Box>
            ))}

            {/* Add Button */}
            <Box
              onClick={() => fileInputRef.current?.click()}
              style={{
                flexShrink: 0,
                width: '76px',
                border: '1px dashed var(--accent-9)',
                borderRadius: 'var(--radius-1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'background-color 0.15s',
                backgroundColor: isAddFileButtonHovered ? 'var(--accent-a2)' : 'transparent',
              }}
              onMouseEnter={() => setIsAddFileButtonHovered(true)}
              onMouseLeave={() => setIsAddFileButtonHovered(false)}
            >
              <MaterialIcon name="add" size={24} color="var(--accent-9)" />
            </Box>
          </Flex>
          </Box>

          {/* Right scroll arrow */}
          {canScrollRight && (
            <Box
              onClick={() => scrollChips('right')}
              style={{
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 24,
                height: 24,
                borderRadius: 'var(--radius-round)',
                backgroundColor: 'var(--slate-4)',
                cursor: 'pointer',
              }}
              aria-label="Scroll attachments right"
            >
              <MaterialIcon name="chevron_right" size={16} color="var(--slate-11)" />
            </Box>
          )}
        </Flex>
      )}

      {/* Action pill bar — sits above the main input container when edit or regenerate is active. */}
      {isActionMode && activeMessageAction && (
        <Flex
          style={{
            background: 'var(--olive-1)',
            borderTop: '1px solid var(--olive-5)',
            borderLeft: '1px solid var(--olive-5)',
            borderRight: '1px solid var(--olive-5)',
            borderTopLeftRadius: 'var(--radius-2)',
            borderTopRightRadius: 'var(--radius-2)',
            padding: 'var(--space-3) var(--space-4)',
          }}
        >
          <MessageActionIndicator
            action={activeMessageAction}
            onDismiss={handleDismissAction}
            onSubmit={() => {}}
          />
        </Flex>
      )}

      {/* Main Chat Input */}
      <Flex
      direction="column"
      gap="2"
      style={{
        backdropFilter: 'blur(25px)',
        background: (isInputFocused || message.trim() || isListening) ? 'var(--olive-2)' : 'var(--effects-translucent)',
        transition: 'background 0.15s ease',
        border: (!isStreaming && (isInputFocused || message.trim() || isEditMode || isListening)) ? '1px solid var(--accent-11)' : '1px solid var(--slate-3)',
        // Flatten top corners whenever there is an element directly above (collections bar,
        // uploaded files preview, or the action pill bar) to avoid a double-radius gap.
        borderRadius:
          (selectedCollections.length > 0 &&
            !isAgentChat &&
            !isCollectionsPanelOpen &&
            !modeChromeOpen) ||
          uploadedFiles.length > 0 ||
          isActionMode
            ? '0 0 var(--radius-2) var(--radius-2)'
            : 'var(--radius-2)',
        padding: isMobile ? 'var(--space-3) var(--space-4)' : 'var(--space-2) var(--space-4)',
      }}
    >
      {/* Hidden file input - always rendered so add button can access it */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={[
          ...Object.keys(ACCEPTED_MIME_TYPES),
          ...ACCEPTED_EXTENSIONS.map((e) => `.${e}`),
        ].join(',')}
        onChange={handleFileSelect}
        style={{ display: 'none' }}
      />

      {/* Upload Area */}
      {showUploadArea && (
        <Flex direction="column" gap="2">
          <Text size="2" style={{ color: 'var(--slate-12)' }}>{t('chat.uploadYourFile')}</Text>
          <Box
            style={{
              position: 'relative',
              border: `1px dashed ${isDragging ? 'var(--accent-11)' : 'var(--slate-9)'}`,
              borderRadius: 'var(--radius-4)',
              padding: 'var(--space-7)',
              transition: 'all 0.15s',
              backgroundColor: isDragging ? 'var(--accent-a3)' : 'transparent',
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <Flex direction="column" align="center" gap="1">
              <MaterialIcon name="add" size={24} color="var(--slate-9)" />
              <Box style={{ textAlign: 'center' }}>
                <Text size="2" style={{ color: 'var(--slate-12)' }}>{t('action.upload')}</Text>
                <br />
              </Box>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {t('chat.supportsFileTypes', { types: SUPPORTED_FILE_TYPES.join(', ') })}
              </Text>
            </Flex>
          </Box>
        </Flex>
      )}

      {/* Input or expansion panel (mutually exclusive) */}
      {isAgentChat && isAgentStrategyPanelOpen ? (
        <ChatInputExpansionPanel
          open={isAgentStrategyPanelOpen}
          onClose={() => setIsAgentStrategyPanelOpen(false)}
          minHeight="0px"
          height="fit-content"
        >
          <AgentStrategyModePanel
            activeStrategy={settings.agentStrategy}
            onSelect={(strategy) => {
              setAgentStrategy(strategy);
              setIsAgentStrategyPanelOpen(false);
            }}
          />
        </ChatInputExpansionPanel>
      ) : isModePanelOpen && !isAgentChat ? (
        <ChatInputExpansionPanel
          open={isModePanelOpen}
          onClose={() => setIsModePanelOpen(false)}
          minHeight='0'
          height='fit-content'
        >
          <QueryModePanel
            activeMode={settings.queryMode}
            onSelect={(queryMode) => {
              setQueryMode(queryMode);
              if (isSearchMode) {
                setMode('chat');
              }
              setIsModePanelOpen(false);
            }}
          />
        </ChatInputExpansionPanel>
      ) : isModelPanelOpen ? (
        <ChatInputExpansionPanel
          open={isModelPanelOpen}
          onClose={() => setIsModelPanelOpen(false)}
        >
          <ModelSelectorPanel
            selectedModel={contextSelectedModel ?? contextDefaultModel}
            onModelSelect={handleModelSelect}
            agentId={agentId}
          />
        </ChatInputExpansionPanel>
      ) : isAgentChat && isAgentResourcesPanelOpen && expansionViewMode === 'inline' ? (
        <ChatInputExpansionPanel
          open={isAgentResourcesPanelOpen}
          onClose={() => {
            setIsAgentResourcesPanelOpen(false);
            setExpansionViewMode('inline');
          }}
        >
          <AgentScopedResourcesPanel viewMode="inline" onToggleView={handleToggleView} />
        </ChatInputExpansionPanel>
      ) : !isAgentChat && settings.queryMode === 'agent' && isCollectionsPanelOpen && expansionViewMode === 'inline' ? (
        <ChatInputExpansionPanel
          open={isCollectionsPanelOpen}
          onClose={() => {
            setIsCollectionsPanelOpen(false);
            setExpansionViewMode('inline');
          }}
        >
          <UniversalAgentResourcesPanel viewMode="inline" onToggleView={handleToggleView} />
        </ChatInputExpansionPanel>
      ) : hubFilterQueryMode && isCollectionsPanelOpen && expansionViewMode === 'inline' ? (
        <ChatInputExpansionPanel
          open={isCollectionsPanelOpen}
          onClose={() => {
            setIsCollectionsPanelOpen(false);
            setExpansionViewMode('inline');
          }}
        >
          <ConnectorsCollectionsPanel
            apps={settings.filters?.apps ?? []}
            kb={settings.filters?.kb ?? []}
            onSelectionChange={(next) => {
              setFilters({
                ...settings.filters,
                apps: next.apps,
                kb: next.kb,
              });
            }}
            viewMode="inline"
            onToggleView={handleToggleView}
          />
        </ChatInputExpansionPanel>
      ) : ((isAgentChat && isAgentResourcesPanelOpen) || assistantCollectionsOverlayActive) &&
        expansionViewMode === 'overlay' ? (
        /* Render textarea underneath while overlay is open */
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsInputFocused(true)}
          onBlur={() => setIsInputFocused(false)}
          placeholder={resolvedPlaceholder}
          rows={1}
          style={{
            width: '100%',
            backgroundColor: 'transparent',
            outline: 'none',
            border: 'none',
            fontSize: 'var(--font-size-2)',
            color: 'var(--slate-11)',
            resize: 'none',
            minHeight: '24px',
            maxHeight: '120px',
            fontFamily: 'Manrope, sans-serif',
            height: 'auto',
            overflow: 'auto',
          }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = 'auto';
            target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
          }}
        />
      ) : !showUploadArea || isActionMode ? (
        // isActionMode keeps the textarea visible even when showUploadArea is true,
        // so the user can see / edit their query during edit or regenerate flows.
        // In regenerate mode the textarea is disabled and text is rendered dimmed;
        // in edit mode it is fully editable (focused immediately on activation).
        <textarea
          ref={textareaRef}
          value={displayValue}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsInputFocused(true)}
          onBlur={() => setIsInputFocused(false)}
          placeholder={isListening ? t('chat.listening') : resolvedPlaceholder}
          disabled={isRegenerateMode}
          rows={1}
          style={{
            width: '100%',
            backgroundColor: 'transparent',
            outline: 'none',
            border: 'none',
            fontSize: 'var(--font-size-2)',
            color: isRegenerateMode ? 'var(--slate-a8)' : 'var(--slate-12)',
            resize: 'none',
            minHeight: isMobile ? '36px' : '44px',
            maxHeight: '120px',
            fontFamily: 'Manrope, sans-serif',
            height: 'auto',
            overflow: 'auto',
          }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = 'auto';
            target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
          }}
        />
      ) : null}

      {/* Bottom controls */}
      <Flex align="center" justify="between">
        {/* Left side — query ModeSwitcher disabled in regenerate (avoid mode churn); agent strategy stays active so regen can use quick/verify/deep. */}
        <Box style={isRegenerateMode && !isAgentChat ? { opacity: 0.5, pointerEvents: 'none' } : undefined}>
          {isAgentChat ? (
            <AgentStrategyModeSwitcher
              activeStrategy={settings.agentStrategy}
              modeColors={agentStrategyToolbarColors}
              isPanelOpen={isMobile ? isMobileModesOpen : isAgentStrategyPanelOpen}
              showFullUI={showFullUI}
              onClick={() => {
                if (isMobile) {
                  setIsMobileModesOpen(true);
                  return;
                }
                setIsAgentStrategyPanelOpen((prev) => !prev);
                setIsCollectionsPanelOpen(false);
                setIsAgentResourcesPanelOpen(false);
                setIsModelPanelOpen(false);
                setShowUploadArea(false);
              }}
            />
          ) : (
            <ModeSwitcher
              activeQueryConfig={activeQueryConfig}
              modeColors={modeColors}
              isSearchMode={isSearchMode}
              isModePanelOpen={isModePanelOpen}
              showFullUI={showFullUI}
              onLeftClick={
                isSearchMode
                  ? () => {
                      setMode('chat');
                      useChatStore.getState().clearSearchResults();
                    }
                  : isMobile
                    ? () => setIsMobileModesOpen(true)
                    : () => {
                        setIsModePanelOpen((prev) => !prev);
                        setIsAgentStrategyPanelOpen(false);
                        setIsCollectionsPanelOpen(false);
                        setIsAgentResourcesPanelOpen(false);
                        setShowUploadArea(false);
                      }
              }
              onRightClick={
                isSearchMode
                  ? () => {}
                  : () => {
                      useCommandStore.getState().dispatch('newChat');
                      setMode('search');
                      setIsModePanelOpen(false);
                    }
              }
            />
          )}
        </Box>

        {/* Right side - Controls */}
        <Flex align="center" gap="2">
          {isMobile ? (
            /* Mobile: meatball opens bottom sheet; attach_file and mic stay inline. */
            <Flex align="center" gap="1">
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={() => setIsMobileOptionsOpen(true)}
                style={{ margin: 0, cursor: 'pointer' }}
              >
                <MaterialIcon name="more_horiz" size={ICON_SIZES.PRIMARY} color={activeIconColor} />
              </IconButton>
              <SpeechInputButton
                tooltip={speechTooltip}
                ariaLabel={speechTooltip}
                isListening={isListening}
                isSupported={isSpeechSupported}
                isDisabled={isSpeechButtonDisabled}
                isRegenerateMode={isRegenerateMode}
                activeIconColor={activeIconColor}
                onToggle={toggleSpeech}
              />
            </Flex>
          ) : (
            /* Desktop: full controls */
            <>
              {settings.queryMode === 'agent' && !isAgentChat ? (
                <AgentStrategyDropdown
                  value={settings.agentStrategy}
                  onChange={setAgentStrategy}
                  accentColor={activeToggleColor}
                />
              ) : null}

              {/* Action buttons group */}
              <Flex align="center" gap="1">
                  
                {!isAgentChat && settings.queryMode !== 'web-search' ? (
                  <Tooltip
                  content={
                    settings.queryMode === 'agent'
                      ? t('chat.agentResourcesTooltip', { defaultValue: 'Connectors, collections & actions' })
                      : t('chat.connectorsTooltip')
                  }
                  side="top"
                >
                    <IconButton
                      variant={
                        isCollectionsPanelOpen ||
                        (settings.queryMode === 'agent'
                          ? universalAgentResourcesCustomized
                          : selectedKbCount > 0)
                          ? 'soft'
                          : 'ghost'
                      }
                      color="gray"
                      size="2"
                      disabled={isRegenerateMode}
                      onClick={() => {
                        setIsCollectionsPanelOpen((prev) => {
                          if (prev) setExpansionViewMode('inline');
                          return !prev;
                        });
                        setIsModePanelOpen(false);
                        setIsAgentStrategyPanelOpen(false);
                        setIsModelPanelOpen(false);
                        setShowUploadArea(false);
                      }}
                      style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer' }}
                    >
                      <MaterialIcon name="apps" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
                    </IconButton>
                  </Tooltip>
                ) : isAgentChat ? (
                  <Tooltip content={t('chat.agentResourcesTooltip')} side="top">
                    <IconButton
                      variant={
                        isAgentResourcesPanelOpen || agentResourcesCustomized ? 'soft' : 'ghost'
                      }
                      color="gray"
                      size="2"
                      disabled={isRegenerateMode}
                      onClick={() => {
                        setIsAgentResourcesPanelOpen((prev) => {
                          if (prev) setExpansionViewMode('inline');
                          return !prev;
                        });
                        setIsModePanelOpen(false);
                        setIsAgentStrategyPanelOpen(false);
                        setIsCollectionsPanelOpen(false);
                        setIsModelPanelOpen(false);
                        setShowUploadArea(false);
                      }}
                      style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer' }}
                    >
                      <MaterialIcon name="apps" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
                    </IconButton>
                  </Tooltip>
                ) : null}
                {/* Attach file button */}
                {!isSearchMode && settings.queryMode !== 'web-search' && (
                  <Tooltip content={t('chat.attachmentTooltip', { defaultValue: 'Attach file' })} side="top">
                    <IconButton
                      variant={showUploadArea ? 'soft' : 'ghost'}
                      color="gray"
                      size="2"
                      disabled={isRegenerateMode}
                      onClick={toggleUploadArea}
                      style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer', '--accent-a3': modeColors.bg } as React.CSSProperties}
                    >
                      <MaterialIcon name="attach_file" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
                    </IconButton>
                  </Tooltip>
                )}
                {/* Model selector button — icon + current model name so the active model is always visible */}
                <Tooltip content={t('chat.aiModelsTooltip')} side="top">
                  <Flex
                    align="center"
                    gap="2"
                    onClick={() => {
                      const next = !isModelPanelOpen;
                      dismissExpansionPanels();
                      setIsModelPanelOpen(next);
                    }}
                    style={{
                      height: '32px',
                      paddingLeft: 'var(--space-2)',
                      paddingRight: 'var(--space-2)',
                      borderRadius: 'var(--radius-2)',
                      cursor: 'pointer',
                      backgroundColor: isModelPanelOpen
                        ? 'var(--olive-4)'
                        : isModelButtonHovered
                          ? 'var(--olive-3)'
                          : 'transparent',
                      transition: 'background-color 0.12s ease',
                      maxWidth: isMobile ? '32px' : '180px',
                      flexShrink: 0,
                    }}
                    onMouseEnter={() => setIsModelButtonHovered(true)}
                    onMouseLeave={() => setIsModelButtonHovered(false)}
                  >
                    <MaterialIcon name="memory" size={ICON_SIZES.PRIMARY} color={activeIconColor} />
                    {!isMobile && (
                      <Text
                        size="1"
                        weight="medium"
                        style={{
                          color: activeIconColor,
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          maxWidth: '140px',
                          opacity: displayModel ? 1 : 0.7,
                        }}
                      >
                        {displayModelLabel}
                      </Text>
                    )}
                  </Flex>
                </Tooltip>
                <SpeechInputButton
                  tooltip={speechTooltip}
                  ariaLabel={speechTooltip}
                  isListening={isListening}
                  isSupported={isSpeechSupported}
                  isDisabled={isSpeechButtonDisabled}
                  isRegenerateMode={isRegenerateMode}
                  activeIconColor={activeIconColor}
                  onToggle={toggleSpeech}
                  style={{ '--accent-a3': modeColors.bg } as React.CSSProperties}
                />
              </Flex>
            </>
          )}

          {/* Send / Stop button */}
          {isStreaming ? (
            <IconButton
              variant="solid"
              size="2"
              onClick={handleStopStream}
              style={{
                margin: 0,
                backgroundColor: activeToggleColor,
              }}
            >
              <MaterialIcon
                name="stop"
                size={ICON_SIZES.PRIMARY}
                color="white"
              />
            </IconButton>
          ) : (
            <IconButton
              variant="solid"
              size="2"
              onClick={handleSubmit}
              disabled={!canSubmit}
              style={{
                margin: 0,
                backgroundColor: canSubmit ? activeToggleColor : 'var(--slate-a3)',
              }}
            >
              <MaterialIcon
                name="arrow_upward"
                size={ICON_SIZES.PRIMARY}
                color={canSubmit ? 'white' : 'var(--slate-a8)'}
              />
            </IconButton>
          )}
        </Flex>
      </Flex>
    </Flex>
    </Flex>

    {/* Mobile query options sheet — meatball → sheet flow */}
    <MobileQueryOptionsSheet
      open={isMobileOptionsOpen}
      onOpenChange={setIsMobileOptionsOpen}
      isAgentChat={isAgentChat}
      agentId={agentId}
    />

    {/* Mobile query modes sheet — mode switcher → sheet flow */}
    <MobileQueryModesSheet
      open={isMobileModesOpen}
      onOpenChange={setIsMobileModesOpen}
      agentChat={isAgentChat}
    />

    {/* Overlay panel — collections (assistant) or agent resources (overlay mode) */}
    <ChatInputOverlayPanel
      open={
        expansionViewMode === 'overlay' &&
        (assistantCollectionsOverlayActive || (isAgentChat && isAgentResourcesPanelOpen))
      }
      onCollapse={() => setExpansionViewMode('inline')}
    >
      {isAgentChat ? (
        <AgentScopedResourcesPanel viewMode="overlay" onToggleView={handleToggleView} />
      ) : settings.queryMode === 'agent' ? (
        <UniversalAgentResourcesPanel viewMode="overlay" onToggleView={handleToggleView} />
      ) : hubFilterQueryMode ? (
        <ConnectorsCollectionsPanel
          apps={settings.filters?.apps ?? []}
          kb={settings.filters?.kb ?? []}
          onSelectionChange={(next) => {
            setFilters({
              ...settings.filters,
              apps: next.apps,
              kb: next.kb,
            });
          }}
          viewMode="overlay"
          onToggleView={handleToggleView}
        />
      ) : null}
    </ChatInputOverlayPanel>

    </>
  );
}
