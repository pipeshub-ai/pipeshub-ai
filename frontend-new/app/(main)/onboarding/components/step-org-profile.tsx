'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text, Button, TextField, Spinner } from '@radix-ui/themes';
import { useOnboardingStore } from '../store';
import { getOrgDetails, updateOrgProfile } from '../api';
import type { OrgProfileFormData, OnboardingStepId } from '../types';
import { toast } from '@/lib/store/toast-store';

// Country and region data (simplified)
const COUNTRIES = [
  { value: 'US', label: 'United States' },
  { value: 'GB', label: 'United Kingdom' },
  { value: 'CA', label: 'Canada' },
  { value: 'AU', label: 'Australia' },
  { value: 'DE', label: 'Germany' },
  { value: 'FR', label: 'France' },
  { value: 'IN', label: 'India' },
  { value: 'JP', label: 'Japan' },
  { value: 'SG', label: 'Singapore' },
  { value: 'OTHER', label: 'Other' },
];

const US_STATES = [
  'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
  'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
  'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
  'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
  'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
];

const selectStyle: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  color: 'var(--gray-12)',
  border: '1px solid var(--gray-5)',
  borderRadius: 'var(--radius-2)',
  padding: '0 8px',
  height: '36px',
  fontSize: '14px',
  width: '100%',
  outline: 'none',
  appearance: 'auto',
};

interface StepOrgProfileProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
}

