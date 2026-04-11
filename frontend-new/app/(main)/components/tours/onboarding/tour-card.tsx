'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Flex, Text, Heading } from '@radix-ui/themes';
import { useOnboardingTourStore } from './store';
import { getTourStatus } from './api';
import { PixelLoader } from './pixel-loader';
import type { TourStepId } from './types';

// ===============================
// TourStepRow
// ===============================

interface TourStepRowProps {
  label: string;
  onClick: () => void;
  isHovered: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

function TourStepRow({
  label,
  onClick,
  isHovered,
  onMouseEnter,
  onMouseLeave,
}: TourStepRowProps) {
  return (
    <button
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        width: '100%',
        padding: '8px 0',
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
        textAlign: 'left',
        transition: 'opacity 0.15s ease',
        opacity: isHovered ? 0.8 : 1,
      }}
    >
      <Text
        size="1"
        style={{
          color: 'var(--olive-12)',
          fontWeight: 500,
          lineHeight: '20px',
          flex: 1,
        }}
      >
        {label}
      </Text>

      <span
        className="material-icons-outlined"
        style={{
          fontSize: '16px',
          color: 'var(--olive-9)',
          flexShrink: 0,
          marginLeft: '8px',
        }}
      >
        chevron_right
      </span>
    </button>
  );
}

// ===============================
// OnboardingTour
// ===============================

export function OnboardingTour() {
  const router = useRouter();
  const { isVisible, tourState, showTour, completeStep, dismissTour, hydrateTour } =
    useOnboardingTourStore();

  const [hoveredStep, setHoveredStep] = useState<TourStepId | null>(null);
  const [closeHovered, setCloseHovered] = useState(false);

  // Fetch tour state from API on first mount
  useEffect(() => {
    getTourStatus()
      .then((state) => hydrateTour(state))
      .catch(() => showTour());
  }, []);

  if (!isVisible || !tourState || tourState.status === 'hidden') return null;

  const isCompleted = tourState.status === 'completed';
  // Reserve 10% as the baseline "started" indicator; the API's completionPercentage
  // fills the remaining 90% (e.g. 33 % → 10 + 0.33 × 90 ≈ 40%).
  const loaderPercentage = isCompleted ? 100 : 10 + (tourState.completionPercentage / 100) * 90;
  const pendingSteps = isCompleted ? [] : tourState.stepsOrder;

  const handleStepClick = (stepId: TourStepId, relativeLink: string) => {
    completeStep(stepId);
    router.push(relativeLink);
  };

  return (
    <Box
      data-tour="onboarding"
      style={{
        position: 'fixed',
        bottom: '16px',
        left: '8px',
        width: '216px',
        zIndex: 200,
        borderRadius: 'var(--radius-3)',
        backgroundColor: 'var(--color-panel-solid)',
        border: '1px solid rgba(255, 255, 255, 0.07)',
        boxShadow:
          '0 0 0 1px rgba(221, 234, 248, 0.08), 0 8px 40px rgba(0, 0, 0, 0.45), 0 12px 32px -16px rgba(221, 234, 248, 0.08)',
        overflow: 'hidden',
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* ── Accent top edge ──────────────────────────── */}
      <Box
        style={{
          height: '2px',
          background: 'linear-gradient(90deg, var(--accent-9) 0%, var(--accent-7) 100%)',
        }}
      />

      <Box style={{ padding: '16px' }}>
        {/* ── Header row ───────────────────────────────── */}
        <Flex justify="between" align="start" style={{ marginBottom: '12px' }}>
          {/* Pixel icon — bulb while in progress, checkmark when all done */}
          <img
            src={isCompleted ? '/icons/tour/pixel-check.svg' : '/icons/tour/pixel-bulb.svg'}
            alt=""
            style={{ width: '24px', height: '30px', display: 'block', flexShrink: 0 }}
          />

          {/* Close / dismiss — only visible in the completed state */}
          {isCompleted && (
            <button
              onClick={() => dismissTour()}
              onMouseEnter={() => setCloseHovered(true)}
              onMouseLeave={() => setCloseHovered(false)}
              aria-label="Dismiss tour"
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                padding: '2px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 'var(--radius-1)',
                opacity: closeHovered ? 1 : 0.5,
                transition: 'opacity 0.15s ease',
              }}
            >
              <span className="material-icons-outlined" style={{ fontSize: '16px', color: 'var(--olive-11)' }}>
                close
              </span>
            </button>
          )}
        </Flex>

        {/* ── Title — comes directly from the API response ── */}
        <Heading
          size="2"
          style={{
            color: 'var(--olive-12)',
            fontWeight: 700,
            lineHeight: '1.3',
            marginBottom: '6px',
          }}
        >
          {tourState.title}
        </Heading>

        {/* ── Description ──────────────────────────────────── */}
        {isCompleted ? (
          <Text
            size="1"
            style={{
              color: 'var(--olive-9)',
              lineHeight: '1.5',
              display: 'block',
              marginBottom: '14px',
            }}
          >
            You&apos;re all set now. If you want to know more check out{' '}
            <a
              href="https://docs.pipeshub.com/"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--accent-9)', textDecoration: 'none' }}
            >
              documentation
            </a>
            .
          </Text>
        ) : (
          <Text
            size="1"
            style={{
              color: 'var(--olive-9)',
              lineHeight: '1.5',
              display: 'block',
              marginBottom: '14px',
            }}
          >
            {tourState.subtitle}
          </Text>
        )}

        {/* ── Pixel progress loader ─────────────────────── */}
        <Box style={{ marginBottom: pendingSteps.length > 0 ? '14px' : '0' }}>
          <PixelLoader percentage={loaderPercentage} width={184} />
        </Box>

        {/* ── Steps list — rendered from the API's stepsOrder ── */}
        {!isCompleted && pendingSteps.length > 0 && (
          <Flex direction="column" style={{ gap: 0 }}>
            {pendingSteps.map((stepId) => {
              const detail = tourState.stepsDetails[stepId];
              if (!detail) return null;
              return (
                <TourStepRow
                  key={stepId}
                  label={detail.title}
                  onClick={() => handleStepClick(stepId, detail.relativeLink)}
                  isHovered={hoveredStep === stepId}
                  onMouseEnter={() => setHoveredStep(stepId)}
                  onMouseLeave={() => setHoveredStep(null)}
                />
              );
            })}
          </Flex>
        )}
      </Box>
    </Box>
  );
}
