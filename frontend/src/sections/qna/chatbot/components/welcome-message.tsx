// WelcomeMessage.tsx - Using ChatInput component
import React, { useRef, useCallback } from 'react';

import useMediaQuery from '@mui/material/useMediaQuery';
import { Box, useTheme, Container, Typography } from '@mui/material';

import { useAuthContext } from 'src/auth/hooks';
import { SiriOrb } from 'src/components/siri-orb';
import ChatInput, { Model, ChatMode } from './chat-input';

interface WelcomeMessageProps {
  onSubmit: (
    message: string,
    modelProvider?: string,
    modelName?: string,
    chatMode?: string,
    filters?: { apps: string[]; kb: string[] }
  ) => Promise<void>;
  isLoading?: boolean;
  selectedModel: Model | null;
  selectedChatMode: ChatMode | null;
  onModelChange: (model: Model) => void;
  onChatModeChange: (mode: ChatMode) => void;
  apps: Array<{ id: string; name: string; iconPath?: string }>;
  knowledgeBases: Array<{ id: string; name: string }>;
  initialSelectedApps?: string[];
  initialSelectedKbIds?: string[];
  onFiltersChange?: (filters: { apps: string[]; kb: string[] }) => void;
}

// Main WelcomeMessage component
const WelcomeMessageComponent = ({ 
  onSubmit, 
  isLoading = false, 
  selectedModel, 
  selectedChatMode, 
  onModelChange, 
  onChatModeChange,
  apps,
  knowledgeBases,
  initialSelectedApps = [],
  initialSelectedKbIds = [],
  onFiltersChange,
}: WelcomeMessageProps) => {
  const theme = useTheme();
  const isSmUp = useMediaQuery(theme.breakpoints.up('sm'));
  const { user } = useAuthContext();
  const isSubmittingRef = useRef(false);
  const displayName =
    (user?.fullName && user.fullName.trim()) ||
    (user?.firstName && user.firstName.trim()) ||
    (user?.email && user.email.trim()) ||
    'there';

  // Direct submission handler that stores message text in a ref
  const handleDirectSubmit = useCallback(
    async (
      text: string,
      modelKey?: string,
      modelName?: string,
      chatMode?: string,
      filters?: { apps: string[]; kb: string[] }
    ) => {
      if (isSubmittingRef.current) return;

      isSubmittingRef.current = true;
      try {
        await onSubmit(text, modelKey, modelName, chatMode, filters);
      } catch (error) {
        console.error('Error during message submission:', error);
        // Potentially handle error display to the user here
      } finally {
        isSubmittingRef.current = false;
      }
    },
    [onSubmit]
  );

  return (
    <Container
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        maxWidth: '960px',
        padding: { xs: '16px', sm: '24px' },
        position: 'relative',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          textAlign: 'center',
          gap: 2.5,
          mb: 8,
          mt: { xs: -2, sm: -6 },
        }}
      >
        <SiriOrb
          size={isSmUp ? 128 : 96}
          animationDuration={18}
          sx={{
            filter: 'drop-shadow(0 24px 60px rgba(0,0,0,0.18))',
          }}
        />

        <Typography
          variant="h4"
          sx={{
            fontWeight: 500,
            color: theme.palette.text.primary,
            letterSpacing: '-0.02em',
            mt: 0,
            lineHeight: 1.1,
            textTransform: 'capitalize',
          }}
        >
          Welcome Back, {displayName}
          <br />
          Tell Agent Which Relationship Intel You Need Now
        </Typography>
      </Box>

      {/* ChatInput Component */}
      <ChatInput
        onSubmit={handleDirectSubmit}
        isLoading={isLoading || isSubmittingRef.current}
        disabled={isLoading || isSubmittingRef.current}
        placeholder="Ask anything..."
        selectedModel={selectedModel}
        selectedChatMode={selectedChatMode}
        onModelChange={onModelChange}
        onChatModeChange={onChatModeChange}
        apps={apps}
        knowledgeBases={knowledgeBases}
        initialSelectedApps={initialSelectedApps}
        initialSelectedKbIds={initialSelectedKbIds}
        onFiltersChange={onFiltersChange}
      />

    </Container>
  );
};

// Memoize the component
const WelcomeMessage = React.memo(WelcomeMessageComponent);
WelcomeMessage.displayName = 'WelcomeMessage';

export default WelcomeMessage;
