'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { OnboardingStep, OnboardingStepId } from '../types';

interface OnboardingStepsProps {
  steps: OnboardingStep[];
  currentStepId: OnboardingStepId;
}

export function OnboardingSteps({ steps, currentStepId }: OnboardingStepsProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const activeIndex = steps.findIndex((s) => s.id === currentStepId);

  if (isMobile) {
    const current = steps[activeIndex];
    return (
      <Box style={{ width: '100%', padding: '12px 0 0' }}>
        <Text as="div" size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('onboarding.stepOf', {
            current: Math.max(activeIndex, 0) + 1,
            total: steps.length,
            title: current ? t(current.title) : '',
          })}
        </Text>
      </Box>
    );
  }

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
              paddingTop: '8px',
              paddingBottom: '8px',
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
              {t(step.title)}
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
              {t(step.description)}
            </Text>
          </Box>
        );
      })}
    </Flex>
  );
}
