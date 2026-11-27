"""Runs a module of this tree's backend/python (or a script) in python:3.12-slim without the app image.

The tree is mounted at /app/python and a Python 3.12 venv's site-packages at /site. ``addsitedir``
(not a plain path entry) so the venv's ``.pth`` files apply as they do in the image.
"""

import runpy
import site
import sys

sys.path.insert(0, "/app/python")
site.addsitedir("/site")
target, sys.argv = sys.argv[1], sys.argv[1:]
if target.endswith(".py"):
    runpy.run_path(target, run_name="__main__")
else:
    runpy.run_module(target, run_name="__main__", alter_sys=True)
