// Updated ChatInterface component with better error handling and status management
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
import React, { useRef, useState, useEffect, useCallback } from 'react';

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

const DRAWER_WIDTH = 300;

// IMPROVED: Enhanced status message mapping with better error handling
const getEngagingStatusMessage = (originalStatus: string, data?: any): string => {
  // Connection and initialization states
  if (originalStatus.includes('Connected') || originalStatus === 'connected') {
    return 'Connected successfully';
  }

  if (originalStatus.includes('Starting AI processing')) {
    return 'Initializing AI processing';
  }

  if (originalStatus.includes('LLM service initialized')) {
    return 'AI service ready';
  }

  if (originalStatus.includes('Processing conversation history')) {
    return 'Loading conversation context';
  }

  // Query analysis phase
  if (originalStatus.includes('Decomposing query')) {
    return 'Analyzing your request';
  }

  if (originalStatus.includes('transforming')) {
    return 'Optimizing search parameters';
  }

  // Search execution phase
  if (originalStatus.includes('Processing') && originalStatus.includes('queries in parallel')) {
    const queryCount = data?.queries?.length || extractNumber(originalStatus);
    return queryCount ? `Executing ${queryCount} parallel searches` : 'Running parallel searches';
  }

  if (originalStatus.includes('Executing searches')) {
    return 'Searching knowledge sources';
  }

  // Results processing phase
  if (originalStatus.includes('Deduplicating search results')) {
    return 'Consolidating search results';
  }

  if (originalStatus.includes('Reranking results')) {
    return 'Prioritizing relevant information';
  }

  if (originalStatus.includes('Found') && originalStatus.includes('sources')) {
    const count = extractNumber(originalStatus);
    return count ? `Retrieved ${count} relevant sources` : 'Sources retrieved successfully';
  }

  // Response generation phase
  if (originalStatus.includes('Preparing user context')) {
    return 'Preparing personalized response';
  }

  if (
    originalStatus.includes('Generating AI response') ||
    originalStatus.includes('Generating response')
  ) {
    return 'Generating response';
  }

  // Generic fallbacks with professional tone
  if (originalStatus.toLowerCase().includes('search')) {
    return 'Searching information';
  }

  if (originalStatus.toLowerCase().includes('processing')) {
    return 'Processing request';
  }

  if (originalStatus.toLowerCase().includes('analyzing')) {
    return 'Analyzing data';
  }

  // Default state
  return 'Processing';
};

// Helper function to extract numbers from status messages
const extractNumber = (text: string): string => {
  const match = text.match(/\d+/);
  return match ? match[0] : '';
};

const StyledCloseButton = styled(Button)(({ theme }) => ({
  position: 'fixed',
  top: 72,
  right: 32,
  backgroundColor: theme.palette.primary.main,
  color: theme.palette.primary.contrastText,
  textTransform: 'none',
  padding: '6px 12px',
  minWidth: 'auto',
  fontSize: '0.875rem',
  fontWeight: 600,
  zIndex: 9999,
  borderRadius: theme.shape.borderRadius,
  boxShadow: theme.shadows[2],
  '&:hover': {
    backgroundColor: theme.palette.primary.dark,
    boxShadow: theme.shadows[4],
  },
  '& .MuiSvgIcon-root': {
    color: theme.palette.primary.contrastText,
  },
}));

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

// Streaming controller interface
interface StreamingController {
  abort: () => void;
}

