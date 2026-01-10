// src/sections/qna/agents/components/notification-panel.tsx
import React from 'react';
import { Alert, Fade, Link, Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useAccountType } from 'src/hooks/use-account-type';
import type { AgentBuilderNotificationPanelProps, AgentBuilderError } from '../../types/agent';

const AgentBuilderNotificationPanel: React.FC<AgentBuilderNotificationPanelProps> = ({
  error,
  success,
  onErrorClose,
  onSuccessClose,
}) => {
  const navigate = useNavigate();
  const { isBusiness } = useAccountType();

  const handleConnectorLinkClick = (connectorId: string) => {
    const basePath = isBusiness 
      ? '/account/company-settings/settings/connector' 
      : '/account/individual/settings/connector';
    navigate(`${basePath}/${connectorId}`);
    onErrorClose();
  };

  // Normalize error to AgentBuilderError format
  const errorData: AgentBuilderError | null = error
    ? typeof error === 'string'
      ? { message: error }
      : error
    : null;

  return (
    <>
      {/* Error Notification */}
      {errorData && (
        <Fade in>
          <Alert
            severity="error"
            onClose={onErrorClose}
            sx={{
              position: 'fixed',
              top: 24,
              right: 24,
              zIndex: 2000,
              borderRadius: 1.5,
              maxWidth: 500,
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
            }}
          >
            <Box>
              <Typography variant="body2" sx={{ mb: errorData.actionLink ? 1 : 0 }}>
                {errorData.message}
              </Typography>
              {errorData.actionLink && errorData.connectorId && (
                <Link
                  component="button"
                  variant="body2"
                  onClick={() => handleConnectorLinkClick(errorData.connectorId!)}
                  sx={{
                    fontWeight: 600,
                    textDecoration: 'underline',
                    cursor: 'pointer',
                    '&:hover': {
                      textDecoration: 'underline',
                    },
                  }}
                >
                  {errorData.actionLink}
                </Link>
              )}
            </Box>
          </Alert>
        </Fade>
      )}

      {/* Success Notification */}
      {success && (
        <Fade in>
          <Alert
            severity="success"
            onClose={onSuccessClose}
            sx={{
              position: 'fixed',
              top: 24,
              right: 24,
              zIndex: 2000,
              borderRadius: 1.5,
              maxWidth: 400,
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
            }}
          >
            {success}
          </Alert>
        </Fade>
      )}
    </>
  );
};

export default AgentBuilderNotificationPanel;
