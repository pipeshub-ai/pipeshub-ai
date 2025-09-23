// RecordSalesAgent.js
import type {
  Message,
  Citation,
  Metadata,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
} from 'src/types/chat-bot';

import { Icon } from '@iconify/react';
import menuIcon from '@iconify-icons/mdi/menu';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useCallback } from 'react';
import chatOutlineIcon from '@iconify-icons/mdi/chat-outline';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';

import { Box, Button, styled, useTheme, Typography, IconButton } from '@mui/material';

import axiosInstance from 'src/utils/axios';

import RecordSidebar from './ask-me-anything-sidebar';
import ChatInput, { Model, ChatMode } from '../qna/chatbot/components/chat-input';
import PdfHighlighterComp from '../qna/chatbot/components/pdf-highlighter';

import type {
  Record,
  RecordHeaderProps,
  ConversationRecord,
  RecordSalesAgentProps,
} from './types/records-ask-me-anything';

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

const StyledHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(2),
  borderBottom: `1px solid ${theme.palette.divider}`,
  backgroundColor: theme.palette.background.paper,
  minHeight: 64,
}));

const MenuButton = styled(IconButton)(({ theme }) => ({
  marginRight: theme.spacing(1),
  '&:hover': {
    backgroundColor: theme.palette.action.hover,
  },
}));

const formatDate = (dateString: string) => {
  if (!dateString) return '';

  const date = new Date(dateString);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date >= today) {
    return `Today at ${date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })}`;
  }

  if (date >= yesterday) {
    return `Yesterday at ${date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })}`;
  }

  return date.toLocaleDateString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const RecordHeader = ({
  record,
  isConversation,
  isDrawerOpen,
  onDrawerToggle,
}: RecordHeaderProps) => {
  const theme = useTheme();

  return (
    <StyledHeader>
      {!isDrawerOpen && (
        <MenuButton onClick={onDrawerToggle} size="small" sx={{ mr: 1 }}>
          <Icon icon={menuIcon} />
        </MenuButton>
      )}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1 }}>
        <Icon
          icon={isConversation ? chatOutlineIcon : fileDocumentOutlineIcon}
          style={{
            color: theme.palette.primary.main,
            width: 24,
            height: 24,
          }}
        />
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {isConversation
            ? record?.title || 'Untitled Conversation'
            : record?.name || 'Select a Record'}
        </Typography>
      </Box>
      {/* {record && !isConversation && (
        <Chip
          size="small"
          label={record.departments?.[0]?.name || 'No Department'}
          sx={{
            backgroundColor: 'background.neutral',
            '& .MuiChip-label': { px: 2 },
            height: 28,
          }}
        />
      )}
      {record && isConversation && (
        <Chip
          size="small"
          label={formatDate(record.lastActivityAt)}
          sx={{
            backgroundColor: 'background.neutral',
            '& .MuiChip-label': { px: 2 },
            height: 28,
          }}
        />
      )} */}
    </StyledHeader>
  );
};

