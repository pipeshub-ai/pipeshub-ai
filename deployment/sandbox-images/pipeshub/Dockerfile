# `ph` and the /pipeshub contract (R3, OWD-6) as one thin layer over an engine's tools image that a host already has.
# The full build (engine-build.sh, each engine's Dockerfile) runs the same install.sh in its tools target; this one
# adds it to an image built before it without the engine's release asset or an admin's binary: the engine binary,
# its digest label and every other layer stay the base image's. Build with build.sh beside this file.
ARG BASE
FROM ${BASE}
ARG PH_CONTRACT
USER 0:0
# A base that already has a /pipeshub (built by hand from an older ph) gets this one in its place, whole.
RUN --mount=type=bind,source=.,target=/build-pipeshub \
    rm -rf /pipeshub /usr/local/bin/ph \
    && sh /build-pipeshub/install.sh \
    && test "$(cat /pipeshub/VERSION)" = "${PH_CONTRACT}" \
    && /usr/local/bin/ph --version
LABEL io.pipeshub.contract.version="${PH_CONTRACT}"
USER 1000:1000
