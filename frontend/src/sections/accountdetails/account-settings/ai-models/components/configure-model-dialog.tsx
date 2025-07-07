import { useRef, useState, useCallback, useEffect } from 'react';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';
import closeIcon from '@iconify-icons/eva/close-outline';
import expandMoreIcon from '@iconify-icons/material-symbols/expand-more';

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
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Typography,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import axios from 'src/utils/axios';

import { createScrollableContainerStyle } from 'src/sections/qna/chatbot/utils/styles/scrollbar';
import LlmConfigForm, { LlmConfigFormRef } from './llm-config-form';
import EmbeddingConfigForm, { EmbeddingConfigFormRef } from './embedding-config-form';
import { MODEL_TYPE_NAMES, MODEL_TYPE_ICONS } from '../types';
import { updateBothModelConfigs } from '../services/universal-config';

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

const ConfigureModelDialog = ({ open, onClose, onSave, modelType }: ConfigureModelDialogProps) => {
  const theme = useTheme();
  const [isLlmValid, setIsLlmValid] = useState(false);
  const [isEmbeddingValid, setIsEmbeddingValid] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const scrollableStyles = createScrollableContainerStyle(theme);

  const [expandedAccordion, setExpandedAccordion] = useState<string | false>(false);
  const llmConfigFormRef = useRef<LlmConfigFormRef>(null);
  const embeddingConfigFormRef = useRef<EmbeddingConfigFormRef>(null);

  useEffect(() => {
    if (open) {
      setExpandedAccordion(modelType || 'llm');
      setDialogError(null);
      setIsSaving(false);
      setIsLlmValid(false);
      setIsEmbeddingValid(false);
    }
  }, [open, modelType]);

  const getModelColor = useCallback(
    (type: string) => {
      const colors: Record<string, string> = {
        llm: '#4CAF50', // Green
        ocr: '#2196F3', // Blue
        embedding: '#9C27B0', // Purple
        slm: '#FF9800', // Orange
        reasoning: '#E91E63', // Pink
        multiModal: '#673AB7', // Deep Purple
      };
      return colors[type] || theme.palette.primary.main;
    },
    [theme.palette.primary.main]
  );

  const getModelIcon = useCallback((type: string) => MODEL_TYPE_ICONS[type] || settingsIcon, []);

  const getModelTitle = useCallback(
    (type: string) => MODEL_TYPE_NAMES[type] || type.toUpperCase(),
    []
  );

  const handleClose = useCallback(() => {
    setExpandedAccordion(false);
    setDialogError(null);
    setIsSaving(false);
    setIsLlmValid(false);
    setIsEmbeddingValid(false);
    onClose();
  }, [onClose]);

  const handleAccordionChange = useCallback(
    (panel: string) => (event: React.SyntheticEvent, isExpanded: boolean) => {
      setExpandedAccordion(isExpanded ? panel : false);
    },
    []
  );

  const handleLlmAccordionChange = handleAccordionChange('llm');
  const handleEmbeddingAccordionChange = handleAccordionChange('embedding');

  const handleLlmValidationChange = useCallback((valid: boolean) => {
    setIsLlmValid(valid);
  }, []);

  const handleEmbeddingValidationChange = useCallback((valid: boolean) => {
    setIsEmbeddingValid(valid);
  }, []);

  const isValid = isLlmValid || isEmbeddingValid;

  const handleSaveClick = useCallback(async () => {
    setIsSaving(true);
    setDialogError(null);

    try {
      let llmFormData = null;
      let embeddingFormData = null;
      const errors: string[] = [];

      // Get LLM form data if valid
      if (isLlmValid && llmConfigFormRef.current?.getFormData) {
        try {
          llmFormData = await llmConfigFormRef.current.getFormData();
        } catch (error) {
          console.error('Error getting LLM form data:', error);
          errors.push('Failed to get LLM configuration data');
        }
      }

      // Get Embedding form data if valid
      if (isEmbeddingValid && embeddingConfigFormRef.current?.getFormData) {
        try {
          embeddingFormData = await embeddingConfigFormRef.current.getFormData();
        } catch (error) {
          console.error('Error getting Embedding form data:', error);
          errors.push('Failed to get Embedding configuration data');
        }
      }

      if (errors.length > 0) {
        setDialogError(errors.join(', '));
        setIsSaving(false);
        return;
      }

      if (!llmFormData && !embeddingFormData) {
        setDialogError('No valid configurations to save');
        setIsSaving(false);
        return;
      }

      // Single API call to update both configurations
      await updateBothModelConfigs(llmFormData, embeddingFormData);

      // Determine success message based on what was saved
      const savedConfigs = [];
      if (llmFormData) savedConfigs.push('LLM');
      if (embeddingFormData) savedConfigs.push('Embedding');

      const successMessage = `${savedConfigs.join(' and ')} configuration${savedConfigs.length > 1 ? 's' : ''} updated successfully`;

      onSave({
        success: true,
        warning: undefined,
      });

      // Close dialog after successful save
      handleClose();
    } catch (error: any) {
      console.error('Error saving configurations:', error);
      const errorMessage =
        error?.response?.data?.message || error?.message || 'An unexpected error occurred';
      setDialogError(`Failed to save configurations: ${errorMessage}`);

      onSave({
        success: false,
        error: `Failed to save configurations ${errorMessage}`,
      });

      setIsSaving(false);
    }
  }, [isLlmValid, isEmbeddingValid, onSave, handleClose]);

  const llmColor = getModelColor('llm');
  const embeddingColor = getModelColor('embedding');
  const llmIcon = getModelIcon('llm');
  const embeddingIcon = getModelIcon('embedding');
  const llmTitle = getModelTitle('llm');
  const embeddingTitle = getModelTitle('embedding');

  return (
    <Dialog
      open={open}
      onClose={handleClose}
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
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: theme.palette.primary.main,
            }}
          >
            <Iconify icon={settingsIcon} width={18} height={18} />
          </Box>
          Configure AI Model Integrations
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
          ...scrollableStyles,
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

        <Box sx={{ mb: 3 }}>
          {/* LLM Configuration Accordion */}
          <Accordion
            expanded={expandedAccordion === 'llm'}
            onChange={handleLlmAccordionChange}
            sx={{
              mb: 2,
              borderRadius: 1,
              border: '1px solid',
              borderColor: theme.palette.divider,
              '&:before': {
                display: 'none',
              },
              '&.Mui-expanded': {
                boxShadow: theme.customShadows.z8,
              },
            }}
          >
            <AccordionSummary
              expandIcon={<Iconify icon={expandMoreIcon} />}
              sx={{
                px: 2.5,
                py: 1.5,
                minHeight: 64,
                '&.Mui-expanded': {
                  minHeight: 64,
                },
                '& .MuiAccordionSummary-content': {
                  margin: '12px 0',
                  '&.Mui-expanded': {
                    margin: '12px 0',
                  },
                },
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 40,
                    height: 40,
                    borderRadius: '8px',
                    bgcolor: alpha(llmColor, 0.1),
                    color: llmColor,
                  }}
                >
                  <Iconify icon={llmIcon} width={20} height={20} />
                </Box>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {llmTitle}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Configure large language model settings
                  </Typography>
                </Box>
              </Box>
            </AccordionSummary>
            <AccordionDetails sx={{ p: 0, pt: 0 }}>
              <Box sx={{ px: 2.5, pb: 2.5 }}>
                <LlmConfigForm
                  onValidationChange={handleLlmValidationChange}
                  ref={llmConfigFormRef}
                />
              </Box>
            </AccordionDetails>
          </Accordion>

          {/* Embedding Configuration Accordion */}
          <Accordion
            expanded={expandedAccordion === 'embedding'}
            onChange={handleEmbeddingAccordionChange}
            sx={{
              borderRadius: 1,
              border: '1px solid',
              borderColor: theme.palette.divider,
              '&:before': {
                display: 'none',
              },
              '&.Mui-expanded': {
                boxShadow: theme.customShadows.z8,
              },
            }}
          >
            <AccordionSummary
              expandIcon={<Iconify icon={expandMoreIcon} />}
              sx={{
                px: 2.5,
                py: 1.5,
                minHeight: 64,
                '&.Mui-expanded': {
                  minHeight: 64,
                },
                '& .MuiAccordionSummary-content': {
                  margin: '12px 0',
                  '&.Mui-expanded': {
                    margin: '12px 0',
                  },
                },
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 40,
                    height: 40,
                    borderRadius: '8px',
                    bgcolor: alpha(embeddingColor, 0.1),
                    color: embeddingColor,
                  }}
                >
                  <Iconify icon={embeddingIcon} width={20} height={20} />
                </Box>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                    {embeddingTitle}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Configure embedding model settings for search and retrieval
                  </Typography>
                </Box>
              </Box>
            </AccordionSummary>
            <AccordionDetails sx={{ p: 0, pt: 0 }}>
              <Box sx={{ px: 2.5, pb: 2.5 }}>
                <EmbeddingConfigForm
                  onValidationChange={handleEmbeddingValidationChange}
                  ref={embeddingConfigFormRef}
                />
              </Box>
            </AccordionDetails>
          </Accordion>
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
          {isSaving ? 'Saving...' : 'Save Configurations'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ConfigureModelDialog;
