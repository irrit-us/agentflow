# Pi coding agent image. Build on top of the AgentFlow base image.
#
# Pi requires Node.js >= 20, so install Node 22 from NodeSource instead of the
# older Debian bookworm distribution package.
FROM agentflow-base:bookworm-slim

RUN apt-get update \
    && curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --no-fund --no-audit --ignore-scripts @earendil-works/pi-coding-agent \
    && npm cache clean --force

WORKDIR /workspace
CMD ["/bin/bash"]
