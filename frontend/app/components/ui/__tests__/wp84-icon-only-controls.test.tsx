import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { Toast } from '@/app/components/feedback/toast';
import { WorkspaceHeaderIconButton } from '@/app/(main)/workspace/components/workspace-header-icon-button';
import { ItemActionMenu } from '@/app/(main)/knowledge-base/components/item-action-menu';

// WP-84 (WP-80 open item 1): once MaterialIcon is aria-hidden, a control that holds only an icon has no name
// unless it gives itself one; before, its name was the icon's ligature ("close", "chevron_left").

afterEach(() => {
  cleanup();
});

const APP_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

// Not rendered anywhere (no importer): kept out of the guard rather than worded for a screen nobody reaches.
const UNUSED_FILES = new Set(['(main)/components/chat-input.tsx']);

const ICON_ONLY = new RegExp(
  String.raw`<(IconButton|button|Button|Link|a)\b((?:[^<>]|=>|\{[^{}]*\})*?)>\s*(?:\{[^{}]*\?\s*)?<MaterialIcon\b((?:[^<>]|=>)*?)/>\s*</\1>`,
  'g',
);

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' || entry.name === 'node_modules' ? [] : sourceFiles(full);
    return entry.name.endsWith('.tsx') ? [full] : [];
  });
}

/** Controls whose only content is an icon and that give themselves no name (nor hide themselves). */
function unnamedIconOnlyControls(): string[] {
  return sourceFiles(APP_DIR).flatMap((file) => {
    const rel = path.relative(APP_DIR, file);
    if (UNUSED_FILES.has(rel)) return [];
    const source = fs.readFileSync(file, 'utf8');
    return [...source.matchAll(ICON_ONLY)]
      .filter((match) => !/aria-label|aria-labelledby|aria-hidden|title=/.test(match[2] ?? ''))
      .map((match) => `${rel}:${source.slice(0, match.index).split('\n').length}`);
  });
}

/**
 * WP84-FU-4: a wrapper whose control takes both its icon and its name from what it is given (`name={icon}`,
 * `aria-label={label}`) passes ICON-5 on its own source while a caller may give it no name. Such a name must be a
 * required member of the type that carries it, so the compiler refuses a caller that leaves it out.
 */
function optionalWrapperNames(source: string): string[] {
  return [...source.matchAll(ICON_ONLY)].flatMap((match) => {
    const label = /aria-label=\{([A-Za-z_$][\w$]*)(?:\.([A-Za-z_$][\w$]*))?\}/.exec(match[2] ?? '');
    if (!label || !/\bname=\{[A-Za-z_$][\w$.]*\}/.test(match[3] ?? '')) return [];
    // `item.label` names the member `label`; a destructured `'aria-label': ariaLabel` names `aria-label`.
    const local = label[2] ?? label[1];
    const renamed = label[2] ? null : new RegExp(String.raw`['"]?([\w-]+)['"]?\s*:\s*${local}\b\s*[,}]`).exec(source);
    const member = renamed?.[1] ?? local;
    const declared = [...source.matchAll(new RegExp(String.raw`['"]?${member.replace(/[-$]/g, '\\$&')}['"]?\s*(\??):\s*[A-Za-z'"{(]`, 'g'))];
    const line = source.slice(0, match.index).split('\n').length;
    return declared.length === 0 || declared.some((d) => d[1] === '?') ? [`${member}@${line}`] : [];
  });
}

describe('icon-only controls', () => {
  it('WP84-ICON-5: every icon-only control names itself or is hidden as decoration', () => {
    expect(unnamedIconOnlyControls()).toEqual([]);
  });

  it('WP84-ICON-9: a wrapper that takes its icon and its name from its caller requires the name (WP84-FU-4)', () => {
    const found = sourceFiles(APP_DIR).flatMap((file) =>
      optionalWrapperNames(fs.readFileSync(file, 'utf8')).map((hit) => `${path.relative(APP_DIR, file)}:${hit}`),
    );
    expect(found).toEqual([]);
    // Not vacuous: each wrapper found where WP-84 checked by hand is seen, and flagged once its name is optional.
    for (const [file, required, optional] of [
      ['(main)/workspace/components/workspace-header-icon-button.tsx', "'aria-label': string", "'aria-label'?: string"],
      ['(main)/chat/components/message-area/expandable-user-query.tsx', 'ariaLabel: string', 'ariaLabel?: string'],
      ['(main)/workspace/skills/personal/components/markdown-editor.tsx', 'label: string;', 'label?: string;'],
    ] as const) {
      const source = fs.readFileSync(path.join(APP_DIR, file), 'utf8');
      expect(source.includes(required), file).toBe(true);
      expect(optionalWrapperNames(source.replace(required, optional)), file).toHaveLength(1);
    }
  });

  it('WP84-ICON-10: the wrapper check sees an optional name, a renamed prop and a member of an item', () => {
    const wrapper = (decl: string, body: string) => `interface P { icon: string; ${decl} }\nexport function W({ icon, ${body} }: P) {\n  return <button type="button" aria-label={${body.includes(':') ? 'ariaLabel' : 'label'}}><MaterialIcon name={icon} /></button>;\n}`;
    expect(optionalWrapperNames(wrapper('label?: string;', 'label'))).toEqual(['label@3']);
    expect(optionalWrapperNames(wrapper('label: string;', 'label'))).toEqual([]);
    expect(optionalWrapperNames(wrapper("'aria-label'?: string;", "'aria-label': ariaLabel"))).toEqual(['aria-label@3']);
    expect(optionalWrapperNames(wrapper("'aria-label': string;", "'aria-label': ariaLabel"))).toEqual([]);
    const items = (decl: string) => `const items: { icon: string; ${decl} }[] = [];\nitems.map((item) => <IconButton aria-label={item.label}><MaterialIcon name={item.icon} /></IconButton>);`;
    expect(optionalWrapperNames(items('label?: string'))).toEqual(['label@2']);
    expect(optionalWrapperNames(items('label: string'))).toEqual([]);
    // A name the control words itself (a translation) is no wrapper's to require.
    expect(optionalWrapperNames(`<button aria-label={t('x')}><MaterialIcon name={icon} /></button>`)).toEqual([]);
  });

  it('WP84-ICON-6: a toast\'s close button is named "Close", not "close" from the icon', () => {
    render(
      <Theme>
        <Toast
          toast={{ id: 't1', variant: 'info', title: 'Saved', showCloseButton: true } as never}
          onDismiss={() => {}}
        />
      </Theme>,
    );
    expect(screen.getByRole('button', { name: en.common.close })).toBeTruthy();
  });

  it('WP84-ICON-7: a workspace header icon button is named by its label', () => {
    render(<WorkspaceHeaderIconButton icon="refresh" onClick={() => {}} aria-label={en.action.refresh} />);
    expect(screen.getByRole('button', { name: en.action.refresh })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /refresh_|^refresh$/ })).toBeNull();
  });

  it('WP84-ICON-8: a row action menu whose caller names no label is still named, never "more_horiz"', () => {
    render(
      <Theme>
        <ItemActionMenu actions={[{ icon: 'folder_open', label: 'Open', onClick: () => {} }]} />
      </Theme>,
    );
    expect(screen.getByRole('button', { name: en.common.moreOptions })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'more_horiz' })).toBeNull();
  });
});
