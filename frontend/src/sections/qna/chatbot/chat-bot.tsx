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

const StyledCloseButton = styled(Button)(({ theme }) => ({
  position: 'fixed',
  top: 60,
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

const ChatInterface = () => {
  // const [searchQuery, setSearchQuery] = useState<string>('');
  const [messages, setMessages] = useState<FormattedMessage[]>([]);
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState<boolean>(false);
  const [expandedCitations, setExpandedCitations] = useState<ExpandedCitationsState>({});
  const [isDrawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  // eslint-disable-next-line
  const [selectedChat, setSelectedChat] = useState<Conversation | null>(null);
  const [shouldRefreshSidebar, setShouldRefreshSidebar] = useState<boolean>(false);
  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId: string }>();
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [aggregatedCitations, setAggregatedCitations] = useState<CustomCitation[] | null>([]);
  const [openPdfView, setOpenPdfView] = useState<boolean>(false);
  // const [showQuestionDetails, setShowQuestionDetails] = useState<boolean>(false);
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
  const isCurrentConversationLoading = useCallback(
    () =>
      currentConversationId
        ? loadingConversations[currentConversationId]
        : loadingConversations.new,
    [currentConversationId, loadingConversations]
  );

  const [conversationStatus, setConversationStatus] = useState<{
    [key: string]: string | undefined;
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

  const isCurrentConversationThinking = useCallback(() => {
    const conversationKey = currentConversationId || 'new';
    return conversationStatus[conversationKey] === 'Inprogress';
  }, [currentConversationId, conversationStatus]);

  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });
  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };
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
  const resetViewerStates = () => {
    setTransitioning(true);
    setIsViewerReady(false);
    setPdfUrl(null);
    setFileBuffer(null);

    // Delay clearing other states to ensure clean unmount
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

  const onViewPdf = async (
    url: string,
    citationMeta: Metadata,
    citations: CustomCitation[],
    isExcelFile: boolean = false,
    bufferData?: ArrayBuffer
  ): Promise<void> => {
    // setAggregatedCitations(citations);
    // setPdfUrl(url);
    // setOpenPdfView(true);
    // console.log(aggregatedCitations);
    // setShowQuestionDetails(true);
    // // setDrawerOpen(false);
    setTransitioning(true);
    setIsViewerReady(false);
    setDrawerOpen(false);
    setOpenPdfView(true);
    setAggregatedCitations(citations);
    setFileBuffer(null);
    setPdfUrl(null);
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

          // Read the blob response as text to check if it's JSON with signedUrl
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
            // Try to parse as JSON to check for signedUrl property
            const jsonData = JSON.parse(text);
            if (jsonData && jsonData.signedUrl) {
              setPdfUrl(jsonData.signedUrl);
            }
          } catch (e) {
            // Case 2: Local storage - Return buffer
            const bufferReader = new FileReader();
            const arrayBufferPromise = new Promise<ArrayBuffer>((resolve) => {
              bufferReader.onload = () => {
                resolve(bufferReader.result as ArrayBuffer);
              };
              bufferReader.readAsArrayBuffer(downloadResponse.data);
            });

            const buffer = await arrayBufferPromise;
            setFileBuffer(buffer);
            // if (['pptx', 'ppt'].includes(citationMeta?.extension)) {

            // }
          }
        } catch (error) {
          console.error('Error downloading document:', error);
          setSnackbar({
            open: true,
            // Provide a clear message about what's happening
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info', // Use 'info' or 'warning' for redirection notice
          });
          let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;

          // Keep the URL fix logic (though less likely needed for non-UPLOAD here, better safe)
          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }

          console.log(`Attempting to redirect to webUrl: ${webUrl}`);

          setTimeout(() => {
            onClosePdf();
          }, 500);

          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
                console.log('Opened document in new tab');
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
          // Extract filename from content-disposition header
          let filename = record.recordName || `document-${recordId}`;
          const contentDisposition = connectorResponse.headers['content-disposition'];
          if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?([^"]*)"?/);
            if (filenameMatch && filenameMatch[1]) {
              filename = filenameMatch[1];
            }
          }

          // Convert blob directly to ArrayBuffer
          const bufferReader = new FileReader();
          const arrayBufferPromise = new Promise<ArrayBuffer>((resolve, reject) => {
            bufferReader.onload = () => {
              // Create a copy of the buffer to prevent detachment issues
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
            // Provide a clear message about what's happening
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info', // Use 'info' or 'warning' for redirection notice
          });
          let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;

          // Keep the URL fix logic (though less likely needed for non-UPLOAD here, better safe)
          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }

          console.log(`Attempting to redirect to webUrl: ${webUrl}`);

          setTimeout(() => {
            onClosePdf();
          }, 500);

          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
                console.log('Opened document in new tab');
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
      // setSnackbar({
      //   open: true,
      //   message: err.message.includes('fetch failed') ? 'Failed to fetch document' : err.message,
      //   severity: 'error',
      // });
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

    // Allow component to mount
    setTimeout(() => {
      setIsViewerReady(true);
      setTransitioning(false);
    }, 100);
  };

  const onClosePdf = (): void => {
    // setOpenPdfView(false);
    // setIsExcel(false);
    // setShowQuestionDetails(false);

    // setTimeout(() => {
    //   setPdfUrl(null);
    //   setAggregatedCitations([]);
    // }, 50); // Match this with your transition duration
    resetViewerStates();
    setFileBuffer(null);
  };

  // Also update the toggleCitations function to handle citation state more explicitly
  const toggleCitations = useCallback((index: number): void => {
    setExpandedCitations((prev) => {
      const newState = { ...prev };
      newState[index] = !prev[index];
      return newState;
    });
  }, []);

  const MemoizedChatMessagesArea = React.memo(ChatMessagesArea);
  const MemoizedWelcomeMessage = React.memo(WelcomeMessage);

  // Handle new chat creation
  const handleNewChat = useCallback((): void => {
    // First apply the critical state changes that would affect routing
    setPendingResponseConversationId(null);
    setActiveRequestTracker({ current: null, type: null });
    currentConversationIdRef.current = null;
    setCurrentConversationId(null);
    navigate('/');

    // Then use setTimeout to delay non-critical UI updates
    // This helps prevent multiple rapid state changes causing flickering
    setTimeout(() => {
      setMessages([]);
      setInputValue('');
      setExpandedCitations({});
      setShouldRefreshSidebar(true);
      setConversationStatus({});
      setShowWelcome(true);
      setLoadingConversations({});
      setIsLoadingConversation(false);
      setSelectedChat(null);
      setFileBuffer(null);
      setOpenPdfView(false);
    }, 0);
  }, [navigate]);

  const handleSendMessage = useCallback(async (): Promise<void> => {
    const trimmedInput = inputValue.trim();
    if (!trimmedInput) return;
    const conversationKey = currentConversationId || 'new';
    const requestConversationId = currentConversationId;
    const wasCreatingNewConversation = currentConversationId === null;

    // Track this request as active
    const requestId = `${Date.now()}-${Math.random()}`;
    setActiveRequestTracker({
      current: requestId,
      type: currentConversationId ? 'continue' : 'create',
    });

    setLoadingConversations((prev) => ({
      ...prev,
      [conversationKey]: true,
    }));

    const tempUserMessage = {
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
    if (showWelcome) {
      setShowWelcome(false);
    }
    try {
      setInputValue('');
      setMessages((prev) => [...prev, tempUserMessage]);

      let response;
      let conversation: any;

      if (!currentConversationId) {
        // Create new conversation
        response = await axios.post<{ conversation: Conversation }>(
          '/api/v1/conversations/create',
          {
            query: trimmedInput,
          }
        );

        if (!response?.data?.conversation) {
          throw new Error('Invalid response format');
        }

        conversation = response.data.conversation;
        const convId = conversation._id;
        
        // FIXED: Remove the confusing duplicate checks and set state immediately
        if (wasCreatingNewConversation) {
          setSelectedChat(conversation);
          setCurrentConversationId(convId);
          currentConversationIdRef.current = convId;
          setShouldRefreshSidebar(true);
          // navigate(`/${convId}`);

          if (conversation.status) {
            setConversationStatus((prev) => ({
              ...prev,
              [convId]: conversation.status,
            }));
          }

          // If conversation is already complete, update messages immediately
          if (conversation.status === 'Complete') {
            const allMessages = conversation.messages
              .map(formatMessage)
              .filter(Boolean) as FormattedMessage[];

            setMessages(allMessages);
          }
        }
      } else {
        // Continue existing conversation
        response = await axios.post<{ conversation: Conversation }>(
          `/api/v1/conversations/${currentConversationId}/messages`,
          { query: trimmedInput }
        );

        if (!response?.data?.conversation?.messages) {
          throw new Error('Invalid response format');
        }

        conversation = response.data.conversation;
        const responseConversationId = conversation._id;

        // FIXED: Simplified condition - just check if we're still on the same conversation
        if (
          currentConversationIdRef.current === responseConversationId &&
          currentConversationId === responseConversationId
        ) {
          if (conversation.status) {
            setConversationStatus((prev) => ({
              ...prev,
              [responseConversationId]: conversation.status,
            }));
          }

          // If conversation is complete, update messages immediately
          if (conversation.status === 'Complete') {
            const allMessages = conversation.messages
              .map(formatMessage)
              .filter(Boolean) as FormattedMessage[];

            setMessages(allMessages);
          }
        }
      }
    } catch (error) {
      console.error('Error:', error);

      // Only show error if this request is still active
      if (
        (requestConversationId === currentConversationId &&
          requestConversationId === currentConversationIdRef.current) ||
        (wasCreatingNewConversation && !currentConversationId)
      ) {
        const errorMessage: FormattedMessage = {
          type: 'bot',
          content: 'Sorry, I encountered an error processing your request.',
          createdAt: new Date(),
          updatedAt: new Date(),
          id: `error-${Date.now()}`,
          contentFormat: 'MARKDOWN',
          followUpQuestions: [],
          citations: [],
          confidence: '',
          messageType: 'bot_response',
          timestamp: new Date(),
        };

        setMessages((prev) => [...prev, errorMessage]);
        if (wasCreatingNewConversation && !currentConversationId) {
          // User was trying to create a new conversation and it failed
          // No need to navigate away, just stay in the "new conversation" state
          setCurrentConversationId(null);
          currentConversationIdRef.current = null;
        }
      }
    } finally {
      // Clear loading states
      setLoadingConversations((prev) => ({
        ...prev,
        [conversationKey]: false,
      }));

      // Clear active request tracker if this was the active request
      if (activeRequestTracker?.current === requestId) {
        setActiveRequestTracker({
          current: null,
          type: null,
        });
      }
    }
  }, [inputValue, currentConversationId, formatMessage, activeRequestTracker, showWelcome]);

  // Update handleRegenerateMessage
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

        // Format all messages from response
        const allMessages = response.data.conversation.messages
          .map(formatMessage)
          .filter(Boolean) as FormattedMessage[];

        // Find the regenerated message - it should be the last bot message
        const regeneratedMessage = allMessages.filter((msg) => msg.type === 'bot').pop();

        if (!regeneratedMessage) {
          throw new Error('No regenerated message found in response');
        }

        // Update messages by replacing only the regenerated message while keeping all others
        setMessages((prevMessages) =>
          prevMessages.map((msg) => {
            // Only replace the message that was regenerated
            if (msg.id === messageId) {
              return {
                ...regeneratedMessage,
                // Keep any existing metadata/state that shouldn't be changed
                createdAt: msg.createdAt, // Preserve original timestamp to maintain order
              };
            }
            return msg;
          })
        );

        // Update citation states
        setExpandedCitations((prevStates) => {
          const newStates = { ...prevStates };
          const messageIndex = messages.findIndex((msg) => msg.id === messageId);
          if (messageIndex !== -1) {
            // Safely check citations array existence and length
            const hasCitations =
              regeneratedMessage.citations && regeneratedMessage.citations.length > 0;
            // Preserve existing citation state or initialize to false
            newStates[messageIndex] = hasCitations ? prevStates[messageIndex] || false : false;
          }
          return newStates;
        });
      } catch (error) {
        // Show error in place of regenerated message while preserving others
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

  const handleChatSelect = useCallback(
    async (chat: Conversation) => {
      if (!chat?._id) return;

      try {
        // Critical state changes first
        setShowWelcome(false);
        setCurrentConversationId(chat._id);
        currentConversationIdRef.current = chat._id;
        navigate(`/${chat._id}`);
        setIsLoadingConversation(true);

        // Clear other states
        setActiveRequestTracker({ current: null, type: null });
        setLoadingConversations({});
        setConversationStatus({});
        setPendingResponseConversationId(null);

        // Reset UI state with slight delay
        setTimeout(() => {
          setMessages([]);
          setExpandedCitations({});
          setOpenPdfView(false);
        }, 0);

        // Get conversation details
        const response = await axios.get(`/api/v1/conversations/${chat._id}`);
        const { conversation } = response.data;

        if (!conversation || !Array.isArray(conversation.messages)) {
          throw new Error('Invalid conversation data');
        }

        // Only proceed if we're still viewing this conversation
        if (currentConversationIdRef.current === chat._id) {
          if (conversation.status) {
            setConversationStatus((prev) => ({
              ...prev,
              [chat._id]: conversation.status,
            }));
          }

          // Set complete conversation data
          setSelectedChat(conversation);

          // Format messages and preserve full data structure
          const formattedMessages = conversation.messages
            .map(formatMessage)
            .filter(Boolean) as FormattedMessage[];

          // Initialize citation states for all bot messages with citations
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
    [formatMessage, navigate]
  );

  const handleSidebarRefreshComplete = useCallback(() => {
    setShouldRefreshSidebar(false);
  }, []);

  // Handle feedback submission
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

  const handleInputChange = (
    input: string | React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ): void => {
    // Get the value directly and store it
    // This ensures we capture the value regardless of what happens to the event object
    if (typeof input === 'string') {
      // If directly passed a string
      setInputValue(input);
    } else if (
      input &&
      typeof input === 'object' &&
      'target' in input &&
      input.target &&
      'value' in input.target
    ) {
      // If passed an event object with target.value
      setInputValue(input.target.value);
    }
  };

  // const handleInputChange = (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
  //   // Make sure you are using event.target.value directly
  //   // without any reversal logic.
  //   console.log(event);
  //   setInputValue(event.target.value);
  // };

  useEffect(() => {
    if (conversationId && conversationId !== currentConversationId) {
      handleChatSelect({ _id: conversationId } as Conversation);
    }
  }, [conversationId, handleChatSelect, currentConversationId]);

  // Add this useEffect to check conversation status periodically
  useEffect(() => {
    let intervalId: NodeJS.Timeout | null = null;

    const checkConversationStatus = async () => {
      const inProgressConversations = Object.entries(conversationStatus)
        .filter(([_, status]) => status === 'Inprogress')
        .map(([id]) => id);

      if (inProgressConversations.length > 0) {
        const promises = inProgressConversations.map(async (convId) => {
          try {
            // Capture the current conversation ID at the start of this request
            const currentId = currentConversationIdRef.current;

            const response = await axios.get(`/api/v1/conversations/${convId}`);
            const { conversation } = response.data;

            if (conversation?.status) {
              setConversationStatus((prev) => ({
                ...prev,
                [convId]: conversation.status,
              }));

              // Handle when conversation becomes complete
              if (conversation.status === 'Complete') {
                // Triple check: the conversation ID must match what we captured initially,
                // what's currently in the ref, and the state
                if (
                  convId === currentId &&
                  convId === currentConversationIdRef.current &&
                  convId === currentConversationId
                ) {
                  const formattedMessages = conversation.messages
                    .map(formatMessage)
                    .filter(Boolean) as FormattedMessage[];

                  setMessages(formattedMessages);
                }
              }
            }
          } catch (error) {
            console.error(`Failed to check status for conversation ${convId}:`, error);
          }
        });

        // Wait for all checks to complete
        await Promise.all(promises);
      }
    };

    // Check every 3 seconds if there are any 'Inprogress' conversations
    if (Object.values(conversationStatus).includes('Inprogress')) {
      intervalId = setInterval(checkConversationStatus, 3000);
    }

    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [conversationStatus, currentConversationId, formatMessage]);

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
              key="welcome-screen" // Key helps React manage this component better
              inputValue={inputValue}
              onInputChange={handleInputChange}
              onSubmit={handleSendMessage}
              isLoading={isLoading}
            />
          ) : (
            <>
              <MemoizedChatMessagesArea
                key={`chat-area-${currentConversationId || 'new'}`} // Key based on conversation ID
                messages={messages}
                isLoading={isCurrentConversationLoading() || isCurrentConversationThinking()}
                expandedCitations={expandedCitations}
                onToggleCitations={toggleCitations}
                onRegenerateMessage={handleRegenerateMessage}
                onFeedbackSubmit={handleFeedbackSubmit}
                conversationId={currentConversationId}
                isLoadingConversation={isLoadingConversation}
                onViewPdf={onViewPdf}
                // Add these new props:
                inputValue={inputValue}
                onInputChange={handleInputChange}
                onSubmit={handleSendMessage}
                showWelcome={showWelcome}
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
                }}
              >
                <ChatInput
                  value={inputValue}
                  onChange={handleInputChange}
                  onSubmit={handleSendMessage}
                  isLoading={isCurrentConversationLoading()}
                />
              </Box>
            </>
          )}
        </Box>

        {/* PDF Viewer */}
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

            {/* Render viewer with citations */}
            {isViewerReady &&
              (pdfUrl || fileBuffer) &&
              aggregatedCitations &&
              // !transitioning &&
              (isExcel ? (
                <ExcelViewer
                  key="excel-viewer"
                  citations={aggregatedCitations}
                  fileUrl={pdfUrl}
                  excelBuffer={fileBuffer}
                />
              ) : isDocx ? (
                <DocxHighlighterComp
                  key="docx-viewer"
                  url={pdfUrl}
                  buffer={fileBuffer}
                  citations={aggregatedCitations}
                  renderOptions={{
                    breakPages: true,
                    renderHeaders: true,
                    renderFooters: true,
                  }}
                />
              ) : isMarkdown ? (
                <MarkdownViewer
                  key="markdown-viewer"
                  url={pdfUrl}
                  buffer={fileBuffer}
                  citations={aggregatedCitations}
                />
              ) : isHtml ? (
                <HtmlViewer
                  key="html-viewer"
                  url={pdfUrl}
                  buffer={fileBuffer}
                  citations={aggregatedCitations}
                />
              ) : isTextFile ? (
                <TextViewer
                  key="text-viewer"
                  url={pdfUrl}
                  buffer={fileBuffer}
                  citations={aggregatedCitations}
                />
              ) : (
                <PdfHighlighterComp
                  key="pdf-viewer"
                  pdfUrl={pdfUrl}
                  pdfBuffer={fileBuffer}
                  citations={aggregatedCitations}
                />
              ))}
            <StyledCloseButton
              onClick={onClosePdf}
              startIcon={<Icon icon={closeIcon} />}
              size="small"
            >
              Close
            </StyledCloseButton>
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
