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
        x0 = max(0.0, float(img.get("x0", 0) or 0))
        y0 = max(0.0, float(img.get("top", 0) or 0))
        
        # Fallbacks if x1/bottom are missing
        w = float(img.get("width", 0) or 0)
        h = float(img.get("height", 0) or 0)
        x1_raw = float(img.get("x1", x0 + w) if img.get("x1") is not None else (x0 + w))
        y1_raw = float(img.get("bottom", y0 + h) if img.get("bottom") is not None else (y0 + h))
        
        x1 = min(float(page_width), x1_raw)
        y1 = min(float(page_height), y1_raw)
        
        if x1 > x0 and y1 > y0:
            rects.append((x0, y0, x1, y1))
            
    if not rects:
        return 0.0

    # Coordinate compression (O(N^3) exact sweep)
    x_coords = sorted(list({x for r in rects for x in (r[0], r[2])}))
    y_coords = sorted(list({y for r in rects for y in (r[1], r[3])}))
    
    total_area = 0.0
    for i in range(len(x_coords) - 1):
        cx0, cx1 = x_coords[i], x_coords[i+1]
        if cx1 <= cx0:
            continue
        for j in range(len(y_coords) - 1):
            cy0, cy1 = y_coords[j], y_coords[j+1]
            if cy1 <= cy0:
                continue
                
            # Midpoint of the cell
            mx = (cx0 + cx1) / 2
            my = (cy0 + cy1) / 2
            
            # If midpoint is inside ANY rectangle, the entire cell is covered
            for r in rects:
                if r[0] <= mx <= r[2] and r[1] <= my <= r[3]:
                    total_area += (cx1 - cx0) * (cy1 - cy0)
                    break
                    
    return total_area
