#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

if declare -F bashio::app.version > /dev/null; then
    export ADDON_VERSION="$(bashio::app.version)"
else
    export ADDON_VERSION="$(bashio::addon.version)"
fi
export DEVICE_ADDRESS="$(bashio::config 'device_address')"
export DEVICE_PORT="$(bashio::config 'device_port')"
export MODE="$(bashio::config 'mode')"
export POLL_INTERVAL="$(bashio::config 'poll_interval')"
export EXPERT_MODE="$(bashio::config 'expert_mode')"
export LOG_LEVEL="$(bashio::config 'log_level')"
export CONTROLLER_HARD_ID="$(bashio::config 'controller_hard_id')"
export CONTROLLER_SOFT_ID="$(bashio::config 'controller_soft_id')"
export DEVICE_HARD_ID="$(bashio::config 'device_hard_id')"
export DEVICE_SOFT_ID="$(bashio::config 'device_soft_id')"
export MQTT_TOPIC_PREFIX="$(bashio::config 'mqtt_topic_prefix')"
export MQTT_DISCOVERY_PREFIX="$(bashio::config 'mqtt_discovery_prefix')"

# MQTT: explicit settings win, otherwise use the broker Home Assistant provides
# (Mosquitto add-on).
if bashio::config.has_value 'mqtt_host'; then
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port')"
    export MQTT_USER="$(bashio::config 'mqtt_user')"
    export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
elif bashio::services.available 'mqtt'; then
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
else
    bashio::exit.nok "No MQTT broker: install the Mosquitto broker add-on or set mqtt_host"
fi

bashio::log.info "MQTT broker: ${MQTT_HOST}:${MQTT_PORT}"

cd /app || bashio::exit.nok "Application directory missing"
exec python3 -u /app/main.py
