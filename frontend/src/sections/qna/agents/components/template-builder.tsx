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
import { createScrollableContainerStyle } from '../../chatbot/utils/styles/scrollbar';


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
  const [formData, setFormData] = useState<AgentTemplateFormData>(getInitialTemplateFormData());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const scrollableContainerStyle = createScrollableContainerStyle(theme);
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
  }, [editingTemplate]);

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

  const handleSave = useCallback(async () => {
    const validationErrors = validateAgentTemplateForm(formData);
    setErrors(validationErrors);

    if (Object.keys(validationErrors).length > 0) {
      return;
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

  const handleClose = useCallback(() => {
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
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
          {label}
        </Typography>

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 1 }}>
          {fieldArray.map((item, index) => (
            <Chip
              key={index}
              label={item}
              onDelete={() => removeArrayItem(field, index)}
              size="small"
              variant="outlined"
              deleteIcon={<Icon icon={deleteIcon} width={14} height={14} />}
            />
          ))}
        </Box>

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
            />
          )}
          <Button
            onClick={() => {
              addArrayItem(field, newValue);
              setNewValue('');
            }}
            disabled={!newValue.trim()}
            variant="outlined"
            size="small"
            sx={{ minWidth: 'auto', px: 2 }}
          >
            <Icon icon={addIcon} width={16} height={16} />
          </Button>
        </Box>

        <Typography variant="caption" color="text.secondary">
          {helperText}
        </Typography>
      </Grid>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
        },
      }}
    >
      {/* Header */}
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pb: 2,
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Icon icon={templateIcon} width={24} height={24} color={theme.palette.primary.main} />
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {editingTemplate ? 'Edit Template' : 'Create Agent Template'}
          </Typography>
        </Box>
        <IconButton onClick={handleClose} size="small" disabled={isSaving}>
          <Icon icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      {/* Content */}
      <DialogContent sx={{ flexGrow: 1, overflow: 'auto', ...scrollableContainerStyle }}>
        <Box sx={{ py: 1 }}>
          {/* Basic Information */}
          <Typography variant="h6" sx={{ mb: 2, fontWeight: 600, color: 'text.primary' }}>
            Basic Information
          </Typography>
          <Grid container spacing={2} sx={{ mb: 3 }}>
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
                  sx={{ borderRadius: 1.5 }}
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
                rows={2}
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
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
                rows={4}
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
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
                  />
                }
                label="Deleted Template"
              />
              <Typography variant="caption" color="text.secondary" display="block">
                Deleted templates can be discovered and used by other users in your organization
              </Typography>
            </Grid>
          </Grid>

          {/* Default Configuration */}
          <Typography variant="h6" sx={{ mb: 2, fontWeight: 600, color: 'text.primary' }}>
            Default Configuration
          </Typography>
          <Grid container spacing={2} sx={{ mb: 3 }}>
            {renderArrayField(
              'tags',
              'Default Tags',
              'Add a tag',
              newTag,
              setNewTag,
              'Tags that will be applied to agents created from this template'
            )}
          </Grid>

          {/* Validation Errors */}
          {errors.general && (
            <Alert severity="error" sx={{ mb: 2, borderRadius: 1.5 }}>
              {errors.general}
            </Alert>
          )}
        </Box>
      </DialogContent>

      {/* Actions */}
      <Box
        sx={{
          p: 2,
          borderTop: 1,
          borderColor: 'divider',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          gap: 1.5,
        }}
      >
        <Button
          onClick={handleClose}
          disabled={isSaving}
          sx={{ borderRadius: 1.5, px: 2.5, py: 0.75 }}
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
          sx={{ borderRadius: 1.5, px: 2.5, py: 0.75, minWidth: 120 }}
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
