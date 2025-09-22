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
import { getConnectorPublicUrl } from 'src/sections/accountdetails/account-settings/services/utils/services-configuration-service';

import { Agent } from 'src/types/agent';
import HtmlViewer from 'src/sections/qna/chatbot/components/html-highlighter';
import TextViewer from 'src/sections/qna/chatbot/components/text-highlighter';
import ExcelViewer from 'src/sections/qna/chatbot/components/excel-highlighter';
import ChatMessagesArea from 'src/sections/qna/chatbot/components/chat-message-area';
import PdfHighlighterComp from 'src/sections/qna/chatbot/components/pdf-highlighter';
import MarkdownViewer from 'src/sections/qna/chatbot/components/markdown-highlighter';
import DocxHighlighterComp from 'src/sections/qna/chatbot/components/docx-highlighter';
import { StreamingContext } from 'src/sections/qna/chatbot/components/chat-message';
import { processStreamingContentLegacy } from 'src/sections/qna/chatbot/utils/styles/content-processing';
import { useConnectors } from 'src/sections/accountdetails/connectors/hooks/use-connectors';
import AgentApiService, { KnowledgeBase } from './services/api';
import AgentChatInput from './components/agent-chat-input';
import AgentChatSidebar from './components/agent-chat-sidebar';

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

export interface Model {
  provider: string;
  modelName: string;
}

export interface ChatMode {
  id: string;
  name: string;
  description: string;
}

