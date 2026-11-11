import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { ForkedIntoLine, ForkOriginHeader, ForkOriginLine } from '../fork-origin-line';

afterEach(cleanup);

// next/link drops the trailing slash, so the path and the query are compared apart.
function hrefOf(link: HTMLElement) {
  const url = new URL(link.getAttribute('href') ?? '', 'http://localhost');
  return { path: url.pathname.replace(/\/$/, ''), params: Object.fromEntries(url.searchParams) };
}

const origin = { conversationId: 'orig-9', messageId: 'p-5', title: 'Quarterly numbers', atMessageId: 'c-2' };

describe('ForkOriginHeader', () => {
  it('says the chat was forked from the original and links to it', () => {
    render(<ForkOriginHeader origin={origin} agentId={null} projectId={null} />);

    expect(screen.getByText('Forked from')).toBeTruthy();
    const link = screen.getByRole('link', { name: 'Quarterly numbers' });
    expect(hrefOf(link)).toEqual({ path: '/chat', params: { conversationId: 'orig-9', messageId: 'c-2' } });
  });

  it('opens the original inside the same project', () => {
    render(<ForkOriginHeader origin={origin} agentId={null} projectId="proj-3" />);

    expect(hrefOf(screen.getByRole('link'))).toEqual({
      path: '/chat',
      params: { projectId: 'proj-3', conversationId: 'orig-9', messageId: 'c-2' },
    });
  });

  it('opens the original inside the same agent, ahead of a project', () => {
    render(<ForkOriginHeader origin={origin} agentId="agent-7" projectId="proj-3" />);

    expect(hrefOf(screen.getByRole('link'))).toEqual({
      path: '/chat',
      params: { agentId: 'agent-7', conversationId: 'orig-9', messageId: 'c-2' },
    });
  });

  it('gives no link, and says so, when the original cannot be opened', () => {
    render(<ForkOriginHeader origin={{ ...origin, title: null }} agentId={null} projectId={null} />);

    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText(/the original chat is no longer available/)).toBeTruthy();
  });

  it('still links to an original that has no title', () => {
    render(<ForkOriginHeader origin={{ ...origin, title: '' }} agentId={null} projectId={null} />);

    expect(hrefOf(screen.getByRole('link', { name: 'Untitled chat' }))).toEqual({
      path: '/chat',
      params: { conversationId: 'orig-9', messageId: 'c-2' },
    });
  });
});

describe('ForkOriginLine', () => {
  it('marks where the copy ends and the new messages begin, with no link', () => {
    render(<ForkOriginLine />);

    expect(screen.getByText('Copied up to here · new messages start below')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('ForkedIntoLine', () => {
  const forks = [
    { conversationId: 'f-1', title: 'Refund policy, EU only', fromMessageId: 'a1' },
    { conversationId: 'f-2', title: '', fromMessageId: 'a1' },
  ];

  it('says the chat was forked into others and links to each fork', () => {
    render(<ForkedIntoLine forks={forks} agentId={null} projectId={null} />);

    expect(screen.getByText(/Forked here/)).toBeTruthy();
    const links = screen.getAllByRole('link');
    expect(links.map((l) => hrefOf(l))).toEqual([
      { path: '/chat', params: { conversationId: 'f-1' } },
      { path: '/chat', params: { conversationId: 'f-2' } },
    ]);
    expect(links[0].textContent).toBe('Refund policy, EU only');
  });

  it('opens the forks inside the same agent, ahead of a project', () => {
    render(<ForkedIntoLine forks={forks.slice(0, 1)} agentId="agent-7" projectId="proj-3" />);

    expect(hrefOf(screen.getByRole('link'))).toEqual({
      path: '/chat',
      params: { agentId: 'agent-7', conversationId: 'f-1' },
    });
  });
});
