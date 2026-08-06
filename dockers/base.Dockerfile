# Base image for AgentFlow container targets.
#
# Provides curl, git, wget, and Python 3.12. `python` resolves to the same
# interpreter as `python3` in the same directory (/usr/local/bin). Python 3.12
# comes from uv's standalone builds so every AgentFlow image shares one
# interpreter version (kimi-cli requires Python >= 3.12).
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://astral.sh/uv/install.sh -o /tmp/uv-install.sh \
    && sh /tmp/uv-install.sh \
    && rm /tmp/uv-install.sh

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV PATH="/root/.local/bin:${PATH}"

RUN uv python install 3.12 \
    && ln -s "$(uv python find 3.12)" /usr/local/bin/python3 \
    && ln -s /usr/local/bin/python3 /usr/local/bin/python

WORKDIR /workspace
CMD ["/bin/bash"]
