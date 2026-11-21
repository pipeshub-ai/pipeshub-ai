# DEV-ONLY: the e2e/human stack's app image (run.sh builds it as pxh-e2e-app:<hash of this file>).
# The stack runs this tree's code from bind mounts on python:3.12-slim; this adds what the shipped image has
# for document conversion, so PPT/PPTX and legacy .doc previews convert here as they do in production
# (PPTXPREV: without it every presentation preview was a 500). The packages are the LibreOffice and font
# packages of Dockerfile.base's runtime-base plus the shipped Dockerfile's; the C-PPTXPREV cohesion test
# (tests/cohesion/test_pptxprev_e2e_image_converts.py) fails when the two lists drift.
# git: the worker's repository_fetch (WP-63) runs the distribution's git, as the shipped image does; without it every
# approved clone on the stack was refused "not available on this deployment" (WP-81).
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    libreoffice-impress-nogui \
    fonts-noto-cjk \
    git \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
# KEYRING-FU-1: an operator's command in this container runs as written for the shipped image, whose `python` sees
# the app's packages: a plain `python` here sees this tree's code mount and the venv's site-packages, as pyrun.py
# sets them up (addsitedir, so the venv's .pth files apply). Both are mounts; a path line for a missing one is skipped.
RUN printf '%s\n' '/app/python' 'import site; site.addsitedir("/site")' \
    > /usr/local/lib/python3.12/site-packages/pxh-e2e-site.pth
