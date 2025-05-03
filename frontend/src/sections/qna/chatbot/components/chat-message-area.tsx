import type {
  Metadata,
  CustomCitation,
  FormattedMessage,
  ExpandedCitationsState,
} from 'src/types/chat-bot';

import React, { useMemo, useState, useEffect, useCallback } from 'react';

import { Box, Fade, Stack, Typography, CircularProgress } from '@mui/material';

import ChatMessage from './chat-message';
import WelcomeMessage from './welcome-message';

type ChatMessagesAreaProps = {
  messages: FormattedMessage[];
  isLoading: boolean;
  expandedCitations: ExpandedCitationsState;
  onToggleCitations: (index: number) => void;
  onRegenerateMessage: (messageId: string) => Promise<void>;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  isLoadingConversation: boolean;
  onViewPdf: (
    url: string,
    citationMeta: Metadata,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
};

type ProcessingIndicatorProps = {
  isLoadingConversation: boolean;
};

type MessageWithControlsProps = {
  message: FormattedMessage;
  index: number;
  isExpanded: boolean;
  onToggleCitations: (index: number) => void;
  onViewPdf: (
    url: string,
    citationMeta: Metadata,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => void;
  onFeedbackSubmit: (messageId: string, feedback: any) => Promise<void>;
  conversationId: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  showRegenerate: boolean;
};

// Loading states for different scenarios
const ProcessingIndicator = ({ isLoadingConversation }: ProcessingIndicatorProps) => (
  <Fade in={Boolean(true)}>
    <Stack
      direction="row"
      spacing={2}
      alignItems="center"
      sx={{
        py: 2,
        px: 3,
        borderRadius: 2,
        bgcolor: 'background.paper',
        boxShadow: '0 2px 12px rgba(0, 0, 0, 0.03)',
        border: '1px solid',
        borderColor: 'divider',
        maxWidth: '300px',
      }}
    >
      <Box sx={{ position: 'relative', width: 20, height: 20 }}>
        <CircularProgress size={20} thickness={4} sx={{ color: 'primary.main' }} />
      </Box>
      <Typography
        variant="body2"
        sx={{
          color: 'text.secondary',
          fontSize: '0.875rem',
          fontWeight: 500,
          display: 'flex',
          alignItems: 'center',
        }}
      >
        {isLoadingConversation ? 'Loading Conversation...' : 'Thinking...'}
      </Typography>
    </Stack>
  </Fade>
);

const ChatMessagesArea = ({
  messages,
  isLoading,
  expandedCitations,
  onToggleCitations,
  onRegenerateMessage,
  onFeedbackSubmit,
  conversationId,
  isLoadingConversation,
  onViewPdf,
}: ChatMessagesAreaProps) => {
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);

  // Remove sorting and use messages directly to maintain order
  const displayMessages = useMemo(() => messages, [messages]);

  // Find the last bot message for regeneration
  const canRegenerateMessage = useCallback(
    (message: FormattedMessage) => {
      const botMessages = messages.filter((msg) => msg.type === 'bot');
      const lastBotMessage = botMessages[botMessages.length - 1];
      return (
       ( message.type === 'bot' &&  message.messageType !== 'error') &&
        message.id === lastBotMessage?.id &&
        !message.id.startsWith('error-')
      );
    },
    [messages]
  );

  const scrollToBottom = useCallback(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const shouldShowLoadingIndicator = (isLoading || isLoadingConversation) && messages.length > 0;


  return (
    <Box
      sx={{
        flexGrow: 1,
        overflow: 'auto',
        p: 3,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        // ...scrollableContainerStyle,
      }}
    >
      {isLoadingConversation && conversationId && messages.length === 0 ? (
        // Centered loading indicator
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '100%',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          <ProcessingIndicator isLoadingConversation={isLoadingConversation} />
        </Box>
      ) : (
        <Box
          sx={{
            flexGrow: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-start',
          }}
        >
          {messages.length === 0  ? (
            <WelcomeMessage />
          ) : (
            <>
              {displayMessages.map((message, index) => (
                <MessageWithControls
                  key={`${message.id}-${message.type}`}
                  message={message}
                  index={index}
                  isExpanded={expandedCitations[index]}
                  onToggleCitations={() => onToggleCitations(index)}
                  onRegenerate={onRegenerateMessage}
                  onFeedbackSubmit={onFeedbackSubmit}
                  conversationId={conversationId}
                  showRegenerate={canRegenerateMessage(message)}
                  onViewPdf={onViewPdf}
                />
              ))}
               {shouldShowLoadingIndicator && (
                <Box sx={{ mt: 2, display: 'flex', justifyContent: 'flex-start' }}>
                  <ProcessingIndicator isLoadingConversation={false} />
                </Box>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </Box>
      )}
    </Box>
  );
};

const MessageWithControls = React.memo(
  ({
    message,
    index,
    isExpanded,
    onToggleCitations,
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
      <Box sx={{ position: 'relative', mb: 2, width: '100%' }}>
        <ChatMessage
          message={message}
          index={index}
          isExpanded={isExpanded}
          onToggleCitations={onToggleCitations}
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
