import type {
  Message,
  Citation,
  Metadata,
  Conversation,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
  CompletionData,
} from 'src/types/chat-bot';

import { Icon } from '@iconify/react';
import menuIcon from '@iconify-icons/mdi/menu';
import { useParams, useNavigate } from 'react-router';
import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';

import {
  Box,
  Alert,
  Button,
  styled,
  Tooltip,
  Snackbar,
  useTheme,
  IconButton,
  CircularProgress,
  alpha,
  Typography,
} from '@mui/material';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { ORIGIN } from 'src/sections/knowledgebase/constants/knowledge-search';
import { useConnectors } from 'src/sections/accountdetails/connectors/context';
import { KnowledgeBaseAPI } from 'src/sections/knowledgebase/services/api';
import { getConnectorPublicUrl } from 'src/sections/accountdetails/account-settings/services/utils/services-configuration-service';

import ChatInput from './components/chat-input';
import ChatSidebar from './components/chat-sidebar';
import HtmlViewer from './components/html-highlighter';
import TextViewer from './components/text-highlighter';
import ExcelViewer from './components/excel-highlighter';
import ChatMessagesArea from './components/chat-message-area';
import PdfHighlighterComp from './components/pdf-highlighter';
import MarkdownViewer from './components/markdown-highlighter';
import DocxHighlighterComp from './components/docx-highlighter';
import WelcomeMessage from './components/welcome-message';
import { StreamingContext } from './components/chat-message';
import { processStreamingContentLegacy } from './utils/styles/content-processing';
import ImageHighlighter from './components/image-highlighter';

const DRAWER_WIDTH = 300;

// Per-conversation streaming state
interface ConversationStreamingState {
  messageId: string | null;
  content: string;
  citations: CustomCitation[];
  isActive: boolean;
  controller: AbortController | null;
  accumulatedContent: string;
  completionData: CompletionData | null;
  isCompletionPending: boolean;
  finalMessageId: string | null;
  isProcessingCompletion: boolean;
  statusMessage: string;
  showStatus: boolean;
  pendingNavigation: {
    conversationId: string;
    shouldNavigate: boolean;
  } | null;
  isStreamingCompleted: boolean;
}

// Store messages per conversation
interface ConversationMessages {
  [conversationKey: string]: FormattedMessage[];
}

interface StreamingContextType {
  streamingState: {
    messageId: string | null;
    content: string;
    citations: CustomCitation[];
    isActive: boolean;
  };
  updateStreamingContent: (messageId: string, content: string, citations: CustomCitation[]) => void;
  clearStreaming: () => void;
}

class StreamingManager {
  private static instance: StreamingManager;

  private conversationStates: { [key: string]: ConversationStreamingState } = {};

  private messageToConversationMap: { [messageId: string]: string } = {};

  private conversationMessages: ConversationMessages = {};

  private updateCallbacks: Set<() => void> = new Set();

  private notifyTimeout: NodeJS.Timeout | null = null;

  private completedNavigations: Set<string> = new Set();

  static getInstance(): StreamingManager {
    if (!StreamingManager.instance) {
      StreamingManager.instance = new StreamingManager();
    }
    return StreamingManager.instance;
  }

  addUpdateCallback(callback: () => void) {
    this.updateCallbacks.add(callback);
  }

  removeUpdateCallback(callback: () => void) {
    this.updateCallbacks.delete(callback);
  }

  private notifyUpdates() {
    if (this.notifyTimeout) {
      clearTimeout(this.notifyTimeout);
    }
    this.notifyTimeout = setTimeout(() => {
      this.updateCallbacks.forEach((callback) => {
        try {
          callback();
        } catch (error) {
          console.error('Error in update callback:', error);
        }
      });
    }, 16);
  }

  // private static processStreamingContent(
  //   rawContent: string,
  //   citations: CustomCitation[] = []
  // ): {
  //   processedContent: string;
  //   processedCitations: CustomCitation[];
  // } {
  //   if (!rawContent) return { processedContent: '', processedCitations: citations };

  //   const processedContent = rawContent
  //     .replace(/\\n/g, '\n')
  //     // .replace(/\*\*(\d+)\*\*/g, '[$1]')
  //     // .replace(/\*\*([^*]+)\*\*/g, '**$1**')
  //     // .replace(/\n{4,}/g, '\n\n\n')
  //     .trim();

  //   const citationMatches = Array.from(processedContent.matchAll(/\[(\d+)\]/g));
  //   const mentionedCitationNumbers = new Set(
  //     citationMatches.map((match) => parseInt(match[1], 10))
  //   );

  //   const processedCitations = [...citations].map((citation, index) => ({
  //     ...citation,
  //     chunkIndex: citation.chunkIndex || index + 1,
  //   }));

  //   mentionedCitationNumbers.forEach((citationNum) => {
  //     if (
  //       !processedCitations.some((c) => c.chunkIndex === citationNum) &&
  //       citations[citationNum - 1]
  //     ) {
  //       processedCitations.push({
  //         ...citations[citationNum - 1],
  //         chunkIndex: citationNum,
  //       });
  //     }
  //   });

  //   return {
  //     processedContent,
  //     processedCitations: processedCitations.sort(
  //       (a, b) => (a.chunkIndex || 0) - (b.chunkIndex || 0)
  //     ),
  //   };
  // }

  getConversationState(conversationKey: string): ConversationStreamingState | null {
    return this.conversationStates[conversationKey] || null;
  }

  getConversationMessages(conversationKey: string): FormattedMessage[] {
    return this.conversationMessages[conversationKey] || [];
  }

  setConversationMessages(conversationKey: string, messages: FormattedMessage[]) {
    this.conversationMessages[conversationKey] = messages;
    this.notifyUpdates();
  }

  updateConversationMessages(
    conversationKey: string,
    updater: (prev: FormattedMessage[]) => FormattedMessage[]
  ) {
    this.conversationMessages[conversationKey] = updater(
      this.conversationMessages[conversationKey] || []
    );
    this.notifyUpdates();
  }

  updateConversationState(conversationKey: string, updates: Partial<ConversationStreamingState>) {
    if (!this.conversationStates[conversationKey]) {
      this.conversationStates[conversationKey] = StreamingManager.initializeStreamingState();
    }
    this.conversationStates[conversationKey] = {
      ...this.conversationStates[conversationKey],
      ...updates,
    };
    this.notifyUpdates();
  }

  updateStatus(conversationKey: string, message: string) {
    this.updateConversationState(conversationKey, {
      statusMessage: message,
      showStatus: true,
    });
  }

  clearStatus(conversationKey: string) {
    this.updateConversationState(conversationKey, {
      statusMessage: '',
      showStatus: false,
    });
  }

  mapMessageToConversation(messageId: string, conversationKey: string) {
    this.messageToConversationMap[messageId] = conversationKey;
  }

