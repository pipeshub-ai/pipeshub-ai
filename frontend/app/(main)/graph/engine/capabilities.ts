import type { Capabilities } from './tiers';

/** Reads what the browser can do. Run once on the client; the pure choice is in tiers.ts. */
export function probeCapabilities(): Capabilities {
  const caps: Capabilities = {
    hasWebgl2: false,
    renderer: '',
    devicePixelRatio: window.devicePixelRatio || 1,
    reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  };
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2');
    if (gl) {
      caps.hasWebgl2 = true;
      const info = gl.getExtension('WEBGL_debug_renderer_info');
      caps.renderer = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : '';
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  } catch {
    // A browser that throws here has no usable GL: the canvas tier is right.
  }
  return caps;
}
