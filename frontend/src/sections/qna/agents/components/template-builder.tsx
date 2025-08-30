// src/sections/agents/components/template-builder.tsx
import React, { useState, useEffect, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Button,
  Typography,
  TextField,
  Grid,
  Chip,
  IconButton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Switch,
  FormControlLabel,
  Alert,
  CircularProgress,
  useTheme,
  Autocomplete,
  alpha,
  useMediaQuery,
  Fade,
} from '@mui/material';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import checkIcon from '@iconify-icons/mdi/check';
import addIcon from '@iconify-icons/mdi/plus';
import deleteIcon from '@iconify-icons/mdi/delete';
import templateIcon from '@iconify-icons/mdi/file-document';

import type { AgentTemplate, AgentTemplateFormData } from 'src/types/agent';
import AgentApiService from '../services/api';
import {
  validateAgentTemplateForm,
  getInitialTemplateFormData,
  TEMPLATE_CATEGORIES,
} from '../utils/agent';

interface TemplateBuilderProps {
  open: boolean;
  onClose: () => void;
  onSuccess: (template: AgentTemplate) => void;
  editingTemplate?: AgentTemplate | null;
}

const TemplateBuilder: React.FC<TemplateBuilderProps> = ({
  open,
  onClose,
  onSuccess,
  editingTemplate,
}) => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const [formData, setFormData] = useState<AgentTemplateFormData>(getInitialTemplateFormData());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [newTag, setNewTag] = useState('');

  // Initialize form data with safe handling
  useEffect(() => {
    if (editingTemplate) {
      setFormData({
        name: editingTemplate.name || '',
        description: editingTemplate.description || '',
        category: editingTemplate.category || '',
        startMessage: editingTemplate.startMessage || '',
        systemPrompt: editingTemplate.systemPrompt || '',
        tags: Array.isArray(editingTemplate.tags) ? [...editingTemplate.tags] : [],
        isDeleted: editingTemplate.isDeleted || false,
      });
    } else {
      setFormData(getInitialTemplateFormData());
    }
  }, [editingTemplate, open]);

  // Reset form when dialog closes
  useEffect(() => {
    if (!open) {
      setFormData(getInitialTemplateFormData());
      setErrors({});
      setNewTag('');
    }
  }, [open]);

  const handleFormChange = useCallback(
    (field: keyof AgentTemplateFormData, value: any) => {
      setFormData((prev) => ({ ...prev, [field]: value }));
      // Clear error for this field
      if (errors[field]) {
        setErrors((prev) => ({ ...prev, [field]: '' }));
      }
    },
    [errors]
  );

  const handleSave = useCallback(async (e?: React.MouseEvent) => {
    // Prevent any event bubbling that might interfere
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }

    try {
      setIsSaving(true);
      const template = editingTemplate
        ? await AgentApiService.updateTemplate(editingTemplate._key, formData)
        : await AgentApiService.createTemplate(formData);

      onSuccess(template);
      onClose();
    } catch (error) {
      console.error('Error saving template:', error);
      setErrors({ general: 'Failed to save template. Please try again.' });
    } finally {
      setIsSaving(false);
    }
  }, [formData, editingTemplate, onSuccess, onClose]);

  const handleClose = useCallback((e?: React.MouseEvent) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (!isSaving) {
      onClose();
    }
  }, [isSaving, onClose]);

  // Array field helpers with safe handling
  const addArrayItem = useCallback(
    (field: keyof AgentTemplateFormData, value: string) => {
      if (!value.trim()) return;

      const currentArray = Array.isArray(formData[field]) ? (formData[field] as string[]) : [];
      if (!currentArray.includes(value.trim())) {
        handleFormChange(field, [...currentArray, value.trim()]);
      }
    },
    [formData, handleFormChange]
  );

  const removeArrayItem = useCallback(
    (field: keyof AgentTemplateFormData, index: number) => {
      const currentArray = Array.isArray(formData[field]) ? (formData[field] as string[]) : [];
      handleFormChange(
        field,
        currentArray.filter((_, i) => i !== index)
      );
    },
    [formData, handleFormChange]
  );

  const handleAddTag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (newTag.trim()) {
      addArrayItem('tags', newTag);
      setNewTag('');
    }
  }, [newTag, addArrayItem]);

  const renderArrayField = (
    field: keyof AgentTemplateFormData,
    label: string,
    placeholder: string,
    newValue: string,
    setNewValue: (value: string) => void,
    helperText: string,
    suggestions: string[] = []
  ) => {
    const fieldArray = Array.isArray(formData[field]) ? (formData[field] as string[]) : [];

    return (
      <Grid item xs={12} key={field}>
        <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'text.primary' }}>
          {label}
        </Typography>

        {fieldArray.length > 0 && (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
            {fieldArray.map((item, index) => (
              <Chip
                key={index}
                label={item}
                onDelete={() => removeArrayItem(field, index)}
                size="small"
                variant="outlined"
                deleteIcon={<Icon icon={deleteIcon} width={14} height={14} />}
                sx={{
                  bgcolor: alpha(theme.palette.primary.main, 0.08),
                  borderColor: alpha(theme.palette.primary.main, 0.2),
                  color: theme.palette.primary.main,
                  '& .MuiChip-deleteIcon': {
                    color: 'inherit',
                    '&:hover': {
                      color: theme.palette.primary.dark,
                    },
                  },
                }}
              />
            ))}
          </Box>
        )}

        <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
          {suggestions.length > 0 ? (
            <Autocomplete
              freeSolo
              options={suggestions}
              value={newValue}
              onChange={(_, value) => setNewValue(value || '')}
              onInputChange={(_, value) => setNewValue(value)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder={placeholder}
                  size="small"
                  fullWidth
                  onKeyPress={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addArrayItem(field, newValue);
                      setNewValue('');
                    }
                  }}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1.5,
                      bgcolor: alpha(theme.palette.background.default, 0.5),
                    },
                  }}
                />
              )}
              sx={{ flexGrow: 1 }}
            />
          ) : (
            <TextField
              placeholder={placeholder}
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addArrayItem(field, newValue);
                  setNewValue('');
                }
              }}
              size="small"
              fullWidth
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1.5,
                  bgcolor: alpha(theme.palette.background.default, 0.5),
                },
              }}
            />
          )}
          <Button
            onClick={field === 'tags' ? handleAddTag : () => {
              addArrayItem(field, newValue);
              setNewValue('');
            }}
            disabled={!newValue.trim()}
            variant="outlined"
            size="small"
            sx={{
              minWidth: 'auto',
              px: 1.5,
              borderRadius: 1.5,
              border: `1px solid ${alpha(theme.palette.primary.main, 0.3)}`,
              color: theme.palette.primary.main,
              '&:hover': {
                bgcolor: alpha(theme.palette.primary.main, 0.08),
                borderColor: theme.palette.primary.main,
              },
              '&:disabled': {
                opacity: 0.5,
              },
            }}
          >
            <Icon icon={addIcon} width={16} height={16} />
          </Button>
        </Box>

        <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
          {helperText}
        </Typography>
      </Grid>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={(_, reason) => {
        // Prevent closing on backdrop click while saving
        if (reason === 'backdropClick' && isSaving) return;
        handleClose();
      }}
      maxWidth="md"
      fullWidth
      TransitionComponent={Fade}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(2px)',
          backgroundColor: alpha(theme.palette.common.black, 0.4),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxHeight: '92vh',
          height: isMobile ? '100vh' : 'auto',
          m: isMobile ? 0 : 2,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
          overflow: 'hidden',
        },
      }}
      scroll="paper"
    >
      {/* Header */}
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 3,
          pb: 2,
          borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: 2,
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Icon icon={templateIcon} width={20} height={20} color={theme.palette.primary.main} />
          </Box>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
              {editingTemplate ? 'Edit Template' : 'Create Agent Template'}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {editingTemplate ? 'Modify template settings' : 'Build a reusable agent template'}
            </Typography>
          </Box>
        </Box>
        <IconButton 
          onClick={handleClose} 
          size="small" 
          disabled={isSaving}
          sx={{
            color: theme.palette.text.secondary,
            '&:hover': {
              bgcolor: alpha(theme.palette.error.main, 0.1),
              color: theme.palette.error.main,
            },
          }}
        >
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      {/* Content */}
      <DialogContent 
        sx={{ 
          flexGrow: 1, 
          overflow: 'auto', 
          p: 3,
          '&::-webkit-scrollbar': { width: 6 },
          '&::-webkit-scrollbar-track': { bgcolor: 'transparent' },
          '&::-webkit-scrollbar-thumb': {
            bgcolor: alpha(theme.palette.divider, 0.2),
            borderRadius: 3,
            '&:hover': { bgcolor: alpha(theme.palette.divider, 0.3) },
          },
        }}
      >
        {/* Basic Information */}
        <Box sx={{ mb: 4 }}>
          <Typography variant="h6" sx={{ mb: 2.5, fontWeight: 600, color: 'text.primary', fontSize: '1.1rem' }}>
            Basic Information
          </Typography>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={8}>
              <TextField
                fullWidth
                label="Template Name"
                placeholder="Enter template name"
                value={formData.name}
                onChange={(e) => handleFormChange('name', e.target.value)}
                error={!!errors.name}
                helperText={errors.name || 'A unique name for your template'}
                variant="outlined"
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
                    bgcolor: alpha(theme.palette.background.default, 0.5),
                    '&:hover': { bgcolor: alpha(theme.palette.background.default, 0.7) },
                    '&.Mui-focused': { bgcolor: alpha(theme.palette.background.default, 0.8) },
                  },
                }}
              />
            </Grid>

            <Grid item xs={12} sm={4}>
              <FormControl fullWidth size="small">
                <InputLabel>Category</InputLabel>
                <Select
                  value={formData.category}
                  onChange={(e) => handleFormChange('category', e.target.value)}
                  label="Category"
                  error={!!errors.category}
                  sx={{ 
                    borderRadius: 1.5,
                    bgcolor: alpha(theme.palette.background.default, 0.5),
                    '&:hover': { bgcolor: alpha(theme.palette.background.default, 0.7) },
                    '&.Mui-focused': { bgcolor: alpha(theme.palette.background.default, 0.8) },
                  }}
                >
                  {TEMPLATE_CATEGORIES.map((category) => (
                    <MenuItem key={category} value={category}>
                      {category}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Description"
                placeholder="Describe what this template is for and what kind of agents it creates"
                value={formData.description}
                onChange={(e) => handleFormChange('description', e.target.value)}
                error={!!errors.description}
                helperText={
                  errors.description || 'Explain the purpose and use case of this template'
                }
                variant="outlined"
                multiline
                rows={3}
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
                    bgcolor: alpha(theme.palette.background.default, 0.5),
                    '&:hover': { bgcolor: alpha(theme.palette.background.default, 0.7) },
                    '&.Mui-focused': { bgcolor: alpha(theme.palette.background.default, 0.8) },
                  },
                }}
              />
            </Grid>

            <Grid item xs={12}>
              <TextField
                fullWidth
                label="System Prompt Template"
                placeholder="You are a helpful AI assistant specialized in... Always be professional and..."
                value={formData.systemPrompt}
                onChange={(e) => handleFormChange('systemPrompt', e.target.value)}
                error={!!errors.systemPrompt}
                helperText={
                  errors.systemPrompt ||
                  'Default system prompt that defines behavior for agents created from this template'
                }
                variant="outlined"
                multiline
                rows={5}
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
                    bgcolor: alpha(theme.palette.background.default, 0.5),
                    '&:hover': { bgcolor: alpha(theme.palette.background.default, 0.7) },
                    '&.Mui-focused': { bgcolor: alpha(theme.palette.background.default, 0.8) },
                  },
                }}
              />
            </Grid>

            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={formData.isDeleted}
                    onChange={(e) => handleFormChange('isDeleted', e.target.checked)}
                    sx={{
                      '& .MuiSwitch-switchBase.Mui-checked': {
                        color: theme.palette.primary.main,
                      },
                      '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                        bgcolor: theme.palette.primary.main,
                      },
                    }}
                  />
                }
                label={
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      Public Template
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Allow other users in your organization to discover and use this template
                    </Typography>
                  </Box>
                }
                sx={{ alignItems: 'flex-start', gap: 1 }}
              />
            </Grid>
          </Grid>
        </Box>

        {/* Default Configuration */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" sx={{ mb: 2.5, fontWeight: 600, color: 'text.primary', fontSize: '1.1rem' }}>
            Default Configuration
          </Typography>
          <Grid container spacing={2.5}>
            {renderArrayField(
              'tags',
              'Default Tags',
              'Add a tag',
              newTag,
              setNewTag,
              'Tags that will be applied to agents created from this template'
            )}
          </Grid>
        </Box>

        {/* Validation Errors */}
        {errors.general && (
          <Alert 
            severity="error" 
            sx={{ 
              mb: 2, 
              borderRadius: 1.5,
              border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
            }}
          >
            {errors.general}
          </Alert>
        )}
      </DialogContent>

      {/* Actions */}
      <Box
        sx={{
          p: 3,
          borderTop: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          gap: 1.5,
          flexShrink: 0,
          bgcolor: alpha(theme.palette.background.default, 0.5),
        }}
      >
        <Button
          onClick={handleClose}
          disabled={isSaving}
          sx={{ 
            borderRadius: 1.5, 
            px: 3, 
            py: 1,
            color: theme.palette.text.secondary,
            '&:hover': {
              bgcolor: alpha(theme.palette.action.hover, 0.08),
            },
          }}
          size="small"
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={isSaving}
          startIcon={
            isSaving ? (
              <CircularProgress size={16} color="inherit" />
            ) : (
              <Icon icon={checkIcon} width={16} height={16} />
            )
          }
          sx={{ 
            borderRadius: 1.5, 
            px: 3, 
            py: 1, 
            minWidth: 140,
            fontWeight: 600,
            boxShadow: 'none',
            '&:hover': {
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
            },
          }}
          size="small"
        >
          {isSaving
            ? editingTemplate
              ? 'Updating...'
              : 'Creating...'
            : editingTemplate
              ? 'Update Template'
              : 'Create Template'}
        </Button>
      </Box>
    </Dialog>
  );
};

export default TemplateBuilder;