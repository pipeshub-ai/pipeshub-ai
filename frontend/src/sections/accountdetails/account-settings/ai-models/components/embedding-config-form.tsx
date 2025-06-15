import React, { forwardRef } from 'react';
import DynamicForm, { DynamicFormRef } from 'src/components/dynamic-form/components/dynamic-form';
import { getEmbeddingConfig, updateEmbeddingConfig } from '../services/universal-config';

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

// 🔥 CRITICAL: This interface MUST have all 4 methods to match DynamicFormRef
export interface EmbeddingConfigFormRef extends DynamicFormRef{

}

const EmbeddingConfigForm = forwardRef<EmbeddingConfigFormRef, EmbeddingConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, initialProvider = 'openAI' }, ref) => (
    <DynamicForm
      ref={ref as React.RefObject<DynamicFormRef>}
      configType="embedding"
      onValidationChange={onValidationChange}
      onSaveSuccess={onSaveSuccess}
      initialProvider={initialProvider}
      getConfig={getEmbeddingConfig}
      updateConfig={updateEmbeddingConfig}
    />
  )
);

EmbeddingConfigForm.displayName = 'EmbeddingConfigForm';
export default EmbeddingConfigForm;
