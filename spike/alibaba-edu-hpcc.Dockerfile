# Candidate Dockerfile for alibaba-edu/High-Precision-Congestion-Control (NS-3.17, Waf base)
# Backup candidate per _reviews/05_doppelganger_v0.2_updates_pending.md
#
# STARTING POINT WITH DOCUMENTED-BUG WORKAROUNDS PRE-APPLIED.
# Expect to still debug. The known issues are:
#   - Issue #4: Python 3 print-statement syntax error in wscript
#   - Issue #6: CommandLine const-qualifier error on Ubuntu 16.04 / gcc 5.4
#   - Issue #8: operator<< ambiguity on ns-3.30.1 / Ubuntu 20.04
# We use Ubuntu 20.04 + gcc 9 to dodge most of these. If you need to go further back
# (Ubuntu 18.04 + gcc 7), un-comment the alternate FROM line.
#
# To build:
#   docker build -t doppelganger-spike-hpcc -f alibaba-edu-hpcc.Dockerfile .
#
# To run:
#   docker run -it --rm -v $(pwd):/work doppelganger-spike-hpcc bash
#
# Expected build time on cold cache: 15–30 minutes.
# Expected image size: 3–5 GB.

FROM ubuntu:20.04
# Alternate (more conservative) base — uncomment if 20.04 fights:
# FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# HPCC's NS-3.17 era was tested against gcc 5–7 era toolchains.
# 20.04 ships gcc 9 by default, which works for most things if -Werror is off.
# If 20.04 fights, fall back to 18.04 (which has gcc 7 default).
#
# Critical: install Python 2 alongside Python 3 because HPCC's wscript was written for Py2 print statements.
# We'll work around the print issue (issue #4) by patching wscript at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    git \
    pkg-config \
    sqlite3 libsqlite3-dev \
    libxml2 libxml2-dev \
    libboost-all-dev \
    libgtk-3-dev \
    python3 python3-dev python3-pip \
    python2 \
    autoconf \
    gdb \
    ca-certificates \
    wget curl \
    vim nano \
    sed \
 && rm -rf /var/lib/apt/lists/*

# Python 2 is required by HPCC's Waf glue
RUN ln -sf /usr/bin/python2 /usr/bin/python || true

WORKDIR /opt
RUN git clone https://github.com/alibaba-edu/High-Precision-Congestion-Control.git hpcc

WORKDIR /opt/hpcc/simulation

# Workaround for issue #4 (Python 3 print-statement bug in wscript):
# If wscript uses Py2 print syntax, patch it. The repo defaults to Py2,
# so if you're on a Py3-only system the wscript fails at parse time.
# Simplest workaround: ensure /usr/bin/python is python2. Already done above.
# Also patch any obvious print-without-parens just in case:
RUN if [ -f wscript ]; then \
      sed -i 's/print \([^(].*\)$/print(\1)/g' wscript || true; \
    fi

# Workaround for issue #8 (operator<< ambiguity):
# Disable -Werror so warnings don't fail the build.
# Configure with --disable-werror.
#
# Workaround for issue #6 (CommandLine const-qualifier):
# This is a gcc-version-specific issue; gcc 9 on 20.04 typically handles it OK.
# If you hit it, the fix is to remove the `const` qualifier from the affected
# CommandLine method signature in src/core/model/command-line.h. We don't
# pre-apply that here because the line numbers vary; do it interactively if needed.

# Configure NS-3
RUN CXXFLAGS="-Wno-error -Wno-deprecated -Wno-deprecated-declarations" \
    ./waf configure --build-profile=optimized --disable-werror \
    || (echo "configure failed; you may need to patch wscript or fall back to ubuntu:18.04 base" && exit 1)

# Build. This is the slow step.
RUN CXXFLAGS="-Wno-error -Wno-deprecated -Wno-deprecated-declarations" \
    ./waf build \
    || (echo "build failed; common causes: operator<< ambiguity (issue #8), CommandLine const (issue #6)" && exit 1)

WORKDIR /work
CMD ["/bin/bash"]

# Once you're inside:
# 1. The HPCC sim entry point is in /opt/hpcc/simulation/src/point-to-point/...
#    or run via /opt/hpcc/simulation/scratch/third.cc-style scratch programs.
# 2. The traffic_gen/ directory has Python tools for generating traffic patterns.
# 3. Run the basic HPCC example via waf:
#      ./waf --run "scratch/third"
# 4. Output files: HPCC writes flow-completion-time and queue-depth files to a results/ dir
#    or a path configured in the example program. Look for *.txt or *.csv in cwd after a run.
# 5. Copy a representative trace file to /work and parse it from the host with run_spike.py.
