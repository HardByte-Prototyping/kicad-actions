# gltfpack (meshoptimizer), for pcb_output_glb_optimize_mesh. Built from a
# pinned tag rather than fetched as a release archive, so the published image
# carries no prebuilt blob and the version is a source reference a reader can
# check out. The stage is discarded; only the binary is copied forward.
FROM debian:bookworm-slim AS gltfpack

ARG MESHOPTIMIZER_VERSION=v1.2

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
       ca-certificates cmake g++ git make \
    && rm -rf /var/lib/apt/lists/*

# The C++ runtime is linked statically because the binary is copied into the
# KiCad image, which is a different distribution with a different libstdc++.
# That costs about a megabyte and removes the question entirely.
RUN git clone --depth 1 --branch "$MESHOPTIMIZER_VERSION" \
      https://github.com/zeux/meshoptimizer.git /src \
    && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
       -DMESHOPT_BUILD_GLTFPACK=ON \
       -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
    && cmake --build /build --target gltfpack --parallel \
    && strip /build/gltfpack

FROM kicad/kicad:10.0-full

USER root

# cwebp, for pcb_output_image_webp. kicad-cli renders PNG and JPEG only, so the
# WebP is converted from the render; libwebp's encoder is the whole dependency
# and it is a few hundred kilobytes.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends webp \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gltfpack /build/gltfpack /usr/local/bin/gltfpack

COPY entrypoint.sh /entrypoint.sh
COPY glb/ /glb/
COPY img/ /img/

ENTRYPOINT ["/entrypoint.sh"]
