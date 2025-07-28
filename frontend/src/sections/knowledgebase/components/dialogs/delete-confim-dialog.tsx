// components/dialogs/DeleteConfirmDialog.tsx
import React from 'react';
import closeIcon from '@iconify-icons/eva/close-outline';
import warningIcon from '@iconify-icons/eva/alert-triangle-outline';

import {
  Box,
  alpha,
  Dialog,
  Button,
  useTheme,
  Typography,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface DeleteConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  title: string;
  message: string;
  loading?: boolean;
}

export const DeleteConfirmDialog: React.FC<DeleteConfirmDialogProps> = ({
  open,
  onClose,
  onConfirm,
  title,
  message,
  loading = false,
}) => {
  const theme = useTheme();

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 1,
          boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
          overflow: 'hidden',
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 2.5,
          pl: 3,
          color: theme.palette.text.primary,
          borderBottom: '1px solid',
          borderColor: theme.palette.divider,
          fontWeight: 500,
          fontSize: '1rem',
          m: 0,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              borderRadius: '6px',
              bgcolor: alpha(theme.palette.error.main, 0.1),
              color: theme.palette.error.main,
            }}
          >
            <Iconify icon={warningIcon} width={18} height={18} />
          </Box>
          {title}
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{ color: theme.palette.text.secondary }}
          aria-label="close"
          disabled={loading}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent
        sx={{
          p: 0,
          '&.MuiDialogContent-root': {
            pt: 3,
            px: 3,
            pb: 0,
          },
        }}
      >
        <Box sx={{ mb: 3 }}>
          <Typography 
            variant="body1" 
            color="text.primary"
            sx={{ 
              lineHeight: 1.6,
              '& strong': {
                fontWeight: 600,
                color: theme.palette.text.primary,
              }
            }}
            dangerouslySetInnerHTML={{ __html: message }} 
          />
          
          <Box
            sx={{
              mt: 2,
              p: 2,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.error.main, 0.08),
              border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
            }}
          >
            <Typography variant="body2" color="error.main" sx={{ fontWeight: 500 }}>
              ⚠️ This action cannot be undone
            </Typography>
          </Box>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          p: 2.5,
          borderTop: '1px solid',
          borderColor: theme.palette.divider,
          bgcolor: alpha(theme.palette.background.default, 0.5),
        }}
      >
        <Button
          variant="text"
          onClick={onClose}
          disabled={loading}
          sx={{
            color: theme.palette.text.secondary,
            fontWeight: 500,
            '&:hover': {
              backgroundColor: alpha(theme.palette.divider, 0.8),
            },
          }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={onConfirm}
          disabled={loading}
          sx={{
            bgcolor: theme.palette.error.main,
            boxShadow: 'none',
            fontWeight: 500,
            '&:hover': {
              bgcolor: theme.palette.error.dark,
              boxShadow: 'none',
            },
            '&.Mui-disabled': {
              bgcolor: alpha(theme.palette.error.main, 0.3),
              color: alpha(theme.palette.error.contrastText, 0.5),
            },
            px: 3,
          }}
        >
          {loading ? 'Deleting...' : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};