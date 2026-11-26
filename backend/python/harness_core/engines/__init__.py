"""Engine layer: types, events, ports and registry (ARCHITECTURE §4.1).

An adapter speaks one engine's protocol; the registry picks an adapter for a
pinned, allow-listed engine version. Controller and kernel live elsewhere and
depend only on these ports.
"""
