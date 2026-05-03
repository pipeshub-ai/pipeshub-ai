'use client';

import React, { useState } from 'react';
import { Flex, Box, Text, Button, Spinner } from '@radix-ui/themes';
import { submitUserBackgroundSurvey } from '@/app/(main)/onboarding/api';
import { PipesHubIcon } from '@/app/components/ui';

// ===============================
// DEV FLAG — set to false to hide the survey
// ===============================
const isUserBackgroundSurveyActive = process.env.NEXT_PUBLIC_USER_BACKGROUND_SURVEY_ACTIVE === 'true';

// ===============================
// Role Options
// ===============================

const ROLE_OPTIONS = [
  { value: 'software-developer', label: 'Software Developer' },
  { value: 'product-manager', label: 'Product Manager' },
  { value: 'marketer', label: 'Marketer' },
  { value: 'designer', label: 'Designer' },
  { value: 'project-manager', label: 'Project Manager' },
  { value: 'operations', label: 'Operations' },
  { value: 'it-support', label: 'IT Support Specialist' },
  { value: 'human-resources', label: 'Human Resources' },
  { value: 'finance-officer', label: 'Finance Officer' },
  { value: 'legal', label: 'Legal' },
  { value: 'compliance-officer', label: 'Compliance Officer' },
  { value: 'sales-representative', label: 'Sales Representative' },
  { value: 'data-scientist', label: 'Data Scientist' },
  { value: 'customer-service', label: 'Customer Service' },
];

// ===============================
// Component
// ===============================

const SURVEY_DISMISSED_KEY = 'pipeshub:userBackgroundSurveyDismissed';

export function UserBackgroundSurvey() {
  const [isVisible, setIsVisible] = useState(() =>
    isUserBackgroundSurveyActive && !localStorage.getItem(SURVEY_DISMISSED_KEY)
  );
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!isVisible) return null;

  const handleRoleSelect = (role: string) => {
    setSelectedRole((prev) => (prev === role ? null : role));
  };

  const handleSave = async () => {
    if (!selectedRole) return;
    setSubmitting(true);

    try {
      await submitUserBackgroundSurvey(selectedRole);
      localStorage.setItem(SURVEY_DISMISSED_KEY, '1');
      setIsVisible(false);
    } catch {
      // Dismiss on error too — do not re-show on refresh
      localStorage.setItem(SURVEY_DISMISSED_KEY, '1');
      setIsVisible(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    /* Full-screen overlay */
    <Flex
      align="center"
      justify="center"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        backgroundColor: 'rgba(0, 0, 0, 0.55)',
        backdropFilter: 'blur(4px)',
      }}
    >
      {/* Modal card */}
      <Flex
        direction="column"
        style={{
          backgroundColor: 'var(--color-background)',
          border: '1px solid var(--gray-3)',
          borderRadius: 'var(--radius-2)',
          width: '460px',
          maxWidth: 'calc(100vw - 32px)',
          maxHeight: 'calc(100vh - 48px)',
          boxShadow: '0 4px 24px 0 rgba(0,0,0,0.32)',
          overflow: 'hidden',
        }}
      >
        {/* ── Sticky header ── */}
        <Box style={{ padding: '20px 20px 0', flexShrink: 0 }}>
          {/* Logo mark */}
          <Flex justify="center" style={{ marginBottom: '12px' }}>
            <PipesHubIcon size={36} color="var(--accent-8)" />
          </Flex>

          {/* Title */}
          <Text
            as="div"
            size="3"
            weight="bold"
            align="center"
            style={{ color: 'var(--gray-12)', marginBottom: '4px' }}
          >
            What kind of work do you do?
          </Text>

          {/* Subtitle */}
          <Text
            as="div"
            size="1"
            align="center"
            style={{ color: 'var(--gray-9)', marginBottom: '12px', display: 'block' }}
          >
            This will help us personalise your Pipeshub experience
          </Text>
        </Box>

        {/* ── Scrollable role grid ── */}
        <Box
          className="no-scrollbar"
          style={{
            overflowY: 'auto',
            padding: '0 20px',
            flex: '1 1 auto',
            minHeight: 0,
          }}
        >
          <Box
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '4px',
              paddingBottom: '4px',
            }}
          >
            {ROLE_OPTIONS.map((role) => {
              const isSelected = selectedRole === role.value;
              return (
                <button
                  key={role.value}
                  onClick={() => handleRoleSelect(role.value)}
                  disabled={submitting}
                  style={{
                    backgroundColor: isSelected ? 'var(--accent-a3)' : 'transparent',
                    color: isSelected ? 'var(--accent-11)' : 'var(--gray-11)',
                    border: `1px solid ${isSelected ? 'var(--accent-7)' : 'var(--gray-4)'}`,
                    borderRadius: 'var(--radius-1)',
                    padding: '6px 10px',
                    textAlign: 'left',
                    fontSize: '12px',
                    fontWeight: '400',
                    cursor: submitting ? 'not-allowed' : 'pointer',
                    transition: 'background-color 0.15s, border-color 0.15s, color 0.15s',
                    lineHeight: '1.3',
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected && !submitting) {
                      (e.currentTarget as HTMLButtonElement).style.backgroundColor =
                        'var(--gray-a2)';
                      (e.currentTarget as HTMLButtonElement).style.borderColor =
                        'var(--gray-6)';
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected && !submitting) {
                      (e.currentTarget as HTMLButtonElement).style.backgroundColor =
                        'transparent';
                      (e.currentTarget as HTMLButtonElement).style.borderColor =
                        'var(--gray-4)';
                    }
                  }}
                >
                  {role.label}
                </button>
              );
            })}
          </Box>
        </Box>

        {/* ── Sticky Save button ── */}
        <Box style={{ padding: '12px 20px 20px', flexShrink: 0 }}>
          <Button
            onClick={handleSave}
            disabled={!selectedRole || submitting}
            style={{
              width: '100%',
              height: '34px',
              backgroundColor:
                !selectedRole || submitting ? 'var(--gray-4)' : 'var(--accent-9)',
              color: !selectedRole || submitting ? 'var(--gray-9)' : 'white',
              cursor: !selectedRole || submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? (
              <Flex align="center" gap="2">
                <Spinner size="1" />
                Saving…
              </Flex>
            ) : (
              'Save'
            )}
          </Button>
        </Box>
      </Flex>
    </Flex>
  );
}
