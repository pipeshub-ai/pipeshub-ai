"""Cost classes and lane admission for heavy-format parses.

``cost`` estimates a parse from what is known at admission, ``admission`` is
the pure reserve/cap/aging policy over the class numbers, ``class_gate`` orders
the local waiters and reserves capacity from the member budget, and ``engines``
holds the Docling engine's concurrency control.
"""
