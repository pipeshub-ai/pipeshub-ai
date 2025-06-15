import { useRef, useState } from 'react';
import robotIcon from '@iconify-icons/mdi/robot';
import closeIcon from '@iconify-icons/eva/close-outline';

import {
  Box,
  alpha,
  Alert,
  Dialog,
  Button,
  useTheme,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import LlmConfigForm, { LlmConfigFormRef } from './llm-config-form';
import EmbeddingConfigForm, {
  EmbeddingConfigFormRef,
} from './embedding-config-form';
import { MODEL_TYPE_NAMES, MODEL_TYPE_ICONS } from '../types';


// Expected save result interface
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

interface ConfigureModelDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (result?: SaveResult) => void;
  modelType: string | null;
}

// Create a type for any form ref that has a handleSave method
type AnyFormRef = LlmConfigFormRef | EmbeddingConfigFormRef;

const ConfigureModelDialog = ({ open, onClose, onSave, modelType }: ConfigureModelDialogProps) => {
  const theme = useTheme();
  const [isValid, setIsValid] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);

  // Form refs for different model types
  const llmConfigFormRef = useRef<LlmConfigFormRef>(null);
  const embeddingConfigFormRef = useRef<EmbeddingConfigFormRef>(null);
  //   const ocrConfigFormRef = useRef<OcrConfigFormRef>(null);
  //   const slmConfigFormRef = useRef<SlmConfigFormRef>(null);
  //   const reasoningConfigFormRef = useRef<ReasoningConfigFormRef>(null);
  //   const multiModalConfigFormRef = useRef<MultiModalConfigFormRef>(null);

  // Get model colors
  const getModelColor = (type: string | null) => {
    if (!type) return theme.palette.primary.main;

    const colors: Record<string, string> = {
      llm: '#4CAF50', // Green
      ocr: '#2196F3', // Blue
      embedding: '#9C27B0', // Purple
      slm: '#FF9800', // Orange
      reasoning: '#E91E63', // Pink
      multiModal: '#673AB7', // Deep Purple
    };

    return colors[type] || theme.palette.primary.main;
  };

  // Get the icon for the current model type
  const getModelIcon = (type: any) => {
    if (!type) return robotIcon;
    return MODEL_TYPE_ICONS[type] || robotIcon;
  };

  // Get the title for the current model type
  const getModelTitle = (type: string | null) => {
    if (!type) return 'AI Model';
    return MODEL_TYPE_NAMES[type] || type.toUpperCase();
  };

  // Reset state when dialog opens
  if (!open) {
    if (dialogError) setDialogError(null);
    if (isSaving) setIsSaving(false);
  }

  // Form validation state
  const handleValidationChange = (valid: boolean) => {
    setIsValid(valid);
  };

  // Handle save button click - triggers the form's save method based on the active model type
  const handleSaveClick = async () => {
    setIsSaving(true);
    setDialogError(null);

    try {
      let result: SaveResult | undefined;

      switch (modelType) {
        case 'llm':
          if (llmConfigFormRef.current?.handleSave) {
            result = await llmConfigFormRef.current.handleSave();
          }
          break;
        case 'embedding':
          if (embeddingConfigFormRef.current?.handleSave) {
            result = await embeddingConfigFormRef.current.handleSave();
          }
          break;
        default:
          setDialogError('Unknown model type');
          setIsSaving(false);
          return;
      }

      if (result) {
        if (result.success) {
          // Pass result to parent for snackbar display
          onSave(result);
        } else {
          // Show error in dialog and don't close
          setDialogError(result.error || 'Failed to save configuration');
          setIsSaving(false);
        }
      } else {
        // No result returned - treat as error
        setDialogError('No response from form');
        setIsSaving(false);
      }
    } catch (error: any) {
      console.error('Error saving configuration:', error);
      const errorMessage = error?.message || 'An unexpected error occurred';
      setDialogError(`An unexpected error occurred: ${errorMessage}`);

      // Also pass the error to parent for snackbar display
      onSave({
        success: false,
        error: 'An unexpected error occurred while saving configuration',
      });

      setIsSaving(false);
      return;
    } finally {
      setIsSaving(false);
    }
  };

  // Get the model color for the current model type
  const modelColor = getModelColor(modelType);
  const modelIcon = getModelIcon(modelType);
  const modelTitle = getModelTitle(modelType);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
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
              bgcolor: alpha(modelColor, 0.1),
              color: modelColor,
            }}
          >
            <Iconify icon={modelIcon} width={18} height={18} />
          </Box>
          Configure {modelTitle} Integration
        </Box>

        <IconButton
          onClick={onClose}
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
        {dialogError && (
          <Alert
            severity="error"
            sx={{
              mb: 3,
              borderRadius: 1,
            }}
          >
            {dialogError}
          </Alert>
        )}

        <Box>
          {modelType === 'llm' && (
            <LlmConfigForm onValidationChange={handleValidationChange} ref={llmConfigFormRef} />
          )}
          {modelType === 'embedding' && (
            <EmbeddingConfigForm
              onValidationChange={handleValidationChange}
              ref={embeddingConfigFormRef}
            />
          )}
          {/* {modelType === 'ocr' && (
            <OcrConfigForm
              onValidationChange={handleValidationChange}
              ref={ocrConfigFormRef}
            />
          )} */}
          {/*
          {modelType === 'slm' && (
            <SlmConfigForm
              onValidationChange={handleValidationChange}
              ref={slmConfigFormRef}
            />
          )}
          {modelType === 'reasoning' && (
            <ReasoningConfigForm
              onValidationChange={handleValidationChange}
              ref={reasoningConfigFormRef}
            />
          )}
          {modelType === 'multiModal' && (
            <MultiModalConfigForm
              onValidationChange={handleValidationChange}
              ref={multiModalConfigFormRef}
            />
          )} */}
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
          onClick={handleSaveClick}
          disabled={!isValid || isSaving}
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
          {isSaving ? 'Saving...' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ConfigureModelDialog;
