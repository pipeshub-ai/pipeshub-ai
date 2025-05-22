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

import LlmConfigForm from '../llm/components/llm-config-form';
import EmbeddingConfigForm from '../embedding/components/embedding-config-form';
import { MODEL_TYPE_NAMES, MODEL_TYPE_ICONS } from '../utils/types';

import type { LlmConfigFormRef } from './llm-config-form';
import type { EmbeddingConfigFormRef } from './embedding-config-form';
// import OcrConfigForm, { OcrConfigFormRef } from './model-forms/ocr-config-form';
// import SlmConfigForm, { SlmConfigFormRef } from './model-forms/slm-config-form';
// import ReasoningConfigForm, { ReasoningConfigFormRef } from './model-forms/reasoning-config-form';
// import MultiModalConfigForm, { MultiModalConfigFormRef } from './model-forms/multimodal-config-form';

// Method configurations
interface ModelConfigType {
  [key: string]: {
    icon: string;
    title: string;
    color: string;
  };
}

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
type AnyFormRef = {
  handleSave: () => Promise<SaveResult | boolean>;
};

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
    let currentRef: React.RefObject<AnyFormRef> | null = null;
    setIsSaving(true);
    setDialogError(null);

    // Determine which form ref to use based on model type
    switch (modelType) {
      case 'llm':
        currentRef = llmConfigFormRef;
        break;
      case 'embedding':
        currentRef = embeddingConfigFormRef;
        break;
      //   case 'ocr':
      //     currentRef = ocrConfigFormRef;
      //     break;
      //   case 'slm':
      //     currentRef = slmConfigFormRef;
      //     break;
      //   case 'reasoning':
      //     currentRef = reasoningConfigFormRef;
      //     break;
      //   case 'multiModal':
      //     currentRef = multiModalConfigFormRef;
      //     break;
      default:
        currentRef = null;
    }

    try {
      // If we have a valid ref with handleSave method
      if (currentRef?.current?.handleSave) {
        const result = await currentRef.current.handleSave();

        // Handle different types of results
        if (result === false) {
          // Legacy support: false means error
          setDialogError('Failed to save configuration');
          setIsSaving(false);
          return;
        }

        if (typeof result === 'object') {
          // New system with structured result
          if (result.success) {
            // Pass any warnings to the parent component for snackbar display
            onSave(result);
          } else {
            // Show error in dialog and don't close
            setDialogError(result.error || 'Failed to save configuration');
            setIsSaving(false);
          }
        } else {
          // Legacy support: any other result (true or undefined) means success
          onSave({ success: true });
        }
      }
    } catch (error) {
      console.error('Error saving configuration:', error);
      setDialogError(`An unexpected error occurred: ${error.message}`);

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