const ChatInterface = () => {
  // Existing state variables
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

  // New streaming-specific state
  const [streamingController, setStreamingController] = useState<StreamingController | null>(null);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [isGeneratingResponse, setIsGeneratingResponse] = useState<boolean>(false);

  // IMPROVED: Enhanced refs for smoother streaming with debouncing
  const streamingContentRef = useRef<string>('');
  const streamingUpdateTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastUpdateTimeRef = useRef<number>(0);
  const pendingContentRef = useRef<string>('');
  const accumulatedContentRef = useRef<string>('');
  const updateDebounceRef = useRef<NodeJS.Timeout | null>(null);

  // IMPROVED: Better conversation status management with error handling
  const [conversationStatus, setConversationStatus] = useState<{
    [key: string]: string | undefined;
  }>({});

  // FIX: Add error state tracking to prevent duplicate error messages
  const [conversationErrors, setConversationErrors] = useState<{
    [key: string]: boolean;
  }>({});

  const [pendingResponseConversationId, setPendingResponseConversationId] = useState<string | null>(
    null
  );
  const [showWelcome, setShowWelcome] = useState<boolean>(
    () => messages.length === 0 && !currentConversationId
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

  // FIX: Updated formatMessage to handle conversation status properly
  const formatMessage = useCallback((apiMessage: Message): FormattedMessage | null => {
    if (!apiMessage) return null;

    // Common base message properties
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

    // For user messages
    if (apiMessage.messageType === 'user_query') {
      return {
        ...baseMessage,
        type: 'user',
        feedback: apiMessage.feedback || [],
      };
    }

    // For bot messages
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

    // FIX: Handle error messages properly - don't create duplicate error messages
    if (apiMessage.messageType === 'error') {
      return {
        ...baseMessage,
        type: 'bot',
        messageType: 'error',
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

    return null;
  }, []);

  // IMPROVED: Much smoother streaming update with proper debouncing
  const smoothUpdateStreamingMessage = useCallback(
    (content: string, messageId: string, immediate = false) => {
      // Store the latest content
      accumulatedContentRef.current = content;

      // Clear any existing debounce
      if (updateDebounceRef.current) {
        clearTimeout(updateDebounceRef.current);
      }

      const updateContent = () => {
        const contentToUpdate = accumulatedContentRef.current;

        if (contentToUpdate !== streamingContentRef.current) {
          streamingContentRef.current = contentToUpdate;

          setMessages((prevMessages) =>
            prevMessages.map((msg) => {
              if (msg.id === messageId && msg.content !== contentToUpdate) {
                return {
                  ...msg,
                  content: contentToUpdate,
                  updatedAt: new Date(),
                  // Preserve citations during streaming
                  citations: msg.citations || [],
                };
              }
              return msg;
            })
          );
        }
      };

      if (immediate) {
        updateContent();
      } else {
        // Use requestAnimationFrame for smooth 60fps updates
        updateDebounceRef.current = setTimeout(() => {
          requestAnimationFrame(updateContent);
        }, 16); // ~60fps
      }
    },
    []
  );

  // IMPROVED: Clean up function for streaming
  const cleanupStreaming = useCallback(() => {
    if (streamingUpdateTimeoutRef.current) {
      clearTimeout(streamingUpdateTimeoutRef.current);
      streamingUpdateTimeoutRef.current = null;
    }

    if (updateDebounceRef.current) {
      clearTimeout(updateDebounceRef.current);
      updateDebounceRef.current = null;
    }

    // Reset all streaming state
    streamingContentRef.current = '';
    pendingContentRef.current = '';
    accumulatedContentRef.current = '';
    lastUpdateTimeRef.current = 0;
    setIsGeneratingResponse(false);
    setStreamingMessageId(null);
  }, []);

  // New streaming utility functions
  const parseSSELine = (line: string): { event?: string; data?: any } | null => {
    if (line.startsWith('event: ')) {
      return { event: line.substring(7).trim() };
    }
    if (line.startsWith('data: ')) {
      try {
        const data = JSON.parse(line.substring(6).trim());
        return { data };
      } catch (e) {
        console.warn('Failed to parse SSE data:', line);
        return null;
      }
    }
    return null;
  };

  const createStreamingController = (
    reader: ReadableStreamDefaultReader<Uint8Array>
  ): StreamingController => {
    let aborted = false;

    return {
      abort: () => {
        aborted = true;
        reader.cancel().catch(console.error);
      },
    };
  };


  // Complete updated functions for ChatInterface component

  // FIXED: Updated handleStreamingResponse with no hardcoded error messages
  const handleStreamingResponse = async (
    url: string,
    body: any,
    isNewConversation: boolean
  ): Promise<void> => {
    const requestId = `${Date.now()}-${Math.random()}`;
    const conversationKey = currentConversationId || 'new';

    setActiveRequestTracker({
      current: requestId,
      type: isNewConversation ? 'create' : 'continue',
    });

    setLoadingConversations((prev) => ({
      ...prev,
      [conversationKey]: true,
    }));

    // Reset error state when starting new request
    setConversationErrors((prev) => ({
      ...prev,
      [conversationKey]: false,
    }));

    setIsGeneratingResponse(false);

    const streamingBotMessageId = `streaming-${Date.now()}`;
    setStreamingMessageId(streamingBotMessageId);

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
      let finalConversation: Conversation | null = null;
      let hasCreatedMessage = false;
      let hasReceivedError = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmedLine = line.trim();
          if (trimmedLine === '') continue;

          const parsed = parseSSELine(trimmedLine);
          if (!parsed) continue;

          if (parsed.event) {
            currentEvent = parsed.event;
          } else if (parsed.data && currentEvent) {
            const errorReceived = await handleStreamingEvent(currentEvent, parsed.data, {
              streamingBotMessageId,
              isNewConversation,
              hasCreatedMessage,
              onConversationComplete: (conversation: Conversation) => {
                finalConversation = conversation;
              },
              onMessageCreated: () => (hasCreatedMessage = true),
              onErrorReceived: () => (hasReceivedError = true),
            });

            if (errorReceived) {
              hasReceivedError = true;
            }
          }
        }
      }

      // Handle completion based on whether we received an error or not
      if (finalConversation && !hasReceivedError) {
        await handleStreamingComplete(finalConversation, isNewConversation, streamingBotMessageId);
      } else if (hasReceivedError) {
        // For error cases, just clean up - don't add additional error messages
        cleanupStreaming();
      }
    } catch (error) {
      console.error('Streaming connection error:', error);

      // REMOVED: All hardcoded error message creation
      // The streaming error events will handle showing appropriate error messages
      // Only log the error for debugging purposes
    } finally {
      cleanupStreaming();
      setStreamingController(null);
      setStreamingMessageId(null);
      setIsGeneratingResponse(false);
      setLoadingConversations((prev) => ({
        ...prev,
        [conversationKey]: false,
      }));

      if (activeRequestTracker?.current === requestId) {
        setActiveRequestTracker({
          current: null,
          type: null,
        });
      }
    }
  };

  // FIXED: Updated handleStreamingEvent to accumulate error messages like answer_chunk
  const handleStreamingEvent = async (
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
    const conversationKey = currentConversationId || 'new';

    switch (event) {
      case 'connected':
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: '🔗 Connected successfully',
          }));
        });
        return false;

      case 'status':
        {
          const statusMessage = getEngagingStatusMessage(
            data.message || data.status || 'Processing...',
            data
          );
          requestAnimationFrame(() => {
            setConversationStatus((prev) => ({
              ...prev,
              [conversationKey]: statusMessage,
            }));
          });
        }
        return false;

      case 'query_decomposed':
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: '🔍 Analyzing your request',
          }));
        });
        return false;

      case 'search_complete':
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: `📚 Found ${data.results_count} relevant sources`,
          }));
        });
        return false;

      case 'results_ready':
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: `📊 Processing ${data.total_results} sources`,
          }));
        });
        return false;

      case 'answer_chunk':
        if (data.accumulated) {
          setIsGeneratingResponse(true);

          if (!context.hasCreatedMessage) {
            const streamingBotMessage: FormattedMessage = {
              type: 'bot',
              content: data.accumulated,
              createdAt: new Date(),
              updatedAt: new Date(),
              id: context.streamingBotMessageId,
              contentFormat: 'MARKDOWN',
              followUpQuestions: [],
              citations: data.citations || [],
              confidence: '',
              messageType: 'bot_response',
              timestamp: new Date(),
            };

            setMessages((prev) => [...prev, streamingBotMessage]);
            streamingContentRef.current = data.accumulated;
            accumulatedContentRef.current = data.accumulated;
            context.onMessageCreated();
          } else {
            smoothUpdateStreamingMessage(data.accumulated, context.streamingBotMessageId, false);

            setMessages((prev) =>
              prev.map((msg) => {
                if (msg.id === context.streamingBotMessageId) {
                  return {
                    ...msg,
                    citations: data.citations || msg.citations || [],
                    updatedAt: new Date(),
                  };
                }
                return msg;
              })
            );
          }

          requestAnimationFrame(() => {
            setConversationStatus((prev) => ({
              ...prev,
              [conversationKey]: undefined,
            }));
          });
        }
        return false;

      case 'complete':
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: undefined,
          }));
        });

        if (data.conversation) {
          context.onConversationComplete(data.conversation);
        }

        setIsGeneratingResponse(false);
        return false;

      case 'error':
        // FIXED: Accumulate error messages like answer_chunk instead of creating new messages
        requestAnimationFrame(() => {
          setConversationStatus((prev) => ({
            ...prev,
            [conversationKey]: undefined,
          }));

          // Mark that we've received an error for this conversation
          setConversationErrors((prev) => ({
            ...prev,
            [conversationKey]: true,
          }));
        });

        // Extract error message from different possible formats
        const currentErrorMessage = data.message || data.error || 'An unexpected error occurred';

        // FIXED: Accumulate errors instead of creating new messages each time
        if (!context.hasCreatedMessage) {
          // First error - create the error message
          const errorMessage: FormattedMessage = {
            type: 'bot',
            content: currentErrorMessage,
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

          setMessages((prev) => [...prev, errorMessage]);
          streamingContentRef.current = currentErrorMessage;
          accumulatedContentRef.current = currentErrorMessage;
          context.onMessageCreated();
        } else {
          // Subsequent errors - append to existing error message
          const currentContent = accumulatedContentRef.current || '';
          const accumulatedErrorContent = currentContent + '\n\n' + currentErrorMessage;

          // Update the accumulated content
          accumulatedContentRef.current = accumulatedErrorContent;
          streamingContentRef.current = accumulatedErrorContent;

          // Update the message with accumulated errors
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === context.streamingBotMessageId) {
                return {
                  ...msg,
                  content: accumulatedErrorContent,
                  updatedAt: new Date(),
                };
              }
              return msg;
            })
          );
        }

        context.onErrorReceived();
        return true;

      default:
        console.log('Unknown event:', event, data);
        return false;
    }
  };

  // IMPROVED: Updated handleStreamingComplete with better final message handling
  const handleStreamingComplete = async (
    conversation: Conversation,
    isNewConversation: boolean,
    streamingBotMessageId: string
  ): Promise<void> => {
    cleanupStreaming();

    const conversationKey = conversation._id;
    requestAnimationFrame(() => {
      setConversationStatus((prev) => ({
        ...prev,
        [conversationKey]: undefined,
        new: undefined,
      }));
    });

    if (isNewConversation) {
      setSelectedChat(conversation);
      setCurrentConversationId(conversation._id);
      currentConversationIdRef.current = conversation._id;
      setShouldRefreshSidebar(true);
    }

    const finalBotMessage = conversation.messages
      .filter((msg: any) => msg.messageType === 'bot_response')
      .pop();

    if (finalBotMessage) {
      const formattedFinalMessage = formatMessage(finalBotMessage);
      if (formattedFinalMessage) {
        requestAnimationFrame(() => {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === streamingBotMessageId) {
                return {
                  ...formattedFinalMessage,
                  id: finalBotMessage._id,
                };
              }
              return msg;
            })
          );

          setExpandedCitations((prevStates) => {
            const newStates = { ...prevStates };
            setMessages((prevMessages) => {
              const updatedIndex = prevMessages.findIndex((msg) => msg.id === finalBotMessage._id);
              if (
                updatedIndex !== -1 &&
                formattedFinalMessage.citations &&
                formattedFinalMessage.citations.length > 0
              ) {
                newStates[updatedIndex] = false;
              }
              return prevMessages;
            });
            return newStates;
          });
        });
      }
    }

    setIsGeneratingResponse(false);
  };

  // FIX: Updated handleChatSelect to better handle conversation status
  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id) return;

      // Stop any ongoing streaming
      if (streamingController) {
        streamingController.abort();
        cleanupStreaming();
      }

      try {
        setShowWelcome(false);
        setCurrentConversationId(chat._id);
        currentConversationIdRef.current = chat._id;
        navigate(`/${chat._id}`);
        setIsLoadingConversation(true);

        setActiveRequestTracker({ current: null, type: null });
        setLoadingConversations({});
        setConversationStatus({});
        setConversationErrors({}); // FIX: Reset error state
        setPendingResponseConversationId(null);
        setIsGeneratingResponse(false);

        setTimeout(() => {
          setMessages([]);
          setExpandedCitations({});
          setOpenPdfView(false);
        }, 0);

        const response = await axios.get(`/api/v1/conversations/${chat._id}`);
        const { conversation } = response.data;

        if (!conversation || !Array.isArray(conversation.messages)) {
          throw new Error('Invalid conversation data');
        }

        if (currentConversationIdRef.current === chat._id) {
          // FIX: Handle conversation status properly based on actual status
          if (conversation.status) {
            if (conversation.status === 'Inprogress') {
              setConversationStatus((prev) => ({
                ...prev,
                [chat._id]: 'Processing your request...',
              }));
            } else if (conversation.status === 'Failed') {
              // FIX: Don't show processing indicator for failed conversations
              setConversationStatus((prev) => ({
                ...prev,
                [chat._id]: undefined,
              }));
              setConversationErrors((prev) => ({
                ...prev,
                [chat._id]: true,
              }));
            } else {
              // For completed conversations, clear status
              setConversationStatus((prev) => ({
                ...prev,
                [chat._id]: undefined,
              }));
            }
          }

          setSelectedChat(conversation);

          const formattedMessages = conversation.messages
            .map(formatMessage)
            .filter(Boolean) as FormattedMessage[];

          const citationStates: ExpandedCitationsState = {};
          formattedMessages.forEach((msg, idx) => {
            if (msg.type === 'bot' && msg.citations && msg.citations.length > 0) {
              citationStates[idx] = false;
            }
          });

          setMessages(formattedMessages);
          setExpandedCitations(citationStates);
        }
      } catch (error) {
        console.error('Error loading conversation:', error);
        setSelectedChat(null);
        setCurrentConversationId(null);
        currentConversationIdRef.current = null;
        setMessages([]);
        setExpandedCitations({});
      } finally {
        setIsLoadingConversation(false);
      }
    },
    [formatMessage, navigate, streamingController, cleanupStreaming]
  );

  // Rest of your existing functions remain exactly the same...
  const handleSendMessage = useCallback(
    async (messageOverride?: string): Promise<void> => {
      let trimmedInput = '';

      if (typeof messageOverride === 'string') {
        trimmedInput = messageOverride.trim();
      } else {
        trimmedInput = inputValue.trim();
      }

      if (!trimmedInput) return;

      // Stop any ongoing streaming
      if (streamingController) {
        streamingController.abort();
        cleanupStreaming();
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

      // Clear input and add user message
      if (typeof messageOverride === 'string' && showWelcome) {
        setShowWelcome(false);
      }

      setInputValue('');
      setMessages((prev) => [...prev, tempUserMessage]);

      // Determine streaming URL and body based on your API format
      let streamingUrl: string;
      let requestBody: any;

      if (wasCreatingNewConversation) {
        streamingUrl = `${CONFIG.backendUrl}/api/v1/conversations/stream`;
        requestBody = { query: trimmedInput };
      } else {
        streamingUrl = `${CONFIG.backendUrl}/api/v1/conversations/${currentConversationId}/messages/stream`;
        requestBody = { query: trimmedInput };
      }

      await handleStreamingResponse(streamingUrl, requestBody, wasCreatingNewConversation);
    },
    [
      inputValue,
      currentConversationId,
      showWelcome,
      streamingController,
      handleStreamingResponse,
      cleanupStreaming,
    ]
  );

  // Keep ALL existing utility functions exactly as they are
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
      console.log('PPT with large file size');
      throw new Error('Large fize size, redirecting to web page ');
    }
  };

  // Keep all existing onViewPdf, onClosePdf, toggleCitations functions exactly the same
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

  const toggleCitations = useCallback((index: number): void => {
    setExpandedCitations((prev) => {
      const newState = { ...prev };
      newState[index] = !prev[index];
      return newState;
    });
  }, []);

  const MemoizedChatMessagesArea = React.memo(ChatMessagesArea);
  const MemoizedWelcomeMessage = React.memo(WelcomeMessage);

  const handleNewChat = useCallback((): void => {
    // Stop any ongoing streaming
    if (streamingController) {
      streamingController.abort();
      cleanupStreaming();
    }

    setPendingResponseConversationId(null);
    setActiveRequestTracker({ current: null, type: null });
    currentConversationIdRef.current = null;
    setCurrentConversationId(null);
    navigate('/');

    setTimeout(() => {
      setMessages([]);
      setInputValue('');
      setExpandedCitations({});
      setShouldRefreshSidebar(true);
      setConversationStatus({});
      setConversationErrors({}); // FIX: Reset error state
      setShowWelcome(true);
      setLoadingConversations({});
      setIsLoadingConversation(false);
      setSelectedChat(null);
      setFileBuffer(null);
      setOpenPdfView(false);
      setIsGeneratingResponse(false);
    }, 0);
  }, [navigate, streamingController, cleanupStreaming]);

  // Keep existing handleRegenerateMessage (using non-streaming API)
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

  const handleInputChange = useCallback(
    (input: string | React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
      let newValue: string;
      if (typeof input === 'string') {
        newValue = input;
      } else if (
        input &&
        typeof input === 'object' &&
        'target' in input &&
        input.target &&
        'value' in input.target
      ) {
        newValue = input.target.value;
      } else {
        return;
      }
      setInputValue(newValue);
    },
    []
  );

  useEffect(() => {
    if (conversationId && conversationId !== currentConversationId) {
      handleChatSelect({ _id: conversationId } as Conversation);
    }
  }, [conversationId, handleChatSelect, currentConversationId]);

  // IMPROVED: Updated cleanup in useEffect with better cleanup
  useEffect(
    () => () => {
      if (streamingController) {
        streamingController.abort();
      }
      cleanupStreaming();
    },
    [streamingController, cleanupStreaming]
  );

  // IMPROVED: Clean up when component unmounts or conversation changes
  useEffect(
    () => () => {
      cleanupStreaming();
    },
    [currentConversationId, cleanupStreaming]
  );

  return (
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
              onSubmit={(message: string) => handleSendMessage(message)}
              isLoading={Boolean(streamingMessageId)}
            />
          ) : (
            <>
              <MemoizedChatMessagesArea
                key={`chat-area-${currentConversationId || 'new'}`}
                messages={messages}
                isLoading={isCurrentConversationLoading() || Boolean(streamingMessageId)}
                onRegenerateMessage={handleRegenerateMessage}
                onFeedbackSubmit={handleFeedbackSubmit}
                conversationId={currentConversationId}
                isLoadingConversation={isLoadingConversation}
                onViewPdf={onViewPdf}
                currentStatus={conversationStatus[currentConversationId || 'new']}
                hasConversationError={conversationErrors[currentConversationId || 'new']}
              />

              <Box
                sx={{
                  flexShrink: 0,
                  borderTop: 1,
                  borderColor: 'divider',
                  backgroundColor:
                    theme.palette.mode === 'dark'
                      ? alpha(theme.palette.background.paper, 0.5)
                      : theme.palette.background.paper,
                  mt: 'auto',
                  py: 1.5,
                  minWidth: '95%',
                  mx: 'auto',
                  borderRadius: 2,
                }}
              >
                <ChatInput onSubmit={handleSendMessage} isLoading={Boolean(streamingMessageId)} />
              </Box>
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
  );
};

export default ChatInterface;
