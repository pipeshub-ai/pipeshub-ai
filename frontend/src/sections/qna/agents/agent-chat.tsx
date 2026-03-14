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
import { ConnectorApiService } from 'src/sections/accountdetails/connectors/services/api';

import { Agent } from 'src/types/agent';
import HtmlViewer from 'src/sections/qna/chatbot/components/html-highlighter';
import TextViewer from 'src/sections/qna/chatbot/components/text-highlighter';
import ExcelViewer from 'src/sections/qna/chatbot/components/excel-highlighter';
import ChatMessagesArea from 'src/sections/qna/chatbot/components/chat-message-area';
import PdfHighlighterComp from 'src/sections/qna/chatbot/components/pdf-highlighter';
import MarkdownViewer from 'src/sections/qna/chatbot/components/markdown-highlighter';
import DocxHighlighterComp from 'src/sections/qna/chatbot/components/docx-highlighter';
import ImageHighlighter from 'src/sections/qna/chatbot/components/image-highlighter';
import { StreamingContext } from 'src/sections/qna/chatbot/components/chat-message';
import { processStreamingContentLegacy } from 'src/sections/qna/chatbot/utils/styles/content-processing';
import { useConnectors } from 'src/sections/accountdetails/connectors/hooks/use-connectors';
import { ConversationStreamingState } from 'src/sections/qna/chatbot/chat-bot';
import AgentApiService, { KnowledgeBase } from './services/api';
import AgentChatInput, { ChatMode } from './components/agent-chat-input';
import AgentChatSidebar from './components/agent-chat-sidebar';

