# Voxframe container image.
#
# LICENSING NOTICE
# ----------------
# This image BUNDLES FFmpeg, which is GPL-licensed. The image as a whole is
# therefore distributed under GPL terms (see /LICENSING-NOTICE.txt inside).
#
# This does NOT apply to Voxframe itself: the source and the PyPI package are
# Apache 2.0 and carry no GPL obligation, because Voxframe invokes FFmpeg as a
# separate process rather than linking it. Only this image combines them.
#
# See DECISIONS.md D-003 and the README "Licensing" section.

FROM python:3.13-slim-bookworm

# ffmpeg pulls in libass; the rest are needed by opencv and torch at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy metadata first so dependency layers cache independently of source edits.
COPY pyproject.toml README.md LICENSE THIRD_PARTY_LICENSES ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

COPY <<'NOTICE' /LICENSING-NOTICE.txt
This container image bundles FFmpeg, which is licensed under the GPL.
The image as a whole is therefore distributed under GPL terms.

Voxframe's own source code is licensed under Apache 2.0 and carries no GPL
obligation. The distinction is that Voxframe invokes FFmpeg as a separate
process rather than linking against it; only this image combines the two into
a single distributable artifact.

Source for the FFmpeg build in this image:
  https://salsa.debian.org/multimedia-team/ffmpeg
  apt-get source ffmpeg

Voxframe source:
  https://github.com/Ngum12/voxframe

See /app/THIRD_PARTY_LICENSES for the full dependency audit.
NOTICE

# Models and renders belong on a mounted volume, not in the image.
ENV VOXFRAME_CACHE_PATH=/data/cache \
    VOXFRAME_LIBRARY_PATH=/data/library \
    VOXFRAME_OUTPUT_PATH=/data/output
VOLUME ["/data"]

# Fails fast if the image is broken; also a useful smoke test in CI.
HEALTHCHECK --interval=30s --timeout=10s --retries=2 \
    CMD voxframe doctor || exit 1

ENTRYPOINT ["voxframe"]
CMD ["--help"]
