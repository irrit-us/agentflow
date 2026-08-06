# Sync utility nodes image: rsync/tar/ssh tooling for syncing to remote hosts.
FROM agentflow-base:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-client \
        rsync \
        tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
CMD ["/bin/bash"]
