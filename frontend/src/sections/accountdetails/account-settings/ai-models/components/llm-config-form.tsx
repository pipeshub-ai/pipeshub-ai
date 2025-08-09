// ===================================================================
// 📁 Legacy Form Components (Simplified)
// ===================================================================

// LLM Config Form (for backward compatibility with dynamic form)
import React, { forwardRef } from 'react';
import { Link, Alert } from '@mui/material';
import DynamicForm, { DynamicFormRef } from 'src/components/dynamic-form/components/dynamic-form';
import { modelService } from '../services/universal-config';

interface LlmConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  initialProvider?: string;
}

export interface LlmConfigFormRef extends DynamicFormRef {}

const LlmConfigForm = forwardRef<LlmConfigFormRef, LlmConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, initialProvider = 'openAI' }, ref) => (
    <>
      <DynamicForm
        ref={ref as React.RefObject<DynamicFormRef>}
        configType="llm"
        onValidationChange={onValidationChange}
        onSaveSuccess={onSaveSuccess}
        initialProvider={initialProvider}
        getConfig={modelService.getLlmConfig}
        updateConfig={modelService.updateLlmConfig}
      />

      <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
        Refer to{' '}
        <Link href="https://docs.pipeshub.com/ai-models/overview" target="_blank" rel="noopener">
          the documentation
        </Link>{' '}
        for more information.
      </Alert>
    </>
  )
);

LlmConfigForm.displayName = 'LlmConfigForm';