export function StepOrgProfile({ onSuccess }: StepOrgProfileProps) {
  const {
    orgProfile,
    setOrgProfile,
    setOrgContext,
    markStepCompleted,
    unmarkStepCompleted,
    submitting,
    setSubmitting,
    setSubmitStatus,
  } = useOnboardingStore();

  const [form, setForm] = useState<OrgProfileFormData>({
    organizationName: orgProfile.organizationName,
    displayName: orgProfile.displayName,
    streetAddress: orgProfile.streetAddress,
    country: orgProfile.country,
    state: orgProfile.state,
    city: orgProfile.city,
    zipCode: orgProfile.zipCode,
  });

  const [savedSuccessfully, setSavedSuccessfully] = useState(false);
  // Tracks whether user has manually edited the form (disables auto-Next activation)
  const isDirtyRef = useRef(false);

  // Mark step as completed when pre-filled from GET (if not dirty)
  useEffect(() => {
    if (!isDirtyRef.current) {
      const prefilled =
        form.organizationName.trim() !== '' &&
        form.displayName.trim() !== '' &&
        form.streetAddress.trim() !== '' &&
        form.country !== '';
      if (prefilled) {
        markStepCompleted('org-profile');
      }
    }
  }, [form]);

  // Pre-populate form from existing org details on mount
  useEffect(() => {
    getOrgDetails()
      .then((org) => {
        setForm((prev) => ({
          ...prev,
          organizationName: org.registeredName ?? prev.organizationName,
          displayName: org.shortName ?? prev.displayName,
          streetAddress: org.permanentAddress?.addressLine1 ?? prev.streetAddress,
          city: org.permanentAddress?.city ?? prev.city,
          state: org.permanentAddress?.state ?? prev.state,
          zipCode: org.permanentAddress?.postCode ?? prev.zipCode,
          country: org.permanentAddress?.country ?? prev.country,
        }));
      })
      .catch(() => {
        // Silently ignore — user can fill manually
      });
  }, []);

  const handleChange = (field: keyof OrgProfileFormData, value: string) => {
    isDirtyRef.current = true;
    unmarkStepCompleted('org-profile');
    setSavedSuccessfully(false);
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitStatus('loading');
    setOrgProfile(form);

    const toastId = toast.loading('Saving organisation…', {
      description: 'This will take a few seconds',
    });

    try {
      await updateOrgProfile(form);
      setOrgContext(form.displayName || form.organizationName);
      setSubmitStatus('success');
      setSavedSuccessfully(true);
      isDirtyRef.current = false;
      toast.update(toastId, {
        variant: 'success',
        title: 'Organisation successfully created!',
        description: 'Click Next to continue',
        showCloseButton: true,
      });
      setTimeout(() => toast.dismiss(toastId), 3000);
      // Stay on the step — user clicks Next to continue
      onSuccess(null);
    } catch {
      setSubmitStatus('error');
      toast.dismiss(toastId);
    } finally {
      setSubmitting(false);
    }
  };

  const isFormValid =
    form.organizationName.trim() !== '' &&
    form.displayName.trim() !== '' &&
    form.streetAddress.trim() !== '' &&
    form.country !== '';

  return (
    <Flex direction="column" style={{ maxHeight: '100%' }}>
      {/* Form Card — fixed header + scrollable fields + fixed button */}
      <Box
        style={{
          backgroundColor: 'var(--gray-2)',
          border: '1px solid var(--gray-4)',
          borderRadius: 'var(--radius-3)',
          width: '576px',
          maxHeight: '100%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Fixed Header */}
        <Box
          style={{
            flexShrink: 0,
            padding: '24px 24px 16px',
            borderBottom: '1px solid var(--gray-4)',
          }}
        >
          <Text
            as="div"
            size="4"
            weight="bold"
            style={{ color: 'var(--gray-12)', marginBottom: '4px' }}
          >
            Setup your Organization*
          </Text>
          <Text
            as="div"
            size="2"
            style={{ color: 'var(--gray-9)' }}
          >
            Create your organization profile
          </Text>
        </Box>

        {/* Scrollable fields */}
        <Box
          className="no-scrollbar"
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}
        >
          <Flex direction="column" gap="6">
          {/* Organization Name */}
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Organization Name*
            </Text>
            <TextField.Root
              placeholder="Legal name of the company"
              value={form.organizationName}
              onChange={(e) => handleChange('organizationName', e.target.value)}
              disabled={submitting}
            />
          </Flex>

          {/* Display Name */}
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Display Name*
            </Text>
            <TextField.Root
              placeholder="This is how your company name will be displayed"
              value={form.displayName}
              onChange={(e) => handleChange('displayName', e.target.value)}
              disabled={submitting}
            />
          </Flex>

          {/* Address Details */}
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Address Details*
            </Text>
            <TextField.Root
              placeholder="Street address"
              value={form.streetAddress}
              onChange={(e) => handleChange('streetAddress', e.target.value)}
              disabled={submitting}
            />
          </Flex>

          {/* Country + State row */}
          <Flex gap="2">
            <Box style={{ flex: 1 }}>
              <select
                value={form.country}
                onChange={(e) => handleChange('country', e.target.value)}
                disabled={submitting}
                style={selectStyle}
              >
                <option value="">Country</option>
                {COUNTRIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Box>
            <Box style={{ flex: 1 }}>
              {form.country === 'US' ? (
                <select
                  value={form.state}
                  onChange={(e) => handleChange('state', e.target.value)}
                  disabled={submitting}
                  style={selectStyle}
                >
                  <option value="">State/Province</option>
                  {US_STATES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              ) : (
                <TextField.Root
                  placeholder="State/Province"
                  value={form.state}
                  onChange={(e) => handleChange('state', e.target.value)}
                  disabled={submitting}
                />
              )}
            </Box>
          </Flex>

          {/* City + Zip row */}
          <Flex gap="2">
            <Box style={{ flex: 1 }}>
              <TextField.Root
                placeholder="City"
                value={form.city}
                onChange={(e) => handleChange('city', e.target.value)}
                disabled={submitting}
              />
            </Box>
            <Box style={{ flex: 1 }}>
              <TextField.Root
                placeholder="Zip/Postal Code"
                value={form.zipCode}
                onChange={(e) => handleChange('zipCode', e.target.value)}
                disabled={submitting}
              />
            </Box>
          </Flex>

          </Flex>
        </Box>

        {/* Fixed footer: Create Account button */}
        <Box style={{ flexShrink: 0, padding: '0 24px 24px' }}>
          <Button
            onClick={handleSubmit}
            disabled={!isFormValid || submitting || savedSuccessfully}
            style={{
              width: '100%',
              backgroundColor:
                !isFormValid || submitting || savedSuccessfully
                  ? 'var(--gray-4)'
                  : 'var(--accent-9)',
              color:
                !isFormValid || submitting || savedSuccessfully
                  ? 'var(--gray-9)'
                  : 'white',
              cursor:
                !isFormValid || submitting || savedSuccessfully
                  ? 'not-allowed'
                  : 'pointer',
              height: '40px',
              opacity: 1,
            }}
          >
            {submitting ? (
              <Flex align="center" gap="2">
                <Spinner size="1" />
                Saving…
              </Flex>
            ) : savedSuccessfully ? (
              <Flex align="center" gap="2">
                <span className="material-icons-outlined" style={{ fontSize: '16px' }}>
                  check
                </span>
                Account Created
              </Flex>
            ) : (
              'Create Account'
            )}
          </Button>
        </Box>
      </Box>
    </Flex>
  );
}
