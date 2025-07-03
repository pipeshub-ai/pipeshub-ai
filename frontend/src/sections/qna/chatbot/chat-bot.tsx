import type {
  Message,
  Citation,
  Metadata,
  Conversation,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
} from 'src/types/chat-bot';

import { Icon } from '@iconify/react';
import menuIcon from '@iconify-icons/mdi/menu';
import closeIcon from '@iconify-icons/mdi/close';
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
} from '@mui/material';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { ORIGIN } from 'src/sections/knowledgebase/constants/knowledge-search';
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

const DRAWER_WIDTH = 300;

interface StreamingState {
  messageId: string | null;
  content: string;
  citations: CustomCitation[];
  isActive: boolean;
}

interface StreamingContextType {
  streamingState: StreamingState;
  updateStreamingContent: (messageId: string, content: string, citations: CustomCitation[]) => void;
  clearStreaming: () => void;
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

interface StreamingController {
  abort: () => void;
}

const getEngagingStatusMessage = (event: string, data: any): string | null => {
  switch (event) {
    case 'status': {
      const message = data.message || data.status || 'Processing...';
      switch (data.status) {
        case 'searching':
          return `${message}`;
        case 'decomposing':
          return `${message}`;
        case 'parallel_processing':
          return `${message}`;
        case 'reranking':
          return `${message}`;
        case 'generating':
          return `${message}`;
        case 'deduplicating':
          return `${message}`;
        case 'preparing_context':
          return `${message}`;
        default:
          return `⚙️ ${message}`;
      }
    }

    case 'query_decomposed': {
      const queryCount = data.queries?.length || 0;
      if (queryCount > 1) {
        return `Breaking your request into ${queryCount} questions for a better answer.`;
      }
      return 'Analyzing your request...';
    }

    case 'search_complete': {
      const resultsCount = data.results_count || 0;
      if (resultsCount > 0) {
        return `Found ${resultsCount} potential sources. Now processing them...`;
      }
      return 'Finished searching...';
    }

    case 'connected':
      return 'Processing ...';
    case 'query_transformed':
    case 'results_ready':
      return null;

    default:
      return null;
  }
};

const ChatInterface = () => {
  const [messages, setMessages] = useState<FormattedMessage[]>([]);
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState<boolean>(false);
  const [expandedCitations, setExpandedCitations] = useState<ExpandedCitationsState>({});
  const [isDrawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [selectedChat, setSelectedChat] = useState<Conversation | null>(null);
  const [shouldRefreshSidebar, setShouldRefreshSidebar] = useState<boolean>(false);
  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId: string }>();
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
  const [loadingConversations, setLoadingConversations] = useState<{ [key: string]: boolean }>({});
  const theme = useTheme();

  const accumulatedContentRef = useRef<string>('');
  const displayedContentRef = useRef<string>('');
  const streamingTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const wordQueueRef = useRef<string[]>([]);
  const isStreamingActiveRef = useRef<boolean>(false);
  const streamingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const completionDataRef = useRef<any>(null);
  const pendingChunksRef = useRef<string[]>([]);
  const isCompletionPendingRef = useRef<boolean>(false);
  const finalMessageIdRef = useRef<string | null>(null);
  const isProcessingCompletionRef = useRef<boolean>(false); 

  const BATCH_SIZE = 1; 
  const TYPING_SPEED = 25;
  const COMPLETION_DELAY = 300;

  const [conversationStatus, setConversationStatus] = useState<{
    [key: string]: string | undefined;
  }>({});

  const [conversationErrors, setConversationErrors] = useState<{
    [key: string]: boolean;
  }>({});

  const [pendingResponseConversationId, setPendingResponseConversationId] = useState<string | null>(
    null
  );

  const [activeRequestTracker, setActiveRequestTracker] = useState<{
    current: string | null;
    type: 'create' | 'continue' | null;
  }>({
    current: null,
    type: null,
  });
  const currentConversationIdRef = useRef<string | null>(null);

  const isCurrentConversationLoading = useCallback(
    () =>
      currentConversationId
        ? loadingConversations[currentConversationId]
        : loadingConversations.new,
    [currentConversationId, loadingConversations]
  );

  const isCurrentConversationThinking = useCallback(() => {
    const conversationKey = currentConversationId || 'new';
    return conversationStatus[conversationKey] === 'Inprogress';
  }, [currentConversationId, conversationStatus]);

  const [highlightedCitation, setHighlightedCitation] = useState<CustomCitation | null>(null);

  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };

