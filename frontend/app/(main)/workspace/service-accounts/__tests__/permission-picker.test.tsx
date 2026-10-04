/**
 * The permission picker in the create-token panel.
 *
 * An administrator choosing what an unattended credential may do should be
 * able to read what each permission allows, and should not have to tick
 * thirteen boxes one at a time when they have decided to grant all of them.
 */
import React from 'react';
import { Theme } from '@radix-ui/themes';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import en from '@/lib/i18n/locales/en-US.json';

vi.mock('@/lib/store/toast-store', () => {
  const addToast = vi.fn();
  const state = { addToast };
  return {
    useToastStore: (selector: (s: typeof state) => unknown) => selector(state),
  };
});

vi.mock('react-i18next', () => {
  const t = (key: string, vars?: Record<string, unknown>) => {
    const parts = key.split('.');
    let cur: unknown = en;
    for (const part of parts) {
      if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
      cur = (cur as Record<string, unknown>)[part];
    }
    if (typeof cur !== 'string') return key;
    return vars
      ? cur.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars[name] ?? ''))
      : cur;
  };
  const value = { i18n: { language: 'en-US' }, t };
  return { useTranslation: () => value };
});

const listTokens = vi.fn();
const getScopes = vi.fn();
const createToken = vi.fn();

vi.mock('../api', () => ({
  ServiceTokensApi: {
    list: (...a: unknown[]) => listTokens(...a),
    getScopes: (...a: unknown[]) => getScopes(...a),
    create: (...a: unknown[]) => createToken(...a),
    revoke: vi.fn(),
  },
}));

// Imported after the mocks so the panel picks them up.
const { ServiceAccountTokensPanel } = await import(
  '../components/service-account-tokens-panel'
);

const ACCOUNT = {
  id: 'account-1',
  slug: 'nightly',
  fullName: 'Nightly sync',
  email: 'svc-nightly-org@service.pipeshub.internal',
  isDisabled: false,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

const SCOPES = [
  {
    name: 'kb:read',
    description: 'Read knowledge bases and records',
    category: 'Knowledge Base',
    requiresUserConsent: true,
  },
  {
    name: 'semantic:write',
    description: 'Execute semantic search queries',
    category: 'Semantic',
    requiresUserConsent: true,
  },
  {
    name: 'user:read',
    description: 'Read user profiles',
    category: 'Users',
    requiresUserConsent: true,
  },
];

const tokensCopy = en.workspace.serviceAccounts.tokens;

function renderPanel() {
  return render(
    <Theme>
      <ServiceAccountTokensPanel open account={ACCOUNT} onOpenChange={vi.fn()} />
    </Theme>
  );
}

/** Opens the create-token form and waits for the permissions to arrive. */
async function openCreateForm() {
  fireEvent.click(await screen.findByText(tokensCopy.newToken));
  await screen.findByText('kb:read');
}

beforeEach(() => {
  listTokens.mockResolvedValue({ tokens: [] });
  getScopes.mockResolvedValue({ scopes: SCOPES });
  createToken.mockResolvedValue({
    token: {
      id: 'token-1',
      name: 'job',
      serviceAccountId: ACCOUNT.id,
      scopes: ['kb:read'],
      createdAt: new Date().toISOString(),
      expiresAt: new Date().toISOString(),
      accessToken: 'phsvc_stub',
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the permission picker', () => {
  it('says what each permission allows, not just its name', async () => {
    renderPanel();
    await openCreateForm();

    // The identifier is there for someone who knows it...
    expect(screen.getByText('semantic:write')).toBeTruthy();
    // ...and so is an explanation for someone who does not.
    expect(screen.getByText('Execute semantic search queries')).toBeTruthy();
    expect(screen.getByText('Read knowledge bases and records')).toBeTruthy();
  });

  it('groups the permissions under the category they belong to', async () => {
    renderPanel();
    await openCreateForm();

    expect(screen.getByText('Knowledge Base')).toBeTruthy();
    expect(screen.getByText('Semantic')).toBeTruthy();
    expect(screen.getByText('Users')).toBeTruthy();
  });

  it('starts with nothing selected, so granting access is deliberate', async () => {
    renderPanel();
    await openCreateForm();

    const boxes = screen.getAllByRole('checkbox');
    expect(boxes).toHaveLength(SCOPES.length);
    boxes.forEach((box) => expect(box.getAttribute('data-state')).toBe('unchecked'));
  });

  it('selects every permission at once, then offers to clear them', async () => {
    renderPanel();
    await openCreateForm();

    fireEvent.click(screen.getByText(tokensCopy.selectAllScopes));

    await waitFor(() =>
      screen
        .getAllByRole('checkbox')
        .forEach((box) => expect(box.getAttribute('data-state')).toBe('checked'))
    );
    // Having selected everything, the same control now clears it.
    expect(screen.getByText(tokensCopy.clearAllScopes)).toBeTruthy();
  });

  it('clears every permission again', async () => {
    renderPanel();
    await openCreateForm();

    fireEvent.click(screen.getByText(tokensCopy.selectAllScopes));
    fireEvent.click(await screen.findByText(tokensCopy.clearAllScopes));

    await waitFor(() =>
      screen
        .getAllByRole('checkbox')
        .forEach((box) => expect(box.getAttribute('data-state')).toBe('unchecked'))
    );
  });

  it('sends every selected permission when the token is created', async () => {
    renderPanel();
    await openCreateForm();

    fireEvent.change(screen.getByPlaceholderText(tokensCopy.namePlaceholder), {
      target: { value: 'Release digest job' },
    });
    fireEvent.click(screen.getByText(tokensCopy.selectAllScopes));
    fireEvent.click(screen.getByText(tokensCopy.mint));

    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
    const payload = createToken.mock.calls[0][0] as { scopes: string[] };
    expect([...payload.scopes].sort()).toEqual(
      SCOPES.map((s) => s.name).sort()
    );
  });
  it('reads whether everything is selected from the permissions themselves', async () => {
    // The panel refetches whenever it is told about a new account object, and
    // it only clears the selection when it closes. So the catalogue can be
    // replaced while a selection stands. Swapping one permission for another
    // keeps the count the same, and counting would call that "all selected"
    // and clear a selection the reader had not finished making.
    const view = renderPanel();
    await openCreateForm();

    fireEvent.click(screen.getByText(tokensCopy.selectAllScopes));
    expect(await screen.findByText(tokensCopy.clearAllScopes)).toBeTruthy();

    getScopes.mockResolvedValue({
      scopes: [
        ...SCOPES.slice(0, SCOPES.length - 1),
        {
          name: 'team:read',
          description: 'Read team information',
          category: 'Teams',
          requiresUserConsent: true,
        },
      ],
    });
    // A new object with the same contents, which is what makes the panel refetch.
    view.rerender(
      <Theme>
        <ServiceAccountTokensPanel open account={{ ...ACCOUNT }} onOpenChange={vi.fn()} />
      </Theme>
    );
    await screen.findByText('team:read');

    // Same number of permissions, but the replacement is not ticked, so the
    // control offers to select rather than to clear.
    expect(screen.getAllByRole('checkbox')).toHaveLength(SCOPES.length);
    expect(screen.getByText(tokensCopy.selectAllScopes)).toBeTruthy();
  });
});
