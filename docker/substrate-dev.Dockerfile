# Doppelgänger NS-3 substrate — dev overlay for Task #8 PFC fix.
# Layers a locally-modified powertcp-evaluation-burst.cc on top of the
# pinned doppelganger-substrate image and runs an incremental waf build.
# This is iteration-only; do not pin Doppelgänger's official Dockerfile to
# this. Once the fix lands on provandal/ns3-datacenter, substrate.Dockerfile
# gets re-pinned to the new upstream SHA.
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

# Temporary diagnostic logging in switch-mmu.cc — reverted before commit.
COPY ${LOCAL_NS3}/simulator/ns-3.39/src/point-to-point/model/switch-mmu.cc \
     /opt/ns3-datacenter/simulator/ns-3.39/src/point-to-point/model/switch-mmu.cc

WORKDIR /opt/ns3-datacenter/simulator/ns-3.39

# Incremental rebuild — should hit the cache for everything except the
# changed example file.
RUN ./waf build

WORKDIR /work
CMD ["/bin/bash"]
