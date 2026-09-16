# ZCode CLI image. Build on top of the AgentFlow base image.
FROM agentflow-base:bookworm-slim

ARG ZCODE_VERSION=3.12.2
ARG ZCODE_DEB_SHA256=2157cc6d36264d7dd024905019dee7a5179603281e542f4f47fcf4a59bedd592

RUN apt-get update \
    && curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://cdn-zcode.z.ai/zcode/electron/releases/${ZCODE_VERSION}/linux-x64/ZCode-${ZCODE_VERSION}-linux-x64.deb" -o /tmp/zcode.deb \
    && echo "${ZCODE_DEB_SHA256}  /tmp/zcode.deb" | sha256sum -c - \
    && dpkg-deb -x /tmp/zcode.deb /tmp/zcode-package \
    && mkdir -p /opt/zcode \
    && mv /tmp/zcode-package/opt/ZCode/resources/glm /opt/zcode/glm \
    && chmod +x /opt/zcode/glm/zcode.cjs \
    && ln -s /opt/zcode/glm/zcode.cjs /usr/local/bin/zcode \
    && rm -rf /tmp/zcode.deb /tmp/zcode-package

WORKDIR /workspace
CMD ["/bin/bash"]
