/**
 * Icons stay empty boxes until their font has loaded, never their ligature's letters: the inline script marks each
 * icon font ready on <html> once the browser reports it loaded, and marks both when the browser has no font API.
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { IconFontScript } from '../icon-font-script';

type Face = { family: string; status: string };

function run(fonts: unknown) {
  const html = renderToStaticMarkup(<IconFontScript />);
  const source = html.replace(/^<script>/, '').replace(/<\/script>$/, '');
  Object.defineProperty(document, 'fonts', { value: fonts, configurable: true });
  new Function(source)();
}

function fakeFonts(faces: Face[]) {
  const listeners: Array<() => void> = [];
  return {
    faces,
    forEach: (fn: (face: Face) => void) => faces.forEach(fn),
    addEventListener: (_type: string, fn: () => void) => listeners.push(fn),
    ready: new Promise<void>(() => undefined),
    fire: () => listeners.forEach((fn) => fn()),
  };
}

const classes = () => [...document.documentElement.classList].filter((c) => c.startsWith('icons-')).sort();

afterEach(() => {
  document.documentElement.classList.remove('icons-outlined-ready', 'icons-filled-ready');
});

describe('IconFontScript', () => {
  it('marks a font ready only once it has loaded, including after a later load', () => {
    const fonts = fakeFonts([
      { family: '"Material Symbols Outlined"', status: 'loading' },
      { family: 'Material Icons', status: 'loaded' },
      { family: 'Inter', status: 'loaded' },
    ]);
    run(fonts);
    expect(classes()).toEqual(['icons-filled-ready']);
    fonts.faces[0]!.status = 'loaded';
    fonts.fire();
    expect(classes()).toEqual(['icons-filled-ready', 'icons-outlined-ready']);
  });

  it('marks both ready where the browser has no font loading API', () => {
    run(undefined);
    expect(classes()).toEqual(['icons-filled-ready', 'icons-outlined-ready']);
  });
});
