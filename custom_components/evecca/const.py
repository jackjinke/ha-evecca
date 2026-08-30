"""Constants for the EVECCA integration."""

from datetime import timedelta

DOMAIN = "evecca"

BASE_URL = "https://whaleapp.evecca.cn:5707/test_v001"
APP_ID = 50774420
CLIENT_MODEL = "ios"
CLIENT_OS = "ios 26.6"
CLIENT_MACHINE = "iPhone17,2"

CONF_FAMILY_ID = "family_id"
CONF_FAMILY_NAME = "family_name"
CONF_HW_ID = "hw_id"
CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_MQTT_TOPIC = "mqtt_topic"

DPID_ACTION = 33619969
DPID_POSITION_SET = 33619970
DPID_POSITION_STATE = 50397285
DPID_RUN_STATE = 50397286
DPID_ONLINE = 50397298

ACTION_OPEN = 1
ACTION_CLOSE = 2
ACTION_STOP = 3
ACTION_OPEN_THEN_CLOSE = 4
ACTION_TILT_OPEN = 5

SCAN_INTERVAL = timedelta(minutes=5)
MQTT_KEEPALIVE = 60
REQUEST_TIMEOUT = 20
