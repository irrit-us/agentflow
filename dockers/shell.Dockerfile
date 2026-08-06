# Shell utility nodes image. bash already ships in the base image.
FROM agentflow-base:bookworm-slim

WORKDIR /workspace
CMD ["/bin/bash"]
