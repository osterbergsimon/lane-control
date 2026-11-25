# Second step of the testbed image: name the orcad slot by the content digest the slot itself reported (run up).
ARG BASE
FROM ${BASE}
ARG VER
USER root
RUN set -e; a=/opt/orca/artifacts/linux-x64-glibc; old=$(cat /opt/orca/VERSION); \
    case $VER in 0.1.0+[0-9a-f]*) ;; *) echo "bad slot version $VER" >&2; exit 1 ;; esac; \
    if [ "$VER" != "$old" ]; then mv $a/$old $a/$VER; fi; echo $VER > $a/$VER/.version; echo $VER > /opt/orca/VERSION
USER u
