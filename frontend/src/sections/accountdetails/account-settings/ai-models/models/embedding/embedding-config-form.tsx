import React, { forwardRef } from 'react';
import UniversalModelForm, { UniversalModelFormRef } from '../../components/universal-model-form';
import { getEmbeddingConfig, updateEmbeddingConfig } from '../../services/universal-config';

interface EmbeddingConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  initialProvider?: string;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface EmbeddingConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const EmbeddingConfigForm = forwardRef<EmbeddingConfigFormRef, EmbeddingConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, initialProvider = 'openAI' }, ref) => 
     (
      <UniversalModelForm
        ref={ref}
        modelType="embedding"
        onValidationChange={onValidationChange}
        onSaveSuccess={onSaveSuccess}
        initialProvider={initialProvider}
        getConfig={getEmbeddingConfig}
        updateConfig={updateEmbeddingConfig}
      />
    )
);

export default EmbeddingConfigForm;