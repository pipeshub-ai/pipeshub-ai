import { describe, expect, it } from 'vitest';
import { chooseTier, FrameMeter, qualityFor, type Capabilities } from '../engine/tiers';

const caps = (over: Partial<Capabilities> = {}): Capabilities => ({
  hasWebgl2: true,
  renderer: 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)',
  devicePixelRatio: 2,
  reducedMotion: false,
  ...over,
});

describe('chooseTier', () => {
  it('uses the GPU tier for a hardware renderer', () => {
    expect(chooseTier(caps())).toBe('gpu');
    expect(chooseTier(caps({ renderer: 'NVIDIA GeForce RTX 4060 Laptop GPU' }))).toBe('gpu');
  });

  it.each([
    ['ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)'],
    ['llvmpipe (LLVM 15.0.7, 256 bits)'],
    ['Microsoft Basic Render Driver'],
  ])('uses the canvas tier for a software renderer: %s', (renderer) => {
    expect(chooseTier(caps({ renderer }))).toBe('canvas');
  });

  it('uses the canvas tier when WebGL2 is missing', () => {
    expect(chooseTier(caps({ hasWebgl2: false }))).toBe('canvas');
  });

  it('works when the browser hides the renderer name', () => {
    expect(chooseTier(caps({ renderer: '' }))).toBe('gpu');
  });
});

describe('qualityFor', () => {
  it('gives the GPU tier full quality at the display pixel ratio', () => {
    const q = qualityFor(caps(), 'gpu', 0);
    expect(q).toMatchObject({ pixelRatio: 2, labelsInMotion: true, edgesInMotion: true, hardCap: 1000, tableOnly: false, simplified: false });
  });

  it('starts the canvas tier simplified: pixel ratio 1, nothing but nodes in motion', () => {
    const q = qualityFor(caps(), 'canvas', 0);
    expect(q).toMatchObject({ pixelRatio: 1, labelsInMotion: false, edgesInMotion: false, simplified: true });
  });

  it('steps down one thing at a time and ends at the table, never refusing the person', () => {
    const rungs = [0, 1, 2, 3, 4, 5].map((s) => qualityFor(caps(), 'gpu', s));
    expect(rungs.map((q) => q.labelsInMotion)).toEqual([true, false, false, false, false, false]);
    expect(rungs.map((q) => q.edgesInMotion)).toEqual([true, true, false, false, false, false]);
    expect(rungs.map((q) => q.pixelRatio)).toEqual([2, 2, 2, 1, 1, 1]);
    expect(rungs.map((q) => q.hardCap)).toEqual([1000, 1000, 1000, 1000, 500, 500]);
    expect(rungs.map((q) => q.tableOnly)).toEqual([false, false, false, false, false, true]);
    expect(qualityFor(caps(), 'gpu', 99).step).toBe(5);
  });

  it('a canvas tier steps down from its own floor: every step changes something, and the table is two steps away', () => {
    const rungs = [0, 1, 2, 3].map((s) => qualityFor(caps(), 'canvas', s));
    expect(rungs.map((q) => q.step)).toEqual([3, 4, 5, 5]);
    expect(rungs.map((q) => q.hardCap)).toEqual([1000, 500, 500, 500]);
    expect(rungs.map((q) => q.tableOnly)).toEqual([false, false, true, true]);
  });

  it('animates unless the person asked their system for reduced motion', () => {
    expect(qualityFor(caps(), 'gpu', 0).animate).toBe(true);
    expect(qualityFor(caps({ reducedMotion: true }), 'gpu', 0).animate).toBe(false);
    expect(qualityFor(caps({ reducedMotion: true }), 'canvas', 2).animate).toBe(false);
  });

  it('clamps an absurd pixel ratio', () => {
    expect(qualityFor(caps({ devicePixelRatio: 9 }), 'gpu', 0).pixelRatio).toBe(3);
    expect(qualityFor(caps({ devicePixelRatio: 0 }), 'gpu', 0).pixelRatio).toBe(1);
  });
});

describe('FrameMeter', () => {
  const feed = (meter: FrameMeter, from: number, count: number, ms: number) => {
    let stepped = false;
    for (let i = 0; i < count; i++) stepped = meter.observe(from + i * ms, ms) || stepped;
    return stepped;
  };

  it('stays put while frames are smooth', () => {
    expect(feed(new FrameMeter(), 0, 300, 16.7)).toBe(false);
  });

  it('steps down when over 15% of the last two seconds of frames took over 33 ms', () => {
    expect(feed(new FrameMeter(), 0, 100, 60)).toBe(true);
  });

  it('is not moved by one stall', () => {
    const meter = new FrameMeter();
    feed(meter, 0, 100, 16.7);
    expect(meter.observe(1_700, 400)).toBe(false);
  });

  it('waits for a real sample before judging', () => {
    expect(feed(new FrameMeter(), 0, 5, 200)).toBe(false);
  });

  it('does not step twice in one window', () => {
    const meter = new FrameMeter();
    expect(feed(meter, 0, 100, 60)).toBe(true);
    expect(feed(meter, 6_000, 20, 60)).toBe(false);
  });
});

describe('FrameMeter: stalls and freezes (review finding)', () => {
  it('is not moved by one hitch among the first frames', () => {
    const meter = new FrameMeter();
    let stepped = false;
    for (let i = 0; i < 25; i++) stepped = meter.observe(i * 17, i === 12 ? 120 : 16.7) || stepped;
    expect(stepped).toBe(false);
  });

  it('steps down at once for a frame over a second, because a frozen page gives too few frames to count', () => {
    expect(new FrameMeter().observe(0, 2_583)).toBe(true);
  });

  it('steps down for three stalls of over 250 ms in the window, but not for two', () => {
    const meter = new FrameMeter();
    expect(meter.observe(0, 300)).toBe(false);
    expect(meter.observe(400, 300)).toBe(false);
    expect(meter.observe(800, 300)).toBe(true);
  });
});
