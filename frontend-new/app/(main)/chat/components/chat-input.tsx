'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { Flex, Box, Text, IconButton, Tooltip } from '@radix-ui/themes';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatInputExpansionPanel } from '@/chat/components/chat-panel/expansion-panels/chat-input-expansion-panel';
import { ChatInputOverlayPanel } from '@/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel';
import { QueryModePanel } from '@/chat/components/chat-panel/expansion-panels/query-mode-panel';
import { ConnectorsCollectionsPanel } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/connectors-collections-panel';
import { MessageActionIndicator } from '@/chat/components/chat-panel/expansion-panels/message-actions';
import { ModelSelectorPanel } from '@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import { SelectedCollections } from '@/chat/components/selected-collections';
import {
  ModeSwitcher,
  AgentStrategyModeSwitcher,
  AgentStrategyModePanel,
} from '@/chat/components/chat-panel';
import { MobileQueryOptionsSheet } from '@/chat/components/chat-panel/expansion-panels/mobile-query-options-sheet';
import { MobileQueryModesSheet } from '@/chat/components/chat-panel/expansion-panels/mobile-query-modes-sheet';
import { AgentStrategyDropdown } from '@/chat/components/agent-strategy-dropdown';
import { getQueryModeConfig } from '@/chat/constants';
import { useChatStore } from '@/chat/store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useCommandStore } from '@/lib/store/command-store';
import { toast } from '@/lib/store/toast-store';
import { streamRegenerateForSlot, cancelStreamForSlot } from '@/chat/streaming';
import { useTranslation } from 'react-i18next';
import { useSpeechRecognition } from '@/lib/hooks/use-speech-recognition';
import type { UploadedFile, ActiveMessageAction, ModelOverride } from '@/chat/types';

type ChatInputVariant = 'full' | 'widget';

