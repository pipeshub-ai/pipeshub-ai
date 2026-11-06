/**
 * A sidebar that swaps what its scroll area lists (the chat rail's main, agent and project views) names it with
 * `contentKey`: the header and the footer stay the same elements, another key starts the scroll area as a new
 * element rather than moving the old one, and a list shown again opens where it was left.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import { SidebarBase } from '../sidebar-base';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

function rail(contentKey: string, list: string) {
  return (
    <Theme>
      <SidebarBase header={<div>Header</div>} footer={<div>Footer</div>} contentKey={contentKey}>
        <span>{list}</span>
      </SidebarBase>
    </Theme>
  );
}

const scrollArea = (list: string) => screen.getByText(list).parentElement as HTMLElement;

describe('SidebarBase contentKey', () => {
  it('another key: a new scroll area, the same header and footer', () => {
    const view = render(rail('main', 'Main chats'));
    const header = screen.getByText('Header');
    const footer = screen.getByText('Footer');
    const area = scrollArea('Main chats');

    view.rerender(rail('agent:a1', 'Agent chats'));

    expect(scrollArea('Agent chats')).not.toBe(area);
    expect(screen.getByText('Header')).toBe(header);
    expect(screen.getByText('Footer')).toBe(footer);
  });

  it('the same key: the same scroll area', () => {
    const view = render(rail('main', 'Main chats'));
    const area = scrollArea('Main chats');

    view.rerender(rail('main', 'Main chats'));

    expect(scrollArea('Main chats')).toBe(area);
  });
});

describe('SidebarBase remembers where each named list was left', () => {
  const scrollTo = (list: string, top: number) => {
    const area = scrollArea(list);
    area.scrollTop = top;
    fireEvent.scroll(area);
  };

  it('the rail mounted again (back from another page) opens its list where it was', () => {
    const first = render(rail('remember-main', 'Main chats'));
    scrollTo('Main chats', 120);
    first.unmount();

    render(rail('remember-main', 'Main chats'));

    expect(scrollArea('Main chats').scrollTop).toBe(120);
  });

  it('each view keeps its own place: main, an agent and main again', () => {
    const view = render(rail('views-main', 'Main chats'));
    scrollTo('Main chats', 200);

    view.rerender(rail('views-agent', 'Agent chats'));
    expect(scrollArea('Agent chats').scrollTop).toBe(0);
    scrollTo('Agent chats', 40);

    view.rerender(rail('views-main', 'Main chats'));
    expect(scrollArea('Main chats').scrollTop).toBe(200);
    view.rerender(rail('views-agent', 'Agent chats'));
    expect(scrollArea('Agent chats').scrollTop).toBe(40);
  });

  it('a sidebar without a key remembers nothing', () => {
    const plain = (list: string) => (
      <Theme>
        <SidebarBase header={<div>Header</div>}>
          <span>{list}</span>
        </SidebarBase>
      </Theme>
    );
    const first = render(plain('Rows'));
    scrollTo('Rows', 90);
    first.unmount();

    render(plain('Rows'));

    expect(scrollArea('Rows').scrollTop).toBe(0);
  });
});
