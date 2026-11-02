import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { OnboardingSteps } from '../onboarding-steps';
import type { OnboardingStep } from '../../types';

const env = vi.hoisted(() => ({ mobile: true }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => env.mobile }));

afterEach(cleanup);

const steps: OnboardingStep[] = [
  { id: 'ai-model', title: 'onboarding.steps.aiModel.title', description: 'onboarding.steps.aiModel.description', required: true },
  { id: 'storage', title: 'onboarding.steps.storage.title', description: 'onboarding.steps.storage.description', required: false },
];

const renderSteps = () =>
  render(
    <Theme>
      <OnboardingSteps steps={steps} currentStepId="storage" />
    </Theme>,
  );

describe('OnboardingSteps', () => {
  it('shows only "Step n of total: title" on phones', () => {
    env.mobile = true;
    renderSteps();
    expect(screen.getByText('Step 2 of 2: Storage')).toBeTruthy();
    expect(screen.queryByText('Connect an LLM to power search answers and insights')).toBeNull();
  });

  it('keeps the full tab strip on larger screens', () => {
    env.mobile = false;
    renderSteps();
    expect(screen.queryByText(/^Step 2 of 2/)).toBeNull();
  });
});
