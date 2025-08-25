// src/sections/qna/agents/components/notification-panel.tsx
import React from 'react';
import { Alert, Fade } from '@mui/material';
import type { AgentBuilderNotificationPanelProps } from '../../types/agent';

const AgentBuilderNotificationPanel: React.FC<AgentBuilderNotificationPanelProps> = ({
  error,
  success,
  onErrorClose,
  onSuccessClose,
}) => (
  <>
    {/* Error Notification */}
    {error && (
      <Fade in>
        <Alert
          severity="error"
          onClose={onErrorClose}
          sx={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            zIndex: 2000,
            borderRadius: 1.5,
            maxWidth: 400,
          }}
        >
          {error}
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
            bottom: 24,
            right: 24,
            zIndex: 2000,
            borderRadius: 1.5,
            maxWidth: 400,
          }}
        >
          {success}
        </Alert>
      </Fade>
    )}
  </>
);

export default AgentBuilderNotificationPanel;
