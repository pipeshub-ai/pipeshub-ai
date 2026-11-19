import type { Core, StylesheetJson } from 'cytoscape';
import type { GraphModel } from '../model/graph-model';
import { DEFAULT_COLOUR, KIND_COLOUR } from './colours';
import { capModel, diffModel } from './model-diff';
import type { Quality } from './tiers';
import type { EngineHandlers, GraphEngine } from './types';

// Node colours by kind (./colours), and a dashed edge when the link is inferred by name. Colour is
// never the only cue: the label, the tooltip and the table twin all name the kind.

export interface EngineTheme {
  /** Label colour and the halo behind it, so a line never strikes through a name. */
  text: string;
  background: string;
}

const LIGHT: EngineTheme = { text: '#1c2220', background: '#ffffff' };

/** The explorer's colours from the page's own theme (Radix variables), with light defaults. */
export function readTheme(el: Element | null): EngineTheme {
  if (!el || typeof getComputedStyle !== 'function') return LIGHT;
  const css = getComputedStyle(el);
  const pick = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return { text: pick('--gray-12', LIGHT.text), background: pick('--color-background', LIGHT.background) };
}

export function buildStyle(theme: EngineTheme = LIGHT): StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        width: 14,
        height: 14,
        'font-size': 11,
        'text-valign': 'center',
        'text-halign': 'right',
        'text-margin-x': 6,
        'background-color': DEFAULT_COLOUR,
        'min-zoomed-font-size': 7,
        color: theme.text,
        'text-background-color': theme.background,
        'text-background-opacity': 0.85,
        'text-background-padding': '1px',
        // Cytoscape's default grey disc under a grabbed node is noise on top of the texture.
        'overlay-opacity': 0,
      },
    },
    ...Object.entries(KIND_COLOUR).map(([kind, colour]) => ({
      selector: `node[kind = "${kind}"]`,
      style: { 'background-color': colour },
    })),
    // Classes and interfaces are squares: hue alone must not be what tells them from functions.
    { selector: 'node[kind = "class"], node[kind = "interface"]', style: { shape: 'round-rectangle' } },
    // Applied while the view moves when labels are hidden in motion (Cytoscape 3.34 types
    // `hideLabelsOnViewport` but does not implement it, so the class does the work).
    { selector: 'node.moving', style: { label: '' } },
    { selector: 'node.focus', style: { width: 22, height: 22, 'border-width': 3, 'border-color': theme.text, 'font-weight': 'bold', 'font-size': 13 } },
    { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#a86f00' } },
    {
      selector: 'edge',
      style: {
        width: 1.2,
        'curve-style': 'straight',
        'line-color': '#7b8985',
        'target-arrow-color': '#7b8985',
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.8,
        'overlay-opacity': 0,
      },
    },
    { selector: 'edge[inferred = 1]', style: { 'line-style': 'dashed' } },
  ] as StylesheetJson;
}

export interface CytoscapeEngineOptions {
  /** Tests only: no container, no canvas. */
  headless?: boolean;
  /** Tests only: the Cytoscape instance, once mounted. */
  expose?: (cy: Core) => void;
}

const IDLE_MS = 250;

/**
 * The Canvas tier: Cytoscape's 2D canvas with texture-on-viewport while the view moves.
 * Loaded on demand so the explorer's chunk, not the app's first load, carries it.
 * The GPU tier is this engine until a hardware run shows Cytoscape's WebGL mode is
 * faster and draws the same labels, arrows and dashes.
 */
