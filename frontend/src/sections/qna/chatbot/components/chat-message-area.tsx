import type { CustomCitation, FormattedMessage } from 'src/types/chat-bot';

import { Icon } from '@iconify/react';
import arrowUpIcon from '@iconify-icons/mdi/arrow-up';
import arrowDownIcon from '@iconify-icons/mdi/arrow-down';
import React, { useMemo, useState, useEffect, useCallback } from 'react';

import { Box, Fade, Stack, alpha, Tooltip, useTheme, Typography, IconButton } from '@mui/material';

import ChatMessage from './chat-message';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

type ChatMessagesAreaProps = {
  messages: FormattedMessage[];
  isLoading: boolean;
  onRegenerateMessage: (messageId: string) => Promise<void>;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  isLoadingConversation: boolean;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  currentStatus?: string;
  isStatusVisible?: boolean;
  isCreatingConversation?: boolean;
  isNavigatingToConversation?: boolean;
};

type ProcessingIndicatorProps = {
  displayText: string;
  isCreating?: boolean;
};

type MessageWithControlsProps = {
  message: FormattedMessage;
  index: number;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  showRegenerate: boolean;
};

const ProcessingIndicator = React.memo(
  ({ displayText, isCreating = false }: ProcessingIndicatorProps) => {
    const theme = useTheme();

    const indicatorColor = isCreating ? theme.palette.primary.main : theme.palette.text.secondary;
    const backgroundColor = isCreating
      ? alpha(theme.palette.primary.main, 0.1)
      : theme.palette.mode === 'dark'
        ? alpha(theme.palette.background.paper, 0.7)
        : alpha(theme.palette.background.default, 0.9);

    return (
      <Fade in timeout={200}>
        <Box sx={{ display: 'flex', justifyContent: 'flex-start', mb: 2 }}>
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{
              py: 1,
              px: 2,
              borderRadius: '12px',
              bgcolor: backgroundColor,
              backdropFilter: 'blur(4px)',
              boxShadow: theme.shadows[2],
              border: '1px solid',
              borderColor: isCreating
                ? alpha(theme.palette.primary.main, 0.3)
                : theme.palette.divider,
              transition: 'all 0.3s ease',
            }}
          >
            <Box sx={{ minWidth: 20, height: 20, display: 'flex', alignItems: 'center' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                {[0, 1, 2].map((i) => (
                  <Box
                    key={i}
                    sx={{
                      width: 5,
                      height: 5,
                      borderRadius: '50%',
                      bgcolor: indicatorColor,
                      animation: `bounce 1.4s ease-in-out ${i * 0.16}s infinite`,
                      '@keyframes bounce': {
                        '0%, 80%, 100%': { transform: 'scale(0.8)', opacity: 0.5 },
                        '40%': { transform: 'scale(1)', opacity: 1 },
                      },
                    }}
                  />
                ))}
              </Box>
            </Box>
            <Typography
              variant="body2"
              sx={{
                fontSize: '0.8rem',
                fontWeight: isCreating ? 600 : 500,
                color: indicatorColor,
                transition: 'color 0.3s ease',
              }}
            >
              {displayText}
            </Typography>
          </Stack>
        </Box>
      </Fade>
    );
  }
);

const ChatMessagesArea = ({
  messages,
  isLoading,
  onRegenerateMessage,
  onFeedbackSubmit,
  conversationId,
  isLoadingConversation,
  onViewPdf,
  currentStatus,
  isStatusVisible,
  isCreatingConversation = false,
  isNavigatingToConversation = false,
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const prevMessagesLength = React.useRef(messages.length);
  const scrollTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);
  const prevConversationId = React.useRef<string | null>(conversationId);
  const [showScrollToTop, setShowScrollToTop] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const theme = useTheme();

  const displayMessages = useMemo(() => messages, [messages]);
  const hasStreamingContent = useMemo(
    () => displayMessages[displayMessages.length - 1]?.id.startsWith('streaming-'),
    [displayMessages]
  );

  const canRegenerateMessage = useCallback(
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return (
        message.type === 'bot' &&
        message.id === lastBotMessage?.id &&
        !message.id.startsWith('streaming-') &&
        !isLoading &&
        !isCreatingConversation &&
        !message.id.startsWith('start-message')
      );
    },
    [messages, isLoading, isCreatingConversation]
  );

  const scrollToBottomSmooth = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, []);

  const scrollToBottomImmediate = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, []);

  const scrollToTop = useCallback(() => {
    messagesContainerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, []);

  const handleScroll = useCallback(() => {
    if (!messagesContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    setShouldAutoScroll(isNearBottom);
    setShowScrollToTop(scrollTop > 200);
    setShowScrollToBottom(!isNearBottom && scrollHeight > clientHeight + 400);
  }, []);

  useEffect(() => {
    if (conversationId !== prevConversationId.current) {
      setShouldAutoScroll(true);
      prevConversationId.current = conversationId;
      if (displayMessages.length > 0) setTimeout(scrollToBottomImmediate, 100);
    }
  }, [conversationId, displayMessages.length, scrollToBottomImmediate]);

  useEffect(() => {
    if (messages.length > prevMessagesLength.current && shouldAutoScroll) {
      setTimeout(scrollToBottomImmediate, 50);
    }
    prevMessagesLength.current = messages.length;
  }, [messages.length, shouldAutoScroll, scrollToBottomImmediate]);

  useEffect(() => {
    if ((hasStreamingContent || isLoading) && shouldAutoScroll) {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      scrollTimeoutRef.current = setTimeout(scrollToBottomSmooth, 100);
    }
    return () => {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    };
  }, [hasStreamingContent, isLoading, shouldAutoScroll, scrollToBottomSmooth, messages]);

  const shouldShowLoadingIndicator = useMemo(() => {
    // Always show status if we have a specific status message
    if (isStatusVisible && currentStatus) return true;

    // Don't show generic loading if we have streaming content
    if (hasStreamingContent) return false;

    // Show loading when initially loading a conversation with no messages
    if (isLoadingConversation && messages.length === 0) return true;

    // Show loading when creating a new conversation
    if (isCreatingConversation) return true;

    // Show loading when navigating to a conversation
    if (isNavigatingToConversation) return true;

    // Show generic loading when processing but no streaming content
    if (isLoading && !hasStreamingContent) return true;

    return false;
  }, [
    isStatusVisible,
    currentStatus,
    hasStreamingContent,
    isLoadingConversation,
    messages.length,
    isCreatingConversation,
    isNavigatingToConversation,
    isLoading,
  ]);

  const indicatorText = useMemo(() => {
    if (isStatusVisible && currentStatus) return currentStatus;
    if (isCreatingConversation) return '🚀 Creating your conversation...';
    if (isNavigatingToConversation) return '🔄 Loading conversation...';
    if (isLoadingConversation && messages.length === 0) return 'Loading conversation...';
    if (isLoading) return 'Processing...';
    return '';
  }, [
    isStatusVisible,
    currentStatus,
    isCreatingConversation,
    isNavigatingToConversation,
    isLoadingConversation,
    messages.length,
    isLoading,
  ]);

  const isCreationLoading = useMemo(
    () => isCreatingConversation || (isStatusVisible && currentStatus?.includes('Creating')),
    [isCreatingConversation, isStatusVisible, currentStatus]
  );

  const scrollableStyles = createScrollableContainerStyle(theme);

  return (
    <Box
      sx={{
        position: 'relative',
        flexGrow: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <Box
        ref={messagesContainerRef}
        onScroll={handleScroll}
        sx={{ flexGrow: 1, overflow: 'auto', p: 3, ...scrollableStyles }}
      >
        <Box sx={{ flexGrow: 1 }}>
          {displayMessages.map((message, index) => (
            <MessageWithControls
              key={message.id}
              message={message}
              index={index}
              onRegenerate={onRegenerateMessage}
              onFeedbackSubmit={onFeedbackSubmit}
              conversationId={conversationId}
              showRegenerate={canRegenerateMessage(message)}
              onViewPdf={onViewPdf}
            />
          ))}
          {shouldShowLoadingIndicator && indicatorText && (
            <Box sx={{ mt: 1, mb: 4 }}>
              <ProcessingIndicator displayText={indicatorText} isCreating={isCreationLoading} />
            </Box>
          )}
          <div ref={messagesEndRef} style={{ height: 1 }} />
        </Box>
      </Box>

      <Fade in={showScrollToTop}>
        <Tooltip title="Scroll to top" placement="left">
          <IconButton
            onClick={scrollToTop}
            sx={{
              position: 'absolute',
              top: 20,
              right: 20,
              zIndex: 10,
              bgcolor: 'background.paper',
              boxShadow: 4,
              '&:hover': { bgcolor: 'background.paper' },
            }}
          >
            <Icon icon={arrowUpIcon} />
          </IconButton>
        </Tooltip>
      </Fade>

      <Fade in={showScrollToBottom}>
        <Tooltip title="Scroll to bottom" placement="left">
          <IconButton
            onClick={scrollToBottom}
            sx={{
              position: 'absolute',
              bottom: 20,
              right: 20,
              zIndex: 10,
              bgcolor: 'background.paper',
              boxShadow: 4,
              '&:hover': { bgcolor: 'background.paper' },
            }}
          >
            <Icon icon={arrowDownIcon} />
          </IconButton>
        </Tooltip>
      </Fade>
    </Box>
  );
};

const MessageWithControls = React.memo(
  ({
    message,
    index,
    onRegenerate,
    onFeedbackSubmit,
    conversationId,
    showRegenerate,
    onViewPdf,
  }: MessageWithControlsProps) => {
    const [isRegenerating, setIsRegenerating] = useState(false);

    const handleRegenerate = async (messageId: string): Promise<void> => {
      setIsRegenerating(true);
      try {
        await onRegenerate(messageId);
      } finally {
        setIsRegenerating(false);
      }
    };

    return (
      <Box sx={{ mb: 2 }}>
        <ChatMessage
          message={message}
          index={index}
          onRegenerate={handleRegenerate}
          onFeedbackSubmit={onFeedbackSubmit}
          conversationId={conversationId}
          isRegenerating={isRegenerating}
          showRegenerate={showRegenerate}
          onViewPdf={onViewPdf}
        />
      </Box>
    );
  },

  (prevProps, nextProps) =>
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.message.updatedAt?.getTime() === nextProps.message.updatedAt?.getTime() &&
    prevProps.showRegenerate === nextProps.showRegenerate &&
    prevProps.conversationId === nextProps.conversationId &&
    prevProps.index === nextProps.index
);

export default ChatMessagesArea;
