# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM python:3.11-slim-bookworm AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    NER_STORAGE_PATH=/data \
    NER_CONFIG_PATH=/app/config.yml \
    NER_HOST=0.0.0.0 \
    NER_PORT=8000
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock .python-version ./
# Optional CA secret supports builds behind a private HTTPS proxy without
# putting that environment's certificate into the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --locked --no-dev --no-managed-python --link-mode=copy
RUN groupadd --gid 10001 ner && useradd --uid 10001 --gid ner --no-create-home ner \
    && mkdir /data && chown ner:ner /data
COPY api_server.py launcher.py LICENSE ./
COPY ner ./ner
COPY web ./web
COPY scripts/download_bergamot_model.py ./scripts/download_bergamot_model.py
COPY config_sample.yml ./config.yml
USER ner
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:' + os.environ.get('NER_PORT', '8000') + '/api/auth/session', timeout=3).close()"]
CMD ["python", "api_server.py"]

# Optional native engine; model weights are prepared separately and mounted.
FROM python:3.11-slim-bookworm AS bergamot-build
ARG BERGAMOT_REVISION=69455acaecbe8650cdba988dbcf7c10ca20e7c48
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    apt-get update && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git init /engine && git -C /engine remote add origin https://github.com/mozilla/translations.git \
    && git -C /engine fetch --depth 1 origin "$BERGAMOT_REVISION" \
    && git -C /engine checkout --detach FETCH_HEAD
COPY native /native
# Initialize only the pinned inference dependencies, including ONNX's Eigen.
# Marian unconditionally updates every submodule; remove that redundant command
# after initialization so it does not fetch training and WASM toolchains.
# GCC 12's AVX intrinsic maybe-uninitialized diagnostics remain warnings;
# upstream's other warnings-as-errors checks stay enabled.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    git -C /engine submodule update --init --recursive --depth 1 \
      inference/3rd_party/ssplit-cpp \
      inference/marian-fork/src/3rd_party/intgemm \
      inference/marian-fork/src/3rd_party/onnxjs \
      inference/marian-fork/src/3rd_party/sentencepiece \
    && sed -i '/^execute_process(COMMAND git submodule update --init --recursive --no-fetch$/,+1d' \
      /engine/inference/marian-fork/CMakeLists.txt \
    && sed -i '/^target_compile_options(marian PRIVATE ${ALL_WARNINGS})$/a target_compile_options(marian PRIVATE -Wno-error=maybe-uninitialized)' \
      /engine/inference/marian-fork/src/CMakeLists.txt \
    && cmake -S /engine/inference -B /engine/inference/build \
      -DCMAKE_BUILD_TYPE=Release -DUSE_WASM_COMPATIBLE_SOURCE=ON \
      -DCOMPILE_CPU=ON -DCOMPILE_CUDA=OFF -DUSE_INTGEMM=ON \
      -DSSPLIT_USE_INTERNAL_PCRE2=ON -DBUILD_ARCH=x86-64 -DGIT_SUBMODULE=OFF \
      -DCMAKE_PROJECT_INCLUDE=/native/bergamot_worker.cmake \
    && grep -Fq -- '-Wno-error=maybe-uninitialized' /engine/inference/build/marian-fork/src/CMakeFiles/marian.dir/flags.make \
    && cmake --build /engine/inference/build --target ner-bergamot-worker -j 2 \
    && mkdir /out && cp /engine/inference/build/ner-bergamot-worker /out/ \
    && rm -rf /engine/inference/build /engine/.git

FROM app AS bergamot
COPY --from=bergamot-build /out/ner-bergamot-worker /usr/local/bin/ner-bergamot-worker
# Include upstream sources and notices alongside the compiled optional engine.
COPY --from=bergamot-build /engine /usr/local/src/firefox-translations
COPY native /usr/local/src/ner-native

# Default image excludes the optional native build and model weights.
FROM app AS runtime
