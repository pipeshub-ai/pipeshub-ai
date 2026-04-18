'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { OnboardingStep, OnboardingStepId } from '../types';

interface OnboardingStepsProps {
  steps: OnboardingStep[];
  currentStepId: OnboardingStepId;
  completedStepIds: OnboardingStepId[];
}

export function OnboardingSteps({
  steps,
  currentStepId,
  completedStepIds: _completedStepIds,
}: OnboardingStepsProps) {
  const activeIndex = steps.findIndex((s) => s.id === currentStepId);

  return (
    <Flex
      style={{
        width: '100%',
        padding: '20px 24px 0',
        gap: '20px',
      }}
    >
      {steps.map((step, index) => {
        const isCurrent = step.id === currentStepId;
        const isPast =
          activeIndex !== -1 && index < activeIndex;
        const isFuture =
          activeIndex !== -1 && index > activeIndex;

        const topBorderColor = isCurrent
          ? 'var(--gray-12)'
          : isPast
            ? 'var(--accent-9)'
            : 'var(--gray-4)';

        return (
          <Box
            key={step.id}
            style={{
              flex: 1,
              paddingTop: '12px',
              paddingBottom: '16px',
              paddingLeft: '16px',
              paddingRight: '16px',
              position: 'relative',
              borderTop: `2px solid ${topBorderColor}`,
              opacity: isFuture ? 0.6 : 1,
            }}
          >
            <Text
              as="div"
              size="2"
              weight="medium"
              style={{
                color: isFuture ? 'var(--gray-9)' : 'var(--gray-12)',
                marginBottom: '4px',
                lineHeight: '1.4',
              }}
            >
              {step.title}
            </Text>
            <Text
              as="div"
              size="1"
              style={{
                color: isCurrent
                  ? 'var(--gray-11)'
                  : isPast
                    ? 'var(--gray-9)'
                    : 'var(--gray-8)',
                lineHeight: '1.4',
              }}
            >
              {step.description}
            </Text>
          </Box>
        );
      })}
    </Flex>
  );
}