  getConversationForMessage(messageId: string): string | null {
    return this.messageToConversationMap[messageId] || null;
  }

  transferNewConversationData(newConversationId: string) {
    const newKey = 'new';
    const actualKey = newConversationId;
    const newMessages = this.getConversationMessages(newKey);
    this.setConversationMessages(actualKey, [...newMessages]);

    const newState = this.getConversationState(newKey);
    if (newState) {
      this.conversationStates[actualKey] = {
        ...newState,
        pendingNavigation: null,
      };

      if (newState.messageId) this.mapMessageToConversation(newState.messageId, actualKey);
      if (newState.finalMessageId)
        this.mapMessageToConversation(newState.finalMessageId, actualKey);
    }

    delete this.conversationStates[newKey];
    delete this.conversationMessages[newKey];
    this.notifyUpdates();
  }

  static getPendingNavigation(): { conversationId: string; shouldNavigate: boolean } | null {
    return null;
  }

  updateStreamingContent(messageId: string, newChunk: string, citations: CustomCitation[] = []) {
    const conversationKey = this.getConversationForMessage(messageId);
    if (!conversationKey) return;

    const state = this.conversationStates[conversationKey];
    if (!state?.isActive) {
      this.updateConversationState(conversationKey, {
        messageId,
        isActive: true,
        isStreamingCompleted: false,
        isProcessingCompletion: false,
        content: '',
        citations: [],
        accumulatedContent: '',
      });
    }

    const currentState = this.conversationStates[conversationKey];
    const updatedAccumulatedContent = (currentState?.accumulatedContent || '') + newChunk;
    const { processedContent, processedCitations } = processStreamingContentLegacy(
      updatedAccumulatedContent,
      citations.length > 0 ? citations : currentState?.citations || []
    );

    this.updateConversationState(conversationKey, {
      accumulatedContent: updatedAccumulatedContent,
      content: processedContent,
      citations: processedCitations,
    });

    this.updateConversationMessages(conversationKey, (prev) => {
      const messageIndex = prev.findIndex((msg) => msg.id === messageId);
      if (messageIndex === -1) return prev;
      const updated = [...prev];
      updated[messageIndex] = {
        ...updated[messageIndex],
        content: processedContent,
        citations: processedCitations,
      };
      return updated;
    });
  }

  finalizeStreaming(conversationKey: string, messageId: string, completionData: CompletionData) {
    const state = this.conversationStates[conversationKey];
    if (state?.isStreamingCompleted) {
      return;
    }

    let finalContent = state?.content || '';
    let finalCitations = state?.citations || [];
    let finalMessageId = messageId;

    if (completionData?.conversation) {
      const finalBotMessage = completionData.conversation.messages
        .filter((msg: any) => msg.messageType === 'bot_response')
        .pop();

      if (finalBotMessage) {
        const formatted = StreamingManager.formatMessage(finalBotMessage);
        if (formatted) {
          finalMessageId = formatted.id;
          const { processedContent, processedCitations } = processStreamingContentLegacy(
            formatted.content,
            formatted.citations
          );
          finalContent = processedContent;
          finalCitations = processedCitations;
        }
      }
    }

    this.updateConversationMessages(conversationKey, (prev) =>
      prev.map((msg) =>
        msg.id === messageId
          ? { ...msg, id: finalMessageId, content: finalContent, citations: finalCitations }
          : msg
      )
    );
    this.mapMessageToConversation(finalMessageId, conversationKey);

    this.updateConversationState(conversationKey, {
      isActive: false,
      isProcessingCompletion: false,
      isCompletionPending: false,
      isStreamingCompleted: true,
      content: finalContent,
      citations: finalCitations,
      finalMessageId,
      messageId: finalMessageId,
      statusMessage: '',
      showStatus: false,
      completionData: null,
    });
  }

  private static formatMessage(apiMessage: any): FormattedMessage | null {
    if (!apiMessage) return null;
    const baseMessage = {
      id: apiMessage._id,
      timestamp: new Date(apiMessage.createdAt || new Date()),
      content: apiMessage.content || '',
      type: apiMessage.messageType === 'user_query' ? 'user' : 'bot',
      contentFormat: apiMessage.contentFormat || 'MARKDOWN',
      followUpQuestions: apiMessage.followUpQuestions || [],
      createdAt: apiMessage.createdAt ? new Date(apiMessage.createdAt) : new Date(),
      updatedAt: apiMessage.updatedAt ? new Date(apiMessage.updatedAt) : new Date(),
    };

    if (apiMessage.messageType === 'user_query') {
      return { ...baseMessage, type: 'user', feedback: apiMessage.feedback || [] };
    }

    if (apiMessage.messageType === 'bot_response') {
      return {
        ...baseMessage,
        type: 'bot',
        confidence: apiMessage.confidence || '',
        citations: (apiMessage?.citations || []).map((citation: any) => ({
          id: citation.citationId,
          _id: citation?.citationData?._id || citation.citationId,
          citationId: citation.citationId,
          content: citation?.citationData?.content || '',
          metadata: citation?.citationData?.metadata || [],
          orgId: citation?.citationData?.metadata?.orgId || '',
          citationType: citation?.citationType || '',
          createdAt: citation?.citationData?.createdAt || new Date().toISOString(),
          updatedAt: citation?.citationData?.updatedAt || new Date().toISOString(),
          chunkIndex: citation?.citationData?.chunkIndex || 1,
        })),
      };
    }
    return baseMessage;
  }

  clearStreaming(conversationKey: string) {
    const state = this.conversationStates[conversationKey];
    if (!state) return;

    if (state.controller && !state.controller.signal.aborted) {
      state.controller.abort();
    }
    // Reset streaming state only; preserve existing messages/history
    this.conversationStates[conversationKey] = StreamingManager.initializeStreamingState();
    this.notifyUpdates();
  }

  // Reset the draft ("new") conversation completely
  resetNewConversation() {
    const draftKey = 'new';
    this.conversationStates[draftKey] = StreamingManager.initializeStreamingState();
    this.conversationMessages[draftKey] = [];
    this.notifyUpdates();
  }

  private static initializeStreamingState(): ConversationStreamingState {
    return {
      messageId: null,
      content: '',
      citations: [],
      isActive: false,
      controller: null,
      accumulatedContent: '',
      completionData: null,
      isCompletionPending: false,
      finalMessageId: null,
      isProcessingCompletion: false,
      statusMessage: '',
      showStatus: false,
      pendingNavigation: null,
      isStreamingCompleted: false,
    };
  }

