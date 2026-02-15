#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# Read add-on options
export DEVICE_ADDRESS=$(bashio::config 'device_address')
export DEVICE_PORT=$(bashio::config 'device_port')
export MODE=$(bashio::config 'mode')
export POLL_INTERVAL=$(bashio::config 'poll_interval')
export LOG_LEVEL=$(bashio::config 'log_level')
export CONTROLLER_HARD_ID=$(bashio::config 'controller_hard_id')
export CONTROLLER_SOFT_ID=$(bashio::config 'controller_soft_id')
export DEVICE_HARD_ID=$(bashio::config 'device_hard_id')
export DEVICE_SOFT_ID=$(bashio::config 'device_soft_id')
export MQTT_TOPIC_PREFIX=$(bashio::config 'mqtt_topic_prefix')
export MQTT_DISCOVERY_PREFIX=$(bashio::config 'mqtt_discovery_prefix')

# MQTT: prefer user config, fall back to HA service discovery
MQTT_HOST_CFG=$(bashio::config 'mqtt_host')
if [ -n "$MQTT_HOST_CFG" ]; then
    export MQTT_HOST="$MQTT_HOST_CFG"
    export MQTT_PORT=$(bashio::config 'mqtt_port')
    export MQTT_USER=$(bashio::config 'mqtt_user')
    export MQTT_PASSWORD=$(bashio::config 'mqtt_password')
else
    export MQTT_HOST=$(bashio::services mqtt "host")
    export MQTT_PORT=$(bashio::services mqtt "port")
    export MQTT_USER=$(bashio::services mqtt "username")
    export MQTT_PASSWORD=$(bashio::services mqtt "password")
fi

bashio::log.info "Starting Hewalex PCWU add-on..."
bashio::log.info "  Device: ${DEVICE_ADDRESS}:${DEVICE_PORT}"
bashio::log.info "  Mode: ${MODE}"
bashio::log.info "  MQTT: ${MQTT_HOST}:${MQTT_PORT}"
bashio::log.info "  Poll interval: ${POLL_INTERVAL}s"

exec python3 /app/main.py
