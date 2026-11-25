/** QAFIX4: a `/run` answer ran no model, so its footer names no mode and no model; a model's answer still does. */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

// MessageActions forks through the app router (main #4225); these tests render no router.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';

const MODEL = { modelKey: 'k1', modelName: 'gpt-6-luna', chatMode: 'quick' };

beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

describe('QAFIX4 the footer of a /run answer', () => {
  it('names no model and no mode', () => {
    renderInTheme(<ChatResponse question="/run echo hi" answer="Exit code 0." modelInfo={MODEL} />);
    expect(screen.queryByText('gpt-6-luna')).toBeNull();
    expect(screen.queryByText('Quick')).toBeNull();
  });

  it('a model answer still names them', () => {
    renderInTheme(<ChatResponse question="echo hi" answer="Hi." modelInfo={MODEL} />);
    expect(screen.getByText('gpt-6-luna')).toBeTruthy();
  });
});