const DRAWER_WIDTH = 300;
const AUTO_CHAT_MODE = 'auto';

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
  modelKey: string;
  modelName: string;
  modelFriendlyName?: string;
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

  resetStreamingContent(messageId: string) {
    const conversationKey = this.getConversationForMessage(messageId);
    if (!conversationKey) return;

    // Reset accumulated content and citations to start fresh
    this.updateConversationState(conversationKey, {
      accumulatedContent: '',
      content: '',
      citations: [],
    });

    // Clear the message content in the UI
    this.updateConversationMessages(conversationKey, (prev) => {
      const messageIndex = prev.findIndex((msg) => msg.id === messageId);
      if (messageIndex === -1) return prev;
      const updated = [...prev];
      updated[messageIndex] = {
        ...updated[messageIndex],
        content: '',
        citations: [],
      };
      return updated;
    });
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
        confidence: '',
      });
    }

    const currentState = this.conversationStates[conversationKey];
    const updatedAccumulatedContent = (currentState?.accumulatedContent || '') + newChunk;

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
      confidence: state?.confidence || '',
    });

    // Update the conversation messages with the processed content
    this.updateConversationMessages(conversationKey, (prev) => {
      const messageIndex = prev.findIndex((msg) => msg.id === messageId);
      if (messageIndex === -1) return prev;
      const updated = [...prev];
      updated[messageIndex] = {
        ...updated[messageIndex],
        content: processedContent,
        citations: processedCitations,
        confidence: state?.confidence || '',
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
    let finalConfidence = state?.confidence || '';
    let finalModelInfo: any = null;

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
          finalConfidence = formatted.confidence || '';
          // Get modelInfo from message first, then fallback to conversation
          finalModelInfo =
            formatted.modelInfo || (completionData.conversation as any).modelInfo || null;
        }
      } else {
        // If no bot message, use conversation-level modelInfo
        finalModelInfo = (completionData.conversation as any).modelInfo || null;
      }
    }

    this.updateConversationMessages(conversationKey, (prev) =>
      prev.map((msg) =>
        msg.id === messageId
          ? {
            ...msg,
            id: finalMessageId,
            content: finalContent,
            citations: finalCitations,
            confidence: finalConfidence,
            modelInfo: finalModelInfo || msg.modelInfo || null,
          }
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
      confidence: finalConfidence,
    });
  }

  private static formatMessage(apiMessage: any): FormattedMessage | null {
    if (!apiMessage) return null;
    const baseMessage = {
      id: apiMessage._id,
      timestamp: new Date(apiMessage.createdAt || new Date()),
      content: apiMessage.content || '',
      type:
        apiMessage.messageType === 'user_query'
          ? 'user'
          : apiMessage.messageType === 'error'
            ? 'error'
            : 'bot',
      contentFormat: apiMessage.contentFormat || 'MARKDOWN',
      followUpQuestions: apiMessage.followUpQuestions || [],
      createdAt: apiMessage.createdAt ? new Date(apiMessage.createdAt) : new Date(),
      updatedAt: apiMessage.updatedAt ? new Date(apiMessage.updatedAt) : new Date(),
      confidence: apiMessage.confidence || '',
      messageType: apiMessage.messageType,
    };

    if (apiMessage.messageType === 'user_query') {
      return { ...baseMessage, type: 'user', feedback: apiMessage.feedback || [] };
    }

    if (apiMessage.messageType === 'bot_response') {
      return {
        ...baseMessage,
        type: 'bot',
        confidence: apiMessage.confidence || '',
        modelInfo: apiMessage.modelInfo || null,
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
    this.conversationStates[conversationKey] = StreamingManager.initializeStreamingState();
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
      confidence: '',
      createdAt: new Date(),
      updatedAt: new Date(),
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

  resetNewConversation() {
    const draftKey = 'new';
    const state = this.conversationStates[draftKey];
    if (state?.controller && !state.controller.signal.aborted) {
      state.controller.abort();
    }
    this.conversationStates[draftKey] = StreamingManager.initializeStreamingState();
    // Clear messages - this ensures start message can be shown again
    this.conversationMessages[draftKey] = [];
    this.notifyUpdates();
  }

  clearAllConversations() {
    // Abort all active streams
    Object.values(this.conversationStates).forEach((state) => {
      if (state?.controller && !state.controller.signal.aborted) {
        state.controller.abort();
      }
    });
    // Clear all states and messages
    this.conversationStates = {};
    this.conversationMessages = {};
    this.messageToConversationMap = {};
    this.completedNavigations.clear();
    this.notifyUpdates();
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
    case 'metadata':
      return '💾 Saving metadata...';
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
  // Chat mode state
  const [chatMode, setChatMode] = useState<ChatMode>('auto');
  // Counter to signal AgentChatInput to clear its text (incremented on new chat, conversation switch, etc.)
  const [clearInputTrigger, setClearInputTrigger] = useState<number>(0);

  // Ref to store persistent chatMode that survives new chat clicks
  const persistentChatModeRef = useRef<ChatMode>('auto');

  // Refs to store latest values to avoid stale closures in callbacks
  const latestModelRef = useRef(selectedModel);
  const latestChatModeRef = useRef<ChatMode>(chatMode);
  const availableModelsRef = useRef<Model[]>([]);

  // Update refs whenever values change
  useEffect(() => {
    latestModelRef.current = selectedModel;
  }, [selectedModel]);

  useEffect(() => {
    latestChatModeRef.current = chatMode;
    // Update persistent ref whenever chatMode changes (user selection or conversation load)
    persistentChatModeRef.current = chatMode;
  }, [chatMode]);

  // Keep availableModels ref in sync
  useEffect(() => {
    availableModelsRef.current = availableModels;
  }, [availableModels]);

  // Helper function to set model from conversation modelInfo
  const setModelFromConversation = useCallback((conversationModelInfo: any) => {
    if (!conversationModelInfo) return;

    // Use ref to get latest models to avoid stale closure issues
    const models = availableModelsRef.current;
    if (models.length === 0) {
      // Models not loaded yet, will be set by useEffect when models load
      return;
    }

    // Set model from conversation if available
    if (conversationModelInfo.modelName) {
      // Try to find matching model by modelName
      const matchingModel = models.find((m) => m.modelName === conversationModelInfo.modelName);

      if (matchingModel) {
        setSelectedModel(matchingModel);
      }
    }
  }, []);

  // Set default model when models are first loaded
  // Only set defaults if no conversation is selected (new chat scenario)
  useEffect(() => {
    // Don't set defaults if we have a selected chat - let the conversation model useEffect handle it
    if (selectedChat) return;

    if (availableModels.length > 0 && !selectedModel) {
      // Set first model as default if no model is selected and no conversation is selected
      setSelectedModel(availableModels[0]);
    }
  }, [availableModels, selectedModel, selectedChat]);

  // When models are loaded and we have a selected chat, try to set model from conversation
  useEffect(() => {
    if (availableModels.length > 0 && selectedChat) {
      const conversationModelInfo = (selectedChat as any).modelInfo;
      if (conversationModelInfo) {
        setModelFromConversation(conversationModelInfo);
      }
    }
  }, [availableModels.length, selectedChat, setModelFromConversation]);

  // Restore chatMode from conversation when selecting an existing chat
  // For new chats, preserve the persistent chatMode (don't reset to 'auto')
  useEffect(() => {
    if (selectedChat) {
      const conversationModelInfo = (selectedChat as any).modelInfo;
      if (conversationModelInfo?.chatMode) {
        const conversationChatMode = conversationModelInfo.chatMode as ChatMode;
        setChatMode(conversationChatMode);
        persistentChatModeRef.current = conversationChatMode;
      }
    } else {
      // When selectedChat is null (new chat), restore the persistent chatMode
      setChatMode(persistentChatModeRef.current);
    }
  }, [selectedChat]);

  const { activeConnectors } = useConnectors();
  const navigate = useNavigate();
  const { agentKey, conversationId } = useParams<{ agentKey: string; conversationId: string }>();
  const [agent, setAgent] = useState<Agent | null>(null);
  const previousAgentKeyRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (agentKey) {
      AgentApiService.getAgent(agentKey).then((agentItem) => {
        setAgent(agentItem);
        
        // Models are now enriched objects with modelName, provider, etc.
        // Backend enriches model keys into full model objects
        if (agentItem.models && agentItem.models.length > 0) {
          const models = agentItem.models.map((m: any) => {
            // Check if it's an enriched object or just a string key
            if (typeof m === 'object' && m !== null) {
              return {
                provider: m.provider || 'unknown',
                modelKey: m.modelKey || '',
                modelName: m.modelName || m.modelKey || 'Unknown Model',
                modelFriendlyName: m.modelFriendlyName || m.modelName || m.modelKey || 'Unknown Model',
              };
            }
            console.log('m', m);
            // Fallback for string model keys
            return {
              provider: 'AI',
              modelKey: m,
              modelName: m,
            };
          });
          setAvailableModels(models);
        }
        
        // Build tools array from toolsets (new format) or use legacy tools array
        const toolsList: { name: string }[] = [];
        if (agentItem.toolsets && agentItem.toolsets.length > 0) {
          // New format: extract tools from toolsets
          agentItem.toolsets.forEach((toolset: any) => {
            if (toolset.tools && toolset.tools.length > 0) {
              toolset.tools.forEach((tool: any) => {
                const fullName = tool.fullName || `${toolset.name}.${tool.name}`;
                toolsList.push({ name: fullName });
              });
            } else if (toolset.selectedTools && toolset.selectedTools.length > 0) {
              // Use selectedTools if tools are not expanded
              toolset.selectedTools.forEach((toolName: string) => {
                const fullName = toolName.includes('.') ? toolName : `${toolset.name}.${toolName}`;
                toolsList.push({ name: fullName });
              });
            }
          });
        } else if (agentItem.tools && Array.isArray(agentItem.tools)) {
          // Legacy format: tools is array of strings
          agentItem.tools.forEach((tool: string) => {
            toolsList.push({ name: tool });
          });
        }
        setAvailableTools(toolsList);
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
  const [isImage, setIsImage] = useState<boolean>(false);
  const [highlightedCitation, setHighlightedCitation] = useState<CustomCitation | null>(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });

  const [updateTrigger, setUpdateTrigger] = useState(0);
  const forceUpdate = useCallback(() => {
    setUpdateTrigger((prev) => prev + 1);
  }, []);

  const streamingManager = StreamingManager.getInstance();
  const theme = useTheme();

  const getConversationKey = useCallback((convId: string | null) => {
    const key = convId || 'new';
    return key;
  }, []);

  const currentConversationKey = useMemo(
    () => getConversationKey(currentConversationId),
    [currentConversationId, getConversationKey]
  );

  const currentMessages = useMemo(
    () => {
      const messages = streamingManager.getConversationMessages(currentConversationKey);
      return messages;
    },
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
    const status = {
      statusMessage: state?.statusMessage || '',
      showStatus: state?.showStatus || false,
    };
    return status;
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger]);

  const isCurrentConversationLoading = useMemo(() => {
    const streamingState = streamingManager.getConversationState(currentConversationKey);
    const isLoading =
      streamingManager.isConversationLoading(currentConversationKey) || isLoadingConversation;
    return isLoading;
    // eslint-disable-next-line
  }, [streamingManager, currentConversationKey, updateTrigger, isLoadingConversation]);

  useEffect(() => {
    streamingManager.addUpdateCallback(forceUpdate);
    return () => {
      streamingManager.removeUpdateCallback(forceUpdate);
    };
  }, [streamingManager, forceUpdate]);

  // Clear all conversation states when agent changes
  useEffect(() => {
    if (agentKey && previousAgentKeyRef.current && previousAgentKeyRef.current !== agentKey) {
      // Agent has changed - clear all conversation states
      streamingManager.clearAllConversations();
      setCurrentConversationId(null);
      setSelectedChat(null);
      setInputValue('');
      setExpandedCitations({});
      setIsNavigationBlocked(false);
      setIsLoadingConversation(false);

      // Reset model selection so it picks up new agent's models
      setSelectedModel(null);

      // Reset chat mode to auto for new agent
      setChatMode('auto');
      persistentChatModeRef.current = 'auto';

      // Navigate to the new agent's base URL (without conversation ID) to ensure clean state
      navigate(`/agents/${agentKey}`, { replace: true });

      // Reset the 'new' conversation state for the new agent
      streamingManager.resetNewConversation();

      // Force update to ensure UI reflects the cleared state
      forceUpdate();
    }
    previousAgentKeyRef.current = agentKey;
  }, [agentKey, streamingManager, navigate, forceUpdate]);

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
      messageType: apiMessage.messageType,
      modelInfo: apiMessage.modelInfo || null,
    };
    if (apiMessage.messageType === 'user_query') {
      return { ...baseMessage, type: 'user', feedback: apiMessage.feedback || [] };
    }
    if (apiMessage.messageType === 'bot_response') {
      return {
        ...baseMessage,
        type: 'bot',
        confidence: apiMessage.confidence || '',
        modelInfo: (apiMessage as any).modelInfo || null,
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

  const parseSSELine = useCallback((line: string): { event?: string; data?: any } | null => {
    if (line.startsWith('event: ')) return { event: line.substring(7).trim() };
    if (line.startsWith('data: ')) {
      try {
        return { data: JSON.parse(line.substring(6).trim()) };
      } catch (e) {
        return null;
      }
    }
    return null;
  }, []);

  // Extract the stream processing logic into a separate helper function
  const processStreamChunk = useCallback(
    async (
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

        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
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
    },
    []
  );

  // Refactored main function as a standard async function
  const handleStreamingResponse = useCallback(
    async (url: string, body: any, isNewConversation: boolean): Promise<string | null> => {
      const streamingBotMessageId = `streaming-${Date.now()}`;
      const conversationKey = isNewConversation ? 'new' : getConversationKey(currentConversationId);
      const currentConvId = currentConversationId; // Capture current conversation ID

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
          case 'restreaming':
            // When restreaming event is received, clear previous accumulated content
            // and wait for new chunks to start streaming
            if (context.hasCreatedMessage.current) {
              streamingManager.resetStreamingContent(context.streamingBotMessageId);
            }
            streamingManager.updateStatus(
              context.conversationKey,
              '🔄 Refining response...'
            );
            break;

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

          case 'metadata':
            // Status message is already handled by getEngagingStatusMessage above
            // This event indicates metadata is being saved, so we keep the status visible
            break;

          case 'complete': {
            streamingManager.clearStatus(context.conversationKey);
            const completedConversation = data.conversation;

            if (completedConversation?._id) {
              let finalKey = context.conversationKey;
              if (context.isNewConversation && context.conversationKey === 'new') {
                streamingManager.transferNewConversationData(completedConversation._id);
                finalKey = completedConversation._id;
                context.conversationIdRef.current = completedConversation._id;
              }
              streamingManager.finalizeStreaming(finalKey, context.streamingBotMessageId, data);

              // Update selectedChat with fresh conversation data to reflect updated modelInfo
              // This ensures the model selection is updated when switching back to this conversation
              const finalConvId = finalKey === 'new' ? context.conversationIdRef.current : finalKey;
              if (finalConvId === currentConvId || finalConvId === context.conversationIdRef.current) {
                // Use setTimeout to ensure this runs after state updates
                setTimeout(() => {
                  setSelectedChat(completedConversation);
                  // Update model selection if modelInfo changed
                  if ((completedConversation as any).modelInfo) {
                    setModelFromConversation((completedConversation as any).modelInfo);
                  }
                }, 0);
              }
            } else {
              // complete event received but conversation data is missing/incomplete —
              // still finalize so the loading state is cleared
              streamingManager.finalizeStreaming(
                context.conversationKey,
                context.streamingBotMessageId,
                data
              );
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

        // Guard: if the stream closed without emitting a complete event (e.g. server
        // timeout, network drop, or CancelledError on the backend), isActive is still
        // true and the loading indicator would spin forever.  Clear it here.
        const resolvedKey = conversationIdRef.current
          ? getConversationKey(conversationIdRef.current)
          : conversationKey;
        const endState = streamingManager.getConversationState(resolvedKey);
        if (endState?.isActive) {
          streamingManager.clearStreaming(resolvedKey);
        }

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
    [
      currentConversationId,
      getConversationKey,
      streamingManager,
      parseSSELine,
      processStreamChunk,
      setModelFromConversation,
    ]
  );

  // Updated handleSendMessage to properly handle the promise
  const handleSendMessage = useCallback(
    async (
      messageOverride?: string,
      modelKey?: string,
      modelName?: string,
      inputChatMode?: string,
      selectedTools?: string[], // app_name.tool_name format - PERSISTENT
      selectedKBs?: string[], // KB IDs - PERSISTENT
      selectedApps?: string[] // App names - PERSISTENT
    ): Promise<void> => {
      const trimmedInput =
        typeof messageOverride === 'string' ? messageOverride.trim() : inputValue.trim();
      if (!trimmedInput) return;
      // Only block if actively loading, not if there's just an error state
      if (isCurrentConversationLoading) return;

      // Use refs to get the latest values (prevents stale closures)
      const currentModel = latestModelRef.current;
      const resolvedChatMode = inputChatMode || latestChatModeRef.current || AUTO_CHAT_MODE;

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

      // Build the request body with the new graph-based format
      // No backward compatibility - only use toolsets and knowledge
      
      // Get tools to enable from user selection or use all tools from agent
      const getEnabledTools = (): string[] => {
        // If user selected specific tools, use those
        if (selectedTools && selectedTools.length > 0) {
          return selectedTools;
        }
        // Otherwise, return empty to let backend use all agent tools
        return [];
      };
      
      // Get apps/knowledge sources to enable from user selection
      const getEnabledApps = (): string[] => {
        // If user selected specific apps, use those
        if (selectedApps && selectedApps.length > 0) {
          return selectedApps;
        }
        // Otherwise, return empty to let backend use all agent knowledge sources
        return [];
      };
      
      const requestBody = {
        query: trimmedInput,
        modelKey: modelKey || currentModel?.modelKey,
        modelName: modelName || currentModel?.modelName,
        modelFriendlyName: currentModel?.modelFriendlyName && currentModel.modelFriendlyName.trim() 
          ? currentModel.modelFriendlyName.trim() 
          : undefined,
        chatMode: resolvedChatMode,

        // Timezone and current time for LLM context
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        currentTime: new Date().toISOString(),

        // Tools: List of tool full names to enable (filters agent's toolsets)
        // Empty means use all tools from agent's toolsets
        tools: getEnabledTools(),

        // Filters structure
        filters: {
          // Apps: List of connector IDs to enable (filters agent's knowledge)
          // Empty means use all knowledge sources from agent
          apps: getEnabledApps(),

          // Knowledge bases: KB IDs to use (extract from knowledge array if not selected)
          kb: selectedKBs || [],
        },

        // Chat mode specific parameters
        ...(resolvedChatMode === 'quick' && {
          limit: 5,
          quickMode: true,
        }),
        ...(resolvedChatMode === 'standard' && {
          temperature: 0.7,
          limit: 10,
          quickMode: false,
        }),
      };

      try {
        const createdConversationId = await handleStreamingResponse(
          streamingUrl,
          requestBody,
          wasCreatingNewConversation
        );

        if (wasCreatingNewConversation && createdConversationId) {
          setCurrentConversationId(createdConversationId);
          setShouldRefreshSidebar(true);
          // Fetch the conversation to get modelInfo and set it
          try {
            const response = await axios.get(
              `/api/v1/agents/${agentKey}/conversations/${createdConversationId}`
            );
            const { conversation } = response.data;
            if (conversation) {
              setSelectedChat(conversation);
              // The useEffect will automatically set the model from conversation.modelInfo
            }
          } catch (error) {
            console.error('Error fetching new conversation:', error);
          }
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
      isCurrentConversationLoading,
      agentKey,
      setSelectedChat,
    ]
  );

  const handleNewChat = useCallback(() => {
    // Abort any active streaming in current conversation
    if (currentConversationId) {
      const currentKey = getConversationKey(currentConversationId);
      const state = streamingManager.getConversationState(currentKey);
      if (state?.controller && !state.controller.signal.aborted) {
        state.controller.abort();
      }
      streamingManager.clearStreaming(currentKey);
    }
    
    // Reset the 'new' conversation state (aborts any active stream there too)
    // This clears messages so start message can be shown again
    streamingManager.resetNewConversation();
    streamingManager.resetNavigationTracking();

    // Reset all UI state (chatMode will be preserved via persistentChatModeRef in useEffect)
    setCurrentConversationId(null);
    navigate(`/agents/${agentKey}`, { replace: true });
    setInputValue('');
    setClearInputTrigger((prev) => prev + 1);
    setShouldRefreshSidebar(true);
    setSelectedChat(null);
    setIsNavigationBlocked(false);
    setExpandedCitations({});
    
    // Explicitly trigger start message after reset
    // Use setTimeout to ensure resetNewConversation has completed
    setTimeout(() => {
      if (agent?.startMessage) {
        const conversationKey = getConversationKey(null);
        const existingMessages = streamingManager.getConversationMessages(conversationKey);
        // Only add if still empty (in case something else added messages)
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
          streamingManager.setConversationMessages(conversationKey, [customStartMessage]);
        }
      }
    }, 0);
  }, [navigate, streamingManager, currentConversationId, getConversationKey, agentKey, agent?.startMessage]);

  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id) return;
      // Only block navigation if actively streaming, not on error states
      const currentKey = getConversationKey(currentConversationId);
      const currentState = streamingManager.getConversationState(currentKey);
      const isCurrentlyStreaming =
        currentState?.isActive ||
        currentState?.isProcessingCompletion ||
        currentState?.showStatus;
      if (isCurrentlyStreaming && currentConversationId) return;

      try {
        const chatKey = getConversationKey(chat._id);

        // Check if this conversation is currently streaming
        const streamingState = streamingManager.getConversationState(chatKey);
        const isChatStreaming =
          streamingState?.isActive ||
          streamingState?.isProcessingCompletion ||
          streamingState?.showStatus;

        // If the conversation is streaming, don't set loading state as it might interfere
        if (!isChatStreaming) {
          setIsLoadingConversation(true);
        }

        // Update current conversation ID before navigation
        setCurrentConversationId(chat._id);

        // Navigate to the chat
        navigate(`/agents/${agentKey}/conversations/${chat._id}`, { replace: true });

        const existingMessages = streamingManager.getConversationMessages(chatKey);

        // Always fetch fresh conversation data to get the latest modelInfo
        // This ensures model changes made during the conversation are reflected
        if (!isChatStreaming) {
          try {
            const response = await axios.get(`/api/v1/agents/${agentKey}/conversations/${chat._id}`);
            const { conversation } = response.data;

            if (conversation) {
              // Update selectedChat with fresh data
              setSelectedChat(conversation);

              // Update messages if we don't have them or if conversation was updated
              if (!existingMessages.length || conversation.messages) {
                const formattedMessages = (conversation.messages || [])
                  .map((msg: any) => {
                    const formatted = formatMessage(msg);
                    if (
                      formatted &&
                      formatted.type === 'bot' &&
                      !formatted.modelInfo &&
                      (conversation as any).modelInfo
                    ) {
                      formatted.modelInfo = (conversation as any).modelInfo;
                    }
                    return formatted;
                  })
                  .filter(Boolean) as FormattedMessage[];

                // Only update messages if we got new data or didn't have messages
                if (!existingMessages.length || formattedMessages.length > existingMessages.length) {
                  streamingManager.setConversationMessages(chatKey, formattedMessages);
                }
              }

              // Always set model from fresh conversation data
              if ((conversation as any).modelInfo) {
                setModelFromConversation((conversation as any).modelInfo);
              }
            }
          } catch (err) {
            console.error('Failed to fetch conversation data:', err);
            // Fallback to using cached chat data if fetch fails
            setSelectedChat(chat);
            if ((chat as any).modelInfo) {
              setModelFromConversation((chat as any).modelInfo);
            }
          }
        } else {
          // If streaming, use cached data but still try to update model if available
          setSelectedChat(chat);
          if ((chat as any).modelInfo) {
            setModelFromConversation((chat as any).modelInfo);
          }
        }
      } catch (error) {
        console.error('❌ Error loading conversation:', error);
        streamingManager.setConversationMessages(getConversationKey(chat._id), []);
      } finally {
        // Only clear loading if we set it
        const chatKey = getConversationKey(chat._id);
        const streamingState = streamingManager.getConversationState(chatKey);
        const isStillStreaming =
          streamingState?.isActive ||
          streamingState?.isProcessingCompletion ||
          streamingState?.showStatus;

        if (!isStillStreaming) {
          setIsLoadingConversation(false);
        }
      }
    },
    [
      formatMessage,
      navigate,
      streamingManager,
      getConversationKey,
      agentKey,
      setModelFromConversation,
      currentConversationId,
    ]
  );

  // Update the useEffect to better handle streaming conversations
  useEffect(() => {
    const urlConversationId = conversationId;
    
    // Don't process if agentKey is not available yet (agent is still loading)
    if (!agentKey) return;
    
    // Check if current conversation is actively streaming before blocking
    const currentKey = getConversationKey(currentConversationId);
    const currentState = streamingManager.getConversationState(currentKey);
    const isCurrentlyStreaming =
      currentState?.isActive ||
      currentState?.isProcessingCompletion ||
      currentState?.showStatus;
    // Only block if actively streaming, not on error states
    if (isCurrentlyStreaming && currentConversationId && urlConversationId !== currentConversationId) return;

    if (urlConversationId && urlConversationId !== currentConversationId) {
      const chatKey = getConversationKey(urlConversationId);
      const existingMessages = streamingManager.getConversationMessages(chatKey);
      const streamingState = streamingManager.getConversationState(chatKey);
      const isUrlStreaming =
        streamingState?.isActive ||
        streamingState?.isProcessingCompletion ||
        streamingState?.showStatus;

      if (existingMessages.length > 0 || isUrlStreaming) {
        // We have existing messages or it's streaming, but still fetch fresh data for modelInfo
        setCurrentConversationId(urlConversationId);

        // Always fetch fresh conversation data to get latest modelInfo
        // This ensures model changes made during the conversation are reflected
        if (!isUrlStreaming) {
          axios
            .get(`/api/v1/agents/${agentKey}/conversations/${urlConversationId}`)
            .then((response) => {
              const { conversation } = response.data;
              if (conversation) {
                setSelectedChat(conversation);
                if ((conversation as any).modelInfo) {
                  setModelFromConversation((conversation as any).modelInfo);
                }
              }
            })
            .catch((err) => {
              console.error('Failed to fetch conversation modelInfo:', err);
              // Fallback to cached data
              const existingConversation =
                selectedChat?._id === urlConversationId
                  ? selectedChat
                  : ({ _id: urlConversationId } as Conversation);
              setSelectedChat(existingConversation);
              if ((existingConversation as any).modelInfo) {
                setModelFromConversation((existingConversation as any).modelInfo);
              }
            })
            .finally(() => {
              setIsLoadingConversation(false);
            });
        } else {
          // If streaming, use cached data
          const existingConversation =
            selectedChat?._id === urlConversationId
              ? selectedChat
              : ({ _id: urlConversationId } as Conversation);
          setSelectedChat(existingConversation);
          if ((existingConversation as any).modelInfo) {
            setModelFromConversation((existingConversation as any).modelInfo);
          }
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
      const isCrtStreaming =
        crtStreamingState?.isActive || crtStreamingState?.isProcessingCompletion;

      if (!isCrtStreaming && crtMessages.length === 0) {
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
    setModelFromConversation,
    agentKey,
  ]);

  // Add effect to show start message when creating new conversation
  useEffect(() => {
    if (!currentConversationId && agent?.startMessage) {
      const conversationKey = getConversationKey(null);
      const existingMessages = streamingManager.getConversationMessages(conversationKey);

      // Only add start message if there are no messages at all
      // This ensures start message shows after new chat, failed conversations, or agent changes
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

        streamingManager.setConversationMessages(conversationKey, [customStartMessage]);
      }
    }
  }, [
    currentConversationId,
    agent?.startMessage,
    getConversationKey,
    streamingManager,
    agent?.name,
    updateTrigger, // Include updateTrigger to ensure it re-runs after resetNewConversation
  ]);

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
      setIsImage(false);
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

      // Unified streaming - use stream/record API for both KB and connector records
      let params: { convertTo?: string } = {};
      if (['pptx', 'ppt'].includes(citationMeta?.extension)) {
        params = { convertTo: 'application/pdf' };
        handleLargePPTFile(record);
      }

      const streamResponse = await axios.get(
          `${CONFIG.backendUrl}/api/v1/knowledgeBase/stream/record/${recordId}`,
          {
            responseType: 'blob',
            params,
          }
        );

      if (!streamResponse) return;

      let filename;
      const contentDisposition = streamResponse.headers['content-disposition'];
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
        bufferReader.readAsArrayBuffer(streamResponse.data);
      });

      const buffer = await arrayBufferPromise;
      setFileBuffer(buffer);
    } catch (err: any) {
      console.error('Failed to fetch document:', err);
      const message = err?.message || 'Failed to load document. Please try again.';
      setSnackbar({
        open: true,
        message,
        severity: err?.statusCode === 503 ? 'warning' : 'error',
      });
      setTimeout(() => {
        onClosePdf();
      }, 500);
      return;
    }

    setTransitioning(true);
    setDrawerOpen(false);
    setOpenPdfView(true);
    const isExcelOrCSV = ['csv', 'xlsx', 'xls', 'tsv'].includes(citationMeta?.extension);
    setIsDocx(['docx'].includes(citationMeta?.extension));
    setIsMarkdown(['mdx', 'md'].includes(citationMeta?.extension));
    setIsHtml(['html'].includes(citationMeta?.extension));
    setIsTextFile(['txt'].includes(citationMeta?.extension));
    setIsImage(['jpg', 'jpeg', 'png', 'webp', 'svg'].includes(citationMeta?.extension));
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

      const conversationKey = getConversationKey(currentConversationId);
      const streamingBotMessageId = `streaming-${Date.now()}-${agentKey}`;

      // Find the message to regenerate and get its index
      const messageIndex = currentMessages.findIndex((msg) => msg.id === messageId);
      if (messageIndex === -1) return;

      // Get the old message to preserve its timestamp
      const oldMessage = currentMessages[messageIndex];

      // Get the user query that preceded this bot response
      const userMessage = messageIndex > 0 ? currentMessages[messageIndex - 1] : null;
      if (!userMessage || userMessage.type !== 'user') {
        console.error('Cannot regenerate: No user query found before this message');
        return;
      }

      // Initialize streaming state
      streamingManager.updateStatus(conversationKey, 'Regenerating response...');
      const controller = new AbortController();
      streamingManager.updateConversationState(conversationKey, { controller });

      // Immediately replace the old message with a new streaming message placeholder
      // This hides the old message right away and shows the new one in the same position
      streamingManager.updateConversationMessages(conversationKey, (prevMessages) => {
        const updated = [...prevMessages];
        // Replace the message at messageIndex with a new streaming message
        // Preserve the original timestamp so it appears in the same position
        updated[messageIndex] = {
          type: 'bot',
          content: '',
          createdAt: oldMessage.createdAt,
          updatedAt: new Date(),
          id: streamingBotMessageId,
          contentFormat: 'MARKDOWN',
          followUpQuestions: [],
          citations: [],
          confidence: '',
          messageType: 'bot_response',
          timestamp: oldMessage.timestamp || oldMessage.createdAt,
        };
        return updated;
      });
      streamingManager.mapMessageToConversation(streamingBotMessageId, conversationKey);

      const hasCreatedMessage = { current: true }; // Already created above

      // Define the event handler for regenerate streaming
      const handleRegenerateStreamingEvent = async (event: string, data: any): Promise<void> => {
        const statusMsg = getEngagingStatusMessage(event, data);
        if (statusMsg) {
          streamingManager.updateStatus(conversationKey, statusMsg);
        }

        switch (event) {
          case 'answer_chunk':
            if (data.chunk) {
              streamingManager.clearStatus(conversationKey);
              streamingManager.updateStreamingContent(
                streamingBotMessageId,
                data.chunk,
                data.citations || []
              );
            }
            break;

          case 'complete': {
            streamingManager.clearStatus(conversationKey);
            const completedConversation = data.conversation;
            if (completedConversation?.messages) {
              // Find the regenerated message in the response
              const regeneratedMessage = completedConversation.messages
                .filter((msg: any) => msg.messageType === 'bot_response')
                .pop();

              if (regeneratedMessage) {
                streamingManager.finalizeStreaming(conversationKey, streamingBotMessageId, {
                  conversation: completedConversation,
                });

                // Update expanded citations state
                const formattedMessage = formatMessage(regeneratedMessage);
                if (formattedMessage) {
                  setExpandedCitations((prevStates) => {
                    const newStates = { ...prevStates };
                    const hasCitations =
                      formattedMessage.citations && formattedMessage.citations.length > 0;
                    newStates[messageIndex] = hasCitations
                      ? prevStates[messageIndex] || false
                      : false;
                    return newStates;
                  });
                }
              }
            }
            break;
          }

          case 'error': {
            streamingManager.clearStreaming(conversationKey);
            const errorMessage =
              data.message || data.error || 'An error occurred while regenerating';

            // Update the streaming message with error
            streamingManager.updateConversationMessages(conversationKey, (prevMessages) =>
              prevMessages.map((msg) =>
                msg.id === streamingBotMessageId
                  ? { ...msg, content: errorMessage, messageType: 'error' }
                  : msg
              )
            );
            throw new Error(errorMessage);
          }

          default:
            break;
        }
      };

      try {
        // Make the streaming request to regenerate endpoint
        const token = localStorage.getItem('jwt_access_token');
        // Use refs to get the latest values (prevents stale closures)
        const currentModel = latestModelRef.current;

        const response = await fetch(
          `${CONFIG.backendUrl}/api/v1/agents/${agentKey}/conversations/${currentConversationId}/message/${messageId}/regenerate`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Accept: 'text/event-stream',
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
              modelName: currentModel?.modelName,
              modelProvider: currentModel?.provider,
              chatMode: latestChatModeRef.current || AUTO_CHAT_MODE,
            }),
            signal: controller.signal,
          }
        );

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Failed to get response reader');
        }

        const decoder = new TextDecoder();

        // Process the stream
        await processStreamChunk(
          reader,
          decoder,
          parseSSELine,
          handleRegenerateStreamingEvent,
          {
            conversationKey,
            streamingBotMessageId,
            isNewConversation: false,
            hasCreatedMessage,
            conversationIdRef: { current: currentConversationId },
          },
          controller
        );
      } catch (error) {
        // Handle AbortError separately
        if (error instanceof Error && error.name === 'AbortError') {
          return;
        }

        console.error('Error regenerating message:', error);
        streamingManager.clearStreaming(conversationKey);

        // Show error in the message
        const errorMessage =
          error instanceof Error
            ? error.message
            : 'Sorry, I encountered an error regenerating this message.';
        streamingManager.updateConversationMessages(conversationKey, (prevMessages) =>
          prevMessages.map((msg) =>
            msg.id === streamingBotMessageId
              ? { ...msg, content: errorMessage, messageType: 'error' }
              : msg
          )
        );
      }
    },
    [
      currentConversationId,
      currentMessages,
      getConversationKey,
      streamingManager,
      isCurrentConversationLoading,
      agentKey,
      parseSSELine,
      processStreamChunk,
      formatMessage,
      setExpandedCitations,
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
            {/* Always show the chat messages area - start message will be shown via useEffect */}
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
              key={`chat-input-${agentKey}`}
              onSubmit={handleSendMessage}
              isLoading={isCurrentConversationLoading}
              disabled={isCurrentConversationLoading}
              placeholder="Type your message..."
              selectedModel={selectedModel}
              onModelChange={(model) => setSelectedModel(model)}
              availableModels={availableModels}
              availableKBs={availableKBs}
              agent={agent}
              activeConnectors={activeConnectors}
              chatMode={chatMode}
              onChatModeChange={setChatMode}
              conversationId={currentConversationId}
              clearInputTrigger={clearInputTrigger}
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
                ) : isImage ? (
                  <ImageHighlighter
                    key="image-highlighter"
                    url={pdfUrl}
                    buffer={fileBuffer}
                    citations={aggregatedCitations}
                    highlightCitation={highlightedCitation}
                    onClosePdf={onClosePdf}
                    fileExtension={highlightedCitation?.metadata?.extension}
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