  createStreamingMessage(messageId: string, conversationKey: string) {
    const streamingMessage: FormattedMessage = {
      type: 'bot',
      content: '',
      createdAt: new Date(),
      updatedAt: new Date(),
      id: messageId,
      contentFormat: 'MARKDOWN',
      followUpQuestions: [],
      citations: [],
      confidence: '',
      messageType: 'bot_response',
      timestamp: new Date(),
    };

    this.mapMessageToConversation(messageId, conversationKey);
    this.updateConversationMessages(conversationKey, (prev) => [...prev, streamingMessage]);
  }

  resetNavigationTracking() {
    this.completedNavigations.clear();
  }

  isConversationLoading(conversationKey: string): boolean {
    const state = this.getConversationState(conversationKey);
    return !!(state && (state.isActive || state.isProcessingCompletion || state.showStatus));
  }
}

const StyledOpenButton = styled(IconButton)(({ theme }) => ({
  position: 'absolute',
  top: 78,
  left: 14,
  zIndex: 1100,
  padding: '6px',
  color: theme.palette.text.secondary,
  backgroundColor: 'transparent',
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius,
  transition: 'all 0.2s ease',
  '&:hover': {
    backgroundColor: theme.palette.action.hover,
    color: theme.palette.primary.main,
  },
}));

const getEngagingStatusMessage = (event: string, data: any): string | null => {
  switch (event) {
    case 'status': {
      const message = data.message || data.status || 'Processing...';
      switch (data.status) {
        case 'searching':
          return `🔍 ${message}`;
        case 'decomposing':
          return `🧩 ${message}`;
        case 'parallel_processing':
          return `⚡ ${message}`;
        case 'reranking':
          return `📊 ${message}`;
        case 'generating':
          return `✨ ${message}`;
        case 'deduplicating':
          return `🔧 ${message}`;
        case 'preparing_context':
          return `📋 ${message}`;
        default:
          return `⚙️ ${message}`;
      }
    }
    case 'query_decomposed': {
      const queryCount = data.queries?.length || 0;
      return queryCount > 1
        ? `🧩 Breaking your request into ${queryCount} questions for a better answer.`
        : '🤔 Analyzing your request...';
    }
    case 'search_complete': {
      const resultsCount = data.results_count || 0;
      return resultsCount > 0
        ? `📚 Found ${resultsCount} potential sources. Now processing them...`
        : '✅ Finished searching...';
    }
    case 'connected':
      return '🔌 Connected and processing...';
    case 'query_transformed':
    case 'results_ready':
      return null;
    default:
      return 'Processing ...';
  }
};

