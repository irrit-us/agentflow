# Codex CLI image. Build on top of the AgentFlow base image.
FROM agentflow-base:bookworm-slim

RUN apt-get update \
    && curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --no-fund --no-audit @openai/codex \
    && npm cache clean --force

WORKDIR /workspace
CMD ["/bin/bash"]