const RecordSalesAgent = ({ initialContext, recordId }: RecordSalesAgentProps) => {
  const [messages, setMessages] = useState<FormattedMessage[]>([]);
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [expandedCitations, setExpandedCitations] = useState<ExpandedCitationsState>({});
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string>('');
  const [aggregatedCitations, setAggregatedCitations] = useState<CustomCitation[]>([]);
  const [openPdfView, setOpenPdfView] = useState<boolean>(false);
  const [isDrawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [shouldRefreshSidebar, setShouldRefreshSidebar] = useState<boolean>(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState<boolean>(false);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [selectedChatMode, setSelectedChatMode] = useState<ChatMode | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<Record | null>(
    initialContext?.recordId
      ? {
          _id: initialContext.recordId,
          name: initialContext.recordName,
          departments: initialContext.departments,
          recordType: initialContext.recordType,
        }
      : null
  );
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
      return {
        ...baseMessage,
        type: 'user',
        feedback: apiMessage.feedback || [],
      };
    }

    if (apiMessage.messageType === 'bot_response') {
      return {
        ...baseMessage,
        type: 'bot',
        confidence: apiMessage.confidence || '',
        citations: (apiMessage.citations || []).map((citation: Citation) => ({
          id: citation.citationId,
          _id: citation.citationData?._id || citation.citationId,
          citationId: citation.citationId,
          content: citation.citationData.content || '',
          metadata: citation.citationData.metadata || [],
          orgId: citation.orgId,
          citationType: citation.citationType,
          createdAt: citation.citationData?.createdAt || new Date().toISOString(),
          updatedAt: citation.citationData?.updatedAt || new Date().toISOString(),
          chunkIndex: citation.citationData.chunkIndex || 1,
        })),
      };
    }

    return null;
  }, []);

  // const loadConversation = async (conversationId) => {
  //   try {
  //     setIsLoading(true);
  //     const response = await axiosInstance.get(`/api/v1/conversations/${conversationId}`);

  //     if (!response?.data?.conversation) {
  //       throw new Error('Invalid response format');
  //     }

  //     const { conversation } = response.data;
  //     setCurrentConversationId(conversation._id);

  //     const formattedMessages = conversation.messages.map(formatMessage).filter(Boolean);

  //     setMessages(formattedMessages);

  //     const newExpandedCitations = {};
  //     formattedMessages.forEach((msg, index) => {
  //       if (msg.citations?.length > 0) {
  //         newExpandedCitations[index] = false;
  //       }
  //     });
  //     setExpandedCitations(newExpandedCitations);
  //   } catch (error) {
  //     console.error('Error loading conversation:', error);
  //     setMessages([
  //       {
  //         type: 'bot',
  //         content: 'Sorry, I encountered an error loading this conversation.',
  //         createdAt: new Date(),
  //         updatedAt: new Date(),
  //         id: `error-${Date.now()}`,
  //         contentFormat: 'MARKDOWN',
  //         followUpQuestions: [],
  //         citations: [],
  //         confidence: '',
  //         messageType: 'bot_response',
  //       },
  //     ]);
  //   } finally {
  //     setIsLoading(false);
  //   }
  // };

  // Add this to your RecordSalesAgent component

  const handleRecordSelect = useCallback(
    async (record: ConversationRecord) => {
      try {
        if (!record?._id) return;

        setIsLoadingConversation(true);
        setMessages([]);
        setExpandedCitations({});

        const response = await axiosInstance.get(`/api/v1/conversations/${record._id}`);
        const { conversation } = response.data;

        if (!conversation || !Array.isArray(conversation.messages)) {
          throw new Error('Invalid conversation data');
        }

        // Set the current conversation ID
        setCurrentConversationId(record._id);

        // Format messages and set them
        const formattedMessages = conversation.messages.map(formatMessage).filter(Boolean);

        // Initialize citation states for all bot messages with citations
        const citationStates: ExpandedCitationsState = {};
        formattedMessages.forEach((msg: FormattedMessage, idx: number) => {
          if (msg.type === 'bot' && msg.citations && msg.citations.length > 0) {
            citationStates[idx] = false;
          }
        });

        setMessages(formattedMessages);
        setExpandedCitations(citationStates);

        // Update selected record with conversation data
        setSelectedRecord({
          ...selectedRecord,
          title: conversation.title,
          _id: conversation._id,
          conversationSource: 'records',
          lastActivityAt: conversation.lastActivityAt,
        });
      } catch (error) {
        setMessages([
          {
            type: 'bot',
            content: 'Sorry, I encountered an error loading this conversation.',
            createdAt: new Date(),
            updatedAt: new Date(),
            id: `error-${Date.now()}`,
            contentFormat: 'MARKDOWN',
            followUpQuestions: [],
            citations: [],
            confidence: '',
            messageType: 'bot_response',
            timestamp: new Date(),
          },
        ]);
        setCurrentConversationId(null);
        setExpandedCitations({});
      } finally {
        setIsLoadingConversation(false);
      }
    },
    [formatMessage, selectedRecord]
  );

  const handleNewChat = useCallback(() => {
    setCurrentConversationId(null);
    setMessages([]);
    setExpandedCitations({});
    setInputValue('');

    // Reset selected record to initial record state
    setSelectedRecord({
      ...initialContext,
      _id: initialContext.recordId,
      name: initialContext.recordName,
      departments: initialContext.departments,
      recordType: initialContext.recordType,
      conversationSource: 'records',
    });
  }, [initialContext]);

  // Update handleSendMessage to include recordIds and sourceRecordId
  const handleSendMessage = useCallback(async () => {
    const trimmedInput = inputValue.trim();
    if (!trimmedInput || isLoading || !selectedRecord) return;

    const tempUserMessage = {
      id: `temp-${Date.now()}`,
      timestamp: new Date(),
      content: trimmedInput,
      type: 'user',
      contentFormat: 'MARKDOWN',
      followUpQuestions: [],
      createdAt: new Date(),
      updatedAt: new Date(),
      feedback: [],
      citations: [],
      messageType: 'user_query',
    };

    try {
      setIsLoading(true);
      setInputValue('');

      let response;
      if (!currentConversationId) {
        // Create new conversation
        response = await axiosInstance.post('/api/v1/conversations/create', {
          query: trimmedInput,
          conversationSource: 'records',
          recordIds: [selectedRecord._id],
          conversationSourceRecordId: selectedRecord._id,
        });

        if (!response?.data?.conversation) {
          throw new Error('Invalid response format');
        }

        const { conversation } = response.data;
        setCurrentConversationId(conversation._id);
        const formattedMessages = conversation.messages.map(formatMessage).filter(Boolean);
        setMessages(formattedMessages);

        // Trigger sidebar refresh after creating new conversation
        setShouldRefreshSidebar(true);
      } else {
        // Continue existing conversation
        setMessages((prev) => [...prev, tempUserMessage]);

        response = await axiosInstance.post(
          `/api/v1/conversations/${currentConversationId}/messages`,
          {
            query: trimmedInput,
            recordIds: [recordId],
            conversationSourceRecordId: recordId,
          }
        );

        if (!response?.data?.conversation?.messages) {
          throw new Error('Invalid response format');
        }

        const botMessage = response.data.conversation.messages
          .filter((msg: any) => msg.messageType === 'bot_response')
          .map(formatMessage)
          .pop();

        if (botMessage) {
          setMessages((prev) => [...prev, botMessage]);
        }
      }

      const lastMessage =
        response.data.conversation.messages[response.data.conversation.messages.length - 1];
      if (lastMessage?.citations?.length > 0) {
        setExpandedCitations((prev) => ({
          ...prev,
          [messages.length]: false,
        }));
      }
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
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
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [
    inputValue,
    isLoading,
    currentConversationId,
    selectedRecord,
    messages,
    formatMessage,
    recordId,
  ]);

  const handleRegenerateMessage = useCallback(
    async (messageId: string) => {
      if (!currentConversationId || !messageId || !selectedRecord) return;

      try {
        setIsLoading(true);
        const response = await axiosInstance.post(
          `/api/v1/conversations/${currentConversationId}/message/${messageId}/regenerate`,
          {
            instruction: 'Improve writing style and clarity',
            context: {
              recordId: selectedRecord._id,
              recordName: selectedRecord.name || selectedRecord.title,
              recordType: selectedRecord.recordType,
              departments: selectedRecord.departments?.map((d) => d),
              source: 'record_details',
            },
          }
        );

        if (!response?.data?.conversation?.messages) {
          throw new Error('Invalid response format');
        }

        const allMessages = response.data.conversation.messages.map(formatMessage).filter(Boolean);
        const regeneratedMessage = allMessages.filter((msg: any) => msg.type === 'bot').pop();

        if (!regeneratedMessage) {
          throw new Error('No regenerated message found in response');
        }

        setMessages((prevMessages) =>
          prevMessages.map((msg) =>
            msg.id === messageId ? { ...regeneratedMessage, createdAt: msg.createdAt } : msg
          )
        );

        setExpandedCitations((prevStates) => {
          const newStates = { ...prevStates };
          const messageIndex = messages.findIndex((msg) => msg.id === messageId);
          if (messageIndex !== -1) {
            newStates[messageIndex] =
              regeneratedMessage.citations?.length > 0 ? prevStates[messageIndex] || false : false;
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
    [currentConversationId, formatMessage, messages, selectedRecord]
  );

  const handleFeedbackSubmit = useCallback(
    async (messageId: string, feedback: any) => {
      if (!currentConversationId || !messageId) return;

      try {
        await axiosInstance.post(
          `/api/v1/conversations/${currentConversationId}/message/${messageId}/feedback`,
          feedback
        );
      } catch (error) {
        console.error('Feedback submission error:', error);
        throw error;
      }
    },
    [currentConversationId]
  );
  const handleInputChange = useCallback((e: any) => {
    setInputValue(e.target.value);
  }, []);

  const onViewPdf = useCallback(
    (url: string, citationMeta: Metadata, citations: CustomCitation[]) => {
      setAggregatedCitations(citations);
      setPdfUrl(url);
      setOpenPdfView(true);
      setDrawerOpen(false);
    },
    []
  );

  const onClosePdf = useCallback(() => {
    setOpenPdfView(false);
  }, []);

  const toggleCitations = useCallback((index: number) => {
    setExpandedCitations((prev) => {
      const newState = { ...prev };
      newState[index] = !prev[index];
      return newState;
    });
  }, []);

  return (
    <Box
      sx={{
        display: 'flex',
        width: '100%',
        height: '100%',
        bgcolor: 'background.default',
      }}
    >
      {isDrawerOpen && (
        <RecordSidebar
          onClose={() => setDrawerOpen(false)}
          onRecordSelect={handleRecordSelect}
          selectedRecordId={currentConversationId}
          initialRecord={selectedRecord}
          recordType={initialContext?.recordType}
          shouldRefresh={shouldRefreshSidebar}
          onRefreshComplete={() => setShouldRefreshSidebar(false)}
          onNewChat={handleNewChat}
          recordId={recordId}
        />
      )}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: openPdfView ? '1fr 2fr' : '1fr',
          width: '100%',
          height: '100%',
          gap: 2,
          transition: 'all 0.3s ease',
        }}
      >
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            height: '100%',
            borderRight: openPdfView ? 1 : 0,
            borderColor: 'divider',
            bgcolor: 'background.paper',
            overflow: 'auto',
          }}
        >
          <RecordHeader
            record={selectedRecord}
            isConversation={Boolean(currentConversationId)}
            isDrawerOpen={isDrawerOpen}
            onDrawerToggle={() => setDrawerOpen(true)}
          />

          {/* <ChatMessagesArea
            messages={messages}
            isLoading={isLoading}
            expandedCitations={expandedCitations}
            onToggleCitations={toggleCitations}
            onRegenerateMessage={handleRegenerateMessage}
            onFeedbackSubmit={handleFeedbackSubmit}
            conversationId={currentConversationId}
            onViewPdf={onViewPdf}
            isLoadingConversation={isLoadingConversation}
          /> */}

          <Box
            sx={{
              p: 2,
              borderTop: 1,
              borderColor: 'divider',
              bgcolor: 'background.paper',
              mt: 'auto',
            }}
          >
            <ChatInput
              // value={inputValue}
              // onChange={handleInputChange}
              onSubmit={handleSendMessage}
              isLoading={isLoading}
              disabled={!selectedRecord}
              placeholder={
                !selectedRecord
                  ? 'Select a record to start chatting'
                  : currentConversationId
                    ? 'Continue the conversation...'
                    : 'Start a new conversation...'
              }
              selectedModel={selectedModel}
              selectedChatMode={selectedChatMode}
              onModelChange={setSelectedModel}
              onChatModeChange={setSelectedChatMode}
              apps={[]}
              knowledgeBases={[]}
              initialSelectedApps={[]}
              initialSelectedKbIds={[]}
            />
          </Box>
        </Box>

        {openPdfView && (
          <Box
            sx={{
              height: '100%',
              overflow: 'hidden',
              position: 'relative',
              bgcolor: 'background.default',
              borderLeft: 1,
              borderColor: 'divider',
              '& > div': {
                height: '100%',
                width: '100%',
              },
            }}
          >
            <PdfHighlighterComp pdfUrl={pdfUrl} citations={aggregatedCitations}  onClosePdf={onClosePdf}/>
            <StyledCloseButton
              onClick={onClosePdf}
              startIcon={<Icon icon={closeIcon} />}
              size="small"
            >
              Close Document
            </StyledCloseButton>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default RecordSalesAgent;