interface ChatInputProps {
  onSend?: (message: string, files?: UploadedFile[]) => void;
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

const SUPPORTED_FILE_TYPES = ['TXT', 'PDF', 'DOCX', 'XLS', 'XLSX', 'PNG', 'JPEG', 'JPG'];
const ACCEPTED_MIME_TYPES = {
  'text/plain': 'TXT',
  'application/pdf': 'PDF',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.ms-excel': 'XLS',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'XLSX',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isFileTypeSupported(file: File): boolean {
  const mimeType = file.type;
  return Object.keys(ACCEPTED_MIME_TYPES).includes(mimeType);
}

export function ChatInput({
  onSend,
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
  const [isModelPanelOpen, setIsModelPanelOpen] = useState(false);
  const [isMobileOptionsOpen, setIsMobileOptionsOpen] = useState(false);
  const [isMobileModesOpen, setIsMobileModesOpen] = useState(false);
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
  } = useSpeechRecognition({
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
  const setSelectedModel = useChatStore((s) => s.setSelectedModel);
  const collectionNamesCache = useChatStore((s) => s.collectionNamesCache);

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
  const selectedKbCount = settings.filters?.kb?.length ?? 0;
  const activeQueryConfig = getQueryModeConfig(settings.queryMode) ?? getQueryModeConfig('chat')!;
  const modeColors = activeQueryConfig.colors;
  const agentQueryToolbarConfig = getQueryModeConfig('agent')!;
  const agentStrategyToolbarColors = agentQueryToolbarConfig.colors;
  /** Query-mode panel or agent-strategy panel — used for chrome (borders, outside click). */
  const modeChromeOpen = isAgentChat ? isAgentStrategyPanelOpen : isModePanelOpen;

  const dismissExpansionPanels = useCallback(() => {
    setIsModePanelOpen(false);
    setIsAgentStrategyPanelOpen(false);
    setIsCollectionsPanelOpen(false);
    setIsModelPanelOpen(false);
    setShowUploadArea(false);
  }, []);

  const dismissExpansionPanelsRef = useRef(dismissExpansionPanels);
  dismissExpansionPanelsRef.current = dismissExpansionPanels;

  // Build selected collections from store
  const selectedCollections = settings.filters.kb.map((id) => ({
    id,
    name: collectionNamesCache[id] || 'Collection',
  }));

  const handleRemoveCollection = useCallback((id: string) => {
    setFilters({
      ...settings.filters,
      kb: settings.filters.kb.filter((kbId) => kbId !== id),
    });
  }, [settings.filters, setFilters]);

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
    const { messageId, text } = payload as { messageId: string; text?: string };
    if (!messageId) return;
    dismissExpansionPanels();
    setRegenModelOverride(null);
    setActiveMessageAction({ type: 'regenerate', messageId });
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
      setActiveMessageAction(null);
      setRegenModelOverride(null);
      if (activeSlotId) {
        streamRegenerateForSlot(activeSlotId, activeMessageAction.messageId, modelOverride);
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

    // ── Message action intercept ──────────────────────────────
    if (activeMessageAction) {
      executeMessageAction();
      return;
    }

    // ── Normal send flow (unchanged) ──────────────────────────
    if ((message.trim() || uploadedFiles.length > 0) && onSend) {
      onSend(message, uploadedFiles.length > 0 ? uploadedFiles : undefined);
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

  const processFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter(isFileTypeSupported);

    const newUploadedFiles: UploadedFile[] = validFiles.map((file) => ({
      id: `${file.name}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
      file,
      name: file.name,
      size: file.size,
      type: file.type,
    }));

    setUploadedFiles((prev) => [...prev, ...newUploadedFiles]);
    if (newUploadedFiles.length > 0) {
      setShowUploadArea(false);
    }
  }, []);

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

  const removeFile = (fileId: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.id !== fileId));
  };

  const toggleUploadArea = () => {
    setShowUploadArea((prev) => {
      if (!prev) {
        dismissExpansionPanels();
        setExpansionViewMode('inline');
      }
      return !prev;
    });
  };

  const hasContent = message.trim() || uploadedFiles.length > 0 || isListening;
  const canSubmit = hasContent || activeMessageAction !== null;

  // Display value combines committed text with interim speech so users see real-time feedback
  const displayValue = interimTranscript
    ? message + (message.length > 0 ? ' ' : '') + interimTranscript
    : message;

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
    }
  }, [isAgentChat]);

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

          {/* Send button */}
          <IconButton
            variant="solid"
            size="2"
            onClick={handleSubmit}
            disabled={!message.trim()}
            style={{
              margin: 0,
              backgroundColor: message.trim() ? activeIconColor : 'var(--slate-a3)',
            }}
          >
            <MaterialIcon
              name="arrow_upward"
              size={ICON_SIZES.PRIMARY}
              color={message.trim() ? 'white' : 'var(--slate-a8)'}
            />
          </IconButton>
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
      style={{
        width: isMobile ? '100%' : '50rem',
        fontFamily: 'Manrope, sans-serif',
        ...(isAnimatingIn && {
          animation: 'chatWidgetExpandIn 220ms ease-out',
        }),
      }}
    >
      {/* Selected Collection Cards — shown above the main input, matching Figma spec */}
      {selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen && (
        <Flex
          align="center"
          style={{
            backgroundColor: 'var(--slate-1)',
            borderTop: '1px solid var(--slate-5)',
            borderLeft: '1px solid var(--slate-5)',
            borderRight: '1px solid var(--slate-5)',
            borderTopLeftRadius: 'var(--radius-1)',
            borderTopRightRadius: 'var(--radius-1)',
            padding: 'var(--space-3) var(--space-4)',
          }}
        >
          <SelectedCollections
            collections={selectedCollections}
            removable
            onRemove={handleRemoveCollection}
          />
        </Flex>
      )}

      {/* Uploaded Files Preview — separate container above the main input, matching Figma spec */}
      {uploadedFiles.length > 0 && (
        <Flex
          align="center"
          style={{
            backgroundColor: 'var(--slate-1)',
            borderTop: selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen
              ? 'none'
              : '1px solid var(--slate-5)',
            borderLeft: '1px solid var(--slate-5)',
            borderRight: '1px solid var(--slate-5)',
            borderTopLeftRadius: selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen
              ? '0'
              : 'var(--radius-1)',
            borderTopRightRadius: selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen
              ? '0'
              : 'var(--radius-1)',
            padding: 'var(--space-3) var(--space-4)',
            overflowX: 'auto',
            overflowY: 'hidden',
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
                  border: '1px solid var(--olive-3)',
                  borderRadius: '3px',
                }}
              >
                <Flex direction="column" gap="2">
                  {/* Header: icon + close button */}
                  <Flex align="center" justify="between">
                    <FileIcon
                      extension={getMimeTypeExtension(file.type) || undefined}
                      filename={file.name}
                      size={16}
                      fallbackIcon="insert_drive_file"
                    />
                    <IconButton
                      variant="ghost"
                      size="1"
                      onClick={() => removeFile(file.id)}
                      style={{ margin: 0, flexShrink: 0 }}
                    >
                      <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} color="var(--slate-11)" />
                    </IconButton>
                  </Flex>

                  {/* Content: filename + size */}
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
                    <Text size="1" style={{ color: 'var(--slate-11)' }}>
                      {formatFileSize(file.size)}
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
                borderRadius: '3px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'background-color 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--accent-a2)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent';
              }}
            >
              <MaterialIcon name="add" size={24} color="var(--accent-9)" />
            </Box>
          </Flex>
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
      gap="3"
      style={{
        backdropFilter: 'blur(25px)',
        background: 'var(--effects-translucent)',
        // Accent border when the user has typed something OR is in edit mode (per Figma spec).
        // In regenerate mode the border stays slate since the textarea is disabled.
        border: (message.trim() || isEditMode || isListening) ? '1px solid var(--accent-11)' : '1px solid var(--slate-3)',
        // Flatten top corners whenever there is an element directly above (collections bar,
        // uploaded files preview, or the action pill bar) to avoid a double-radius gap.
        borderRadius: (selectedCollections.length > 0 && !isCollectionsPanelOpen && !modeChromeOpen) || uploadedFiles.length > 0 || isActionMode
          ? '0 0 var(--radius-2) var(--radius-2)'
          : 'var(--radius-2)',
        padding: 'var(--space-3) var(--space-4)',
      }}
    >
      {/* Hidden file input - always rendered so add button can access it */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={Object.keys(ACCEPTED_MIME_TYPES).join(',')}
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
          minHeight='0px'
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
            selectedModel={settings.selectedModel}
            onModelSelect={(model) => {
              setSelectedModel(model);
            }}
            agentId={agentId}
          />
        </ChatInputExpansionPanel>
      ) : isCollectionsPanelOpen && expansionViewMode === 'inline' ? (
        <ChatInputExpansionPanel
          open={isCollectionsPanelOpen}
          onClose={() => {
            setIsCollectionsPanelOpen(false);
            setExpansionViewMode('inline');
          }}
        >
          <ConnectorsCollectionsPanel
            selectedKbIds={settings.filters?.kb ?? []}
            onSelectionChange={(kbIds) => {
              setFilters({
                apps: settings.filters?.apps ?? [],
                kb: kbIds,
              });
            }}
            viewMode="inline"
            onToggleView={handleToggleView}
          />
        </ChatInputExpansionPanel>
      ) : isCollectionsPanelOpen && expansionViewMode === 'overlay' ? (
        /* Render textarea underneath while overlay is open */
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
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
            overflow: message.split('\n').length > 5 ? 'auto' : 'hidden',
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
            minHeight: isMobile ? '36px' : '64px',
            maxHeight: '120px',
            fontFamily: 'Manrope, sans-serif',
            height: 'auto',
            overflow: message.split('\n').length > 5 ? 'auto' : 'hidden',
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
            /* Mobile: meatball opens bottom sheet; attach_file and mic stay inline */
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
              <IconButton
                variant={showUploadArea ? 'soft' : 'ghost'}
                color="gray"
                size="2"
                disabled={isRegenerateMode}
                onClick={toggleUploadArea}
                style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer' }}
              >
                <MaterialIcon name="attach_file" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
              </IconButton>
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                disabled={isRegenerateMode}
                style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer' }}
              >
                <MaterialIcon name="mic" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
              </IconButton>
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
                {/* Apps / Collections — hidden in agent-scoped chat */}
                {!isAgentChat ? (
                  <Tooltip content={t('chat.connectorsTooltip')} side="top">
                    <IconButton
                      variant={isCollectionsPanelOpen || selectedKbCount > 0 ? 'soft' : 'ghost'}
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
                ) : null}
                {/* Model selector button */}
                <Tooltip content={t('chat.aiModelsTooltip')} side="top">
                  <IconButton
                    variant={isModelPanelOpen ? 'soft' : 'ghost'}
                    color="gray"
                    size="2"
                    onClick={() => {
                      setIsModelPanelOpen((prev) => !prev);
                      setIsModePanelOpen(false);
                      setIsAgentStrategyPanelOpen(false);
                      setIsCollectionsPanelOpen(false);
                      setShowUploadArea(false);
                    }}
                    style={{ margin: 0, cursor: 'pointer' }}
                  >
                    <MaterialIcon name="memory" size={ICON_SIZES.PRIMARY} color={activeIconColor} />
                  </IconButton>
                </Tooltip>
                <Tooltip content={t('chat.attachmentTooltip')} side="top">
                  <IconButton
                    variant={showUploadArea ? 'soft' : 'ghost'}
                    color="gray"
                    size="2"
                    disabled={isRegenerateMode}
                    onClick={toggleUploadArea}
                    style={{ margin: 0, cursor: isRegenerateMode ? 'default' : 'pointer' }}
                  >
                    <MaterialIcon name="attach_file" size={ICON_SIZES.PRIMARY} color={isRegenerateMode ? 'var(--slate-5)' : activeIconColor} />
                  </IconButton>
                </Tooltip>
                <Tooltip
                  content={
                    !isSpeechSupported
                      ? t('chat.voiceInputNotSupported')
                      : isListening
                        ? t('chat.listening')
                        : t('chat.micTooltip')
                  }
                  side="top"
                >
                  <IconButton
                    variant={isListening ? 'soft' : 'ghost'}
                    color={isListening ? 'red' : 'gray'}
                    size="2"
                    disabled={isRegenerateMode || !isSpeechSupported}
                    onClick={toggleSpeech}
                    style={{
                      margin: 0,
                      cursor: isRegenerateMode || !isSpeechSupported ? 'default' : 'pointer',
                      ...(isListening && { animation: 'pulse 1.5s ease-in-out infinite' }),
                    }}
                  >
                    <MaterialIcon
                      name={isListening ? 'mic' : 'mic_none'}
                      size={ICON_SIZES.PRIMARY}
                      color={
                        isRegenerateMode || !isSpeechSupported
                          ? 'var(--slate-5)'
                          : isListening
                            ? 'var(--red-11)'
                            : activeIconColor
                      }
                    />
                  </IconButton>
                </Tooltip>
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
                backgroundColor: activeIconColor,
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

    {/* Overlay panel — rendered via portal when collections panel is in overlay mode */}
    <ChatInputOverlayPanel
      open={isCollectionsPanelOpen && expansionViewMode === 'overlay'}
      onCollapse={() => setExpansionViewMode('inline')}
    >
      <ConnectorsCollectionsPanel
        selectedKbIds={settings.filters?.kb ?? []}
        onSelectionChange={(kbIds) => {
          setFilters({
            apps: settings.filters?.apps ?? [],
            kb: kbIds,
          });
        }}
        viewMode="overlay"
        onToggleView={handleToggleView}
      />
    </ChatInputOverlayPanel>

    </>
  );
}