export interface Tool {
  name: string;
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
      console.log('Notifying updates, callback count:', this.updateCallbacks.size);
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
    const state = this.conversationStates[conversationKey] || null;
    console.log('Getting conversation state:', conversationKey, {
      hasState: !!state,
      messageId: state?.messageId,
      isActive: state?.isActive,
      content: state?.content?.substring(0, 50),
    });
    return state;
  }

  getConversationMessages(conversationKey: string): FormattedMessage[] {
    const messages = this.conversationMessages[conversationKey] || [];
    console.log('Getting conversation messages:', conversationKey, {
      messageCount: messages.length,
      messages: messages.map((m) => ({
        id: m.id,
        type: m.type,
        content: m.content.substring(0, 50),
      })),
    });
    return messages;
  }

  setConversationMessages(conversationKey: string, messages: FormattedMessage[]) {
    console.log('Setting conversation messages:', conversationKey, {
      messageCount: messages.length,
      messages: messages.map((m) => ({
        id: m.id,
        type: m.type,
        content: m.content.substring(0, 50),
      })),
    });
    this.conversationMessages[conversationKey] = messages;
    this.notifyUpdates();
  }

  updateConversationMessages(
    conversationKey: string,
    updater: (prev: FormattedMessage[]) => FormattedMessage[]
  ) {
    const prevMessages = this.conversationMessages[conversationKey] || [];
    const newMessages = updater(prevMessages);
    console.log('Updating conversation messages:', conversationKey, {
      prevCount: prevMessages.length,
      newCount: newMessages.length,
      newMessages,
    });
    this.conversationMessages[conversationKey] = newMessages;
    this.notifyUpdates();
  }

  updateConversationState(conversationKey: string, updates: Partial<ConversationStreamingState>) {
    console.log('Updating conversation state:', conversationKey, updates);
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
    console.log('Updating conversation status:', conversationKey, message);
    this.updateConversationState(conversationKey, {
      statusMessage: message,
      showStatus: true,
    });
  }

  clearStatus(conversationKey: string) {
    console.log('Clearing conversation status:', conversationKey);
    this.updateConversationState(conversationKey, {
      statusMessage: '',
      showStatus: false,
    });
  }

  mapMessageToConversation(messageId: string, conversationKey: string) {
    console.log('Mapping message to conversation:', { messageId, conversationKey });
    this.messageToConversationMap[messageId] = conversationKey;
  }

  getConversationForMessage(messageId: string): string | null {
    const conversationKey = this.messageToConversationMap[messageId] || null;
    console.log('Getting conversation for message:', { messageId, conversationKey });
    return conversationKey;
  }

  transferNewConversationData(newConversationId: string) {
    console.log('Transferring new conversation data:', newConversationId);
    const newKey = 'new';
    const actualKey = newConversationId;
    const newMessages = this.getConversationMessages(newKey);
    console.log('New conversation messages:', {
      newKey,
      actualKey,
      messageCount: newMessages.length,
    });
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
    console.log('New conversation data transfer completed');
  }

  static getPendingNavigation(): { conversationId: string; shouldNavigate: boolean } | null {
    console.log('Getting pending navigation');
    return null;
  }

  updateStreamingContent(messageId: string, newChunk: string, citations: CustomCitation[] = []) {
    const conversationKey = this.getConversationForMessage(messageId);
    console.log('updateStreamingContent called:', {
      messageId,
      newChunk: newChunk.substring(0, 50),
      conversationKey,
      citationsLength: citations.length,
    });

    if (!conversationKey) {
      console.error('No conversation key found for message:', messageId);
      return;
    }

    const state = this.conversationStates[conversationKey];
    if (!state?.isActive) {
      console.log('Initializing streaming state for conversation:', conversationKey);
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

    console.log('Content accumulation:', {
      previousLength: currentState?.accumulatedContent?.length || 0,
      newChunkLength: newChunk.length,
      totalLength: updatedAccumulatedContent.length,
    });

    // Process the accumulated content to get proper formatting
    const { processedContent, processedCitations } = processStreamingContentLegacy(
      updatedAccumulatedContent,
      citations.length > 0 ? citations : currentState?.citations || []
    );

    // Update the conversation state with accumulated content
    this.updateConversationState(conversationKey, {
      accumulatedContent: updatedAccumulatedContent,
      content: processedContent,
      citations: processedCitations,
    });

    // Update the conversation messages with the processed content
    this.updateConversationMessages(conversationKey, (prev) => {
      const messageIndex = prev.findIndex((msg) => msg.id === messageId);
      if (messageIndex === -1) {
        console.warn('Message not found in conversation for update:', messageId);
        return prev;
      }
      const updated = [...prev];
      updated[messageIndex] = {
        ...updated[messageIndex],
        content: processedContent,
        citations: processedCitations,
      };

      console.log('Updated message in conversation:', {
        messageId,
        updatedContentLength: processedContent.length,
        updatedCitationsLength: processedCitations.length,
      });

      return updated;
    });

    console.log('Streaming content updated for message:', messageId, {
      processedContentLength: processedContent.length,
      processedCitationsLength: processedCitations.length,
    });
  }

  finalizeStreaming(conversationKey: string, messageId: string, completionData: CompletionData) {
    console.log('Finalizing streaming for conversation:', conversationKey, {
      messageId,
      completionData,
    });
    const state = this.conversationStates[conversationKey];
    if (state?.isStreamingCompleted) {
      console.log('Streaming already completed for conversation:', conversationKey);
      return;
    }

    // Use the accumulated content from streaming as the primary source
    let finalContent = state?.accumulatedContent || '';
    let finalCitations = state?.citations || [];
    let finalMessageId = messageId;

    console.log('Pre-finalization state:', {
      hasAccumulatedContent: !!state?.accumulatedContent,
      accumulatedLength: state?.accumulatedContent?.length || 0,
      hasCompletionData: !!completionData?.conversation,
      citationsCount: finalCitations.length,
    });

    // Only use completion data if we don't have accumulated content from streaming
    if (!finalContent && completionData?.conversation) {
      console.log('Using completion data as fallback');
      const finalBotMessage = completionData.conversation.messages
        .filter((msg: any) => msg.messageType === 'bot_response')
        .pop();

      if (finalBotMessage) {
        const formatted = StreamingManager.formatMessage(finalBotMessage);
        if (formatted) {
          finalMessageId = formatted.id;
          finalContent = formatted.content;
          finalCitations = formatted.citations || [];
        }
      }
    }

    // Process the final content only once to ensure proper formatting
    const { processedContent, processedCitations } = processStreamingContentLegacy(
      finalContent,
      finalCitations
    );

    console.log('Final content processing:', {
      originalLength: finalContent.length,
      processedLength: processedContent.length,
      citationsCount: processedCitations.length,
    });

    // Update the conversation messages with the final processed content
    this.updateConversationMessages(conversationKey, (prev) =>
      prev.map((msg) =>
        msg.id === messageId
          ? {
              ...msg,
              id: finalMessageId,
              content: processedContent,
              citations: processedCitations,
              isStreamingCompleted: true,
            }
          : msg
      )
    );

    // Map the final message ID to the conversation
    this.mapMessageToConversation(finalMessageId, conversationKey);

    // Update the streaming state
    this.updateConversationState(conversationKey, {
      isActive: false,
      isProcessingCompletion: false,
      isCompletionPending: false,
      isStreamingCompleted: true,
      content: processedContent,
      citations: processedCitations,
      finalMessageId,
      messageId: finalMessageId,
      statusMessage: '',
      showStatus: false,
      completionData: null,
      accumulatedContent: finalContent, // Keep the original accumulated content
    });

    console.log('Streaming finalized for conversation:', conversationKey, {
      finalMessageId,
      contentLength: processedContent.length,
      citationsCount: processedCitations.length,
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
    console.log('Clearing streaming for conversation:', conversationKey);
    const state = this.conversationStates[conversationKey];
    if (!state) return;

    if (state.controller && !state.controller.signal.aborted) {
      state.controller.abort();
    }
    this.conversationStates[conversationKey] = StreamingManager.initializeStreamingState();
    this.notifyUpdates();
    console.log('Streaming cleared for conversation:', conversationKey);
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
    console.log('Creating streaming message:', { messageId, conversationKey });
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
    console.log('Streaming message created and added to conversation');
  }

  resetNavigationTracking() {
    console.log('Resetting navigation tracking');
    this.completedNavigations.clear();
  }

  isConversationLoading(conversationKey: string): boolean {
    const state = this.getConversationState(conversationKey);
    const isLoading = !!(
      state &&
      (state.isActive || state.isProcessingCompletion || state.showStatus)
    );
    console.log('Checking if conversation is loading:', conversationKey, {
      hasState: !!state,
      isActive: state?.isActive,
      isProcessingCompletion: state?.isProcessingCompletion,
      showStatus: state?.showStatus,
      isLoading,
    });
    return isLoading;
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

const AgentChat = () => {
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoadingConversation, setIsLoadingConversation] = useState<boolean>(false);
  const [expandedCitations, setExpandedCitations] = useState<ExpandedCitationsState>({});
  const [isDrawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [selectedChat, setSelectedChat] = useState<Conversation | null>(null);
  const [shouldRefreshSidebar, setShouldRefreshSidebar] = useState<boolean>(false);
  const [isNavigationBlocked, setIsNavigationBlocked] = useState<boolean>(false);
  const [availableModels, setAvailableModels] = useState<Model[]>([]);
  const [availableTools, setAvailableTools] = useState<Tool[]>([]);
  const [availableKBs, setAvailableKBs] = useState<KnowledgeBase[]>([]);
  // Model selection state
  const [selectedModel, setSelectedModel] = useState<Model | null>(availableModels[0]);
  const [selectedChatMode, setSelectedChatMode] = useState<ChatMode | null>(null);
  const { activeConnectors } = useConnectors();
  const navigate = useNavigate();
  const { agentKey, conversationId } = useParams<{ agentKey: string; conversationId: string }>();
  console.log(agentKey, conversationId);
  const [agent, setAgent] = useState<Agent | null>(null);
  useEffect(() => {
    if (agentKey) {
      AgentApiService.getAgent(agentKey).then((agentItem) => {
        setAgent(agentItem);
        setAvailableModels(agentItem.models);
        setAvailableTools(agentItem.tools.map((tool: string) => ({ name: tool })));
      });
    }
  }, [agentKey]);
  
  const startMessage = agent?.startMessage || '';

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
  const [highlightedCitation, setHighlightedCitation] = useState<CustomCitation | null>(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });

  const [updateTrigger, setUpdateTrigger] = useState(0);
  const forceUpdate = useCallback(() => {
    console.log('Force update triggered, current trigger:', updateTrigger);
    setUpdateTrigger((prev) => prev + 1);
  }, [updateTrigger]);

  const streamingManager = StreamingManager.getInstance();
  const theme = useTheme();

  const getConversationKey = useCallback((convId: string | null) => {
    const key = convId || 'new';
    console.log('Getting conversation key:', { convId, key });
    return key;
  }, []);

  const currentConversationKey = useMemo(
    () => getConversationKey(currentConversationId),
    [currentConversationId, getConversationKey]
  );

  const currentMessages = useMemo(
    () => {
      const messages = streamingManager.getConversationMessages(currentConversationKey);
      console.log('Current messages for conversation:', currentConversationKey, {
        messageCount: messages.length,
        messages: messages.map((m) => ({
          id: m.id,
          type: m.type,
          content: m.content.substring(0, 50),
        })),
      });
      return messages;
    },
    // eslint-disable-next-line
    [streamingManager, currentConversationKey, updateTrigger]
  );

  const currentStreamingState = useMemo(() => {
    const state = streamingManager.getConversationState(currentConversationKey);
    console.log('Current streaming state for conversation:', currentConversationKey, {
      messageId: state?.messageId,
      isActive: state?.isActive,
      content: state?.content?.substring(0, 50),
      citationsCount: state?.citations?.length,
    });
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
    const status = {
      statusMessage: state?.statusMessage || '',
      showStatus: state?.showStatus || false,
    };
    console.log('Current conversation status for conversation:', currentConversationKey, status);
    return status;
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger]);

  const isCurrentConversationLoading = useMemo(() => {
    const streamingState = streamingManager.getConversationState(currentConversationKey);
    const isLoading =
      streamingManager.isConversationLoading(currentConversationKey) || isLoadingConversation;
    console.log('Current conversation loading state for conversation:', currentConversationKey, {
      streamingLoading: streamingManager.isConversationLoading(currentConversationKey),
      conversationLoading: isLoadingConversation,
      totalLoading: isLoading,
    });
    return isLoading;
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger, isLoadingConversation]);

  useEffect(() => {
    streamingManager.addUpdateCallback(forceUpdate);
    console.log('Added update callback to streaming manager');
    return () => {
      streamingManager.removeUpdateCallback(forceUpdate);
      console.log('Removed update callback from streaming manager');
    };
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
      if (done) {
        console.log('Stream reading completed');
        return;
      }

      const chunk = decoder.decode(value, { stream: true });
      console.log('Raw chunk received:', chunk);
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      console.log('Processing lines:', lines);
      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        const trimmedLine = line.trim();
        // eslint-disable-next-line
        if (!trimmedLine) continue;

        const parsed = parseSSELineFunc(trimmedLine);
        console.log('Parsed line:', { line: trimmedLine, parsed });
        // eslint-disable-next-line
        if (!parsed) continue;

        if (parsed.event) {
          currentEvent = parsed.event;
          console.log('Current event set to:', currentEvent);
        } else if (parsed.data && currentEvent) {
          console.log('Processing event data:', { event: currentEvent, data: parsed.data });
          // eslint-disable-next-line
          await handleStreamingEvent(currentEvent, parsed.data, context);
        }
      }

      if (!controller.signal.aborted) {
        console.log('Continuing to read next chunk...');
        await readNextChunk();
      } else {
        console.log('Stream processing aborted');
      }
    };

    await readNextChunk();
  };

  // Refactored main function as a standard async function
  const handleStreamingResponse = useCallback(
    async (url: string, body: any, isNewConversation: boolean): Promise<string | null> => {
      const streamingBotMessageId = `streaming-${Date.now()}`;
      const conversationKey = isNewConversation ? 'new' : getConversationKey(currentConversationId);
      console.log(conversationKey);
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
        console.log('🔥 Streaming event received:', {
          event,
          dataKeys: Object.keys(data),
          conversationKey: context.conversationKey,
          hasCreatedMessage: context.hasCreatedMessage.current,
          streamingBotMessageId: context.streamingBotMessageId,
        });

        const statusMsg = getEngagingStatusMessage(event, data);
        if (statusMsg) {
          streamingManager.updateStatus(context.conversationKey, statusMsg);
        }

        switch (event) {
          case 'answer_chunk':
            if (data.chunk) {
              console.log('📝 Answer chunk received:', {
                chunkLength: data.chunk.length,
                chunkPreview: data.chunk.substring(0, 50),
                citationsLength: data.citations?.length || 0,
                hasCreatedMessage: context.hasCreatedMessage.current,
                messageId: context.streamingBotMessageId,
              });

              if (!context.hasCreatedMessage.current) {
                console.log('🆕 Creating streaming message for first chunk');
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
            } else {
              console.warn('⚠️ Answer chunk event received but no chunk data');
            }
            break;

          case 'complete': {
            console.log('✅ Streaming complete event received:', {
              hasConversation: !!data.conversation,
              conversationId: data.conversation?._id,
              messagesLength: data.conversation?.messages?.length || 0,
            });

            streamingManager.clearStatus(context.conversationKey);
            const completedConversation = data.conversation;

            if (completedConversation?._id) {
              let finalKey = context.conversationKey;
              if (context.isNewConversation && context.conversationKey === 'new') {
                console.log('🔄 Transferring new conversation data:', completedConversation._id);
                streamingManager.transferNewConversationData(completedConversation._id);
                finalKey = completedConversation._id;
                context.conversationIdRef.current = completedConversation._id;
              }
              streamingManager.finalizeStreaming(finalKey, context.streamingBotMessageId, data);
            } else {
              console.warn('⚠️ Complete event received but no conversation ID found');
            }
            break;
          }

          case 'error': {
            console.error('❌ Streaming error event received:', data);
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
            console.log('🤷 Unhandled streaming event:', event, data);
            break;
        }
      };

      try {
        // Make the HTTP request
        const token = localStorage.getItem('jwt_access_token');
        console.log('Making HTTP request to:', url);
        console.log('Request headers:', {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          Authorization: `Bearer ${token}`,
        });
        console.log('Request body:', body);

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

        console.log('Response received:', { status: response.status, ok: response.ok });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Failed to get response reader');
        }

        console.log('Stream reader created successfully');
        const decoder = new TextDecoder();

        // Process the stream using the helper function
        console.log('Starting stream processing for conversation:', conversationKey);
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

        console.log('Stream processing completed for conversation:', conversationKey);

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
      selectedTools?: string[], // app_name.tool_name format - PERSISTENT
      selectedKBs?: string[], // KB IDs - PERSISTENT
      selectedApps?: string[] // App names - PERSISTENT
    ): Promise<void> => {
      const trimmedInput =
        typeof messageOverride === 'string' ? messageOverride.trim() : inputValue.trim();
      if (!trimmedInput) return;
      if (isNavigationBlocked || isCurrentConversationLoading) return;

      console.log('handleSendMessage called with persistent selections:', {
        trimmedInput,
        currentConversationId,
        agentKey,
        persistentTools: selectedTools, // These persist across messages
        persistentKBs: selectedKBs, // These persist across messages
        persistentApps: selectedApps, // These persist across messages
        chatMode: chatMode || selectedChatMode?.id,
        modelName: modelName || selectedModel?.modelName,
      });

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

      setInputValue('');
      streamingManager.updateConversationMessages(conversationKey, (prev) => [
        ...prev,
        tempUserMessage,
      ]);

      const streamingUrl = wasCreatingNewConversation
        ? `${CONFIG.backendUrl}/api/v1/agents/${agentKey}/conversations/stream`
        : `${CONFIG.backendUrl}/api/v1/agents/${agentKey}/conversations/${currentConversationId}/messages/stream`;

      // Build the request body with persistent selections
      const requestBody = {
        query: trimmedInput,
        modelName: selectedModel?.modelName || modelName,
        chatMode: chatMode || selectedChatMode?.id,

        // Tools: Use persistent selected tools (app_name.tool_name format)
        // If no tools selected, use agent defaults, if no agent defaults, use empty array
        tools: selectedTools && selectedTools.length > 0 ? selectedTools : agent?.tools || [],

        // Enhanced filters structure with persistent selections
        filters: {
          departments: [],
          moduleIds: [],
          appSpecificRecordTypes: [],

          // Apps: Use persistent selected app names
          // If no apps selected, use agent defaults, if no agent defaults, use empty array
          apps: selectedApps && selectedApps.length > 0 ? selectedApps : agent?.apps || [],

          // Knowledge bases: Use persistent selected KB IDs
          // If no KBs selected, use agent defaults, if no agent defaults, use empty array
          kb: selectedKBs && selectedKBs.length > 0 ? selectedKBs : agent?.kb || [],
        },

        // Chat mode specific parameters (persistent throughout conversation)
        ...(chatMode === 'quick' && {
          limit: 5,
          quickMode: true,
        }),
        ...(chatMode === 'standard' && {
          temperature: 0.7,
          limit: 10,
          quickMode: false,
        }),
      };

      console.log('Streaming URL:', streamingUrl);
      console.log('Request body with persistent selections:', requestBody);
      console.log('Persistent filter details:', {
        'tools (app_name.tool_name)': requestBody.tools,
        'filters.apps (app names)': requestBody.filters.apps,
        'filters.kb (KB IDs)': requestBody.filters.kb,
        chatMode: requestBody.chatMode,
        totalToolsCount: requestBody.tools.length,
        totalAppsCount: requestBody.filters.apps.length,
        totalKBsCount: requestBody.filters.kb.length,
      });

      try {
        const createdConversationId = await handleStreamingResponse(
          streamingUrl,
          requestBody,
          wasCreatingNewConversation
        );

        if (wasCreatingNewConversation && createdConversationId) {
          setCurrentConversationId(createdConversationId);
          setShouldRefreshSidebar(true);
        }
      } catch (error) {
        console.error('Error in streaming response:', error);
      }
    },
    [
      inputValue,
      currentConversationId,
      streamingManager,
      getConversationKey,
      handleStreamingResponse,
      isNavigationBlocked,
      isCurrentConversationLoading,
      selectedModel,
      selectedChatMode,
      agent,
      agentKey,
    ]
  );

  const handleNewChat = useCallback(() => {
    // Clear both current conversation and 'new' conversation states
    if (currentConversationId) {
      streamingManager.clearStreaming(getConversationKey(currentConversationId));
    }
    // Also clear the 'new' conversation state
    streamingManager.clearStreaming('new');
    streamingManager.resetNavigationTracking();

    setCurrentConversationId(null);
    navigate(`/agents/${agentKey}`, { replace: true });
    setInputValue('');
    setShouldRefreshSidebar(true);
    setSelectedChat(null);
    setIsNavigationBlocked(false);

    console.log('New chat initiated, cleared all streaming states');
  }, [navigate, streamingManager, currentConversationId, getConversationKey, agentKey]);

  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id || isNavigationBlocked) return;

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

        // Update current conversation ID before navigation
        setCurrentConversationId(chat._id);

        // Navigate to the chat
        navigate(`/agents/${agentKey}/conversations/${chat._id}`, { replace: true });

        const existingMessages = streamingManager.getConversationMessages(chatKey);

        if (!existingMessages.length && !isCurrentlyStreaming) {
          // Only fetch if we don't have messages and it's not currently streaming
          const response = await axios.get(`/api/v1/agents/${agentKey}/conversations/${chat._id}`);
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
      }
    },
    [formatMessage, navigate, streamingManager, getConversationKey, isNavigationBlocked, agentKey]
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

  // Add effect to show start message when creating new conversation
  useEffect(() => {
    if (!currentConversationId && agent?.startMessage) {
      const conversationKey = getConversationKey(null);
      const existingMessages = streamingManager.getConversationMessages(conversationKey);

      // Only add start message if there are no messages at all
      if (existingMessages.length === 0) {
        const customStartMessage: FormattedMessage = {
          type: 'bot',
          content: agent.startMessage,
          createdAt: new Date(),
          updatedAt: new Date(),
          id: 'start-message',
          contentFormat: 'MARKDOWN',
          followUpQuestions: [],
          citations: [],
          confidence: '',
          messageType: 'bot_response',
          timestamp: new Date(),
        };

        console.log('Adding start message for agent:', agent.name);
        streamingManager.setConversationMessages(conversationKey, [customStartMessage]);
      }
    }
  }, [currentConversationId, agent?.startMessage, getConversationKey, streamingManager,agent?.name]);

  useEffect(() => {
    const fetchKBs = async () => {
      const kbs = await AgentApiService.getKnowledgeBases({
        page: 1,
        limit: 100,
      });
      setAvailableKBs(kbs.knowledgeBases);
    };
    fetchKBs();
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
    setTransitioning(true);
    setIsViewerReady(false);
    setDrawerOpen(false);
    setOpenPdfView(true);
    setAggregatedCitations(citations);
    setFileBuffer(null);
    setPdfUrl(null);
    setHighlightedCitation(citation || null);

    try {
      const recordId = citationMeta?.recordId;
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

          let filename = fileName || `document-${externalRecordId}`;
          const contentDisposition = downloadResponse.headers['content-disposition'];
          if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?([^"]*)"?/);
            if (filenameMatch && filenameMatch[1]) {
              filename = filenameMatch[1];
            }
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

          let filename = record.recordName || `document-${recordId}`;
          const contentDisposition = connectorResponse.headers['content-disposition'];
          if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?([^"]*)"?/);
            if (filenameMatch && filenameMatch[1]) {
              filename = filenameMatch[1];
            }
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
    setIsExcel(isExcelOrCSV);
    setIsPdf(['pptx', 'ppt', 'pdf'].includes(citationMeta?.extension));

    setTimeout(() => {
      setIsViewerReady(true);
      setTransitioning(false);
    }, 100);
  };

  const onClosePdf = (): void => {
    resetViewerStates();
    setFileBuffer(null);
    setHighlightedCitation(null);
  };

  const handleRegenerateMessage = useCallback(
    async (messageId: string): Promise<void> => {
      if (!currentConversationId || !messageId || isCurrentConversationLoading) return;

      try {
        const conversationKey = getConversationKey(currentConversationId);

        const response = await axios.post<{ conversation: Conversation }>(
          `/api/v1/agents/${agentKey}/conversations/${currentConversationId}/message/${messageId}/regenerate`,
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
      agentKey,
    ]
  );

  const handleSidebarRefreshComplete = useCallback(() => setShouldRefreshSidebar(false), []);

  const handleFeedbackSubmit = useCallback(
    async (messageId: string, feedback: any) => {
      if (!currentConversationId || !messageId) return;
      try {
        await axios.post(
          `/api/v1/agents/${agentKey}/conversations/${currentConversationId}/message/${messageId}/feedback`,
          feedback
        );
      } catch (error) {
        throw new Error('Feedback submission error');
      }
    },
    [currentConversationId, agentKey]
  );

  const MemoizedChatMessagesArea = useMemo(() => React.memo(ChatMessagesArea), []);

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
            <AgentChatSidebar
              onClose={() => setDrawerOpen(false)}
              onChatSelect={handleChatSelect}
              onNewChat={handleNewChat}
              selectedId={currentConversationId}
              shouldRefresh={shouldRefreshSidebar}
              onRefreshComplete={handleSidebarRefreshComplete}
              agent={agent}
              activeConnectors={activeConnectors}
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
            {/* Always show the chat messages area instead of welcome message */}
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
            <AgentChatInput
              key={`chat-input-${currentConversationId || 'new'}`}
              onSubmit={handleSendMessage}
              isLoading={isCurrentConversationLoading}
              disabled={isCurrentConversationLoading || isNavigationBlocked}
              placeholder="Type your message..."
              selectedModel={selectedModel}
              selectedChatMode={selectedChatMode}
              onModelChange={(model) => setSelectedModel(model)}
              onChatModeChange={(mode) => setSelectedChatMode(mode)}
              availableModels={availableModels}
              availableKBs={availableKBs}
              agent={agent}
              activeConnectors={activeConnectors}
            />
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

export default AgentChat;
