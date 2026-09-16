# DeepSeek Harness CLI image. Build on top of the AgentFlow base image.
FROM agentflow-base:bookworm-slim

# Build the official Harness at the release tag that ships the native
# `--json` headless stream the adapter consumes. The earlier irrit-us fork
# patches (structured headless output, ddgr search fallback) are superseded by
# upstream and no longer needed: upstream exposes `--json` itself and owns its
# own web-search/fetch posture.
ARG DSH_REPOSITORY=https://github.com/deepseek-ai/deepseek-harness.git
ARG DSH_REF=0a15e36e7f82b6ed45af6fa9759f29b40dcd965d

RUN apt-get update \
    && curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends build-essential nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --no-fund --no-audit pnpm@11.7.0 \
    && git init /opt/deepseek-harness \
    && git -C /opt/deepseek-harness fetch --depth 1 "$DSH_REPOSITORY" "$DSH_REF" \
    && git -C /opt/deepseek-harness checkout --detach FETCH_HEAD \
    && pnpm --dir /opt/deepseek-harness install --frozen-lockfile \
    && pnpm --dir /opt/deepseek-harness run build:lib \
    && chmod +x /opt/deepseek-harness/apps/cli/lib/bin.js \
    && ln -s /opt/deepseek-harness/apps/cli/lib/bin.js /usr/local/bin/dsh \
    && apt-get purge -y --auto-remove build-essential \
    && npm cache clean --force

WORKDIR /workspace
CMD ["/bin/bash"]
