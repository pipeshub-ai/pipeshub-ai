import React from 'react';
import { Icon } from '@iconify/react';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';

import {
  Box,
  Fade,
  alpha,
  Dialog,
  Button,
  useTheme,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

interface DeleteConversationDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  title?: string;
  isDeleting: boolean;
}

const DeleteConversationDialog = ({
  open,
  onClose,
  onConfirm,
  title,
  isDeleting,
}: DeleteConversationDialogProps) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Dialog
      open={open}
      onClose={!isDeleting ? onClose : undefined}
      maxWidth="xs"
      fullWidth
      TransitionComponent={Fade}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        elevation: 6,
        sx: {
          borderRadius: 2,
          overflow: 'hidden',
          bgcolor: theme.palette.background.paper,
          boxShadow: isDark
            ? `0 12px 28px 0 ${alpha(theme.palette.common.black, 0.6)}, 0 0 0 1px ${alpha(theme.palette.divider, 0.1)}`
            : `0 10px 32px -4px ${alpha(theme.palette.common.black, 0.12)}, 0 4px 16px 0 ${alpha(theme.palette.common.black, 0.08)}`,
        },
      }}
    >
      <DialogTitle
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: `1px solid ${alpha(theme.palette.divider, isDark ? 0.1 : 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Icon icon={alertCircleIcon} width={20} height={20} color={theme.palette.error.main} />
          <Typography
            variant="subtitle1"
            sx={{
              fontWeight: 600,
              color: theme.palette.text.primary,
              fontSize: '0.9375rem',
            }}
          >
            Delete Conversation
          </Typography>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ px: 2.5, pt: 2, pb: 2.5 }}>
        <Typography
          variant="body2"
          sx={{
            mb: 2,
            color: theme.palette.text.secondary,
            fontSize: '0.875rem',
          }}
        >
          Are you sure you want to delete this conversation? This action cannot be undone.
        </Typography>

        <Box
          sx={{
            px: 1.5,
            py: 1.5,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.action.selected, isDark ? 0.2 : 0.08),
            borderLeft: `2px solid ${theme.palette.error.main}`,
          }}
        >
          <Typography
            variant="body2"
            sx={{
              color: theme.palette.text.primary,
              fontSize: '0.8125rem',
              fontWeight: 500,
            }}
          >
            {title || 'Untitled Conversation'}
          </Typography>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 2.5,
          py: 2,
          bgcolor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.default, 0.9)
              : alpha(theme.palette.background.default, 0.5),
          borderTop: `1px solid ${alpha(theme.palette.divider, isDark ? 0.1 : 0.08)}`,
          gap: 1,
        }}
      >
        <Button
          onClick={onClose}
          disabled={isDeleting}
          variant="outlined"
          color="inherit"
          sx={{
            fontSize: '0.8125rem',
            fontWeight: 500,
            textTransform: 'none',
            borderColor: alpha(theme.palette.divider, isDark ? 0.5 : 0.8),
            color: theme.palette.text.secondary,
            '&:hover': {
              borderColor: theme.palette.divider,
              bgcolor: alpha(theme.palette.action.hover, 0.05),
            },
          }}
        >
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          color="error"
          variant="contained"
          disableElevation
          disabled={isDeleting}
          startIcon={
            isDeleting ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={deleteIcon} width={18} height={18} />
            )
          }
          sx={{
            borderRadius: 1,
            px: 2,
            py: 0.75,
            fontSize: '0.8125rem',
            fontWeight: 500,
            textTransform: 'none',
            '&:hover': {
              boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.error.main, 0.3)}` : 'none',
            },
          }}
        >
          {isDeleting ? 'Deleting...' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DeleteConversationDialog;
