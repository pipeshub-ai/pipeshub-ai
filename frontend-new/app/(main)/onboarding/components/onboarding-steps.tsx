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
  completedStepIds,
}: OnboardingStepsProps) {
  return (
    <Flex
      style={{
        width: '100%',
        padding: '20px 24px 0',
        gap: '20px',
      }}
    >
      {steps.map((step) => {
        const isActive = step.id === currentStepId;
        const isCompleted = completedStepIds.includes(step.id);

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
              borderTop: `2px solid ${
                isActive
                  ? 'var(--gray-11)'
                  : isCompleted
                  ? 'var(--accent-9)'
                  : 'var(--gray-4)'
              }`,
              opacity: isActive || isCompleted ? 1 : 0.6,
            }}
          >
            <Text
              as="div"
              size="2"
              weight="medium"
              style={{
                color:
                  isActive || isCompleted
                    ? 'var(--gray-12)'
                    : 'var(--gray-9)',
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
                color: isActive ? 'var(--gray-11)' : 'var(--gray-8)',
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
