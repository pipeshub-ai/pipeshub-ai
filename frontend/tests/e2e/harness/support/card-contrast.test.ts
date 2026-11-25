/** R25UI-FU-1: the theme-class check the Playwright specs run on every card, on jsdom. */
import { afterEach, describe, expect, it } from 'vitest';
import { themeClassLeaks } from './card-contrast';

function card(html: string): Element {
  document.body.innerHTML = html;
  return document.querySelector('[data-card]')!;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('themeClassLeaks', () => {
  it('CONTRAST-1: a card under the page theme only is fine', () => {
    const el = card('<div class="radix-themes dark"><div data-card><span>Tool results</span></div></div>');
    expect(themeClassLeaks(el, 'dark')).toEqual([]);
  });

  it('CONTRAST-2: a light ancestor under a dark page is named (React Flow left at its default colour mode)', () => {
    const el = card('<div class="radix-themes dark"><div class="react-flow light"><div data-card>x</div></div></div>');
    expect(themeClassLeaks(el, 'dark')).toEqual(['<div.react-flow.light>']);
  });

  it('CONTRAST-3: a dark Theme inside a card on a light page is named', () => {
    const el = card('<div class="radix-themes light"><div data-card><div class="radix-themes dark">x</div></div></div>');
    expect(themeClassLeaks(el, 'light')).toEqual(['<div.radix-themes.dark>']);
  });

  it('CONTRAST-4: a Theme marked data-fixed-theme (a terminal, dark on purpose) is left to axe', () => {
    const el = card(
      '<div class="radix-themes light"><div data-card><div class="radix-themes dark" data-fixed-theme><b class="dark">$ ls</b></div></div></div>',
    );
    expect(themeClassLeaks(el, 'light')).toEqual([]);
  });

  it('CONTRAST-5: the html element is not counted (the provider writes the page theme there)', () => {
    document.documentElement.classList.add('light');
    try {
      const el = card('<div data-card>x</div>');
      expect(themeClassLeaks(el, 'light')).toEqual([]);
    } finally {
      document.documentElement.classList.remove('light');
    }
  });
});
