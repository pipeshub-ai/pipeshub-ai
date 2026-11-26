/** R25FIX-FU-1: the card-fit measure the Playwright specs run in the page, on jsdom with stubbed boxes. */
import { afterEach, describe, expect, it } from 'vitest';
import { cardFitProblems, MIN_FIELD_PX } from './card-fit';

type Box = [left: number, width: number];

/** Builds `html` and gives each element its `data-box="left,width"` box (height 20) and layout width. */
function mount(html: string): Element {
  document.body.innerHTML = html;
  for (const el of Array.from(document.body.querySelectorAll<HTMLElement>('[data-box]'))) {
    const [left, width] = el.dataset.box!.split(',').map(Number) as Box;
    el.getBoundingClientRect = () => ({ left, right: left + width, width, top: 0, bottom: 20, height: 20, x: left, y: 0, toJSON: () => ({}) });
    Object.defineProperty(el, 'offsetWidth', { configurable: true, value: width });
  }
  return document.body.firstElementChild!;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('cardFitProblems', () => {
  it('CARDFIT-1: what stays inside the card is fine', () => {
    const card = mount('<div data-box="0,340"><div data-box="8,324"><input data-box="8,200" /><button data-box="216,80">Search</button></div></div>');
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([]);
  });

  it('CARDFIT-2: a field and button past the right edge are named once, by the outermost element', () => {
    const card = mount(
      '<div data-box="0,340"><div data-box="8,370" aria-label="Check for someone else"><input data-box="8,260" /><button data-box="276,100">Search</button></div></div>',
    );
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([
      { kind: 'overflow', what: '<div> "Check for someone else" Search', by: 38 },
    ]);
  });

  it('CARDFIT-3: overflow to the left counts too', () => {
    const card = mount('<div data-box="100,340"><span data-box="90,40">x</span></div>');
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([{ kind: 'overflow', what: '<span> x', by: 10 }]);
  });

  it('CARDFIT-4: a text field squeezed below a readable width is named; checkboxes are not fields', () => {
    const card = mount('<div data-box="0,340"><input placeholder="Not set" data-box="200,12" /><input type="checkbox" data-box="10,16" /></div>');
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([{ kind: 'squeezed', what: '<input> "Not set"', by: MIN_FIELD_PX - 12 }]);
  });

  it('CARDFIT-5: content held by a clipping box inside the card is measured against that box', () => {
    const card = mount('<div data-box="0,340"><div style="overflow-x: auto" data-box="0,300"><pre data-box="0,900">long</pre></div></div>');
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([]);
  });

  it('CARDFIT-6: hidden elements, aria-hidden form inputs and flow handles are not measured', () => {
    const card = mount(
      '<div data-box="0,340"><div style="display: none" data-box="0,900"></div><input aria-hidden="true" data-box="-20,16" />' +
        '<div class="react-flow__handle" data-box="-6,12"></div><div style="opacity: 0" data-box="0,900"></div></div>',
    );
    expect(cardFitProblems(card, MIN_FIELD_PX)).toEqual([]);
  });
});
