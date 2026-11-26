#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Builds a separate network namespace "ap2" with its own LAN IP (macvlan).
# nqptp, avahi and shairport-sync run in it. That keeps UDP 319/320 free
# for Music Assistant, which also uses them for AirPlay 2.

set -u
mkdir -p /run/ap2
: > /run/ap2/netns_exec

AP2_IP="$(bashio::config 'ap2_ip')"
AP2_GW="$(bashio::config 'ap2_gateway')"
AP2_IF="$(bashio::config 'ap2_interface')"

if bashio::var.is_empty "${AP2_IP}"; then
  bashio::log.warning "No ap2_ip set: AirPlay runs on the host IP."
  bashio::log.warning "If Music Assistant uses AirPlay 2, ports 319/320 will clash."
  exit 0
fi

if bashio::var.is_empty "${AP2_IF}"; then
  # Interface of the default route with the lowest metric
  AP2_IF="$(awk 'NR>1 && $2=="00000000" {print $7, $1}' /proc/net/route \
            | sort -n | head -n1 | cut -d' ' -f2)"
fi
if bashio::var.is_empty "${AP2_IF}"; then
  bashio::log.error "Could not find a LAN interface. Set ap2_interface."
  exit 1
fi

if bashio::var.is_empty "${AP2_GW}"; then
  AP2_GW="$(ip -4 route show default dev "${AP2_IF}" | awk '{print $3; exit}')"
fi

bashio::log.info "Creating netns ap2: ${AP2_IP} on ${AP2_IF} (gateway ${AP2_GW:-none})"

# Clean up leftovers from an earlier start
ip netns del ap2 2>/dev/null || true
ip link del ap2mv 2>/dev/null || true

if ! { ip netns add ap2 \
    && ip link add ap2mv link "${AP2_IF}" type macvlan mode bridge \
    && ip link set ap2mv netns ap2 \
    && ip -n ap2 link set lo up \
    && ip -n ap2 addr add "${AP2_IP}" dev ap2mv \
    && ip -n ap2 link set ap2mv up; }; then
  bashio::log.error "macvlan setup failed. Is HA connected by cable? Falling back to the host IP."
  ip netns del ap2 2>/dev/null || true
  ip link del ap2mv 2>/dev/null || true
  exit 0
fi

if ! bashio::var.is_empty "${AP2_GW}"; then
  ip -n ap2 route add default via "${AP2_GW}" dev ap2mv || \
    bashio::log.warning "Could not set a default route in ap2."
fi

echo "ip netns exec ap2" > /run/ap2/netns_exec
echo "ap2mv" > /run/ap2/interface
bashio::log.info "netns ap2 ready."
