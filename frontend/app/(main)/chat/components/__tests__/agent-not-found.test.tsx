import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { AgentNotFound } from '../agent-not-found';

afterEach(cleanup);

describe('agent not found', () => {
  it('says the agent is missing and offers the way back', () => {
    const onBack = vi.fn();
    render(<Theme><AgentNotFound onBack={onBack} /></Theme>);

    expect(screen.getByText("This agent doesn't exist or you no longer have access")).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back to chats' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
