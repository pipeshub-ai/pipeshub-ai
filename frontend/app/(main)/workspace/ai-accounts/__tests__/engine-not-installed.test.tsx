/**
 * An engine the server has no image for (`engine_unavailable`) is not a setting anyone can turn on in AI settings:
 * the picker says so and names the command its operator runs, so the person reading it can act or pass it on.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { CodingEngineSelect } from '../components/coding-engine-select';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const select = (engines: Array<{ kind: string; available: boolean; reason: string | null }>) =>
  render(
    <Theme>
      <CodingEngineSelect
        label="Coding engine"
        helper="Where code tasks run"
        value="none"
        leading={[{ value: 'none', label: 'None' }]}
        engines={engines as never}
        onChange={() => {}}
      />
    </Theme>,
  );

describe('an engine not installed on this server', () => {
  it('is disabled as "Not installed on this server" and says who installs it, with the command', () => {
    select([
      { kind: 'codex', available: false, reason: 'engine_unavailable' },
      { kind: 'opencode', available: true, reason: null },
    ]);
    const option = screen.getByRole('option', { name: /Codex/ }) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
    expect(option.textContent).toContain('Not installed on this server');
    const note = screen.getByTestId('engine-not-installed-codex').textContent ?? '';
    expect(note).toContain("isn't a setting");
    expect(note).toContain('deployment/sandbox-images/engine-build.sh codex');
    expect(screen.queryByTestId('engine-not-installed-opencode')).toBeNull();
  });

  it('says nothing of the kind for an engine refused for another reason', () => {
    select([{ kind: 'codex', available: false, reason: 'engine_sandbox_missing' }]);
    expect(screen.queryByTestId('engine-not-installed-codex')).toBeNull();
  });
});
