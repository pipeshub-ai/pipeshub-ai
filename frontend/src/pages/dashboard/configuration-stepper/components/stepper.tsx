// ===================================================================
// 📁 Complete Updated OnBoarding Stepper with Merged AI Models Configuration
// ===================================================================

import type { DynamicFormRef } from 'src/components/dynamic-form/components/dynamic-form';
import type { ConfigType, LlmFormValues, EmbeddingFormValues } from 'src/components/dynamic-form';

import React, { useRef, useMemo, useState, useEffect, useCallback } from 'react';

import {
  Step,
  Alert,
  alpha,
  Button,
  Dialog,
  Stepper,
  useTheme,
  Snackbar,
  StepLabel,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import DynamicForm from 'src/components/dynamic-form/components/dynamic-form';

import { createScrollableContainerStyle } from 'src/sections/qna/chatbot/utils/styles/scrollbar';

import {
  getLlmConfig,
  getUrlConfig,
  getSmtpConfig,
  updateLlmConfig,
  updateUrlConfig,
  getStorageConfig,
  updateSmtpConfig,
  getEmbeddingConfig,
  updateStorageConfig,
  updateEmbeddingConfig,
  updateStepperAiModelsConfig, // NEW: Import the merged update function
} from '../services/config-services';

// Configuration steps definition with ConfigType
const CONFIGURATION_STEPS = [
  {
    id: 'llm' as ConfigType,
    label: 'LLM',
    title: 'Large Language Model',
    description: 'Configure your LLM provider to enable AI capabilities in your application.',
    infoMessage:
      'LLM configuration is required to proceed with setup. All fields marked with * are required.',
    isRequired: true,
    canSkip: false,
    documentationUrl: 'https://docs.pipeshub.com/ai-models/overview',
  },
  {
    id: 'embedding' as ConfigType,
    label: 'Embeddings',
    title: 'Embeddings Configuration',
    description:
      'Configure the embedding model for generating text embeddings. Embeddings are used for semantic search and document retrieval.',
    infoMessage:
      'Select the embedding provider to use. You can use the default system embeddings or configure a specific provider.',
    isRequired: false,
    canSkip: true,
    documentationUrl: 'https://docs.pipeshub.com/ai-models/overview',
  },
  {
    id: 'storage' as ConfigType,
    label: 'Storage',
    title: 'Storage Configuration',
    description: 'Configure storage settings for your application data.',
    infoMessage:
      'Choose your preferred storage solution. Local storage is used by default if skipped.',
    isRequired: false,
    canSkip: true,
    documentationUrl: 'https://docs.pipeshub.com/system-overview/storage',
  },
  {
    id: 'url' as ConfigType,
    label: 'Public URLs',
    title: 'Public URL Configuration',
    description:
      'Configure the public URLs for your frontend and connector services. These URLs are used for OAuth redirects and webhook callbacks.',
    infoMessage:
      'Set up public URLs for external integrations. These are optional but recommended for production.',
    isRequired: false,
    canSkip: true,
    documentationUrl: 'https://docs.pipeshub.com/system-overview/internal-services',
  },
  {
    id: 'smtp' as ConfigType,
    label: 'SMTP',
    title: 'SMTP Configuration',
    description: 'Configure SMTP settings for email notifications.',
    infoMessage:
      'Set up email delivery for notifications and alerts. This is optional but recommended.',
    isRequired: false,
    canSkip: true,
    documentationUrl: 'https://docs.pipeshub.com/smtp',
  },
] as const;

// API endpoints
const ORG_API_BASE_URL = '/api/v1/org';

interface OnBoardingStepperProps {
  open: boolean;
  onClose: () => void;
  onComplete?: () => void;
}

interface StepState {
  completed: boolean;
  skipped: boolean;
  hasValidData: boolean;
  formData: any | null;
}

const OnBoardingStepper: React.FC<OnBoardingStepperProps> = ({ open, onClose, onComplete }) => {
  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Core state
  const [activeStep, setActiveStep] = useState<number>(0);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submissionError, setSubmissionError] = useState<string>('');
  const [submissionSuccess, setSubmissionSuccess] = useState<boolean>(false);

  const [stepStates, setStepStates] = useState<Record<string, StepState>>(() => {
    const initial: Record<string, StepState> = {};
    CONFIGURATION_STEPS.forEach((step) => {
      initial[step.id] = {
        completed: false,
        skipped: false,
        hasValidData: false,
        formData: null,
      };
    });
    return initial;
  });

  // Individual useRef calls for each dynamic form
  const llmRef = useRef<DynamicFormRef>(null);
  const embeddingRef = useRef<DynamicFormRef>(null);
  const storageRef = useRef<DynamicFormRef>(null);
  const urlRef = useRef<DynamicFormRef>(null);
  const smtpRef = useRef<DynamicFormRef>(null);

  // Group them into a stable object reference
  const formRefs = useMemo(
    () => ({
      llm: llmRef,
      embedding: embeddingRef,
      storage: storageRef,
      url: urlRef,
      smtp: smtpRef,
    }),
    []
  );

  // Snackbar state
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'info' | 'warning',
  });

  // Onboarding status tracking
  const statusUpdated = useRef<boolean>(false);

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setActiveStep(0);
      setSubmissionSuccess(false);
      setSubmissionError('');
      statusUpdated.current = false;

      // Reset step states completely
      const resetStates: Record<string, StepState> = {};
      CONFIGURATION_STEPS.forEach((step) => {
        resetStates[step.id] = {
          completed: false,
          skipped: false,
          hasValidData: false,
          formData: null,
        };
      });
      setStepStates(resetStates);
    }
  }, [open]);

  // Update onboarding status
  const updateOnboardingStatus = async (status: 'configured' | 'skipped'): Promise<void> => {
    if (statusUpdated.current) return;
    try {
      await axios.put(`${ORG_API_BASE_URL}/onboarding-status`, { status });
      statusUpdated.current = true;
    } catch (error) {
      console.error('Error updating onboarding status:', error);
    }
  };

  // Get current step configuration
  const getCurrentStep = useCallback(() => CONFIGURATION_STEPS[activeStep], [activeStep]);
  const getCurrentFormRef = useCallback(() => {
    const stepId = getCurrentStep()?.id;
    return stepId ? formRefs[stepId as keyof typeof formRefs] : null;
  }, [formRefs, getCurrentStep]);

  // Service functions mapping
  const getServiceFunctions = useCallback((configType: ConfigType) => {
    switch (configType) {
      case 'llm':
        return { getConfig: getLlmConfig, updateConfig: updateLlmConfig };
      case 'embedding':
        return { getConfig: getEmbeddingConfig, updateConfig: updateEmbeddingConfig };
      case 'storage':
        return { getConfig: getStorageConfig, updateConfig: updateStorageConfig };
      case 'url':
        return { getConfig: getUrlConfig, updateConfig: updateUrlConfig };
      case 'smtp':
        return { getConfig: getSmtpConfig, updateConfig: updateSmtpConfig };
      default:
        return {
          getConfig: async () => null,
          updateConfig: async (config: any) => config,
        };
    }
  }, []);

  const handleValidationChange = useCallback(
    (stepId: string, isValid: boolean, formData?: any) => {
      // Only update the specific step that triggered the validation change
      setStepStates((prev) => {
        // Don't update if it's not the current step to prevent cross-contamination
        const currentStep = getCurrentStep();
        if (currentStep?.id !== stepId) {
          return prev;
        }

        const newState = {
          ...prev,
          [stepId]: {
            ...prev[stepId],
            hasValidData: isValid,
            formData: formData || null,
            // Don't auto-complete until user clicks continue
            completed: prev[stepId].completed && isValid,
          },
        };

        return newState;
      });
    },
    [getCurrentStep]
  );

  // NEW: Helper function to prepare AI model configuration
  const prepareAiModelConfig = useCallback(
    (stepData: LlmFormValues | EmbeddingFormValues | null, configType: 'llm' | 'embedding') => {
      if (!stepData) return null;
      let isReasoning = false;

      const { providerType, modelType, _provider, isMultimodal, ...cleanConfig } = stepData;
      if (configType === 'llm') {
        const llmData = stepData as LlmFormValues;
        isReasoning = Boolean(llmData.isReasoning);
      }
      const provider = providerType || modelType;
      if (!provider) {
        console.warn(`Provider is undefined for ${configType} configuration`);
        return null;
      }

      if (configType === 'llm') {
        return [
          {
            provider,
            configuration: cleanConfig,
            isMultimodal: Boolean(isMultimodal),
            isReasoning: Boolean(isReasoning),
          },
        ];
      }

      return [
        {
          provider,
          configuration: cleanConfig,
          isMultimodal: Boolean(isMultimodal),
        },
      ];
    },
    []
  );

  // Continue handler with proper isolation and validation
  const handleContinue = async () => {
    const currentStep = getCurrentStep();
    if (!currentStep) return;

    setSubmissionError('');

    const formRef = getCurrentFormRef();

    // ALWAYS fresh validation for the current form only
    if (formRef?.current) {
      try {
        // Get isolated form data for this specific form
        const formData = await formRef.current.getFormData();
        const hasData = await formRef.current.hasFormData();
        const isFormValid = await formRef.current.validateForm();

        // Better validation logic for different step types
        if (currentStep.isRequired) {
          // Required steps must have valid data
          if (!hasData || !isFormValid) {
            setSubmissionError(
              `${currentStep.title} configuration is required. Please complete all required fields.`
            );
            return;
          }

          // Mark as completed with valid data
          setStepStates((prev) => ({
            ...prev,
            [currentStep.id]: {
              ...prev[currentStep.id],
              hasValidData: true,
              formData,
              completed: true,
              skipped: false,
            },
          }));
        } else if (hasData) {
          // Optional steps handling - if user entered data, it must be valid
          if (!isFormValid) {
            setSubmissionError(
              `Please complete all required fields for ${currentStep.title} or use the "Skip" button to proceed without configuration.`
            );
            return;
          }

          // Valid data provided
          setStepStates((prev) => ({
            ...prev,
            [currentStep.id]: {
              ...prev[currentStep.id],
              hasValidData: true,
              formData,
              completed: true,
              skipped: false,
            },
          }));
        } else if (currentStep.id === 'storage') {
          // Storage: Check if local storage is selected (always valid)
          if (formData?.providerType === 'local') {
            setStepStates((prev) => ({
              ...prev,
              [currentStep.id]: {
                ...prev[currentStep.id],
                hasValidData: true,
                formData,
                completed: true,
                skipped: false,
              },
            }));
          } else {
            // No data for non-local storage - skip it
            setStepStates((prev) => ({
              ...prev,
              [currentStep.id]: {
                ...prev[currentStep.id],
                hasValidData: false,
                formData: null,
                completed: true,
                skipped: true,
              },
            }));
          }
        } else if (currentStep.id === 'embedding') {
          // Embedding: Check if default is selected
          if (formData?.providerType === 'default') {
            setStepStates((prev) => ({
              ...prev,
              [currentStep.id]: {
                ...prev[currentStep.id],
                hasValidData: true,
                formData,
                completed: true,
                skipped: false,
              },
            }));
          } else {
            // No data for other embedding types - skip it
            setStepStates((prev) => ({
              ...prev,
              [currentStep.id]: {
                ...prev[currentStep.id],
                hasValidData: false,
                formData: null,
                completed: true,
                skipped: true,
              },
            }));
          }
        } else {
          // Other optional steps with no data - mark as skipped
          setStepStates((prev) => ({
            ...prev,
            [currentStep.id]: {
              ...prev[currentStep.id],
              hasValidData: false,
              formData: null,
              completed: true,
              skipped: true,
            },
          }));
        }
      } catch (error) {
        console.error('Error validating form:', error);
        setSubmissionError('Error validating form. Please try again.');
        return;
      }
    } else {
      // No form ref available
      if (currentStep.isRequired) {
        setSubmissionError(
          `${currentStep.title} configuration is required but form is not available.`
        );
        return;
      }

      // Optional step with no form - mark as skipped
      setStepStates((prev) => ({
        ...prev,
        [currentStep.id]: {
          ...prev[currentStep.id],
          hasValidData: false,
          formData: null,
          completed: true,
          skipped: true,
        },
      }));
    }

    // Move to next step or complete
    if (activeStep < CONFIGURATION_STEPS.length - 1) {
      const nextIndex = activeStep + 1;
      setActiveStep(nextIndex);
    } else {
      // This is the last step, complete configuration with current data
      await completeConfiguration();
    }
  };

  const handleSkip = () => {
    const currentStep = getCurrentStep();
    if (!currentStep || !currentStep.canSkip) return;

    // Mark step as skipped
    setStepStates((prev) => ({
      ...prev,
      [currentStep.id]: {
        ...prev[currentStep.id],
        skipped: true,
        completed: true,
        hasValidData: false,
        formData: null,
      },
    }));

    // Move to next step or complete
    if (activeStep < CONFIGURATION_STEPS.length - 1) {
      const nextIndex = activeStep + 1;
      setActiveStep(nextIndex);
    } else {
      completeConfiguration();
    }
  };

  // Robust rehydration on step change to ensure persistence across multiple navigations
  useEffect(() => {
    if (!open) return () => {};
    const timer = setTimeout(async () => {
      try {
        const step = getCurrentStep();
        if (!step) return;
        const state = stepStates[step.id];
        const ref = formRefs[step.id as keyof typeof formRefs];
        if (ref?.current && state?.formData) {
          await ref.current.rehydrateForm?.(state.formData);
        }
      } catch (e) {
        // no-op
      }
    }, 80);

    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStep, open]);

  const handleBack = () => {
    if (activeStep > 0) {
      const previousIndex = activeStep - 1;
      setActiveStep(previousIndex);
      setSubmissionError(''); // Clear any error when going back

      // After moving back, rehydrate the previous step's form with its last known data
      setTimeout(async () => {
        const prevStep = CONFIGURATION_STEPS[previousIndex];
        const prevState = stepStates[prevStep.id];
        const prevFormRef = formRefs[prevStep.id as keyof typeof formRefs];

        if (prevFormRef?.current && prevState?.formData) {
          try {
            await prevFormRef.current.rehydrateForm?.(prevState.formData);
          } catch (e) {
            // no-op: do not block navigation on rehydrate
          }
        }
      }, 50);
    }
  };

  useEffect(() => {
    const currentStep = getCurrentStep();
    const formRef = getCurrentFormRef();

    if (currentStep && formRef?.current && open) {
      // Delay to ensure form is fully rendered and isolated
      const timer = setTimeout(async () => {
        try {
          // Get current form data without affecting other forms
          const formData = await formRef.current!.getFormData();
          const hasData = await formRef.current!.hasFormData();
          const isValid = await formRef.current!.validateForm();

          // Update validation state for this specific step only
          handleValidationChange(currentStep.id, isValid, hasData ? formData : null);
        } catch (error) {
          console.error(`Error re-validating step ${currentStep.id}:`, error);
          // Reset validation state on error
          handleValidationChange(currentStep.id, false, null);
        }
      }, 300); // Reduced delay but still enough for form isolation

      return () => clearTimeout(timer);
    }

    return () => {};
  }, [getCurrentStep, getCurrentFormRef, handleValidationChange, activeStep, open]);

  // Complete configuration process
  const completeConfiguration = async () => {
    try {
      setIsSubmitting(true);
      setSubmissionError('');

      // Check if LLM is configured (required)
      const llmConfigured = stepStates.llm.completed && stepStates.llm.hasValidData;
      if (!llmConfigured) {
        setSubmissionError('LLM configuration is required to complete setup.');
        return;
      }

      setSnackbar({
        open: true,
        message: 'Saving all configurations, this may take a few seconds...',
        severity: 'info',
      });

      // Before completing, try to get the latest form data from current step if it's the last step
      if (activeStep === CONFIGURATION_STEPS.length - 1) {
        const currentStep = getCurrentStep();
        const formRef = getCurrentFormRef();

        if (currentStep && formRef?.current && currentStep.id === 'smtp') {
          try {
            // Get the latest SMTP form data
            const latestFormData = await formRef.current.getFormData();
            const hasData = await formRef.current.hasFormData();
            const isValid = await formRef.current.validateForm();

            // Update step state with latest data if valid
            if (hasData && isValid) {
              setStepStates((prev) => ({
                ...prev,
                smtp: {
                  ...prev.smtp,
                  hasValidData: true,
                  formData: latestFormData,
                  completed: true,
                  skipped: false,
                },
              }));

              // Wait a bit for state to update
              await new Promise((resolve) => setTimeout(resolve, 100));
            }
          } catch (error) {
            console.error('Error getting latest form data:', error);
          }
        }
      }

      // Collect all valid configurations
      const configurationsToSave = [];

      // Prepare LLM configuration (required)
      let llmConfig = null;
      if (stepStates.llm.hasValidData && stepStates.llm.formData) {
        llmConfig = prepareAiModelConfig(stepStates.llm.formData, 'llm');
        if (!llmConfig) {
          setSubmissionError('Invalid LLM configuration - provider is required.');
          return;
        }
      }

      // Prepare embedding configuration (optional)
      let embeddingConfig = null;
      if (stepStates.embedding.hasValidData && stepStates.embedding.formData) {
        if (stepStates.embedding.formData.providerType === 'default') {
          embeddingConfig = []; // Empty array for default
        } else {
          embeddingConfig = prepareAiModelConfig(stepStates.embedding.formData, 'embedding');
          if (!embeddingConfig) {
            setSubmissionError('Invalid embedding configuration - provider is required.');
            return;
          }
        }
      } else if (stepStates.embedding.skipped) {
        embeddingConfig = []; // Empty array for skipped (default)
      }

      // Ensure we have LLM configuration
      if (!llmConfig) {
        setSubmissionError('LLM configuration is required to complete setup.');
        return;
      }

      // Add AI models configuration (single call for both LLM and embedding)
      configurationsToSave.push({
        type: 'ai-models',
        saveFn: () => updateStepperAiModelsConfig(llmConfig, embeddingConfig),
      });

      // Add other configurations that are not skipped
      Object.keys(stepStates).forEach((stepId) => {
        if (stepId !== 'llm' && stepId !== 'embedding') {
          const state = stepStates[stepId];
          if (state.hasValidData && state.formData && !state.skipped) {
            const updateFns = {
              storage: () => updateStorageConfig(state.formData),
              url: () => updateUrlConfig(state.formData),
              smtp: () => updateSmtpConfig(state.formData),
            };

            const updateFn = updateFns[stepId as keyof typeof updateFns];
            if (updateFn) {
              configurationsToSave.push({
                type: stepId,
                saveFn: updateFn,
              });
            }
          }
        }
      });

      // Save all configurations
      const savePromises = configurationsToSave.map((config) =>
        config.saveFn().catch((error: any) => {
          console.error(`Error saving ${config.type}:`, error);
          throw new Error(
            error.response?.data?.error?.message ||
              error.response?.data?.message ||
              error.message ||
              `Failed to save ${config.type} configuration`
          );
        })
      );

      await Promise.all(savePromises);

      // Update onboarding status
      await updateOnboardingStatus('configured');

      setSubmissionSuccess(true);
      setSnackbar({
        open: true,
        message: `All configurations saved successfully! (${configurationsToSave.length} services configured)`,
        severity: 'success',
      });

      // Close dialog after a short delay
      setTimeout(() => {
        if (onComplete) onComplete();
        onClose();
      }, 2000);
    } catch (error: any) {
      console.error('Error completing configuration:', error);
      setSubmissionError(error.message || 'Failed to save configurations. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Handle close with status update
  const handleCloseWithStatus = async () => {
    try {
      setIsSubmitting(true);

      // If LLM is configured, mark as configured, otherwise skipped
      const llmConfigured = stepStates.llm.completed && stepStates.llm.hasValidData;
      await updateOnboardingStatus(llmConfigured ? 'configured' : 'skipped');
    } catch (error) {
      console.error('Error updating status on close:', error);
    } finally {
      setIsSubmitting(false);
      onClose();
    }
  };

  // Render step content using DynamicForm
  const renderStepContent = (stepIndex: number): React.ReactNode => {
    const step = CONFIGURATION_STEPS[stepIndex];
    if (!step) return null;

    const { getConfig, updateConfig } = getServiceFunctions(step.id);

    return (
      <div key={`${step.id}-${stepIndex}`}>
        <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 600, mb: 1 }}>
          {step.title}
        </Typography>

        <DynamicForm
          ref={formRefs[step.id as keyof typeof formRefs]}
          configType={step.id}
          title=""
          description={step.description}
          infoMessage={step.infoMessage}
          documentationUrl={step.documentationUrl}
          onValidationChange={(isValid, formData) =>
            handleValidationChange(step.id, isValid, formData)
          }
          onSaveSuccess={() => {}}
          getConfig={getConfig}
          updateConfig={updateConfig}
          isRequired={step.isRequired}
          stepperMode
          key={`form-${step.id}-${stepIndex}`}
        />
      </div>
    );
  };

  // Render footer buttons
  const renderFooterButtons = () => {
    if (submissionSuccess) {
      return (
        <Typography variant="body2" color="success.main" sx={{ fontWeight: 500 }}>
          Configuration completed successfully!
        </Typography>
      );
    }

    const currentStep = getCurrentStep();
    const isLastStep = activeStep === CONFIGURATION_STEPS.length - 1;
    const canGoBack = activeStep > 0;
    const canSkip = currentStep?.canSkip && !currentStep?.isRequired;

    return (
      <>
        {/* Back button */}
        {canGoBack && (
          <Button onClick={handleBack} disabled={isSubmitting} sx={{ mr: 1 }}>
            Back
          </Button>
        )}

        {/* Skip button */}
        {canSkip && (
          <Button color="inherit" onClick={handleSkip} disabled={isSubmitting} sx={{ mr: 1 }}>
            Skip {currentStep?.label}
          </Button>
        )}

        {/* Primary action button */}
        <Button
          variant="contained"
          color="primary"
          onClick={handleContinue}
          disabled={isSubmitting}
          startIcon={isSubmitting ? <CircularProgress size={16} /> : null}
          sx={{ px: 3 }}
        >
          {isSubmitting ? 'Saving...' : isLastStep ? 'Complete Setup' : 'Continue'}
        </Button>
      </>
    );
  };

  // Get step status for stepper display
  const getStepStatus = (stepIndex: number): 'completed' | 'active' | undefined => {
    if (activeStep === stepIndex) return 'active';

    if (stepIndex < activeStep) {
      const step = CONFIGURATION_STEPS[stepIndex];
      const state = stepStates[step.id];
      return state.completed ? 'completed' : undefined;
    }

    return undefined;
  };

  // Get step label with skip indicator
  const getStepLabel = (step: (typeof CONFIGURATION_STEPS)[number], index: number) => {
    const state = stepStates[step.id];
    return (
      <span>
        {step.label}
        {state.skipped && (
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
            (Skipped)
          </Typography>
        )}
      </span>
    );
  };

  return (
    <Dialog
      open={open}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      onClose={(event, reason) => {
        if (isSubmitting || reason === 'backdropClick' || reason === 'escapeKeyDown') {
          return;
        }
        onClose();
      }}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxHeight: '90vh',
        },
      }}
      disableEscapeKeyDown={isSubmitting}
    >
      {/* Dialog Header */}
      <DialogTitle
        sx={{
          px: 3,
          pt: 2.5,
          pb: 2,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          System Configuration
        </Typography>
      </DialogTitle>

      {/* Dialog Content */}
      <DialogContent
        sx={{
          px: 3,
          pt: 3,
          pb: 1,
          mt: 2,
          ...scrollableStyles,
          minHeight: 400,
        }}
      >
        {/* Progress Stepper */}
        <Stepper
          activeStep={activeStep}
          alternativeLabel
          sx={{
            mb: 4,
            '& .MuiStepLabel-label': {
              fontSize: '0.875rem',
            },
          }}
        >
          {CONFIGURATION_STEPS.map((step, index) => (
            <Step key={step.id} completed={getStepStatus(index) === 'completed'}>
              <StepLabel>{getStepLabel(step, index)}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {/* Error Alert */}
        {submissionError && (
          <Alert
            severity="error"
            sx={{ mb: 3, borderRadius: 1 }}
            onClose={() => setSubmissionError('')}
          >
            {submissionError}
          </Alert>
        )}

        {/* Success Alert */}
        {submissionSuccess && (
          <Alert severity="success" sx={{ mb: 3, borderRadius: 1 }}>
            Configuration completed successfully! The dialog will close automatically.
          </Alert>
        )}

        {/* Step Content */}
        {!submissionSuccess && renderStepContent(activeStep)}
      </DialogContent>

      {/* Dialog Actions */}
      <DialogActions
        sx={{
          px: 3,
          py: 2.5,
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: alpha(theme.palette.background.default, 0.5),
        }}
      >
        {renderFooterButtons()}
      </DialogActions>

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert
          severity={snackbar.severity}
          variant="filled"
          sx={{
            width: '100%',
            boxShadow: theme.shadows[8],
            fontSize: '0.875rem',
          }}
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Dialog>
  );
};

export default OnBoardingStepper;
