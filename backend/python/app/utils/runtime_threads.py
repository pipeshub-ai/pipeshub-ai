"""
runtime_threads.py — Cap ML library thread fan-out before any import of torch,
sentence_transformers, docling, or numpy.

Must be the first import in every service entrypoint. In this order it:

1. deletes every capacity knob (``app.capacity.knobs``) whose value is blank, so ``KEY=${KEY:-}`` in
   compose and an empty value in a Helm chart read as "not set";
2. builds the capacity plan (``app.capacity``, standard library only);
3. ``setdefault``s the plan's thread env for ``$PIPESHUB_SERVICE``.

Values already set in the environment are never overwritten, so operators can override
via their .env or docker-compose file.
"""

from app.capacity.apply import announce, apply_process_env

announce(apply_process_env())
