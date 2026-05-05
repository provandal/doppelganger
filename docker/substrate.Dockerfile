# Doppelgänger NS-3 substrate: provandal/ns3-datacenter (fork of inet-tub/ns3-datacenter, NS-3.39)
# Pinned at SHA 4dd55d89a46e742e505a92dc7873f82ded6db638 (validated by 2026-05-02 fork spike).
#
# To build:
#   docker build -t doppelganger-substrate -f docker/substrate.Dockerfile .
#
# To run interactively:
#   docker run -it --rm -v $(pwd):/work doppelganger-substrate bash
#
# Spike-validated cold-cache build: ~5 minutes wall-clock; image size ~1.23 GB.
# (Pre-spike 20–40 min / 4–6 GB estimates were too pessimistic.)

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# NS-3 build dependencies. NS-3.39 era requires:
# - gcc 9–12 (we'll get gcc 11 from 22.04)
# - Python 3.10+
# - cmake 3.13+ (we'll get 3.22 from 22.04, even though ns3-datacenter still uses Waf)
# - Boost 1.66+
# - Standard libxml2, sqlite3, pkg-config
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    cmake \
    git \
    pkg-config \
    sqlite3 libsqlite3-dev \
    libxml2 libxml2-dev \
    libgsl-dev \
    libboost-all-dev \
    libgtk-3-dev \
    python3 python3-dev python3-pip python3-venv \
    autoconf \
    cvs \
    bzr \
    unrar \
    gdb valgrind \
    uncrustify \
    doxygen graphviz imagemagick \
    texlive texlive-extra-utils texlive-latex-extra texlive-font-utils \
    dvipng latexmk \
    python3-sphinx dia \
    gsl-bin \
    libgslcblas0 \
    tcpdump \
    sqlite \
    libxml2-utils \
    cmake-data \
    ca-certificates \
    wget curl \
    vim nano \
 && rm -rf /var/lib/apt/lists/*

# Python packages used by ns3-datacenter examples per its README
RUN pip3 install --no-cache-dir \
    numpy \
    matplotlib \
    cycler \
    pandas

WORKDIR /opt

# Clone ns3-datacenter from the provandal pinned fork at the spike-validated SHA.
# provandal/ns3-datacenter is a fork of inet-tub/ns3-datacenter; pinning here ensures
# reproducible builds against the exact commit that the 2026-05-02 spike validated.
# (Master HEAD on inet-tub has been this SHA since 2025-06-15; matches what the spike ran.)
RUN git clone https://github.com/provandal/ns3-datacenter.git \
 && cd ns3-datacenter \
 && git checkout 4dd55d89a46e742e505a92dc7873f82ded6db638
WORKDIR /opt/ns3-datacenter

# The repo structure is `simulator/ns-3.39/` per its README
WORKDIR /opt/ns3-datacenter/simulator/ns-3.39

# Configure with optimized profile for spike speed
# Disable the heavy modules we don't need (lte, mesh, wave) to cut build time
# If this command fails, the first thing to check is python version + waf compatibility
RUN ./waf configure --build-profile=optimized --enable-examples --enable-tests \
        --disable-python \
        --disable-werror \
        --disable-modules=lte,mesh,wave,wifi,uan,wimax,energy,aodv,olsr,dsr,dsdv

# Build core + RDMA modules. This is the slow step.
RUN ./waf build

# Smoke test: NS-3 itself runs
RUN ./waf --run "hello-simulator" || echo "hello-simulator failed; check waf output above"

# Working directory for spike artifacts
WORKDIR /work

CMD ["/bin/bash"]

# Once you're inside:
# 1. Find an RDMA example: ls /opt/ns3-datacenter/simulator/ns-3.39/examples/PowerTCP/
#    or ls /opt/ns3-datacenter/simulator/ns-3.39/scratch/  (HPCC-style configs may live here)
# 2. Run an example: ./waf --run "<example-name>"
# 3. Look for output files in the cwd or a scratch/ subdirectory
# 4. Copy a representative trace file to /work and parse it from the host with run_spike.py
