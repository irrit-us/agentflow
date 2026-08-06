# Kimi CLI image. Build on top of the AgentFlow base image.
#
# kimi-cli requires Python >= 3.12; `uv tool install` reuses the base image's
# Python 3.12 and keeps the CLI in an isolated venv.
FROM agentflow-base:bookworm-slim

RUN uv tool install --python 3.12 --no-cache kimi-cli==1.49.0

WORKDIR /workspace
CMD ["/bin/bash"]
