import type { ScaledPosition } from "react-pdf-highlighter";

import type { Citation } from "./chat-bot";

export interface PdfHighlighterCompProps {
  pdfUrl: string;
  citations: Citation[];
  initialHighlights?: Citation[];
}

export interface Comment {
  text: string;
  emoji: string;
}

export interface Position {
  boundingRect: BoundingRect;
  rects: BoundingRect[];
  pageNumber: number;
}

export interface BoundingRect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  width: number;
  height: number;
  pageNumber?: number;
}

export interface Content {
  text?: string;
  image?: string;
 
}

export interface HighlightType {
  id: string;
  content: any;
  position: Position;
  comment: Comment;
}

export interface HighlightPopupProps {
  comment?: Comment;
}

export interface ProcessedCitation extends Citation {
  highlight: HighlightType | null;
}

export interface BoundingBox {
  x: number;
  y: number;
}

export type OnSelectionFinished = (
  position: ScaledPosition,
  content: Content,
  hideTipAndSelection: () => void,
  transformSelection: () => void
) => JSX.Element;

export type HighlightTransform = (
  highlight: HighlightType,
  index: number,
  setTip: (highlight: HighlightType, callback: () => JSX.Element) => void,
  hideTip: () => void,
  viewportToScaled: (boundingRect: BoundingRect) => BoundingRect,
  screenshot: (boundingRect: BoundingRect) => string,
  isScrolledTo: boolean
) => JSX.Element;