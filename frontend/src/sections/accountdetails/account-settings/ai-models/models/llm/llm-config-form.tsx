import React, { forwardRef } from 'react';
import { Link, Alert } from '@mui/material';
import UniversalModelForm, { UniversalModelFormRef } from '../../components/universal-model-form';
import { getLlmConfig, updateLlmConfig } from '../../services/universal-config';

interface LlmConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  initialProvider?: string;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface LlmConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const LlmConfigForm = forwardRef<LlmConfigFormRef, LlmConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, initialProvider = 'openAI' }, ref) => 
     (
      <>
        <UniversalModelForm
          ref={ref}
          modelType="llm"
          onValidationChange={onValidationChange}
          onSaveSuccess={onSaveSuccess}
          initialProvider={initialProvider}
          getConfig={getLlmConfig}
          updateConfig={updateLlmConfig}
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

export default LlmConfigForm;