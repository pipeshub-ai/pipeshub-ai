"""Capacity profile: what this container has, and how its services are sized from it.

Standard library only, so ``app.utils.runtime_threads`` can use it before ``torch``, ``numpy``
or ``docling`` load. It must not import ``app.services.resource_governor``: that package's
``__init__`` imports the controller.

- :mod:`.cgroup`: readers of the container's CPU and memory limits.
- :mod:`.facts`, :mod:`.sources`: the facts, read once per container start and inherited.
- :mod:`.knobs`: every operator-tunable capacity setting, with the class of its source.
- :mod:`.plan`, :mod:`.policy`: the plan (pure): ``plan_capacity(facts, env, roles)``.
- :mod:`.apply`: what a process does with its plan before heavy imports.
- :mod:`.cells`: one stats file per indexing worker process, merged for ``/health``.
"""
