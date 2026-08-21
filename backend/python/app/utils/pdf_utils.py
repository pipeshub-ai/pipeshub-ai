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

    # Sweep-line algorithm for exact rectangle union area using a Segment Tree
    events = []
    y_vals = set()
    for x0, y0, x1, y1 in rects:
        events.append((x0, 1, y0, y1))
        events.append((x1, -1, y0, y1))
        y_vals.add(y0)
        y_vals.add(y1)
        
    if not events:
        return 0.0
    
    events.sort(key=lambda e: (e[0], -e[1]))
    y_list = sorted(list(y_vals))
    
    y_to_idx = {y: i for i, y in enumerate(y_list)}
    n = len(y_list)
    
    # Segment tree arrays
    count = [0] * (4 * n)
    length = [0.0] * (4 * n)
    
    def update(node: int, l: int, r: int, ql: int, qr: int, val: int):
        if ql >= r or qr <= l:
            return
        if ql <= l and r <= qr:
            count[node] += val
        else:
            mid = (l + r) // 2
            update(node * 2, l, mid, ql, qr, val)
            update(node * 2 + 1, mid, r, ql, qr, val)
            
        if count[node] > 0:
            length[node] = y_list[r] - y_list[l]
        else:
            if r - l == 1:
                length[node] = 0.0
            else:
                length[node] = length[node * 2] + length[node * 2 + 1]

    total_area = 0.0
    last_x = events[0][0]
    
    for x, typ, y0, y1 in events:
        total_area += (x - last_x) * length[1]
        idx0 = y_to_idx[y0]
        idx1 = y_to_idx[y1]
        if idx1 > idx0:
            update(1, 0, n - 1, idx0, idx1, typ)
        last_x = x
        
    return total_area
