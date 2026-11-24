// Which way the explorer draws, and how it simplifies itself to stay smooth.
// Pure logic over injected measurements: the renderer adapter feeds it frame times
// and acts on the answers. The explorer must work on a laptop with or without a GPU,
// so no tier is "unsupported": the last step is always the table twin.

export type Tier = 'gpu' | 'canvas';

export interface Capabilities {
  hasWebgl2: boolean;
  /** WEBGL_debug_renderer_info UNMASKED_RENDERER, or '' when the browser hides it. */
  renderer: string;
  devicePixelRatio: number;
  reducedMotion: boolean;
}

// A software rasteriser draws in software however it is reached.
const SOFTWARE_RENDERER = /swiftshader|llvmpipe|softpipe|software|microsoft basic render|basic render driver/i;

export function chooseTier(caps: Capabilities): Tier {
  return caps.hasWebgl2 && !SOFTWARE_RENDERER.test(caps.renderer) ? 'gpu' : 'canvas';
}

/** What the renderer is told to do at one rung of the ladder. */
export interface Quality {
  tier: Tier;
  /** Rung number: 0 is full quality. */
  step: number;
  pixelRatio: number;
  labelsInMotion: boolean;
  edgesInMotion: boolean;
  /** Most nodes drawn; past it the same action continues in the table. */
  hardCap: number;
  /** Past this, expansion groups neighbours by file. */
  softCap: number;
  /** Past the last rung: offer the table twin. */
  tableOnly: boolean;
  /** Animated fits and centring; off for a person who asked their system for reduced motion. */
  animate: boolean;
  /** Text for the quiet line "Simplified view for smooth movement", shown from step 1. */
  simplified: boolean;
}

export const SOFT_CAP = 300;
export const HARD_CAP = 1000;
export const DEGRADED_HARD_CAP = 500;
export const MAX_STEP = 5;
/** The rung a canvas tier starts on. */
const CANVAS_FLOOR = 3;

/**
 * The ladder, one rung at a time:
 * 0 full; 1 hide labels in motion; 2 hide edges in motion; 3 pixel ratio 1;
 * 4 lower node cap; 5 table only.
 * A canvas tier starts on rung 3 (with no GPU, edges and labels are hidden while moving and the
 * pixel ratio is 1): at 2x with no GPU, panning fell to 34 and 15.8 fps at 250 and 1,000 nodes.
 * Each step down from there is one more rung, so it always changes something: 4, then 5.
 */
export function qualityFor(caps: Capabilities, tier: Tier, step: number): Quality {
  const s = Math.min((tier === 'canvas' ? CANVAS_FLOOR : 0) + Math.max(0, step), MAX_STEP);
  const nativeRatio = Math.max(1, Math.min(caps.devicePixelRatio || 1, 3));
  return {
    tier,
    step: s,
    pixelRatio: s >= 3 ? 1 : nativeRatio,
    labelsInMotion: s < 1,
    edgesInMotion: s < 2,
    hardCap: s >= 4 ? DEGRADED_HARD_CAP : HARD_CAP,
    softCap: SOFT_CAP,
    tableOnly: s >= MAX_STEP,
    animate: !caps.reducedMotion,
    simplified: s >= 1,
  };
}

const WINDOW_MS = 2_000;
const SLOW_FRAME_MS = 33;
/** Share of frames over 33 ms that means the view is not coping (one hitch in 20 frames is 5%). */
const SLOW_SHARE = 0.15;
/** Frames this long are a stall on their own; a few in the window mean the view is not coping. */
const STALL_MS = 250;
const STALLS_TO_STEP = 3;
/** A frame this long steps down at once: at that speed there are not enough frames in a window to count. */
const FROZEN_MS = 1_000;
const MIN_FRAMES = 10;
/** The window must be at least this long before a share of slow frames is believed. */
const MIN_SPAN_MS = 1_500;

/**
 * Watches frame times and says when to step down one rung: when more than 15% of the frames in the last
 * 2 seconds (at least 10 frames over at least 1.5 s) took over 33 ms, or 3 frames in the window each took
 * over 250 ms, or one frame took over a second
 * (a frozen page gives too few frames to measure). One hitch alone does nothing. Steps down only, and no
 * faster than once per window; the person brings full quality back with the notice's button.
 */
export class FrameMeter {
  private frames: { at: number; ms: number }[] = [];
  private lastStepAt = -Infinity;

  /** `at` is a monotonic time in ms, `ms` the frame's duration. Returns true when the caller should step down. */
  observe(at: number, ms: number): boolean {
    this.frames.push({ at, ms });
    const from = at - WINDOW_MS;
    while (this.frames.length && this.frames[0].at < from) this.frames.shift();
    if (at - this.lastStepAt < WINDOW_MS) return false;
    const stalls = this.frames.filter((f) => f.ms > STALL_MS).length;
    let slow = ms >= FROZEN_MS || stalls >= STALLS_TO_STEP;
    if (!slow && this.frames.length >= MIN_FRAMES && at - this.frames[0].at >= MIN_SPAN_MS) {
      slow = this.frames.filter((f) => f.ms > SLOW_FRAME_MS).length / this.frames.length > SLOW_SHARE;
    }
    if (!slow) return false;
    this.lastStepAt = at;
    this.frames = [];
    return true;
  }
}
