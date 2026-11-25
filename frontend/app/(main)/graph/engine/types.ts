import type { GraphModel } from '../model/graph-model';
import type { NodeId } from '../types';
import type { Quality } from './tiers';

export interface EngineHandlers {
  onSelect: (id: NodeId | null) => void;
  /** Double click, or Enter on the selected node. */
  onExpand: (id: NodeId) => void;
  /** `point` is where the pointer is, in the container's pixels. */
  onHover: (id: NodeId | null, point?: { x: number; y: number }) => void;
  /** One frame while the view is moving: `at` is a monotonic ms time, `ms` the frame's duration. */
  onFrame: (at: number, ms: number) => void;
}

/**
 * The renderer, behind one interface so the library can change without touching the
 * model, the store or the screens. The engine draws what the model holds and reports
 * input; it makes no decision about access, paging or what to fetch.
 */
export interface GraphEngine {
  mount: (container: HTMLElement, quality: Quality, handlers: EngineHandlers) => Promise<void>;
  /** Draw this model. Only the difference from the last one is applied. */
  setModel: (model: GraphModel) => void;
  setQuality: (quality: Quality) => void;
  select: (id: NodeId | null) => void;
  /** The node the view is centred on: drawn larger. */
  setFocus: (id: NodeId | null) => void;
  /** The container moved or changed size (a banner appeared above it): re-read where it is. */
  resize: () => void;
  /** Centre on a node, or fit everything when no id is given. */
  reveal: (id?: NodeId) => void;
  destroy: () => void;
}
