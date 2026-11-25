# The testbed's one image (tests/testbed/README.md). Built by `tests/testbed/run up` from a staged context that also
# holds a copy of the owner's Orca build (orca/), bd and opencode (bin/): nothing is mounted from the host.
ARG BASE=python:3.12-slim-bookworm
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      openssh-server openssh-client git jq procps iproute2 ca-certificates less tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3
ARG UID=1000
RUN useradd -m -u ${UID} -s /bin/bash lc && mkdir -p /forge && chown lc:lc /forge
COPY --chown=root:root orca/ /opt/orca/
COPY --chown=root:root bin/ /usr/local/bin/
COPY --chown=root:root systemctl entrypoint answer gh gateway.py /usr/local/lib/testbed/
RUN chmod -R a+rX /opt/orca /usr/local/lib/testbed && chmod 755 /usr/local/bin/* /usr/local/lib/testbed/* \
    && ln -sf /usr/local/lib/testbed/systemctl /usr/local/bin/systemctl \
    && ln -sf /usr/local/lib/testbed/systemctl /usr/local/bin/journalctl \
    && ln -sf /usr/local/lib/testbed/gh /usr/local/bin/gh \
    && ln -sf /usr/local/lib/testbed/answer /usr/local/bin/answer \
    && ln -sf /opt/orca/resources/bin/orca-ide /usr/local/bin/orca-ide
# The slot finds its node at ../runtimes/node-<sha>, as under ~/.orca-remote on a box. Its name is provisional: orcad
# accepts only a slot named by its own content digest, which `run up` asks the slot for (its profile preflight needs a
# PTY, which a build step lacks) and then renames it to (image/rename.Dockerfile).
RUN set -e; a=/opt/orca/artifacts/linux-x64-glibc; old=$(cat /opt/orca/VERSION); sha=$(cat $a/$old/.runtime-node); \
    mkdir -p $a/runtimes/node-$sha/bin; ln /opt/orca/resources/node-runtime/bin/node $a/runtimes/node-$sha/bin/node
USER lc
WORKDIR /home/lc
ENV ORCA_ARTIFACTS=/opt/orca/artifacts TESTBED_FORGE=/forge PATH=/home/lc/.local/bin:/home/lc/.agents/bin:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/lib/testbed/entrypoint"]
