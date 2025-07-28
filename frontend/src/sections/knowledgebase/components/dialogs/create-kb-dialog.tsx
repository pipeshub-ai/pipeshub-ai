// components/dialogs/CreateKnowledgeBaseDialog.tsx
import React, { useState } from 'react';
import closeIcon from '@iconify-icons/eva/close-outline';
import knowledgeIcon from '@iconify-icons/eva/book-open-outline';

import {
  Box,
  alpha,
  Dialog,
  Button,
  useTheme,
  TextField,
  Typography,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface CreateKnowledgeBaseDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
  loading?: boolean;
}

export const CreateKnowledgeBaseDialog: React.FC<CreateKnowledgeBaseDialogProps> = ({
  open,
  onClose,
  onSubmit,
  loading = false,
}) => {
  const [name, setName] = useState('');
  const theme = useTheme();

  const handleSubmit = async () => {
    if (name.trim()) {
      await onSubmit(name.trim());
      setName('');
    }
  };

  const handleClose = () => {
    setName('');
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
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
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: theme.palette.primary.main,
            }}
          >
            <Iconify icon={knowledgeIcon} width={18} height={18} />
          </Box>
          Create Knowledge Base
        </Box>

        <IconButton
          onClick={handleClose}
          size="small"
          sx={{ color: theme.palette.text.secondary }}
          aria-label="close"
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
            variant="body2" 
            color="text.secondary" 
            sx={{ mb: 2 }}
          >
            Create a new knowledge base to organize and store your documents, information, and data for AI-powered search and retrieval.
          </Typography>
          
          <TextField
            autoFocus
            fullWidth
            label="Knowledge Base Name"
            placeholder="Enter knowledge base name..."
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && !loading && name.trim() && handleSubmit()}
            disabled={loading}
            sx={{
              '& .MuiOutlinedInput-root': {
                borderRadius: 1,
                '&:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: theme.palette.primary.main,
                },
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderWidth: 1,
                },
              },
              '& .MuiInputLabel-root': {
                fontWeight: 500,
              },
            }}
          />
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
          onClick={handleClose}
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
          onClick={handleSubmit}
          disabled={!name.trim() || loading}
          sx={{
            bgcolor: theme.palette.primary.main,
            boxShadow: 'none',
            fontWeight: 500,
            '&:hover': {
              bgcolor: theme.palette.primary.dark,
              boxShadow: 'none',
            },
            px: 3,
          }}
        >
          {loading ? 'Creating...' : 'Create Knowledge Base'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};