  const [showWelcome, setShowWelcome] = useState<boolean>(true);

  const [streamingState, setStreamingState] = useState<StreamingState>({
    messageId: null,
    content: '',
    citations: [],
    isActive: false,
  });

  const [statusMessage, setStatusMessage] = useState<string>('');
  const [showStatus, setShowStatus] = useState<boolean>(false);

  const [streamingController, setStreamingController] = useState<StreamingController | null>(null);

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

  const clearStreaming = useCallback(() => {
    if (isProcessingCompletionRef.current) {
      return;
    }

    // Clear all intervals and timeouts
    if (streamingIntervalRef.current) {
      clearInterval(streamingIntervalRef.current);
      streamingIntervalRef.current = null;
    }
    if (streamingTimeoutRef.current) {
      clearTimeout(streamingTimeoutRef.current);
      streamingTimeoutRef.current = null;
    }

    // Reset all refs
    accumulatedContentRef.current = '';
    displayedContentRef.current = '';
    pendingChunksRef.current = [];
    isStreamingActiveRef.current = false;
    completionDataRef.current = null;
    isCompletionPendingRef.current = false;
    finalMessageIdRef.current = null;
    isProcessingCompletionRef.current = false;

    // Only clear streaming state if it's still active
    setStreamingState((prev) => {
      if (prev.isActive) {
        return {
          messageId: null,
          content: '',
          citations: [],
          isActive: false,
        };
      }
      return prev;
    });
  }, []);

  const finalizeStreamingWithCompletion = useCallback(
    (messageId: string, completionData: any) => {
      // Mark that we're processing completion
      isProcessingCompletionRef.current = true;

      if (completionData?.conversation) {
        const finalBotMessage = completionData.conversation.messages
          .filter((msg: any) => msg.messageType === 'bot_response')
          .pop();

        if (finalBotMessage) {
          const formattedFinalMessage = formatMessage(finalBotMessage);
          if (formattedFinalMessage) {
            // Store the final message ID for reference
            finalMessageIdRef.current = finalBotMessage._id;

            // Apply the final message content with all proper formatting and citations
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === messageId
                  ? {
                      ...formattedFinalMessage,
                      id: finalBotMessage._id,
                      content: formattedFinalMessage.content,
                      citations: formattedFinalMessage.citations || [],
                    }
                  : msg
              )
            );

            // Update streaming state to show completion
            setStreamingState((prev) => ({
              ...prev,
              messageId: finalBotMessage._id,
              content: formattedFinalMessage.content,
              citations: formattedFinalMessage.citations || [],
              isActive: false, 
            }));
          }
        }
      }