const ChatInterface = () => {
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoadingConversation, setIsLoadingConversation] = useState<boolean>(false);
  const [expandedCitations, setExpandedCitations] = useState<ExpandedCitationsState>({});
  const [isDrawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [selectedChat, setSelectedChat] = useState<Conversation | null>(null);
  const [shouldRefreshSidebar, setShouldRefreshSidebar] = useState<boolean>(false);
  const [isNavigationBlocked, setIsNavigationBlocked] = useState<boolean>(false);
  const currentOpenDocumentRef = useRef<string | null>(null);

  // Model selection state
  const [selectedModel, setSelectedModel] = useState<{
    modelType: string;
    provider: string;
    modelName: string;
    modelKey: string;
    isMultimodal: boolean;
    isDefault: boolean;
  } | null>(null);
  const [selectedChatMode, setSelectedChatMode] = useState<{
    id: string;
    name: string;
    description: string;
  } | null>(null);

  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId: string }>();

  // PDF viewer states
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [aggregatedCitations, setAggregatedCitations] = useState<CustomCitation[] | null>([]);
  const [openPdfView, setOpenPdfView] = useState<boolean>(false);
  const [isExcel, setIsExcel] = useState<boolean>(false);
  const [isViewerReady, setIsViewerReady] = useState<boolean>(false);
  const [transitioning, setTransitioning] = useState<boolean>(false);
  const [fileBuffer, setFileBuffer] = useState<ArrayBuffer | null>();
  const [isPdf, setIsPdf] = useState<boolean>(false);
  const [isDocx, setIsDocx] = useState<boolean>(false);
  const [isMarkdown, setIsMarkdown] = useState<boolean>(false);
  const [isHtml, setIsHtml] = useState<boolean>(false);
  const [isTextFile, setIsTextFile] = useState<boolean>(false);
  const [isImage, setIsImage] = useState<boolean>(false);
  const [highlightedCitation, setHighlightedCitation] = useState<CustomCitation | null>(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });
  const [showWelcome, setShowWelcome] = useState<boolean>(true);

  // Filters: selected apps and knowledge base IDs (shared with ChatInput)
  const [selectedApps, setSelectedApps] = useState<string[]>([]);
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>([]);
  const [allApps, setAllApps] = useState<Array<{ id: string; name: string; iconPath?: string }>>(
    []
  );
  const [allKBs, setAllKBs] = useState<Array<{ id: string; name: string }>>([]);
  const { activeConnectors } = useConnectors();

  // Helper to keep latest filters inline without refs
  const currentFilters = useMemo(
    () => ({ apps: selectedApps, kb: selectedKbIds }),
    [selectedApps, selectedKbIds]
  );

  // Build app sources from connectors
  useEffect(() => {
    const connectors = [...(activeConnectors || [])];
    const apps = connectors.map((c: any) => ({
      id: (c.name || '').toLowerCase(),
      name: c.name || '',
      iconPath: c.iconPath || '/assets/icons/connectors/default.svg',
    }));
    // include local KB app selector
    setAllApps([{ id: 'local', name: 'KB', iconPath: '/assets/icons/connectors/kb.svg' }, ...apps]);
  }, [activeConnectors]);

  // Load knowledge bases once
  useEffect(() => {
    const loadKBs = async () => {
      try {
        const data = await KnowledgeBaseAPI.getKnowledgeBases({ page: 1, limit: 100, search: '' });
        const list = data?.knowledgeBases ?? (Array.isArray(data) ? data : []);
        setAllKBs(list.map((kb: any) => ({ id: kb.id, name: kb.name })));
      } catch (e) {
        console.error('Failed to load knowledge bases:', e);
        setAllKBs([]);
      }
    };
    loadKBs();
  }, []);

  const [updateTrigger, setUpdateTrigger] = useState(0);
  const forceUpdate = useCallback(() => setUpdateTrigger((prev) => prev + 1), []);

  const streamingManager = StreamingManager.getInstance();
  const theme = useTheme();

  const getConversationKey = useCallback((convId: string | null) => convId || 'new', []);

  const currentConversationKey = useMemo(
    () => getConversationKey(currentConversationId),
    [currentConversationId, getConversationKey]
  );

  const currentMessages = useMemo(
    () => streamingManager.getConversationMessages(currentConversationKey),
    // eslint-disable-next-line
    [streamingManager, currentConversationKey, updateTrigger]
  );

  const currentStreamingState = useMemo(() => {
    const state = streamingManager.getConversationState(currentConversationKey);
    return state
      ? {
          messageId: state.messageId,
          content: state.content,
          citations: state.citations,
          isActive: state.isActive,
        }
      : { messageId: null, content: '', citations: [], isActive: false };
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger]);

  const currentConversationStatus = useMemo(() => {
    const state = streamingManager.getConversationState(currentConversationKey);
    return { statusMessage: state?.statusMessage || '', showStatus: state?.showStatus || false };
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger]);

  const isCurrentConversationLoading = useMemo(() => {
    const streamingState = streamingManager.getConversationState(currentConversationKey);
    return streamingManager.isConversationLoading(currentConversationKey) || isLoadingConversation;
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger, isLoadingConversation]);

  useEffect(() => {
    streamingManager.addUpdateCallback(forceUpdate);
    return () => streamingManager.removeUpdateCallback(forceUpdate);
  }, [streamingManager, forceUpdate]);

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };

  const formatMessage = useCallback((apiMessage: Message): FormattedMessage | null => {
    if (!apiMessage) return null;
    const baseMessage = {
      id: apiMessage._id,
      timestamp: new Date(apiMessage.createdAt || new Date()),
      content: apiMessage.content || '',
      type: apiMessage.messageType === 'user_query' ? 'user' : 'bot',
      contentFormat: apiMessage.contentFormat || 'MARKDOWN',
      followUpQuestions: apiMessage.followUpQuestions || [],
      createdAt: apiMessage.createdAt ? new Date(apiMessage.createdAt) : new Date(),
      updatedAt: apiMessage.updatedAt ? new Date(apiMessage.updatedAt) : new Date(),
    };
    if (apiMessage.messageType === 'user_query') {
      return { ...baseMessage, type: 'user', feedback: apiMessage.feedback || [] };
    }
    if (apiMessage.messageType === 'bot_response') {
      return {
        ...baseMessage,
        type: 'bot',
        confidence: apiMessage.confidence || '',
        citations: (apiMessage?.citations || []).map((citation: Citation) => ({
          id: citation.citationId,
          _id: citation?.citationData?._id || citation.citationId,
          citationId: citation.citationId,
          content: citation?.citationData?.content || '',
          metadata: citation?.citationData?.metadata || [],
          orgId: citation?.citationData?.metadata?.orgId || '',
          citationType: citation?.citationType || '',
          createdAt: citation?.citationData?.createdAt || new Date().toISOString(),
          updatedAt: citation?.citationData?.updatedAt || new Date().toISOString(),
          chunkIndex: citation?.citationData?.chunkIndex || 1,
        })),
      };
    }
    return baseMessage;
  }, []);

  const streamingContextValue: StreamingContextType = useMemo(
    () => ({
      streamingState: currentStreamingState,
      updateStreamingContent: (messageId: string, content: string, citations: CustomCitation[]) => {
        streamingManager.updateStreamingContent(messageId, content, citations);
      },
      clearStreaming: () => {
        streamingManager.clearStreaming(currentConversationKey);
      },
    }),
    [currentStreamingState, streamingManager, currentConversationKey]
  );

  const parseSSELine = (line: string): { event?: string; data?: any } | null => {
    if (line.startsWith('event: ')) return { event: line.substring(7).trim() };
    if (line.startsWith('data: ')) {
      try {
        return { data: JSON.parse(line.substring(6).trim()) };
      } catch (e) {
        return null;
      }
    }
    return null;
  };

  // Extract the stream processing logic into a separate helper function
  const processStreamChunk = async (
    reader: ReadableStreamDefaultReader<Uint8Array>,
    decoder: TextDecoder,
    parseSSELineFunc: (line: string) => { event?: string; data?: any } | null,
    handleStreamingEvent: (event: string, data: any, context: any) => Promise<void>,
    context: {
      conversationKey: string;
      streamingBotMessageId: string;
      isNewConversation: boolean;
      hasCreatedMessage: React.MutableRefObject<boolean>;
      conversationIdRef: React.MutableRefObject<string | null>;
    },
    controller: AbortController
  ): Promise<void> => {
    let buffer = '';
    let currentEvent = '';

    const readNextChunk = async (): Promise<void> => {
      const { done, value } = await reader.read();
      if (done) return;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        const trimmedLine = line.trim();
        // eslint-disable-next-line
        if (!trimmedLine) continue;

        const parsed = parseSSELineFunc(trimmedLine);
        // eslint-disable-next-line
        if (!parsed) continue;

        if (parsed.event) {
          currentEvent = parsed.event;
        } else if (parsed.data && currentEvent) {
          // eslint-disable-next-line
          await handleStreamingEvent(currentEvent, parsed.data, context);
        }
      }

      if (!controller.signal.aborted) {
        await readNextChunk();
      }
    };

    await readNextChunk();
  };

  // Refactored main function as a standard async function
  const handleStreamingResponse = useCallback(
    async (url: string, body: any, isNewConversation: boolean): Promise<string | null> => {
      const streamingBotMessageId = `streaming-${Date.now()}`;
      const conversationKey = isNewConversation ? 'new' : getConversationKey(currentConversationId);

      // Initialize streaming state
      streamingManager.updateStatus(conversationKey, 'Connecting...');
      const controller = new AbortController();
      streamingManager.updateConversationState(conversationKey, { controller });

      const hasCreatedMessage = { current: false };
      const conversationIdRef = { current: null as string | null };

      // Define the event handler
      const handleStreamingEvent = async (
        event: string,
        data: any,
        context: {
          conversationKey: string;
          streamingBotMessageId: string;
          isNewConversation: boolean;
          hasCreatedMessage: React.MutableRefObject<boolean>;
          conversationIdRef: React.MutableRefObject<string | null>;
        }
      ): Promise<void> => {
        const statusMsg = getEngagingStatusMessage(event, data);
        if (statusMsg) {
          streamingManager.updateStatus(context.conversationKey, statusMsg);
        }

        switch (event) {
          case 'answer_chunk':
            if (data.chunk) {
              if (!context.hasCreatedMessage.current) {
                streamingManager.createStreamingMessage(
                  context.streamingBotMessageId,
                  context.conversationKey
                );
                context.hasCreatedMessage.current = true;
              }
              streamingManager.clearStatus(context.conversationKey);
              streamingManager.updateStreamingContent(
                context.streamingBotMessageId,
                data.chunk,
                data.citations || []
              );
            }
            break;

          case 'complete': {
            streamingManager.clearStatus(context.conversationKey);
            const completedConversation = data.conversation;
            if (completedConversation?._id) {
              let finalKey = context.conversationKey;
              if (context.isNewConversation && context.conversationKey === 'new') {
                streamingManager.transferNewConversationData(completedConversation._id);
                finalKey = completedConversation._id;
                // Store the conversation ID in the ref for the calling function
                context.conversationIdRef.current = completedConversation._id;
              }
              streamingManager.finalizeStreaming(finalKey, context.streamingBotMessageId, data);
            }
            break;
          }

          case 'error': {
            streamingManager.clearStreaming(context.conversationKey);
            const errorMessage = data.message || data.error || 'An error occurred';

            if (!context.hasCreatedMessage.current) {
              const errorMsg: FormattedMessage = {
                type: 'bot',
                content: errorMessage,
                createdAt: new Date(),
                updatedAt: new Date(),
                id: context.streamingBotMessageId,
                contentFormat: 'MARKDOWN',
                followUpQuestions: [],
                citations: [],
                confidence: '',
                messageType: 'error',
                timestamp: new Date(),
              };
              streamingManager.mapMessageToConversation(
                context.streamingBotMessageId,
                context.conversationKey
              );
              streamingManager.updateConversationMessages(context.conversationKey, (prev) => [
                ...prev,
                errorMsg,
              ]);
              context.hasCreatedMessage.current = true;
            } else {
              streamingManager.updateConversationMessages(context.conversationKey, (prev) =>
                prev.map((msg) =>
                  msg.id === context.streamingBotMessageId
                    ? { ...msg, content: errorMessage, messageType: 'error' }
                    : msg
                )
              );
            }
            throw new Error(errorMessage);
          }

          default:
            break;
        }
      };

      try {
        // Make the HTTP request
        const token = localStorage.getItem('jwt_access_token');
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Failed to get response reader');
        }

        const decoder = new TextDecoder();

        // Process the stream using the helper function
        await processStreamChunk(
          reader,
          decoder,
          parseSSELine,
          handleStreamingEvent,
          {
            conversationKey,
            streamingBotMessageId,
            isNewConversation,
            hasCreatedMessage,
            conversationIdRef,
          },
          controller
        );

        // Return the conversation ID if it was captured during streaming
        return conversationIdRef.current;
      } catch (error) {
        // Handle AbortError separately
        if (error instanceof Error && error.name === 'AbortError') {
          // Don't log abort errors as they're intentional
          return null;
        }

        console.error('Streaming connection error:', error);
        streamingManager.clearStreaming(conversationKey);
        throw error; // Re-throw non-abort errors
      }
    },
    [currentConversationId, getConversationKey, streamingManager]
  );

  // Updated handleSendMessage to properly handle the promise
  const handleSendMessage = useCallback(
    async (
      messageOverride?: string,
      modelKey?: string,
      modelName?: string,
      chatMode?: string,
      filters?: { apps: string[]; kb: string[] }
    ): Promise<void> => {
      const trimmedInput =
        typeof messageOverride === 'string' ? messageOverride.trim() : inputValue.trim();
      if (!trimmedInput) return;
      if (isNavigationBlocked || isCurrentConversationLoading) return;

      const wasCreatingNewConversation = !currentConversationId;
      const conversationKey = getConversationKey(currentConversationId);

      const tempUserMessage: FormattedMessage = {
        type: 'user',
        content: trimmedInput,
        createdAt: new Date(),
        updatedAt: new Date(),
        id: `temp-${Date.now()}`,
        contentFormat: 'MARKDOWN',
        followUpQuestions: [],
        citations: [],
        feedback: [],
        messageType: 'user_query',
        timestamp: new Date(),
      };

      if (typeof messageOverride === 'string' && showWelcome) setShowWelcome(false);
      setInputValue('');
      streamingManager.updateConversationMessages(conversationKey, (prev) => [
        ...prev,
        tempUserMessage,
      ]);

      const streamingUrl = wasCreatingNewConversation
        ? `${CONFIG.backendUrl}/api/v1/conversations/stream`
        : `${CONFIG.backendUrl}/api/v1/conversations/${currentConversationId}/messages/stream`;
      try {
        // If child provided filters, capture them for subsequent messages and UI
        if (filters) {
          setSelectedApps(filters.apps || []);
          setSelectedKbIds(filters.kb || []);
        }

        const createdConversationId = await handleStreamingResponse(
          streamingUrl,
          {
            query: trimmedInput,
            modelKey: selectedModel?.modelKey,
            modelName: selectedModel?.modelName,
            chatMode,
            filters: filters || currentFilters,
          },
          wasCreatingNewConversation
        );

        if (wasCreatingNewConversation && createdConversationId) {
          setCurrentConversationId(createdConversationId);
          setShouldRefreshSidebar(true);
          setShowWelcome(false);
        }
      } catch (error) {
        console.error('Error in streaming response:', error);
      }
    },
    [
      inputValue,
      currentConversationId,
      showWelcome,
      streamingManager,
      getConversationKey,
      handleStreamingResponse,
      isNavigationBlocked,
      isCurrentConversationLoading,
      selectedModel,
      currentFilters,
    ]
  );

  const onClosePdf = useCallback((): void => {
    resetViewerStates();
    setFileBuffer(null);
    setHighlightedCitation(null);
    currentOpenDocumentRef.current = null;
  }, []);

  const handleNewChat = useCallback(() => {
    // Do not clear the current conversation's messages; just reset the draft ("new") state
    streamingManager.resetNewConversation();
    streamingManager.resetNavigationTracking();

    setCurrentConversationId(null);
    navigate('/', { replace: true });
    setInputValue('');
    setShouldRefreshSidebar(true);
    setShowWelcome(true);
    setSelectedChat(null);
    setIsNavigationBlocked(false);
    // Reset filters for a fresh chat
    setSelectedApps([]);
    setSelectedKbIds([]);
  }, [navigate, streamingManager]);

  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id || isNavigationBlocked) return;
      onClosePdf();
      try {
        const chatKey = getConversationKey(chat._id);

        // Check if this conversation is currently streaming
        const streamingState = streamingManager.getConversationState(chatKey);
        const isCurrentlyStreaming =
          streamingState?.isActive ||
          streamingState?.isProcessingCompletion ||
          streamingState?.showStatus;

        // If the conversation is streaming, don't set loading state as it might interfere
        if (!isCurrentlyStreaming) {
          setIsLoadingConversation(true);
        }

        // Hide welcome screen immediately when selecting a chat
        setShowWelcome(false);

        // Decide filter behavior on switching chats.
        // Since filters are not stored per-conversation, reset to defaults on switch.
        setSelectedApps([]);
        setSelectedKbIds([]);

        // Update current conversation ID before navigation
        setCurrentConversationId(chat._id);

        // Navigate to the chat
        navigate(`/${chat._id}`, { replace: true });

        const existingMessages = streamingManager.getConversationMessages(chatKey);

        if (!existingMessages.length && !isCurrentlyStreaming) {
          // Only fetch if we don't have messages and it's not currently streaming
          const response = await axios.get(`/api/v1/conversations/${chat._id}`);
          const { conversation } = response.data;
          if (conversation?.messages) {
            const formattedMessages = conversation.messages
              .map(formatMessage)
              .filter(Boolean) as FormattedMessage[];
            streamingManager.setConversationMessages(chatKey, formattedMessages);
            setSelectedChat(conversation);
          }
        } else {
          setSelectedChat(chat);
        }
      } catch (error) {
        console.error('❌ Error loading conversation:', error);
        streamingManager.setConversationMessages(getConversationKey(chat._id), []);
      } finally {
        // Only clear loading if we set it
        const chatKey = getConversationKey(chat._id);
        const streamingState = streamingManager.getConversationState(chatKey);
        const isCurrentlyStreaming =
          streamingState?.isActive ||
          streamingState?.isProcessingCompletion ||
          streamingState?.showStatus;

        if (!isCurrentlyStreaming) {
          setIsLoadingConversation(false);
        }
        setTimeout(() => {
          onClosePdf();
        }, 500);
      }
    },
    [formatMessage, navigate, streamingManager, getConversationKey, isNavigationBlocked, onClosePdf]
  );

  // Update the useEffect to better handle streaming conversations
  useEffect(() => {
    const urlConversationId = conversationId;
    if (isNavigationBlocked) return;

    if (urlConversationId && urlConversationId !== currentConversationId) {
      const chatKey = getConversationKey(urlConversationId);
      const existingMessages = streamingManager.getConversationMessages(chatKey);
      const streamingState = streamingManager.getConversationState(chatKey);
      const isCurrentlyStreaming =
        streamingState?.isActive ||
        streamingState?.isProcessingCompletion ||
        streamingState?.showStatus;

      if (existingMessages.length > 0 || isCurrentlyStreaming) {
        // We have existing messages or it's streaming, just switch to this conversation
        setCurrentConversationId(urlConversationId);
        setShowWelcome(false);
        const existingConversation =
          selectedChat?._id === urlConversationId
            ? selectedChat
            : ({ _id: urlConversationId } as Conversation);
        setSelectedChat(existingConversation);

        // Don't set loading if it's currently streaming
        if (!isCurrentlyStreaming) {
          setIsLoadingConversation(false);
        }
      } else if (currentConversationId !== urlConversationId) {
        handleChatSelect({ _id: urlConversationId } as Conversation);
      }
    } else if (!urlConversationId && currentConversationId !== null) {
      // Only reset to new chat if we're not in the middle of creating a conversation
      const crtMessages = streamingManager.getConversationMessages(
        getConversationKey(currentConversationId)
      );
      const crtStreamingState = streamingManager.getConversationState(
        getConversationKey(currentConversationId)
      );
      const isCurrentlyStreaming =
        crtStreamingState?.isActive || crtStreamingState?.isProcessingCompletion;

      if (!isCurrentlyStreaming && crtMessages.length === 0) {
        handleNewChat();
      }
    }
  }, [
    conversationId,
    currentConversationId,
    streamingManager,
    handleChatSelect,
    selectedChat,
    isNavigationBlocked,
    handleNewChat,
    getConversationKey,
  ]);

  // Update the shouldShowWelcome logic to consider streaming state
  const shouldShowWelcome = useMemo(() => {
    const crtStreamingState = streamingManager.getConversationState(currentConversationKey);
    const isCurrentlyStreaming =
      crtStreamingState?.isActive ||
      crtStreamingState?.isProcessingCompletion ||
      crtStreamingState?.showStatus;

    // Show welcome only if:
    // 1. showWelcome is true AND
    // 2. we don't have a current conversation ID AND
    // 3. we don't have any messages in the current conversation AND
    // 4. we're not loading a conversation AND
    // 5. we're not currently streaming
    return (
      showWelcome &&
      !currentConversationId &&
      currentMessages.length === 0 &&
      !isLoadingConversation &&
      !isCurrentlyStreaming
    );
  }, [
    showWelcome,
    currentConversationId,
    currentMessages.length,
    isLoadingConversation,
    streamingManager,
    currentConversationKey,
  ]);

  // Stable handler for filters change passed to children
  const handleFiltersChange = useCallback((f: { apps: string[]; kb: string[] }) => {
    setSelectedApps(f?.apps || []);
    setSelectedKbIds(f?.kb || []);
  }, []);

  // PDF viewer functions
  const resetViewerStates = () => {
    setTransitioning(true);
    setIsViewerReady(false);
    setPdfUrl(null);
    setFileBuffer(null);
    setHighlightedCitation(null);
    setTimeout(() => {
      setOpenPdfView(false);
      setIsExcel(false);
      setAggregatedCitations(null);
      setTransitioning(false);
      setFileBuffer(null);
    }, 100);
  };

  const handleLargePPTFile = (record: any) => {
    if (record.sizeInBytes / 1048576 > 5) {
      throw new Error('Large file size, redirecting to web page');
    }
  };

  const onViewPdf = async (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile = false,
    bufferData?: ArrayBuffer
  ): Promise<void> => {
    const citationMeta = citation.metadata;
    const recordId = citationMeta?.recordId;

    if (currentOpenDocumentRef.current === recordId) {
      setHighlightedCitation(citation || null);
      return;
    }

    currentOpenDocumentRef.current = recordId;

    setTransitioning(true);
    setIsViewerReady(false);
    setDrawerOpen(false);
    setOpenPdfView(true);
    setAggregatedCitations(citations);
    setFileBuffer(null);
    setPdfUrl(null);
    setHighlightedCitation(citation || null);

    try {
      const response = await axios.get(`/api/v1/knowledgebase/record/${recordId}`);
      const { record } = response.data;
      const { externalRecordId } = record;
      const fileName = record.recordName;

      if (record.origin === ORIGIN.UPLOAD) {
        try {
          const downloadResponse = await axios.get(
            `/api/v1/document/${externalRecordId}/download`,
            { responseType: 'blob' }
          );

          const reader = new FileReader();
          const textPromise = new Promise<string>((resolve) => {
            reader.onload = () => {
              resolve(reader.result?.toString() || '');
            };
          });

          reader.readAsText(downloadResponse.data);
          const text = await textPromise;

          let filename;
          const contentDisposition = downloadResponse.headers['content-disposition'];
          if (contentDisposition) {
            const filenameStarMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (filenameStarMatch && filenameStarMatch[1]) {
              try {
                filename = decodeURIComponent(filenameStarMatch[1]);
              } catch (e) {
                console.error('Failed to decode UTF-8 filename', e);
              }
            }

            if (!filename) {
              const filenameMatch = contentDisposition.match(/filename="?([^";\n]*)"?/i);
              if (filenameMatch && filenameMatch[1]) {
                filename = filenameMatch[1];
              }
            }
          }

          if (!filename && fileName) {
            filename = fileName;
          }

          try {
            const jsonData = JSON.parse(text);
            if (jsonData && jsonData.signedUrl) {
              setPdfUrl(jsonData.signedUrl);
            }
          } catch (e) {
            const bufferReader = new FileReader();
            const arrayBufferPromise = new Promise<ArrayBuffer>((resolve) => {
              bufferReader.onload = () => {
                resolve(bufferReader.result as ArrayBuffer);
              };
              bufferReader.readAsArrayBuffer(downloadResponse.data);
            });

            const buffer = await arrayBufferPromise;
            setFileBuffer(buffer);
          }
        } catch (error) {
          console.error('Error downloading document:', error);
          setSnackbar({
            open: true,
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info',
          });
          let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;

          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }

          setTimeout(() => {
            onClosePdf();
          }, 500);

          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
              } catch (openError) {
                console.error('Error opening new tab:', openError);
                setSnackbar({
                  open: true,
                  message:
                    'Failed to automatically open the document. Please check your browser pop-up settings.',
                  severity: 'error',
                });
              }
            } else {
              console.error('Cannot redirect: No webUrl found for the record.');
              setSnackbar({
                open: true,
                message: 'Failed to load preview and cannot redirect (document URL not found).',
                severity: 'error',
              });
            }
          }, 2500);
          return;
        }
      } else if (record.origin === ORIGIN.CONNECTOR) {
        try {
          let params = {};
          if (['pptx', 'ppt'].includes(citationMeta?.extension)) {
            params = {
              convertTo: 'pdf',
            };
            handleLargePPTFile(record);
          }

          const publicConnectorUrlResponse = await getConnectorPublicUrl();
          let connectorResponse;
          if (publicConnectorUrlResponse && publicConnectorUrlResponse.url) {
            const CONNECTOR_URL = publicConnectorUrlResponse.url;
            connectorResponse = await axios.get(
              `${CONNECTOR_URL}/api/v1/stream/record/${recordId}`,
              {
                responseType: 'blob',
                params,
              }
            );
          } else {
            connectorResponse = await axios.get(
              `${CONFIG.backendUrl}/api/v1/knowledgeBase/stream/record/${recordId}`,
              {
                responseType: 'blob',
                params,
              }
            );
          }
          if (!connectorResponse) return;

          let filename;
          const contentDisposition = connectorResponse.headers['content-disposition'];
          if (contentDisposition) {
            const filenameStarMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (filenameStarMatch && filenameStarMatch[1]) {
              try {
                filename = decodeURIComponent(filenameStarMatch[1]);
              } catch (e) {
                console.error('Failed to decode UTF-8 filename', e);
              }
            }

            if (!filename) {
              const filenameMatch = contentDisposition.match(/filename="?([^";\n]*)"?/i);
              if (filenameMatch && filenameMatch[1]) {
                filename = filenameMatch[1];
              }
            }
          }

          if (!filename && record.recordName) {
            filename = record.recordName;
          }

          const bufferReader = new FileReader();
          const arrayBufferPromise = new Promise<ArrayBuffer>((resolve, reject) => {
            bufferReader.onload = () => {
              const originalBuffer = bufferReader.result as ArrayBuffer;
              const bufferCopy = originalBuffer.slice(0);
              resolve(bufferCopy);
            };
            bufferReader.onerror = () => {
              reject(new Error('Failed to read blob as array buffer'));
            };
            bufferReader.readAsArrayBuffer(connectorResponse.data);
          });

          const buffer = await arrayBufferPromise;
          setFileBuffer(buffer);
        } catch (err) {
          console.error('Error downloading document:', err);
          setSnackbar({
            open: true,
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info',
          });
          let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;

          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }

          setTimeout(() => {
            onClosePdf();
          }, 500);

          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
              } catch (openError) {
                console.error('Error opening new tab:', openError);
                setSnackbar({
                  open: true,
                  message:
                    'Failed to automatically open the document. Please check your browser pop-up settings.',
                  severity: 'error',
                });
              }
            } else {
              console.error('Cannot redirect: No webUrl found for the record.');
              setSnackbar({
                open: true,
                message: 'Failed to load preview and cannot redirect (document URL not found).',
                severity: 'error',
              });
            }
          }, 2500);
          return;
        }
      }
    } catch (err) {
      console.error('Failed to fetch document:', err);
      setTimeout(() => {
        onClosePdf();
      }, 500);
      return;
    }

    setTransitioning(true);
    setDrawerOpen(false);
    setOpenPdfView(true);
    const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(citationMeta?.extension);
    setIsDocx(['docx'].includes(citationMeta?.extension));
    setIsMarkdown(['mdx', 'md'].includes(citationMeta?.extension));
    setIsHtml(['html'].includes(citationMeta?.extension));
    setIsTextFile(['txt'].includes(citationMeta?.extension));
    setIsImage(['jpg', 'jpeg', 'png', 'webp','svg'].includes(citationMeta?.extension));
    setIsExcel(isExcelOrCSV);
    setIsPdf(['pptx', 'ppt', 'pdf'].includes(citationMeta?.extension));

    setTimeout(() => {
      setIsViewerReady(true);
      setTransitioning(false);
      currentOpenDocumentRef.current = recordId;
    }, 100);
  };

  const handleRegenerateMessage = useCallback(
    async (messageId: string): Promise<void> => {
      if (!currentConversationId || !messageId || isCurrentConversationLoading) return;

      try {
        const conversationKey = getConversationKey(currentConversationId);

        const response = await axios.post<{ conversation: Conversation }>(
          `/api/v1/conversations/${currentConversationId}/message/${messageId}/regenerate`,
          { instruction: 'Improve writing style and clarity' }
        );

        if (!response?.data?.conversation?.messages) throw new Error('Invalid response format');

        const allMessages = response.data.conversation.messages
          .map(formatMessage)
          .filter(Boolean) as FormattedMessage[];
        const regeneratedMessage = allMessages.filter((msg) => msg.type === 'bot').pop();
        if (!regeneratedMessage) throw new Error('No regenerated message found in response');

        streamingManager.updateConversationMessages(conversationKey, (prevMessages) =>
          prevMessages.map((msg) =>
            msg.id === messageId ? { ...regeneratedMessage, createdAt: msg.createdAt } : msg
          )
        );

        setExpandedCitations((prevStates) => {
          const newStates = { ...prevStates };
          const messageIndex = currentMessages.findIndex((msg) => msg.id === messageId);
          if (messageIndex !== -1) {
            const hasCitations =
              regeneratedMessage.citations && regeneratedMessage.citations.length > 0;
            newStates[messageIndex] = hasCitations ? prevStates[messageIndex] || false : false;
          }
          return newStates;
        });
      } catch (error) {
        const conversationKey = getConversationKey(currentConversationId);
        streamingManager.updateConversationMessages(conversationKey, (prevMessages) =>
          prevMessages.map((msg) =>
            msg.id === messageId
              ? {
                  ...msg,
                  content: 'Sorry, I encountered an error regenerating this message.',
                  error: true,
                }
              : msg
          )
        );
      }
    },
    [
      currentConversationId,
      formatMessage,
      currentMessages,
      getConversationKey,
      streamingManager,
      isCurrentConversationLoading,
    ]
  );

  const handleSidebarRefreshComplete = useCallback(() => setShouldRefreshSidebar(false), []);

  const handleFeedbackSubmit = useCallback(
    async (messageId: string, feedback: any) => {
      if (!currentConversationId || !messageId) return;
      try {
        await axios.post(
          `/api/v1/conversations/${currentConversationId}/message/${messageId}/feedback`,
          feedback
        );
      } catch (error) {
        throw new Error('Feedback submission error');
      }
    },
    [currentConversationId]
  );

  const MemoizedChatMessagesArea = useMemo(() => React.memo(ChatMessagesArea), []);
  const MemoizedWelcomeMessage = useMemo(() => React.memo(WelcomeMessage), []);

  return (
    <StreamingContext.Provider value={streamingContextValue}>
      <Box sx={{ display: 'flex', width: '100%', height: '90vh', overflow: 'hidden' }}>
        {!isDrawerOpen && (
          <Tooltip title="Open Sidebar" placement="right">
            <StyledOpenButton
              onClick={() => setDrawerOpen(true)}
              size="small"
              aria-label="Open sidebar"
            >
              <Icon icon={menuIcon} fontSize="medium" />
            </StyledOpenButton>
          </Tooltip>
        )}
        {isDrawerOpen && (
          <Box
            sx={{
              width: DRAWER_WIDTH,
              borderRight: 1,
              borderColor: 'divider',
              bgcolor: 'background.paper',
              overflow: 'hidden',
              flexShrink: 0,
            }}
          >
            <ChatSidebar
              onClose={() => setDrawerOpen(false)}
              onChatSelect={handleChatSelect}
              onNewChat={handleNewChat}
              selectedId={currentConversationId}
              shouldRefresh={shouldRefreshSidebar}
              onRefreshComplete={handleSidebarRefreshComplete}
            />
          </Box>
        )}

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: openPdfView ? '1fr 2fr' : '1fr',
            width: '100%',
            gap: 2,
            transition: 'grid-template-columns 0.3s ease',
          }}
        >
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              minWidth: 0,
              height: '90vh',
              borderRight: openPdfView ? 1 : 0,
              borderColor: 'divider',
              marginLeft: isDrawerOpen ? 0 : 4,
              position: 'relative',
            }}
          >
            {shouldShowWelcome ? (
              <MemoizedWelcomeMessage
                key="welcome-screen"
                onSubmit={handleSendMessage}
                isLoading={isCurrentConversationLoading}
                selectedModel={selectedModel}
                selectedChatMode={selectedChatMode}
                onModelChange={setSelectedModel}
                onChatModeChange={setSelectedChatMode}
                apps={allApps}
                knowledgeBases={allKBs}
                initialSelectedApps={selectedApps}
                initialSelectedKbIds={selectedKbIds}
                onFiltersChange={handleFiltersChange}
              />
            ) : (
              <>
                <MemoizedChatMessagesArea
                  messages={currentMessages}
                  isLoading={isCurrentConversationLoading}
                  onRegenerateMessage={handleRegenerateMessage}
                  onFeedbackSubmit={handleFeedbackSubmit}
                  conversationId={currentConversationId}
                  isLoadingConversation={isLoadingConversation}
                  onViewPdf={onViewPdf}
                  currentStatus={currentConversationStatus.statusMessage}
                  isStatusVisible={currentConversationStatus.showStatus}
                />
                <ChatInput
                  onSubmit={handleSendMessage}
                  isLoading={isCurrentConversationLoading}
                  disabled={isCurrentConversationLoading || isNavigationBlocked}
                  placeholder="Type your message..."
                  selectedModel={selectedModel}
                  selectedChatMode={selectedChatMode}
                  onModelChange={setSelectedModel}
                  onChatModeChange={setSelectedChatMode}
                  apps={allApps}
                  knowledgeBases={allKBs}
                  initialSelectedApps={selectedApps}
                  initialSelectedKbIds={selectedKbIds}
                  onFiltersChange={handleFiltersChange}
                />
              </>
            )}
          </Box>

          {/* PDF Viewer */}
          {openPdfView && (
            <Box
              sx={{
                height: '90vh',
                overflow: 'hidden',
                position: 'relative',
                bgcolor: 'background.default',
                '& > div': {
                  height: '100%',
                  width: '100%',
                },
              }}
            >
              {transitioning && (
                <Box
                  sx={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: 'background.paper',
                  }}
                >
                  <CircularProgress />
                </Box>
              )}

              {isViewerReady &&
                (pdfUrl || fileBuffer) &&
                aggregatedCitations &&
                (isExcel ? (
                  <ExcelViewer
                    key="excel-viewer"
                    citations={aggregatedCitations}
                    fileUrl={pdfUrl}
                    excelBuffer={fileBuffer}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ) : isDocx ? (
                  <DocxHighlighterComp
                    key="docx-viewer"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    renderOptions={{
                      breakPages: true,
                      renderHeaders: true,
                      renderFooters: true,
                    }}
                    onClosePdf={onClosePdf}
                  />
                ) : isMarkdown ? (
                  <MarkdownViewer
                    key="markdown-viewer"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ) : isHtml ? (
                  <HtmlViewer
                    key="html-viewer"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ) : isTextFile ? (
                  <TextViewer
                    key="text-viewer"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ) : isImage ? (
                  <ImageHighlighter
                    key="image-highlighter"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ) : (
                  <PdfHighlighterComp
                    key="pdf-viewer"
                    pdfUrl={pdfUrl}
                    pdfBuffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                  />
                ))}
            </Box>
          )}
        </Box>
        <Snackbar
          open={snackbar.open}
          autoHideDuration={4000}
          onClose={handleCloseSnackbar}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          sx={{ mt: 6 }}
        >
          <Alert
            onClose={handleCloseSnackbar}
            severity={snackbar.severity}
            variant="filled"
            sx={{
              width: '100%',
              borderRadius: 0.75,
              boxShadow: theme.shadows[3],
              '& .MuiAlert-icon': { fontSize: '1.2rem' },
            }}
          >
            {snackbar.message}
          </Alert>
        </Snackbar>
      </Box>
    </StreamingContext.Provider>
  );
};

export default ChatInterface;
