import { Icon } from '@iconify/react';
import React, { useState } from 'react';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';

import {
  Box,
  alpha,
  Alert,
  Button,
  Dialog,
  Divider,
  useTheme,
  TextField,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';
import { KnowledgeBaseAPI } from './services/api';

interface DeleteRecordDialogProps {
  open: boolean;
  onClose: () => void;
  recordId: string;
  recordName: string;
  onRecordDeleted: () => void;
}

const DeleteRecordDialog = ({
  open,
  onClose,
  recordId,
  recordName,
  onRecordDeleted,
}: DeleteRecordDialogProps) => {
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState<string>('');
  const theme = useTheme();
  const handleConfirmChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setConfirmText(event.target.value);
  };

  const isDeleteDisabled = confirmText !== recordName;

  const handleDelete = async () => {
    if (isDeleteDisabled) return;

    setIsDeleting(true);
    setError(null);

    try {
      await KnowledgeBaseAPI.deleteRecord(recordId);
      onRecordDeleted();
      onClose();
    } catch (err) {
      console.error('Error deleting record:', err);
      setError(err.response?.data?.message || 'Failed to delete the record. Please try again.');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleClose = () => {
    if (!isDeleting) {
      setError(null);
      setConfirmText('');
      onClose();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      fullWidth
      maxWidth="sm"
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 1,
          boxShadow: '0 8px 24px rgba(0, 0, 0, 0.12)',
        },
      }}
    >
      <DialogTitle
        sx={{
          p: 3,
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 40,
            height: 40,
            borderRadius: 1,
            bgcolor: alpha('#f44336', 0.1),
            color: '#f44336',
          }}
        >
          <Icon icon={trashCanIcon} fontSize={24} />
        </Box>
        <Typography variant="h6" fontWeight={500}>
          Delete Record
        </Typography>
      </DialogTitle>

      <Divider />

      <DialogContent sx={{ p: 3 }}>
        <Typography paragraph>
          Are you sure you want to delete <strong>{recordName}</strong>? This action cannot be
          undone.
        </Typography>

        <Typography variant="body2" color="text.secondary" paragraph>
          All data associated with this record will be permanently removed from the system. This
          includes any documents, files, metadata, and relationships.
        </Typography>

        <Box sx={{ mt: 3 }}>
          <Typography variant="body2" gutterBottom fontWeight={500}>
            Type <strong>{recordName}</strong> to confirm deletion:
          </Typography>
          <TextField
            fullWidth
            variant="outlined"
            value={confirmText}
            onChange={handleConfirmChange}
            placeholder={`Type "${recordName}" to confirm`}
            sx={{
              mt: 1,
              '& .MuiOutlinedInput-root': {
                borderRadius: 1,
              },
            }}
            error={confirmText !== '' && confirmText !== recordName}
            disabled={isDeleting}
          />
        </Box>

        {error && (
          <Alert severity="error" sx={{ mt: 3 }}>
            {error}
          </Alert>
        )}
      </DialogContent>

      <DialogActions
        sx={{
          p: 2.5,
          bgcolor: (themeVal) => alpha(themeVal.palette.background.default, 0.5),
        }}
      >
        <Button
          variant="outlined"
          onClick={handleClose}
          disabled={isDeleting}
          sx={{
            borderRadius: 1,
            fontWeight: 500,
            textTransform: 'none',
          }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={handleDelete}
          disabled={isDeleteDisabled || isDeleting}
          startIcon={
            isDeleting ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={trashCanIcon} />
            )
          }
          sx={{
            borderRadius: 1,
            fontWeight: 500,
            textTransform: 'none',
          }}
        >
          {isDeleting ? 'Deleting...' : 'Delete Record'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DeleteRecordDialog;
