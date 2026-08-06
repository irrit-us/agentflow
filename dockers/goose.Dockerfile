# Goose CLI image. Build on top of the AgentFlow base image.
FROM agentflow-base:bookworm-slim

RUN curl -fsSL https://github.com/block/goose/releases/download/v1.45.0/goose-x86_64-unknown-linux-gnu.tar.gz -o /tmp/goose.tar.gz \
    && tar -xzf /tmp/goose.tar.gz -C /tmp \
    && mv /tmp/goose /usr/local/bin/goose \
    && rm /tmp/goose.tar.gz

WORKDIR /workspace
CMD ["/bin/bash"]
