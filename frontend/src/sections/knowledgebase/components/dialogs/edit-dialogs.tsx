import { Icon } from '@iconify/react';
import React, { useState, useEffect } from 'react';

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

// Icons
const editIcon = 'material-symbols:edit';
const closeIcon = 'material-symbols:close';
const brainIcon = 'material-symbols:psychology';
const folderIcon = 'material-symbols:folder';

interface EditKnowledgeBaseDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
  currentName: string;
  loading?: boolean;
}

interface EditFolderDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
  currentName: string;
  loading?: boolean;
}

export const EditKnowledgeBaseDialog: React.FC<EditKnowledgeBaseDialogProps> = ({
  open,
  onClose,
  onSubmit,
  currentName,
  loading = false,
}) => {
  const theme = useTheme();
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) {
      setName(currentName);
      setError('');
    }
  }, [open, currentName]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();

    if (!trimmedName) {
      setError('Knowledge base name is required');
      return;
    }

    if (trimmedName.length < 2) {
      setError('Knowledge base name must be at least 2 characters');
      return;
    }

    if (trimmedName.length > 100) {
      setError('Knowledge base name must be less than 100 characters');
      return;
    }

    if (trimmedName === currentName) {
      onClose();
      return;
    }

    try {
      await onSubmit(trimmedName);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to update knowledge base');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !loading) {
      handleSubmit(e);
    }
  };

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
          borderRadius: 3,
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
            <Icon icon={brainIcon} width={18} height={18} />
          </Box>
          Edit Knowledge Base
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{ color: theme.palette.text.secondary }}
          aria-label="close"
          disabled={loading}
        >
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <form onSubmit={handleSubmit}>
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
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Update the name of your knowledge base. This will be visible to all users with access.
            </Typography>

            <TextField
              autoFocus
              fullWidth
              label="Knowledge Base Name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError('');
              }}
              onKeyDown={handleKeyDown}
              error={!!error}
              helperText={error || 'Enter a descriptive name for your knowledge base'}
              disabled={loading}
              variant="outlined"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 2,
                  '&.Mui-focused': {
                    '& .MuiOutlinedInput-notchedOutline': {
                      borderColor: theme.palette.primary.main,
                      borderWidth: 2,
                    },
                  },
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
            type="submit"
            variant="contained"
            disabled={loading || !name.trim() || name.trim() === currentName}
            sx={{
              bgcolor: theme.palette.primary.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.primary.dark,
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                bgcolor: alpha(theme.palette.primary.main, 0.3),
                color: alpha(theme.palette.primary.contrastText, 0.5),
              },
              px: 3,
            }}
          >
            {loading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export const EditFolderDialog: React.FC<EditFolderDialogProps> = ({
  open,
  onClose,
  onSubmit,
  currentName,
  loading = false,
}) => {
  const theme = useTheme();
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) {
      setName(currentName);
      setError('');
    }
  }, [open, currentName]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();

    if (!trimmedName) {
      setError('Folder name is required');
      return;
    }

    if (trimmedName.length < 1) {
      setError('Folder name must be at least 1 character');
      return;
    }

    if (trimmedName.length > 100) {
      setError('Folder name must be less than 100 characters');
      return;
    }

    if (trimmedName === currentName) {
      onClose();
      return;
    }

    try {
      await onSubmit(trimmedName);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to update folder');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !loading) {
      handleSubmit(e);
    }
  };

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
          borderRadius: 3,
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
            <Icon icon={folderIcon} width={18} height={18} />
          </Box>
          Edit Folder
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{ color: theme.palette.text.secondary }}
          aria-label="close"
          disabled={loading}
        >
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <form onSubmit={handleSubmit}>
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
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Update the name of your folder. This will be visible to all users with access.
            </Typography>

            <TextField
              autoFocus
              fullWidth
              label="Folder Name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError('');
              }}
              onKeyDown={handleKeyDown}
              error={!!error}
              helperText={error || 'Enter a descriptive name for your folder'}
              disabled={loading}
              variant="outlined"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 2,
                  '&.Mui-focused': {
                    '& .MuiOutlinedInput-notchedOutline': {
                      borderColor: theme.palette.primary.main,
                      borderWidth: 2,
                    },
                  },
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
            type="submit"
            variant="contained"
            disabled={loading || !name.trim() || name.trim() === currentName}
            sx={{
              bgcolor: theme.palette.primary.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.primary.dark,
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                bgcolor: alpha(theme.palette.primary.main, 0.3),
                color: alpha(theme.palette.primary.contrastText, 0.5),
              },
              px: 3,
            }}
          >
            {loading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};