'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text, Button, TextField, Spinner } from '@radix-ui/themes';
import { InfoBanner } from './info-banner';
import { useOnboardingStore } from '../store';
import { getSmtpConfig, saveSmtpConfig } from '../api';
import type { SmtpFormData, OnboardingStepId } from '../types';

interface StepSmtpProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepSmtp({
  onSuccess,
  systemStepIndex,
  totalSystemSteps,
}: StepSmtpProps) {
  const { smtp, setSmtp, markStepCompleted, unmarkStepCompleted, submitting, setSubmitting, setSubmitStatus } =
    useOnboardingStore();

  const [form, setForm] = useState<SmtpFormData>({
    host: smtp.host,
    port: smtp.port ?? 587,
    fromEmail: smtp.fromEmail,
    username: smtp.username,
    password: smtp.password,
  });

  const [showPassword, setShowPassword] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const isDirtyRef = useRef(false);

  // Mark step as pre-completed when GET fills required fields (host + fromEmail)
  useEffect(() => {
    if (!loadingConfig && !isDirtyRef.current) {
      if (form.host.trim() !== '' && form.fromEmail.trim() !== '') {
        markStepCompleted('smtp');
      }
    }
  }, [form, loadingConfig]);

  useEffect(() => {
    getSmtpConfig()
      .then((config) => {
        setForm((prev) => ({ ...prev, ...config }));
      })
      .catch(() => { /* use defaults */ })
      .finally(() => setLoadingConfig(false));
  }, []);

  const handleChange = <K extends keyof SmtpFormData>(field: K, value: SmtpFormData[K]) => {
    isDirtyRef.current = true;
    unmarkStepCompleted('smtp');
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitStatus('loading');
    setSmtp(form);

    try {
      await saveSmtpConfig(form);
      setSubmitStatus('success');
      onSuccess(null);
    } catch {
      setSubmitStatus('error');
    } finally {
      setSubmitting(false);
    }
  };

  // host and fromEmail are the two required SMTP fields
  const isFormValid = form.host.trim() !== '' && form.fromEmail.trim() !== '';

  if (loadingConfig) {
    return (
      <Box
        style={{
          backgroundColor: 'var(--gray-2)',
          border: '1px solid var(--gray-4)',
          borderRadius: 'var(--radius-3)',
          padding: '24px',
          width: '576px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '200px',
        }}
      >
        <Spinner size="3" />
      </Box>
    );
  }

  return (
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
      {/* Fixed Sub-header */}
      <Box
        style={{
          flexShrink: 0,
          padding: '24px 24px 16px',
          borderBottom: '1px solid var(--gray-4)',
        }}
      >
        <Text
          as="div"
          size="1"
          style={{ color: 'var(--gray-9)', marginBottom: '4px', letterSpacing: '0.02em' }}
        >
          System Configuration
        </Text>
        <Text
          as="div"
          size="4"
          weight="bold"
          style={{ color: 'var(--gray-12)' }}
        >
          Step {systemStepIndex}/{totalSystemSteps}: Configure SMTP*
        </Text>
      </Box>

      {/* Scrollable fields */}
      <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
        <Flex direction="column" gap="6">
        <InfoBanner message="This is a recommended step to enable emails, invites & password resets" />
        {/* SMTP Host */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            SMTP Host*
          </Text>
          <TextField.Root
            placeholder="eg: smtp.gmail.com"
            value={form.host}
            onChange={(e) => handleChange('host', e.target.value)}
            disabled={submitting}
          />
        </Flex>

        {/* Port */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Port
          </Text>
          <TextField.Root
            placeholder="eg: 587"
            value={String(form.port)}
            onChange={(e) => handleChange('port', Number(e.target.value))}
            disabled={submitting}
            type="number"
          />
        </Flex>

        {/* From Email */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            From Email*
          </Text>
          <TextField.Root
            placeholder="eg: noreply@company.com"
            value={form.fromEmail}
            onChange={(e) => handleChange('fromEmail', e.target.value)}
            disabled={submitting}
            type="email"
          />
        </Flex>

        {/* Username + Password row */}
        <Flex gap="2">
          <Flex direction="column" gap="1" style={{ flex: 1 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Username
            </Text>
            <TextField.Root
              placeholder="eg: user@gmail.com"
              value={form.username}
              onChange={(e) => handleChange('username', e.target.value)}
              disabled={submitting}
            />
          </Flex>
          <Flex direction="column" gap="1" style={{ flex: 1 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Password
            </Text>
            <TextField.Root
              type={showPassword ? 'text' : 'password'}
              placeholder="Enter Password"
              value={form.password}
              onChange={(e) => handleChange('password', e.target.value)}
              disabled={submitting}
            >
              <TextField.Slot side="right">
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '0',
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <span
                    className="material-icons-outlined"
                    style={{ fontSize: '14px', color: 'var(--gray-9)' }}
                  >
                    {showPassword ? 'visibility_off' : 'visibility'}
                  </span>
                </button>
              </TextField.Slot>
            </TextField.Root>
          </Flex>
        </Flex>

        </Flex>
      </Box>

      {/* Fixed footer: Save button */}
      <Box style={{ flexShrink: 0, padding: '0 24px 24px' }}>
        <Button
          onClick={handleSubmit}
          disabled={submitting || !isFormValid}
          style={{
            width: '100%',
            backgroundColor: submitting || !isFormValid ? 'var(--gray-4)' : 'var(--accent-9)',
            color: submitting || !isFormValid ? 'var(--gray-9)' : 'white',
            cursor: submitting || !isFormValid ? 'not-allowed' : 'pointer',
            height: '40px',
            opacity: 1,
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
    </Box>
  );
}