export function createCytoscapeEngine(options: CytoscapeEngineOptions = {}): GraphEngine {
  let cy: Core | null = null;
  let drawn: GraphModel | null = null;
  /** The whole model last given, so a change of cap can redraw. */
  let latest: GraphModel | null = null;
  let handlers: EngineHandlers | null = null;
  let quality: Quality | null = null;
  let sampler = 0;
  let lastMotion = 0;
  /** Our own animated fits are not the person moving the picture: they do not count towards the frame meter. */
  let ignoreMotionUntil = 0;
  /** Kept so a redraw, a remount of the model or a resize keeps the ring and the focus mark. */
  let selected: string | null = null;
  let focused: string | null = null;
  let stopWatching: (() => void) | null = null;
  /** destroy() ran: a mount still waiting for the library must not build anything afterwards. */
  let destroyed = false;

  const applyQuality = (q: Quality) => {
    quality = q;
    if (!cy) return;
    // Cytoscape's canvas renderer reads these at draw time; the pixel ratio it forces is `forcedPixelRatio`.
    const renderer = (cy as unknown as { renderer: () => Record<string, unknown> }).renderer();
    if (renderer) {
      renderer.hideEdgesOnViewport = !q.edgesInMotion;
      renderer.forcedPixelRatio = q.pixelRatio;
    }
    cy.resize();
  };

  // Frame times only while the view moves; an idle page runs no loop. Labels are
  // dropped for the length of the motion when the quality asks for it.
  const sampleFrames = () => {
    if (!quality?.labelsInMotion) cy?.nodes().addClass('moving');
    if (sampler) return;
    let last = performance.now();
    const tick = (now: number) => {
      handlers?.onFrame(now, now - last);
      last = now;
      if (performance.now() - lastMotion < IDLE_MS) {
        sampler = requestAnimationFrame(tick);
      } else {
        sampler = 0;
        cy?.nodes().removeClass('moving');
      }
    };
    sampler = requestAnimationFrame(tick);
  };

  return {
    async mount(container, q, h) {
      const { default: cytoscape } = await import('cytoscape');
      if (destroyed) return;
      handlers = h;
      quality = q;
      cy = cytoscape({
        container: options.headless ? undefined : container,
        headless: options.headless,
        style: buildStyle(readTheme(container)),
        layout: { name: 'preset' },
        textureOnViewport: true,
        hideEdgesOnViewport: !q.edgesInMotion,
        pixelRatio: q.pixelRatio,
        minZoom: 0.1,
        maxZoom: 1.5, // a view of two nodes is fitted, not blown up to three times their size
        boxSelectionEnabled: false,
        autounselectify: false,
      });
      options.expose?.(cy);
      cy.on('tap', 'node', (e) => h.onSelect(e.target.id()));
      cy.on('tap', (e) => {
        if (e.target === cy) h.onSelect(null);
      });
      cy.on('dbltap', 'node', (e) => h.onExpand(e.target.id()));
      cy.on('mouseover', 'node', (e) => h.onHover(e.target.id(), e.renderedPosition));
      cy.on('mouseout', 'node', () => h.onHover(null));
      cy.on('viewport drag', () => {
        if (performance.now() < ignoreMotionUntil) return;
        lastMotion = performance.now();
        sampleFrames();
      });
      // Cytoscape caches where its container is. A banner appearing above it moves the container
      // without changing the window, so the cache is refreshed whenever the container resizes or the
      // pointer comes back to it, or clicks land far from where the picture is.
      if (!options.headless && typeof ResizeObserver !== 'undefined') {
        const refresh = () => cy?.resize();
        const observer = new ResizeObserver(refresh);
        observer.observe(container);
        // Where the container sits can change with no resize (a banner appears above it while the pointer is
        // inside): the position is compared on every move, and Cytoscape is told when it differs.
        let top = NaN;
        let left = NaN;
        const onMove = () => {
          const r = container.getBoundingClientRect();
          if (r.top !== top || r.left !== left) {
            top = r.top;
            left = r.left;
            refresh();
          }
        };
        container.addEventListener('pointermove', onMove);
        container.addEventListener('pointerenter', onMove);
        stopWatching = () => {
          observer.disconnect();
          container.removeEventListener('pointermove', onMove);
          container.removeEventListener('pointerenter', onMove);
        };
      }
    },

    setModel(whole) {
      if (!cy) return;
      latest = whole;
      const model = capModel(whole, quality?.hardCap ?? Infinity);
      const diff = diffModel(drawn, model);
      // A new view (Open, a filter change, a link) is fitted into the picture; later additions are not.
      const first = !drawn || Object.keys(drawn.nodes).length === 0 || drawn.view !== model.view;
      cy.batch(() => {
        for (const id of diff.removeIds) cy!.getElementById(id).remove();
        cy!.add(diff.addNodes);
        cy!.add(diff.addEdges);
        for (const { id, data } of diff.updateNodes) cy!.getElementById(id).data(data);
        for (const { id, position } of diff.moveNodes) cy!.getElementById(id).position(position);
      });
      drawn = model;
      this.select(selected);
      this.setFocus(focused);
      if (diff.addNodes.length === 0 && diff.removeIds.length === 0 && diff.moveNodes.length === 0) return;
      const duration = quality?.simplified || quality?.animate === false ? 0 : 200;
      ignoreMotionUntil = performance.now() + duration + 150;
      if (first) {
        cy.fit(undefined, 40);
      } else if (diff.addNodes.length > 0) {
        // The picture stays where it is unless some new node would be off screen: then everything is
        // brought into view, so what was just asked for is never somewhere the person cannot see.
        const view = cy.extent();
        const outside = diff.addNodes.some((n) => {
          const p = n.position as { x: number; y: number };
          return p.x < view.x1 || p.x > view.x2 || p.y < view.y1 || p.y > view.y2;
        });
        if (outside) cy.animate({ fit: { eles: cy.elements(), padding: 40 }, duration });
      } else {
        // Something was removed: close the gap it left.
        cy.animate({ fit: { eles: cy.elements(), padding: 40 }, duration });
      }
    },

    setQuality(q) {
      const capChanged = quality?.hardCap !== q.hardCap;
      applyQuality(q);
      if (capChanged && latest) this.setModel(latest);
    },

    select(id) {
      selected = id;
      if (!cy) return;
      cy.elements(':selected').unselect();
      if (id) cy.getElementById(id).select();
    },

    setFocus(id) {
      focused = id;
      if (!cy) return;
      cy.nodes('.focus').removeClass('focus');
      if (id) cy.getElementById(id).addClass('focus');
    },

    resize() {
      cy?.resize();
    },

    reveal(id) {
      if (!cy) return;
      if (id) cy.animate({ center: { eles: cy.getElementById(id) }, duration: quality?.simplified || quality?.animate === false ? 0 : 150 });
      else cy.fit(undefined, 40);
    },

    destroy() {
      destroyed = true;
      if (sampler) cancelAnimationFrame(sampler);
      sampler = 0;
      stopWatching?.();
      stopWatching = null;
      cy?.destroy();
      cy = null;
      drawn = null;
      latest = null;
      selected = null;
      focused = null;
      handlers = null;
    },
  };
}
