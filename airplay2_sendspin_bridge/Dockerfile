ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG SPS_VERSION=5.6.2
ARG NQPTP_REF=development
ARG AIOSENDSPIN_SPEC="aiosendspin[server]==11.0.0"
ARG PAHO_SPEC="paho-mqtt==2.1.0"
ARG BUILD_VERSION=dev

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1

# Proof-of-concept: one stage, the -dev packages stay installed.
# That makes the image bigger, but nobody has to guess runtime package names.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential git ca-certificates autoconf automake libtool pkg-config \
      libpopt-dev libconfig-dev libssl-dev libsoxr-dev \
      libavahi-client-dev avahi-daemon dbus \
      libplist-dev libplist-utils libsodium-dev libgcrypt20-dev uuid-dev xxd \
      libavutil-dev libavcodec-dev libavformat-dev libswresample-dev \
      iproute2 jq procps \
      python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

# nqptp – PTP clock for AirPlay 2 (needs UDP 319/320)
RUN git clone https://github.com/mikebrady/nqptp.git /tmp/nqptp \
 && cd /tmp/nqptp \
 && git checkout "${NQPTP_REF}" \
 && autoreconf -fi \
 && ./configure \
 && make -j"$(nproc)" \
 && make install \
 && rm -rf /tmp/nqptp

# shairport-sync with AirPlay 2 and stdout output; patched so each instance
# can report the real model and manufacturer (icon in the iOS AirPlay menu)
COPY patches /tmp/patches
RUN git clone --depth 1 --branch "${SPS_VERSION}" \
      https://github.com/mikebrady/shairport-sync.git /tmp/sps \
 && python3 /tmp/patches/sps_identity.py /tmp/sps \
 && cd /tmp/sps \
 && autoreconf -fi \
 && ./configure --prefix=/usr --sysconfdir=/etc \
      --with-airplay-2 --with-avahi --with-ssl=openssl --with-soxr \
      --with-stdout --with-pipe --with-metadata \
 && make -j"$(nproc)" \
 && make install \
 && rm -rf /tmp/sps

# Python bridge (aiosendspin needs Python >= 3.12; Debian trixie ships 3.13)
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install "${AIOSENDSPIN_SPEC}" "${PAHO_SPEC}"

ENV ADDON_VERSION=${BUILD_VERSION}

COPY app /app
COPY rootfs /
RUN chmod a+x /etc/cont-init.d/*.sh /etc/services.d/*/run \
 && mkdir -p /run/dbus

LABEL io.hass.type="addon" io.hass.arch="amd64|aarch64"
