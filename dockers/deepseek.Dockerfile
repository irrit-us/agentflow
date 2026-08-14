# DeepSeek Harness CLI image. Build on top of the AgentFlow base image.
FROM agentflow-base:bookworm-slim

ARG DSH_REPOSITORY=https://github.com/irrit-us/deepseek-harness.git
ARG DSH_REF=1f9f79c71e28ac844a7d56eaa14432aa056b8706

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
