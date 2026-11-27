import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';
import type { KnowledgeLimitValue } from '@/app/components/knowledge-limit';

vi.mock('@/config', () => ({
  EnterpriseFeatureNotice: ({ title }: { title: string }) => <div data-testid="notice">{title}</div>,
}));

import {
  ClientCredentialsKnowledgeLimitField,
  clientCredentialsKnowledgeLimit,
} from '../client-credentials-knowledge-limit';

const text = en.knowledgeLimit.app.clientCredentials;
const saved: KnowledgeLimitValue = { apps: ['a1', 'a2'], kb: [], recordGroups: [], records: [] };
const savedLimit = { apps: ['a1', 'a2'], recordGroups: [], records: [] };

function renderField(value: KnowledgeLimitValue, onChange = vi.fn()) {
  render(
    <Theme>
      <ClientCredentialsKnowledgeLimitField appName="Support bot" value={value} onChange={onChange} idPrefix="t" />
    </Theme>,
  );
  return onChange;
}

describe('ClientCredentialsKnowledgeLimitField', () => {
  afterEach(cleanup);

  it('shows the notice and no picker', () => {
    renderField(null);

    expect(screen.getByTestId('notice').textContent).toBe(text.title);
    expect(screen.queryByLabelText(/Only what I pick/)).toBeNull();
    expect(screen.queryByTestId('kept-knowledge-limit')).toBeNull();
  });

  it('lets the app remove the limit it already has, and nothing else', () => {
    const onChange = renderField(saved);

    expect(screen.getByTestId('kept-knowledge-limit').textContent).toContain('2');
    fireEvent.click(screen.getByRole('button', { name: text.remove }));

    expect(onChange).toHaveBeenCalledWith(null);
    expect(screen.queryByLabelText(/Only what I pick/)).toBeNull();
  });
});

describe('clientCredentialsKnowledgeLimit', () => {
  it('creates an app with no limit, whatever was picked', () => {
    expect(clientCredentialsKnowledgeLimit.onCreate(saved)).toBeNull();
  });

  it('saves no limit, or the one the app already had with the grant', () => {
    expect(clientCredentialsKnowledgeLimit.problem(null, { hadClientCredentials: false })).toBeNull();
    expect(clientCredentialsKnowledgeLimit.problem(saved, { limit: savedLimit, hadClientCredentials: true })).toBeNull();
  });

  it('refuses a new or changed limit, and a limit kept while gaining the grant', () => {
    const changed: KnowledgeLimitValue = { ...saved, apps: ['a1'] };
    expect(clientCredentialsKnowledgeLimit.problem(changed, { limit: savedLimit, hadClientCredentials: true })).toBe(
      'knowledgeLimit.app.clientCredentials.removeToSave',
    );
    expect(clientCredentialsKnowledgeLimit.problem(saved, { limit: savedLimit, hadClientCredentials: false })).toBe(
      'knowledgeLimit.app.clientCredentials.removeToSave',
    );
  });
});
