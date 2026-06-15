"""
runtime_threads.py — Cap ML library thread fan-out before any import of torch,
sentence_transformers, docling, or numpy.

Must be the first import in every service entrypoint. Values already set in
the environment are never overwritten (setdefault), so operators can override
via their .env or docker-compose file.
"""

import os

_n = os.environ.get("OMP_NUM_THREADS", "2")  # honour pre-set value if any

os.environ.setdefault("OMP_NUM_THREADS", _n)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _n)
os.environ.setdefault("MKL_NUM_THREADS", _n)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
