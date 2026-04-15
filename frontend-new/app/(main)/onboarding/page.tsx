'use client';

import React, { useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, Button, Spinner } from '@radix-ui/themes';
import { useOnboardingStore } from './store';
import { useAuthStore } from '@/lib/store/auth-store';
import { updateOnboardingStatus } from './api';
import {
  OnboardingHeader,
  OnboardingSteps,
  StepOrgProfile,
  StepAiModel,
  StepEmbeddingModel,
  StepStorage,
  StepSmtp,
  StepLoading,
} from './components';
import type { OnboardingStepId } from './types';

// ===============================
// Inner page (needs useSearchParams inside Suspense)
// ===============================

function OnboardingPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    steps,
    currentStepId,
    completedStepIds,
    setCurrentStep,
    markStepCompleted,
    setOnboardingActive,
    submitting,
    orgDisplayName,
    orgInitial,
  } = useOnboardingStore();

  // Auth store — real user data
  const { user } = useAuthStore();
  const userName = user?.name ?? '';
  const userEmail = user?.email ?? '';
  const userInitials = userName
    ? userName.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)
    : '';

  // Read step from URL
  const stepFromUrl = (searchParams.get('step') as OnboardingStepId | null) ?? 'org-profile';

  // Sync URL → store with validation; redirect to safe default if step is missing or unrecognised
  useEffect(() => {
    if (steps.length === 0) return;
    const stepParam = searchParams.get('step');
    const isValidStep = stepParam ? steps.some((s) => s.id === stepParam) : false;
    if (!stepParam || !isValidStep) {
      router.replace(`/onboarding?step=${steps[0].id}`);
      return;
    }
    if (stepFromUrl !== currentStepId) {
      setCurrentStep(stepFromUrl);
    }
  }, [searchParams, steps, router, stepFromUrl, currentStepId, setCurrentStep]);

  // ---- Navigation helpers ----

  const currentIndex = steps.findIndex((s) => s.id === stepFromUrl);

  const navigateTo = (stepId: OnboardingStepId) => {
    setCurrentStep(stepId);
    router.push(`/onboarding?step=${stepId}`);
  };

  const handlePrev = () => {
    if (currentIndex > 0) {
      navigateTo(steps[currentIndex - 1].id);
    }
  };

  const handleNext = () => {
    if (currentIndex < steps.length - 1) {
      navigateTo(steps[currentIndex + 1].id);
    }
  };

  /**
   * Called when the user clicks "Enter Pipeshub" on the final step.
   * Marks onboarding as configured and navigates to the main app.
   */
  const handleFinishOnboarding = async () => {
    navigateTo('loading');
    try {
      await updateOnboardingStatus('configured');
    } catch {
      // Non-fatal — proceed to chat regardless
    } finally {
      setOnboardingActive(false);
      router.replace('/chat');
    }
  }

  const handleStepSuccess = (nextStep: OnboardingStepId | null) => {
    // Mark current step as completed
    markStepCompleted(stepFromUrl);

    if (nextStep !== null) {
      navigateTo(nextStep);
    }
    // If nextStep is null (e.g. final optional step saved), stay on the step —
    // the user will click "Enter Pipeshub" in the footer when ready.
  };

  // ---- System config step numbering ----

  // System config steps are all steps except 'org-profile' and 'loading'
  const systemConfigSteps = steps.filter(
    (s) => s.id !== 'org-profile' && s.id !== 'loading'
  );
  const systemStepIndex =
    systemConfigSteps.findIndex((s) => s.id === stepFromUrl) + 1;
  const totalSystemSteps = systemConfigSteps.length;

  // ---- Footer visibility ----

  const isFirstStep = currentIndex === 0;
  const isLastStep = currentIndex === steps.length - 1;
  const isLoadingStep = stepFromUrl === 'loading';

  // "Enter Pipeshub" is only enabled once the required LLM step is saved
  const isLlmCompleted = completedStepIds.includes('ai-model');

  // Highlight Next button when the current step has been saved
  const isCurrentStepCompleted = completedStepIds.includes(stepFromUrl);

  const showPrev = !isFirstStep && !isLoadingStep;
  const showNext = !isLoadingStep;

  // ---- Org context for header ----

  const showOrgBadge = stepFromUrl !== 'org-profile' && !!orgDisplayName;

  // ---- Render active form step ----

  function renderStep() {
    switch (stepFromUrl) {
      case 'org-profile':
        return <StepOrgProfile onSuccess={handleStepSuccess} />;
      case 'ai-model':
        return (
          <StepAiModel
            onSuccess={handleStepSuccess}
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
      case 'embedding-model':
        return (
          <StepEmbeddingModel
            onSuccess={handleStepSuccess}
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
      case 'storage':
        return (
          <StepStorage
            onSuccess={handleStepSuccess}
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
      case 'smtp':
        return (
          <StepSmtp
            onSuccess={handleStepSuccess}
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
      case 'loading':
        return <StepLoading />;
      default:
        return <StepOrgProfile onSuccess={handleStepSuccess} />;
    }
  }

  // Progress steps to show (exclude 'loading' from the visible progress bar)
  const visibleSteps = steps.filter((s) => s.id !== 'loading');

  return (
    <Flex
      direction="column"
      style={{
        height: '100vh',
        overflow: 'hidden',
        backgroundColor: 'var(--color-background)',
      }}
    >
      {/* Header — fixed height, never scrolls */}
      <OnboardingHeader
        userName={userName}
        userEmail={userEmail}
        userInitials={userInitials}
        orgDisplayName={orgDisplayName}
        orgInitial={orgInitial}
        orgSubtitle={orgDisplayName}
        showOrgBadge={showOrgBadge}
      />

      {/* Loading step — fills remaining space */}
      {isLoadingStep && (
        <Flex
          align="center"
          justify="center"
          style={{ flex: 1, minHeight: 0 }}
        >
          {renderStep()}
        </Flex>
      )}

      {/* Bordered content box — tabs (fixed) + form (scrollable) */}
      {!isLoadingStep && (
        <Box
          style={{
            maxWidth: '1228px',
            width: '100%',
            margin: '24px auto 0',
            border: '1px solid var(--gray-4)',
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* Tab strip — fixed, never scrolls */}
          <OnboardingSteps
            steps={visibleSteps}
            currentStepId={stepFromUrl}
            completedStepIds={completedStepIds}
          />

          {/* Form area — card scrolls internally; outer never scrolls */}
          <Flex
            align="center"
            justify="center"
            className="no-scrollbar"
            style={{ flex: 1, minHeight: 0, overflow: 'hidden', padding: '40px 24px' }}
          >
            {renderStep()}
          </Flex>
        </Box>
      )}

      {/* Nav row — fixed height, never scrolls */}
      {!isLoadingStep && (
        <Flex
          align="center"
          justify="between"
          style={{
            maxWidth: '1228px',
            width: '100%',
            margin: '0 auto',
            padding: '16px 0',
            flexShrink: 0,
          }}
        >
          {showPrev ? (
            <Button
              variant="outline"
              color="gray"
              onClick={handlePrev}
              style={{ cursor: 'pointer' }}
            >
              <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                arrow_back
              </span>
              Prev
            </Button>
          ) : (
            <Box />
          )}

          {showNext ? (
            isLastStep ? (
              <Button
                variant="solid"
                disabled={!isLlmCompleted || submitting}
                onClick={handleFinishOnboarding}
                style={{
                  cursor: !isLlmCompleted || submitting ? 'not-allowed' : 'pointer',
                  backgroundColor:
                    !isLlmCompleted || submitting ? 'var(--gray-4)' : 'var(--accent-9)',
                  color: !isLlmCompleted || submitting ? 'var(--gray-9)' : 'white',
                }}
                title={!isLlmCompleted ? 'Save the AI Model step first to continue' : undefined}
              >
                {submitting ? (
                  <Flex align="center" gap="2">
                    <Spinner size="1" />
                    Finishing…
                  </Flex>
                ) : (
                  <>
                    Enter Pipeshub
                    <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                      arrow_forward
                    </span>
                  </>
                )}
              </Button>
            ) : (
              <Button
                variant="solid"
                disabled={!isCurrentStepCompleted}
                onClick={handleNext}
                style={{
                  cursor: isCurrentStepCompleted ? 'pointer' : 'not-allowed',
                  backgroundColor: isCurrentStepCompleted ? 'var(--accent-9)' : 'var(--gray-4)',
                  color: isCurrentStepCompleted ? 'white' : 'var(--gray-9)',
                  opacity: 1,
                }}
              >
                Next
                <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                  arrow_forward
                </span>
              </Button>
            )
          ) : (
            <Box />
          )}
        </Flex>
      )}

      {/* Footer copyright row — fixed height, never scrolls */}
      {!isLoadingStep && (
        <Flex
          justify="center"
          align="center"
          style={{ padding: '8px 0 24px', flexShrink: 0 }}
        >
          <Text size="1" style={{ color: 'var(--gray-9)', textAlign: 'center' }}>
            © 2026 Pipeshub LLC &nbsp;·&nbsp;{' '}
            <a href="/privacy" style={{ color: 'var(--gray-9)', textDecoration: 'none' }}>
              Privacy Policy
            </a>{' '}
            &nbsp;·&nbsp;{' '}
            <a
              href="https://pipeshub.com"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--gray-9)', textDecoration: 'none' }}
            >
              Website
            </a>{' '}
            &nbsp;·&nbsp; Visit{' '}
            <a
              href="https://docs.pipeshub.com"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent-11)', textDecoration: 'none' }}
            >
              documentation
            </a>{' '}
            to learn more.
          </Text>
        </Flex>
      )}
    </Flex>
  );
}

// ===============================
// Page export (wrapped in Suspense for useSearchParams)
// ===============================

export default function OnboardingPage() {
  return (
    <Suspense
      fallback={
        <Flex
          align="center"
          justify="center"
          style={{ minHeight: '100vh', backgroundColor: 'var(--color-background)' }}
        >
          <StepLoading />
        </Flex>
      }
    >
      <OnboardingPageInner />
    </Suspense>
  );
}
