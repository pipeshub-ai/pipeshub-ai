import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/app/(main)/feedback/components/send-feedback-dialog', () => ({
  SendFeedbackDialog: () => <div data-testid="send-feedback-dialog" />,
}));
vi.mock('@/app/(main)/support/components/get-support-dialog', () => ({
  GetSupportDialog: () => <div data-testid="contact-support-dialog" />,
}));

import { AppLayoutExtras } from '../app-layout-extras';

describe('AppLayoutExtras', () => {
  it('mounts the Send feedback and Contact support dialogs once', () => {
    render(<AppLayoutExtras />);
    expect(screen.getAllByTestId('send-feedback-dialog')).toHaveLength(1);
    expect(screen.getAllByTestId('contact-support-dialog')).toHaveLength(1);
  });
});
