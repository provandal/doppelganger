# Doppelgänger NS-3 substrate — dev overlay for substrate-iteration loops.
# Layers locally-modified substrate sources on top of the pinned
# doppelganger-substrate image and runs an incremental waf build.
# This is iteration-only; do not pin Doppelgänger's official Dockerfile to
# this. Once changes land on provandal/ns3-datacenter, substrate.Dockerfile
# gets re-pinned to the new upstream SHA.
#
# Active overlay: none. The base pin (640ea8d) now contains both the ECN
# counter emission (Stage 5a, 2026-05-08) and the per-port counter rollup
# (Stage 5a-realistic, 2026-05-09). Re-purpose this Dockerfile when the
# next substrate-side iteration starts.
#
# Build (from doppelganger/ root):
#   docker build -t doppelganger-substrate-dev \
#     -f docker/substrate-dev.Dockerfile \
#     --build-arg LOCAL_NS3=../ns3-datacenter ..
#
# (The build context is the workspace root so the COPY can reach the
# sibling ns3-datacenter directory.)

FROM doppelganger-substrate

ARG LOCAL_NS3=ns3-datacenter

COPY ${LOCAL_NS3}/simulator/ns-3.39/examples/PowerTCP/powertcp-evaluation-burst.cc \
     /opt/ns3-datacenter/simulator/ns-3.39/examples/PowerTCP/powertcp-evaluation-burst.cc

COPY ${LOCAL_NS3}/simulator/ns-3.39/examples/PowerTCP/config-burst.txt \
     /opt/ns3-datacenter/simulator/ns-3.39/examples/PowerTCP/config-burst.txt

COPY ${LOCAL_NS3}/simulator/ns-3.39/src/point-to-point/model/switch-node.h \
     /opt/ns3-datacenter/simulator/ns-3.39/src/point-to-point/model/switch-node.h

COPY ${LOCAL_NS3}/simulator/ns-3.39/src/point-to-point/model/switch-node.cc \
     /opt/ns3-datacenter/simulator/ns-3.39/src/point-to-point/model/switch-node.cc

WORKDIR /opt/ns3-datacenter/simulator/ns-3.39

# Incremental rebuild — should hit the cache for everything except the
# changed example file.
RUN ./waf build

WORKDIR /work
CMD ["/bin/bash"]
