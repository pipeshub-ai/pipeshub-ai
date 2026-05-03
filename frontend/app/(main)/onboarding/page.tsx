'use client';

import React, { useEffect, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, Button, Spinner } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useOnboardingStore } from './store';
import { useAuthStore } from '@/lib/store/auth-store';
import { getOnboardingStatus, updateOnboardingStatus } from './api';
import {
  OnboardingHeader,
  OnboardingSteps,
  StepAiModel,
  StepEmbeddingModel,
  StepStorage,
  StepSmtp,
} from './components';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import type { OnboardingStepId } from './types';

// ===============================
// Inner page (needs useSearchParams inside Suspense)
// ===============================

function OnboardingPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useTranslation();
  const [onboardingGate, setOnboardingGate] = useState<'checking' | 'allowed'>('checking');
  const [embeddingDefaultDialog, setEmbeddingDefaultDialog] = useState(false);
  const [embeddingRegistryHasDefault, setEmbeddingRegistryHasDefault] = useState(false);

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
  const { user, isHydrated, isAuthenticated } = useAuthStore();
  const userName = user?.name ?? '';
  const userEmail = user?.email ?? '';
  const userInitials = userName
    ? userName.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)
    : '';

  // Read step from URL
  const stepFromUrl = (searchParams.get('step') as OnboardingStepId | null) ?? 'ai-model';

  useEffect(() => {
    if (stepFromUrl !== 'embedding-model') {
      setEmbeddingDefaultDialog(false);
      setEmbeddingRegistryHasDefault(false);
    }
  }, [stepFromUrl]);

  // Only allow this route when org onboarding is still required (matches layout gate semantics).
  useEffect(() => {
    if (!isHydrated || !isAuthenticated) {
      return;
    }

    getOnboardingStatus()
      .then(({ status }) => {
        if (status === 'notConfigured') {
          setOnboardingActive(true);
          setOnboardingGate('allowed');
        } else {
          setOnboardingActive(false);
          router.replace('/');
        }
      })
      .catch(() => {
        // Match main layout: do not block the app on a failed status check
        setOnboardingGate('allowed');
      });
  }, [isHydrated, isAuthenticated, router, setOnboardingActive]);

  // Sync URL → store with validation; redirect to safe default if step is missing or unrecognised
  useEffect(() => {
    if (onboardingGate !== 'allowed') return;
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
  }, [
    onboardingGate,
    searchParams,
    steps,
    router,
    stepFromUrl,
    currentStepId,
    setCurrentStep,
  ]);

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

  // System config steps are all steps except the terminal 'loading' step
  const systemConfigSteps = steps.filter((s) => s.id !== 'loading');
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

  const isMiddleNextEnabled = isCurrentStepCompleted;

  const showSkipEmbeddingDefault =
    stepFromUrl === 'embedding-model' &&
    !isCurrentStepCompleted &&
    embeddingRegistryHasDefault;

  const showPrev = !isFirstStep && !isLoadingStep;
  const showNext = !isLoadingStep;

  // ---- Org context for header ----

  const showOrgBadge = !!orgDisplayName;

  // ---- Render active form step ----

  function renderStep() {
    switch (stepFromUrl) {
      // case 'org-profile':
      //   return <StepOrgProfile onSuccess={handleStepSuccess} />;
      case 'ai-model':
        return (
          <StepAiModel
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
      case 'embedding-model':
        return (
          <StepEmbeddingModel
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
            embeddingDefaultDialog={embeddingDefaultDialog}
            setEmbeddingDefaultDialog={setEmbeddingDefaultDialog}
            onRegistryHasSystemDefaultEmbedding={setEmbeddingRegistryHasDefault}
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
        return <LoadingScreen />;
      default:
        // return <StepOrgProfile onSuccess={handleStepSuccess} />;
        return (
          <StepAiModel
            systemStepIndex={systemStepIndex}
            totalSystemSteps={totalSystemSteps}
          />
        );
    }
  }

  // Progress steps to show (exclude 'loading' from the visible progress bar)
  const visibleSteps = steps.filter((s) => s.id !== 'loading');

  if (onboardingGate !== 'allowed') {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ minHeight: '100vh', backgroundColor: 'var(--color-background)' }}
      >
        <LoadingScreen />
      </Flex>
    );
  }

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
          <OnboardingSteps steps={visibleSteps} currentStepId={stepFromUrl} />

          {/* Form area — card scrolls internally; outer never scrolls */}
          <Flex
            align="center"
            justify="center"
            className="no-scrollbar"
            style={{ flex: 1, minHeight: 0, overflow: 'hidden', padding: '8px 16px' }}
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
            padding: '8px 0',
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
              {t('onboarding.nav.prev')}
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
                title={!isLlmCompleted ? t('onboarding.nav.saveLlmFirst') : undefined}
              >
                {submitting ? (
                  <Flex align="center" gap="2">
                    <Spinner size="1" />
                    {t('onboarding.nav.finishing')}
                  </Flex>
                ) : (
                  <>
                    {t('onboarding.nav.enterPipeshub')}
                    <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                      arrow_forward
                    </span>
                  </>
                )}
              </Button>
            ) : (
              <Flex align="center" gap="3" wrap="wrap" justify="end">
                {showSkipEmbeddingDefault ? (
                  <Button
                    variant="outline"
                    color="gray"
                    title={t('onboarding.skipEmbeddingTitle')}
                    onClick={() => setEmbeddingDefaultDialog(true)}
                    style={{ cursor: 'pointer' }}
                  >
                    {t('onboarding.skipEmbeddingDefault')}
                  </Button>
                ) : null}
                <Button
                  variant="solid"
                  disabled={!isMiddleNextEnabled}
                  onClick={handleNext}
                  title={
                    !isMiddleNextEnabled && stepFromUrl === 'embedding-model'
                      ? embeddingRegistryHasDefault
                        ? t('onboarding.nextDisabledEmbeddingHint')
                        : t('onboarding.nextDisabledEmbeddingHintNoRegistryDefault')
                      : undefined
                  }
                  style={{
                    cursor: isMiddleNextEnabled ? 'pointer' : 'not-allowed',
                    backgroundColor: isMiddleNextEnabled ? 'var(--accent-9)' : 'var(--gray-4)',
                    color: isMiddleNextEnabled ? 'white' : 'var(--gray-9)',
                    opacity: 1,
                  }}
                >
                  {t('onboarding.nav.next')}
                  <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                    arrow_forward
                  </span>
                </Button>
              </Flex>
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
            {t('onboarding.footer.copyright')} &nbsp;·&nbsp;{' '}
            <a href="/privacy" style={{ color: 'var(--gray-9)', textDecoration: 'none' }}>
              {t('onboarding.footer.privacyPolicy')}
            </a>{' '}
            &nbsp;·&nbsp;{' '}
            <a
              href="https://pipeshub.com"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--gray-9)', textDecoration: 'none' }}
            >
              {t('onboarding.footer.website')}
            </a>{' '}
            &nbsp;·&nbsp; {t('onboarding.footer.visitDocs')}{' '}
            <a
              href="https://docs.pipeshub.com"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent-11)', textDecoration: 'none' }}
            >
              {t('onboarding.footer.docsLabel')}
            </a>{' '}
            {t('onboarding.footer.toLearnMore')}
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
          <LoadingScreen />
        </Flex>
      }
    >
      <OnboardingPageInner />
    </Suspense>
  );
}
