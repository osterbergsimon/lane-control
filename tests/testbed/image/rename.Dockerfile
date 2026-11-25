# Second step of the testbed image: name the orcad slot by the content digest the slot itself reported (run up), and
# give the official release's launcher (`orca serve`) its own copies of the slot and node: serve finds its root by
# resolving its own path, so links to the slot would make it start wrongly.
ARG BASE
FROM ${BASE}
ARG VER
USER root
RUN set -e; a=/opt/orca/artifacts/linux-x64-glibc; old=$(cat /opt/orca/VERSION); \
    case $VER in 0.1.0+[0-9a-f]*) ;; *) echo "bad slot version $VER" >&2; exit 1 ;; esac; \
    if [ "$VER" != "$old" ]; then mv $a/$old $a/$VER; fi; echo $VER > $a/$VER/.version; echo $VER > /opt/orca/VERSION; \
    if [ -d /opt/orca/server ]; then cp -a $a/$VER /opt/orca/server/orcad; cp -a $a/runtimes /opt/orca/server/runtimes; fi
USER u
