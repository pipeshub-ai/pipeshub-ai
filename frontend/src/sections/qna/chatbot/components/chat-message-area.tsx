import type { CustomCitation, FormattedMessage } from 'src/types/chat-bot';

import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { Box, Fade, Stack, Typography, useTheme, alpha, IconButton, Tooltip } from '@mui/material';
import { Icon } from '@iconify/react';
import arrowUpIcon from '@iconify-icons/mdi/arrow-up';
import arrowDownIcon from '@iconify-icons/mdi/arrow-down';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

import ChatMessage from './chat-message';

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
};

type ProcessingIndicatorProps = {
  displayText: string;
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

const ProcessingIndicator = React.memo(({ displayText }: ProcessingIndicatorProps) => {
  const theme = useTheme();

  const renderAnimation = () => {
    if (!displayText) return 'thinking';
    if (displayText.includes('🔍') || displayText.toLowerCase().includes('search')) {
      return 'searching';
    }
    return 'processing';
  };

  const getAnimationType = () => {
    if (!displayText) return 'thinking';
    if (displayText.includes('🔍') || displayText.toLowerCase().includes('search')) {
      return 'searching';
    }
    return 'processing';
  };

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
            bgcolor:
              theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.7)
                : alpha(theme.palette.background.default, 0.9),
            backdropFilter: 'blur(4px)',
            boxShadow: theme.shadows[2],
            border: '1px solid',
            borderColor: theme.palette.divider,
          }}
        >
          <Box sx={{ minWidth: 20, height: 20, display: 'flex', alignItems: 'center' }}>
            {/* This is a simplified animation part */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              {[0, 1, 2].map((i) => (
                <Box
                  key={i}
                  sx={{
                    width: 5,
                    height: 5,
                    borderRadius: '50%',
                    bgcolor: 'text.secondary',
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
            sx={{ fontSize: '0.8rem', fontWeight: 500, color: 'text.secondary' }}
          >
            {displayText}
          </Typography>
        </Stack>
      </Box>
    </Fade>
  );
});

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
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = React.useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const prevMessagesLength = React.useRef(messages.length);
  const scrollTimeoutRef = React.useRef<NodeJS.Timeout | null>(null);

  // NEW: Scroll button states
  const [showScrollToTop, setShowScrollToTop] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const displayMessages = useMemo(() => messages, [messages]);

  const hasStreamingContent = useMemo(() => {
    if (displayMessages.length === 0) return false;
    const lastMessage = displayMessages[displayMessages.length - 1];
    return lastMessage?.type === 'bot' && lastMessage?.id?.startsWith('streaming-');
  }, [displayMessages]);

  const canRegenerateMessage = useCallback(
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return (
        message.type === 'bot' &&
        message.id === lastBotMessage?.id &&
        !message.id.startsWith('streaming-')
      );
    },
    [messages]
  );

  const scrollToBottomSmooth = useCallback(() => {
    if (!messagesEndRef.current || !shouldAutoScroll) return;
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  }, [shouldAutoScroll]);

  const scrollToBottomImmediate = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, []);

  // NEW: Scroll to top function
  const scrollToTop = useCallback(() => {
    if (!messagesContainerRef.current) return;
    messagesContainerRef.current.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  // NEW: Scroll to bottom function (manual)
  const scrollToBottom = useCallback(() => {
    if (!messagesEndRef.current) return;
    messagesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, []);

  const handleScroll = useCallback(() => {
    if (!messagesContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    const isNearTop = scrollTop < 150;

    setShouldAutoScroll(isNearBottom);

    // NEW: Show/hide scroll buttons based on position
    setShowScrollToTop(scrollTop > 200);
    setShowScrollToBottom(!isNearBottom && scrollHeight > clientHeight + 400);
  }, []);

  useEffect(() => {
    if (messages.length > prevMessagesLength.current) {
      const latestMessage = messages[messages.length - 1];
      if (latestMessage?.type === 'user' && shouldAutoScroll) {
        setTimeout(scrollToBottomImmediate, 50);
      }
    }
    prevMessagesLength.current = messages.length;
  }, [messages, shouldAutoScroll, scrollToBottomImmediate]);

  useEffect(() => {
    if (hasStreamingContent && shouldAutoScroll) {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      scrollTimeoutRef.current = setTimeout(scrollToBottomSmooth, 100);
      return () => {
        if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
      };
    }
    return undefined;
  }, [hasStreamingContent, shouldAutoScroll, scrollToBottomSmooth]);

  useEffect(() => {
    if (conversationId) {
      setShouldAutoScroll(true);
      if (displayMessages.length > 0) setTimeout(scrollToBottomImmediate, 100);
    }
  }, [conversationId, displayMessages.length, scrollToBottomImmediate]);

  const shouldShowLoadingIndicator = useMemo(() => {
    if (hasStreamingContent) return false;
    if (isLoadingConversation && messages.length === 0) return true;
    if (isStatusVisible && currentStatus) return true;

    // NEW: Check if last message is from user (waiting for bot response)
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      if (lastMessage?.type === 'user' && !hasStreamingContent) {
        return true;
      }
    }

    return false;
  }, [
    isLoadingConversation,
    currentStatus,
    isStatusVisible,
    hasStreamingContent,
    messages,
  ]);

  const indicatorText = useMemo(() => {
    if (isLoadingConversation && messages.length === 0) return 'Loading conversation...';
    if (currentStatus) return currentStatus;

    // NEW: Show processing message when last message is from user
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      if (lastMessage?.type === 'user' && !isLoadingConversation) {
        return 'Processing your request...';
      }
    }

    return '';
  }, [isLoadingConversation, currentStatus, messages]);

  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  useEffect(
    () => () => {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    },
    []
  );

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
        sx={{
          flexGrow: 1,
          overflow: 'auto',
          p: 3,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          ...scrollableStyles,
        }}
      >
        <Box
          sx={{
            flexGrow: 1,
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            position: 'relative',
          }}
        >
          <Box sx={{ minHeight: 4 }} />
          {displayMessages.map((message, index) => (
            <MessageWithControls
              key={`msg-${message.id}`}
              message={message}
              index={index}
              onRegenerate={onRegenerateMessage}
              onFeedbackSubmit={onFeedbackSubmit}
              conversationId={conversationId}
              showRegenerate={canRegenerateMessage(message)}
              onViewPdf={onViewPdf}
            />
          ))}
          {/* FIX: Render the indicator in the flow with the correct text */}
          {shouldShowLoadingIndicator && (
            <Box sx={{ mt: 1, mb: 4 }}>
              <ProcessingIndicator displayText={indicatorText} />
            </Box>
          )}
          <Box sx={{ minHeight: 20 }} />
          <div
            ref={messagesEndRef}
            style={{ float: 'left', clear: 'both', height: 1, width: '100%' }}
          />
        </Box>
      </Box>

      {/* NEW: Scroll to Top Button */}
      <Fade in={showScrollToTop} timeout={200}>
        <Box
          sx={{
            position: 'absolute',
            top: 20,
            right: 20,
            zIndex: 10,
          }}
        >
          <Tooltip title="Scroll to top" placement="left">
            <IconButton
              onClick={scrollToTop}
              sx={{
                backgroundColor: (themeVal) => alpha(themeVal.palette.background.paper, 0.9),
                border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                backdropFilter: 'blur(8px)',
                boxShadow: (themeVal) => themeVal.shadows[4],
                width: 36,
                height: 36,
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.background.paper, 1),
                  transform: 'translateY(-2px)',
                  boxShadow: (themeVal) => themeVal.shadows[8],
                },
                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
              }}
            >
              <Icon
                icon={arrowUpIcon}
                width={16}
                height={16}
                color={theme.palette.text.secondary}
              />
            </IconButton>
          </Tooltip>
        </Box>
      </Fade>

      {/* NEW: Scroll to Bottom Button */}
      <Fade in={showScrollToBottom} timeout={200}>
        <Box
          sx={{
            position: 'absolute',
            bottom: 20,
            right: 20,
            zIndex: 10,
          }}
        >
          <Tooltip title="Scroll to bottom" placement="left">
            <IconButton
              onClick={scrollToBottom}
              sx={{
                backgroundColor: (themeVal) => alpha(themeVal.palette.background.paper, 0.9),
                border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                backdropFilter: 'blur(8px)',
                boxShadow: (themeVal) => themeVal.shadows[4],
                width: 36,
                height: 36,
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.background.paper, 1),
                  transform: 'translateY(-2px)',
                  boxShadow: (themeVal) => themeVal.shadows[8],
                },
                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
              }}
            >
              <Icon
                icon={arrowDownIcon}
                width={16}
                height={16}
                color={theme.palette.text.secondary}
              />
            </IconButton>
          </Tooltip>
        </Box>
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
  }
);

export default ChatMessagesArea;