      // Clean up after completion
      setTimeout(() => {
        isCompletionPendingRef.current = false;
        completionDataRef.current = null;
        isProcessingCompletionRef.current = false;

        // Now safe to clear streaming
        setTimeout(() => {
          clearStreaming();
        }, 100);
      }, 100);
    },
    [formatMessage, clearStreaming]
  );

  const processChunkQueue = useCallback(
    (messageId: string, citations: CustomCitation[]) => {
      if (!isStreamingActiveRef.current && !isCompletionPendingRef.current) {
        if (streamingIntervalRef.current) {
          clearInterval(streamingIntervalRef.current);
          streamingIntervalRef.current = null;
        }
        return;
      }

      // If no more chunks to process
      if (pendingChunksRef.current.length === 0) {
        // If completion is pending, finalize now
        if (isCompletionPendingRef.current && completionDataRef.current) {
          if (streamingIntervalRef.current) {
            clearInterval(streamingIntervalRef.current);
            streamingIntervalRef.current = null;
          }

          // Apply final message
          setTimeout(() => {
            finalizeStreamingWithCompletion(messageId, completionDataRef.current);
          }, COMPLETION_DELAY);

          return;
        }

        // No completion pending, just stop the interval
        if (streamingIntervalRef.current) {
          clearInterval(streamingIntervalRef.current);
          streamingIntervalRef.current = null;
        }
        return;
      }

      // Process the next chunk
      const nextChunk = pendingChunksRef.current.shift();
      if (nextChunk) {
        displayedContentRef.current += nextChunk;

        // Update the streaming state
        setStreamingState((prev) => ({
          ...prev,
          messageId,
          content: displayedContentRef.current,
          citations,
          isActive: true,
        }));

        // Update the messages array
        setMessages((prevMessages) => {
          const messageIndex = prevMessages.findIndex((msg) => msg.id === messageId);
          if (messageIndex === -1) return prevMessages;

          const updatedMessages = [...prevMessages];
          updatedMessages[messageIndex] = {
            ...updatedMessages[messageIndex],
            content: displayedContentRef.current,
            citations,
            updatedAt: new Date(),
          };
          return updatedMessages;
        });
      }
    },
    [finalizeStreamingWithCompletion]
  );

  const startChunkStreaming = useCallback(
    (messageId: string, citations: CustomCitation[]) => {
      if (streamingIntervalRef.current) {
        clearInterval(streamingIntervalRef.current);
      }

      streamingIntervalRef.current = setInterval(() => {
        processChunkQueue(messageId, citations);
      }, TYPING_SPEED);
    },
    [processChunkQueue]
  );

  const updateStreamingContent = useCallback(
    (messageId: string, newChunk: string, citations: CustomCitation[] = []) => {
      if (!isStreamingActiveRef.current) {
        // Start new streaming session
        accumulatedContentRef.current = '';
        displayedContentRef.current = '';
        pendingChunksRef.current = [];
        isStreamingActiveRef.current = true;
        completionDataRef.current = null;
        isCompletionPendingRef.current = false;
        finalMessageIdRef.current = null;
        isProcessingCompletionRef.current = false;
      }

      if (newChunk && newChunk.trim()) {
        const cleanedChunk = newChunk
          .replace(/\\n/g, '\n')
          .replace(/\*\*(\d+)\*\*/g, '[$1]')
          .replace(/\*\*([^*]+)\*\*/g, '**$1**');

        pendingChunksRef.current.push(cleanedChunk);
      }

      if (!streamingIntervalRef.current) {
        startChunkStreaming(messageId, citations);
      }
    },
    [startChunkStreaming]
  );

  const updateStatus = useCallback((message: string) => {
    setStatusMessage(message);
    setShowStatus(true);
  }, []);

  const streamingContextValue: StreamingContextType = useMemo(
    () => ({
      streamingState,
      updateStreamingContent,
      clearStreaming,
    }),
    [streamingState, updateStreamingContent, clearStreaming]
  );

  const parseSSELine = (line: string): { event?: string; data?: any } | null => {
    if (line.startsWith('event: ')) {
      return { event: line.substring(7).trim() };
    }
    if (line.startsWith('data: ')) {
      try {
        const data = JSON.parse(line.substring(6).trim());
        return { data };
      } catch (e) {
        return null;
      }
    }
    return null;
  };

  const createStreamingController = (
    reader: ReadableStreamDefaultReader<Uint8Array>
  ): StreamingController => ({
    abort: () => {
      reader.cancel().catch(console.error);
    },
  });

  const createStreamingMessage = useCallback((messageId: string) => {
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
    setMessages((prev) => [...prev, streamingMessage]);
  }, []);

  const handleStreamingEvent = useCallback(
    async (
      event: string,
      data: any,
      context: {
        streamingBotMessageId: string;
        isNewConversation: boolean;
        hasCreatedMessage: boolean;
        onConversationComplete: (conversation: Conversation) => void;
        onMessageCreated: () => void;
        onErrorReceived: () => void;
      }
    ): Promise<boolean> => {
      const statusMsg = getEngagingStatusMessage(event, data);

      if (statusMsg) {
        updateStatus(statusMsg);
      }

      switch (event) {
        case 'answer_chunk':
          if (data.chunk) {
            if (!context.hasCreatedMessage) {
              createStreamingMessage(context.streamingBotMessageId);
              context.onMessageCreated();
            }

            setShowStatus(false);
            setStatusMessage('');

            updateStreamingContent(context.streamingBotMessageId, data.chunk, data.citations || []);
          }
          return false;

        case 'complete':
          setShowStatus(false);
          setStatusMessage('');

          // Store completion data and mark as pending
          completionDataRef.current = data;
          isCompletionPendingRef.current = true;

          // Mark that we're processing completion to prevent clearing
          isProcessingCompletionRef.current = true;

          // If there are no pending chunks, finalize immediately
          if (pendingChunksRef.current.length === 0) {
            setTimeout(() => {
              finalizeStreamingWithCompletion(context.streamingBotMessageId, data);
              if (data.conversation) {
                context.onConversationComplete(data.conversation);
              }
            }, COMPLETION_DELAY);
          }

          return false;

        case 'error': {
          setShowStatus(false);
          setStatusMessage('');

          // Stop streaming on error
          isStreamingActiveRef.current = false;
          isProcessingCompletionRef.current = false;
          clearStreaming();

          const errorMessage = data.message || data.error || 'An error occurred';
          if (!context.hasCreatedMessage) {
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
            setMessages((prev) => [...prev, errorMsg]);
            context.onMessageCreated();
          } else {
            setMessages((prevMessages) => {
              const messageIndex = prevMessages.findIndex(
                (msg) => msg.id === context.streamingBotMessageId
              );
              if (messageIndex !== -1) {
                const updatedMessages = [...prevMessages];
                updatedMessages[messageIndex] = {
                  ...updatedMessages[messageIndex],
                  content: errorMessage,
                  messageType: 'error',
                  updatedAt: new Date(),
                };
                return updatedMessages;
              }
              return prevMessages;
            });
          }
          context.onErrorReceived();
          return true;
        }

        default:
          return false;
      }
    },
    [
      createStreamingMessage,
      updateStreamingContent,
      updateStatus,
      clearStreaming,
      finalizeStreamingWithCompletion,
    ]
  );

  const handleStreamingComplete = useCallback(
    async (
      conversation: Conversation,
      isNewConversation: boolean,
      streamingBotMessageId: string
    ): Promise<void> => {
      if (isNewConversation) {
        setSelectedChat(conversation);
        setCurrentConversationId(conversation._id);
        currentConversationIdRef.current = conversation._id;
        setShouldRefreshSidebar(true);
      }
    },
    []
  );

  const handleStreamingResponse = useCallback(
    async (url: string, body: any, isNewConversation: boolean): Promise<void> => {
      const streamingBotMessageId = `streaming-${Date.now()}`;

      accumulatedContentRef.current = '';

      const streamState = {
        finalConversation: null as Conversation | null,
        hasCreatedMessage: false,
        hasReceivedError: false,
      };

      const callbacks = {
        onConversationComplete: (conversation: Conversation) => {
          streamState.finalConversation = conversation;
        },
        onMessageCreated: () => {
          streamState.hasCreatedMessage = true;
        },
        onErrorReceived: () => {
          streamState.hasReceivedError = true;
        },
      };

      try {
        const token = localStorage.getItem('jwt_access_token');
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('Failed to get response reader');
        }

        const controller = createStreamingController(reader);
        setStreamingController(controller);
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';

        const processLine = async (line: string): Promise<void> => {
          const trimmedLine = line.trim();
          if (!trimmedLine) return;

          const parsed = parseSSELine(trimmedLine);
          if (!parsed) return;

          if (parsed.event) {
            currentEvent = parsed.event;
          } else if (parsed.data && currentEvent) {
            const errorReceived = await handleStreamingEvent(currentEvent, parsed.data, {
              streamingBotMessageId,
              isNewConversation,
              hasCreatedMessage: streamState.hasCreatedMessage,
              onConversationComplete: callbacks.onConversationComplete,
              onMessageCreated: callbacks.onMessageCreated,
              onErrorReceived: callbacks.onErrorReceived,
            });

            if (errorReceived) {
              streamState.hasReceivedError = true;
            }
          }
        };

        const readNextChunk = async (): Promise<void> => {
          const { done, value } = await reader.read();
          if (done) return;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          await Promise.all(lines.map(processLine));
          await readNextChunk();
        };

        await readNextChunk();

        if (streamState.finalConversation && !streamState.hasReceivedError) {
          await handleStreamingComplete(
            streamState.finalConversation,
            isNewConversation,
            streamingBotMessageId
          );
        }
      } catch (error) {
        console.error('Streaming connection error:', error);
        setShowStatus(false);
        clearStreaming();
        // Reset accumulated content on error
        accumulatedContentRef.current = '';
      } finally {
        setStreamingController(null);
      }
    },
    [handleStreamingEvent, handleStreamingComplete, clearStreaming]
  );

  const handleSendMessage = useCallback(
    async (messageOverride?: string): Promise<void> => {
      const trimmedInput =
        typeof messageOverride === 'string' ? messageOverride.trim() : inputValue.trim();
      if (!trimmedInput) return;
      if (streamingController) {
        streamingController.abort();
      }
      const wasCreatingNewConversation = currentConversationId === null;
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
      if (typeof messageOverride === 'string' && showWelcome) {
        setShowWelcome(false);
      }
      setInputValue('');
      setMessages((prev) => [...prev, tempUserMessage]);
      const streamingUrl = wasCreatingNewConversation
        ? `${CONFIG.backendUrl}/api/v1/conversations/stream`
        : `${CONFIG.backendUrl}/api/v1/conversations/${currentConversationId}/messages/stream`;
      await handleStreamingResponse(
        streamingUrl,
        { query: trimmedInput },
        wasCreatingNewConversation
      );
    },
    [inputValue, currentConversationId, showWelcome, streamingController, handleStreamingResponse]
  );

  const handleNewChat = useCallback(() => {
    if (streamingController) {
      streamingController.abort();
    }

    // Force clear streaming even if completion is processing (user wants new chat)
    isProcessingCompletionRef.current = false;
    clearStreaming();

    currentConversationIdRef.current = null;
    setCurrentConversationId(null);
    navigate('/');
    setShowStatus(false);
    setMessages([]);
    setInputValue('');
    setShouldRefreshSidebar(true);
    setShowWelcome(true);
    setSelectedChat(null);
    accumulatedContentRef.current = '';
  }, [navigate, streamingController, clearStreaming]);

  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id) return;
      if (streamingController) {
        streamingController.abort();
      }

      // Force clear streaming even if completion is processing (user wants different chat)
      isProcessingCompletionRef.current = false;
      clearStreaming();

      try {
        setShowWelcome(false);
        setCurrentConversationId(chat._id);
        currentConversationIdRef.current = chat._id;
        navigate(`/${chat._id}`);
        setIsLoadingConversation(true);
        setShowStatus(false);
        setMessages([]);
        accumulatedContentRef.current = '';

        const response = await axios.get(`/api/v1/conversations/${chat._id}`);
        const { conversation } = response.data;
        if (conversation?.messages) {
          const formattedMessages = conversation.messages
            .map(formatMessage)
            .filter(Boolean) as FormattedMessage[];
          setMessages(formattedMessages);
          setSelectedChat(conversation);
        }
      } catch (error) {
        console.error('Error loading conversation:', error);
        setMessages([]);
      } finally {
        setIsLoadingConversation(false);
      }
    },
    [formatMessage, navigate, streamingController, clearStreaming]
  );

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
      throw new Error('Large fize size, redirecting to web page ');
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
    setIsMarkdown(['md'].includes(citationMeta?.extension));
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
      if (!currentConversationId || !messageId) return;

      try {
        setIsLoading(true);
        const response = await axios.post<{ conversation: Conversation }>(
          `/api/v1/conversations/${currentConversationId}/message/${messageId}/regenerate`,
          { instruction: 'Improve writing style and clarity' }
        );

        if (!response?.data?.conversation?.messages) {
          throw new Error('Invalid response format');
        }

        const allMessages = response.data.conversation.messages
          .map(formatMessage)
          .filter(Boolean) as FormattedMessage[];

        const regeneratedMessage = allMessages.filter((msg) => msg.type === 'bot').pop();

        if (!regeneratedMessage) {
          throw new Error('No regenerated message found in response');
        }

        setMessages((prevMessages) =>
          prevMessages.map((msg) => {
            if (msg.id === messageId) {
              return {
                ...regeneratedMessage,
                createdAt: msg.createdAt,
              };
            }
            return msg;
          })
        );

        setExpandedCitations((prevStates) => {
          const newStates = { ...prevStates };
          const messageIndex = messages.findIndex((msg) => msg.id === messageId);
          if (messageIndex !== -1) {
            const hasCitations =
              regeneratedMessage.citations && regeneratedMessage.citations.length > 0;
            newStates[messageIndex] = hasCitations ? prevStates[messageIndex] || false : false;
          }
          return newStates;
        });
      } catch (error) {
        setMessages((prevMessages) =>
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
      } finally {
        setIsLoading(false);
      }
    },
    [currentConversationId, formatMessage, messages]
  );

  const handleSidebarRefreshComplete = useCallback(() => {
    setShouldRefreshSidebar(false);
  }, []);

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

  useEffect(
    () => () => {
      if (streamingController) {
        streamingController.abort();
      }
      clearStreaming();
    },
    [streamingController, clearStreaming]
  );

  useEffect(() => {
    if (conversationId && conversationId !== currentConversationId) {
      handleChatSelect({ _id: conversationId } as Conversation);
    }
  }, [conversationId, handleChatSelect, currentConversationId]);

  useEffect(
    () => () => {
      if (streamingController) {
        streamingController.abort();
      }
    },
    [streamingController]
  );

  const MemoizedChatMessagesArea = useMemo(() => React.memo(ChatMessagesArea), []);
  const MemoizedWelcomeMessage = useMemo(() => React.memo(WelcomeMessage), []);

  return (
    <StreamingContext.Provider value={streamingContextValue}>
      <Box
        sx={{
          display: 'flex',
          width: '100%',
          height: '90vh',
          overflow: 'hidden',
        }}
      >
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
            {showWelcome ? (
              <MemoizedWelcomeMessage
                key="welcome-screen"
                onSubmit={handleSendMessage}
                isLoading={streamingState.isActive}
              />
            ) : (
              <>
                <MemoizedChatMessagesArea
                  messages={messages}
                  isLoading={streamingState.isActive}
                  onRegenerateMessage={handleRegenerateMessage}
                  onFeedbackSubmit={handleFeedbackSubmit}
                  conversationId={currentConversationId}
                  isLoadingConversation={isLoadingConversation}
                  onViewPdf={onViewPdf}
                  currentStatus={statusMessage}
                  isStatusVisible={showStatus}
                />

                {/* <Box
                  sx={{
                    flexShrink: 0,
                    borderTop: 1,
                    borderColor: 'divider',
                    // backgroundColor:
                    //   theme.palette.mode === 'dark'
                    //     ? alpha(theme.palette.background.paper, 0.5)
                    //     : theme.palette.background.paper,
                    mt: 'auto',
                    // py: 1.5,
                    minWidth: '95%',
                    mx: 'auto',
                    borderRadius: 2,
                  }}
                > */}
                  <ChatInput onSubmit={handleSendMessage} isLoading={streamingState.isActive} />
                {/* </Box> */}
              </>
            )}
          </Box>

          {/* PDF Viewer remains the same */}
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
              '& .MuiAlert-icon': {
                fontSize: '1.2rem',
              },
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
