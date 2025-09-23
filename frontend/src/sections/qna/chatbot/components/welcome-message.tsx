// WelcomeMessage.tsx - Using ChatInput component
import { Icon } from '@iconify/react';
import githubIcon from '@iconify-icons/mdi/github';
import React, { memo, useRef, useCallback } from 'react';

import {
  Box,
  Link,
  alpha,
  useTheme,
  Container,
  Typography,
} from '@mui/material';

import ChatInput, { Model, ChatMode } from './chat-input';

// Simple Footer component
const Footer = memo(({ isDark }: { isDark: boolean }) => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        mt: 'auto',
        pt: 2,
        pb: 3,
        width: '100%',
        marginTop: 6,
      }}
    >
      <Link
        href="https://github.com/pipeshub-ai/pipeshub-ai"
        target="_blank"
        underline="none"
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          fontSize: '0.7rem',
          color: isDark ? alpha('#fff', 0.5) : alpha('#000', 0.4),
          opacity: 0.85,
          transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
          padding: '6px 12px',
          borderRadius: '6px',
          '&:hover': {
            color: theme.palette.primary.main,
            opacity: 1,
            backgroundColor: isDark ? alpha('#fff', 0.03) : alpha('#000', 0.02),
            transform: 'translateY(-1px)',
          },
        }}
      >
        <Icon
          icon={githubIcon}
          style={{
            fontSize: '0.9rem',
            color: 'inherit',
          }}
        />
        pipeshub-ai
      </Link>
    </Box>
  );
});

Footer.displayName = 'Footer';

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
  const isDark = theme.palette.mode === 'dark';
  const isSubmittingRef = useRef(false);

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
          mb: 8,
          mt: { xs: -2, sm: -8 },
        }}
      >
        <Typography
          variant="h5"
          sx={{
            fontWeight: 600,
            color: theme.palette.text.primary,
            mb: 2,
            fontSize: { xs: '1.6rem', sm: '1.85rem' },
            letterSpacing: '-0.03em',
            background: isDark
              ? 'linear-gradient(90deg, #fff 0%, #e0e0e0 100%)'
              : 'linear-gradient(90deg, #222 0%, #555 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          PipesHub AI
        </Typography>

        <Typography
          variant="caption"
          sx={{
            fontWeight: 500,
            color: theme.palette.primary.main,
            letterSpacing: '0.02em',
            fontSize: '0.85rem',
            opacity: 0.92,
            background: isDark
              ? `linear-gradient(90deg, ${theme.palette.primary.main} 0%, ${alpha(theme.palette.primary.light, 0.8)} 100%)`
              : `linear-gradient(90deg, ${theme.palette.primary.main} 0%, ${theme.palette.primary.dark} 100%)`,
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          Workplace AI that understands your workplace inside out
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

      {/* Footer */}
      <Footer isDark={isDark} />
    </Container>
  );
};

// Memoize the component
const WelcomeMessage = React.memo(WelcomeMessageComponent);
WelcomeMessage.displayName = 'WelcomeMessage';

export default WelcomeMessage;
