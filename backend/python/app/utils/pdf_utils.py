"""Lightweight PDF helpers shared by the Docling client and the local processor.

Kept free of heavy Docling/converter dependencies so importers that only need
page-count/batching info (e.g. the external Docling HTTP client) don't pull in
the full conversion stack.
"""
import os

import pypdfium2 as pdfium

DEFAULT_PAGE_BATCH_SIZE = 10


def _get_page_batch_size() -> int:
    raw = os.getenv("DOCLING_PAGE_BATCH_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_PAGE_BATCH_SIZE


PAGE_BATCH_SIZE = _get_page_batch_size()


def get_pdf_page_count(content: bytes) -> int:
    """Return the number of pages in a PDF binary using pypdfium2."""
    pdf = pdfium.PdfDocument(content)
    try:
        return len(pdf)
    finally:
        pdf.close()


def calculate_exact_image_union_area(images: list[dict], page_width: float, page_height: float) -> float:
    """
    Calculate the exact union area of multiple image rectangles clipped to page bounds,
    avoiding double-counting overlapping regions.
    """
    rects = []
    for img in images:
        raw_x0 = float(img.get("x0", 0) or 0)
        raw_y0 = float(img.get("top", 0) or 0)
        
        # Fallbacks if x1/bottom are missing
        w = float(img.get("width", 0) or 0)
        h = float(img.get("height", 0) or 0)
        x1_raw = float(img.get("x1", raw_x0 + w) if img.get("x1") is not None else (raw_x0 + w))
        y1_raw = float(img.get("bottom", raw_y0 + h) if img.get("bottom") is not None else (raw_y0 + h))
        
        # Clip AFTER derivations
        x0 = max(0.0, raw_x0)
        y0 = max(0.0, raw_y0)
        x1 = min(float(page_width), x1_raw)
        y1 = min(float(page_height), y1_raw)
        
        if x1 > x0 and y1 > y0:
            rects.append((x0, y0, x1, y1))
            
    if not rects:
        return 0.0

    # Sweep-line algorithm for exact rectangle union area
    events = []
    for x0, y0, x1, y1 in rects:
        events.append((x0, 1, y0, y1))
        events.append((x1, -1, y0, y1))
    
    # Sort events by x. Tie-breaker: Left edges before right edges
    events.sort(key=lambda e: (e[0], -e[1]))
    
    def get_active_y_length(active_intervals: list[tuple[float, float]]) -> float:
        if not active_intervals:
            return 0.0
        # Sort intervals by y0
        active_intervals.sort(key=lambda i: i[0])
        y_length = 0.0
        current_y0, current_y1 = active_intervals[0]
        
        for y0, y1 in active_intervals[1:]:
            if y0 <= current_y1:
                current_y1 = max(current_y1, y1)
            else:
                y_length += (current_y1 - current_y0)
                current_y0, current_y1 = y0, y1
                
        y_length += (current_y1 - current_y0)
        return y_length

    total_area = 0.0
    active_intervals = []
    last_x = events[0][0]
    
    for x, typ, y0, y1 in events:
        total_area += (x - last_x) * get_active_y_length(active_intervals)
        if typ == 1:
            active_intervals.append((y0, y1))
        else:
            active_intervals.remove((y0, y1))
        last_x = x
        
    return total_area
