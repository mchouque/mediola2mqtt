#!/usr/bin/env python
# (c) 2021 Andreas Böhler
# License: Apache 2.0

import os
import sys
import socket
import json
import yaml
import requests
import paho.mqtt.client as mqtt

# Define MQTT event callbacks
def on_connect(client, userdata, flags, rc):
    connect_statuses = {
        0: "Connected",
        1: "incorrect protocol version",
        2: "invalid client ID",
        3: "server unavailable",
        4: "bad username or password",
        5: "not authorised"
    }
    print("MQTT: " + connect_statuses.get(rc, "Unknown error"))

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print("Unexpected disconnection")
    else:
        print("Disconnected")

def on_message(client, obj, msg):
    print("Msg: " + ' '.join(msg.topic, str(msg.qos), str(msg.payload)))
    # Here we should send a HTTP request to Mediola to open the blind
    dtype, addr = msg.topic.split("_")
    dtype = dtype[dtype.rfind("/")+1:]
    addr = addr[:addr.find("/")]
    print(dtype)
    print(addr)
    sub_identifier = None
    if '-' in addr:
        addr, sub_identifier = addr.split('-')
    for blind in config['blinds']:
        if dtype != blind['type'] or addr != blind['addr']:
            continue

        if sub_identifier:
            if sub_identifier == 'doubleup':
                data = addr + "0A"
            elif sub_identifier == 'doubledown':
                data = addr + "0B"
            else:
                return
        elif msg.payload == b'open':
            if dtype == 'RT':
                data = "20" + addr
            elif dtype == 'ER':
                data = addr + "01"
            else:
                return
        elif msg.payload == b'close':
            if dtype == 'RT':
                data = "40" + addr
            elif dtype == 'ER':
                data = addr + "00"
            else:
                return
        elif msg.payload == b'stop':
            if dtype == 'RT':
                data = "10" + addr
            elif dtype == 'ER':
                data = addr + "02"
            else:
                return
        else:
            print("Wrong command")
            return

        payload = {
          "XC_FNC" : "SendSC",
          "type" : dtype,
          "data" : data
        }
        url = 'http://' + config['mediola']['host'] + '/command'
        response = requests.get(url, params=payload, headers={'Connection':'close'})
        print(response)

def on_publish(client, obj, mid):
    print("Pub: " + str(mid))

def on_subscribe(client, obj, mid, granted_qos):
    print("Subscribed: " + str(mid) + " " + str(granted_qos))

def on_log(client, obj, level, string):
    print(string)

def publish_button(button, sub_identifier=None, sub_name=None):
    identifier = button['type'] + '_' + button['addr']
    if sub_identifier:
        identifier += '-' + sub_identifier
    dtopic = config['mqtt']['disCovery_prefix'] + '/device_automation/' + \
             identifier + '/config'
    topic = config['mqtt']['topic'] + '/buttons/' + identifier
    if 'name' in button['type']:
        name = button['name']
    else:
        name = "Mediola Button"
    if sub_name:
        name += ' ' + sub_name

    payload = {
      "automation_type" : "trigger",
      "topic" : topic,
      "type" : "button_short_press",
      "subtype" : "button_1",
      "name" : name,
      "device" : {
        "identifiers" : identifier,
        "manufacturer" : "Mediola",
        "name" : "Mediola Button",
      },
    }
    payload = json.dumps(payload)
    mqttc.publish(dtopic, payload=payload, retain=True)

def publish_blind(blind):
    identifier = blind['type'] + '_' + blind['addr']
    dtopic = config['mqtt']['discovery_prefix'] + '/cover/' + \
             identifier + '/config'
    topic = config['mqtt']['topic'] + '/blinds/' + identifier
    if 'name' in blind:
        name = blind['name']
    else:
        name = "Mediola Blind"

    payload = {
      "command_topic" : topic + "/set",
      "payload_open" : "open",
      "payload_close" : "close",
      "payload_stop" : "stop",
      "optimistic" : True,
      "device_class" : "blind",
      "unique_id" : identifier,
      "name" : name,
      "device" : {
        "identifiers" : identifier,
        "manufacturer" : "Mediola",
        "name" : "Mediola Blind",
      },
    }
    if blind['type'] == 'ER':
        payload["state_topic"] = topic + "/state"

    payload = json.dumps(payload)
    mqttc.subscribe(topic + "/set")
    mqttc.publish(dtopic, payload=payload, retain=True)

config_files = [
#        ['/data/options.json', 'Running in hass.io add-on mode'],
        ['/config/mediola2mqtt.yaml', 'Running in legacy add-on mode'],
        ['mediola2mqtt.yaml', 'Running in local mode'],
    ]
config = None

for config_file, comment in config_files:
    if not os.path.isfile(config_file):
        continue
    print(comment)
    with open('/data/options.json', 'r') as fp:
        config = json.load(fp)
    break

if not config:
    print('Configuration file not found, exiting.')
    sys.exit(1)

# Setup MQTT connection
mqttc = mqtt.Client()

mqttc.on_connect = on_connect
mqttc.on_subscribe = on_subscribe
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message

if config['mqtt']['debug']:
    print("Debugging messages enabled")
    mqttc.on_log = on_log
    mqttc.on_publish = on_publish

if config['mqtt']['username'] and config['mqtt']['password']:
    mqttc.username_pw_set(config['mqtt']['username'], config['mqtt']['password'])
try:
    mqttc.connect(config['mqtt']['host'], config['mqtt']['port'], 60)
except:
    print('Error connecting to MQTT, will now quit.')
    sys.exit(1)
mqttc.loop_start()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', config['mediola']['udp_port']))

# Set up discovery structure

if 'buttons' in config:
    # Buttons are configured as MQTT device triggers
    for button in config['buttons']:
        payload = publish_button(button)

if 'blinds' in config:
    for blind in config['blinds']:
        payload = publish_blind(blind)

        # ER blinds have double tap up and down which tell the blind to go to
        # preset settings. So we create two buttons for these
        if blind['type'] != 'ER':
            continue
        payload = get_button_payload(blind, sub_identifier='doubleup',
                sub_name='double up')
        payload = get_button_payload(blind, sub_identifier='doubledown',
                sub_name='double down')

while True:
    data, addr = sock.recvfrom(1024)
    if config['mqtt']['debug']:
        print('Received message: %s' % data)
        mqttc.publish(config['mqtt']['topic'], payload=data, retain=False)

    if not data.startswith(b'{XC_EVT}'):
        continue

    data = data.replace(b'{XC_EVT}', b'')
    data_dict = json.loads(data)
    for button in config['buttons']:
        if data_dict['type'] != button['type']:
            continue

        if data_dict['data'][0:-2].lower() != button['addr'].lower():
            continue

        identifier = button['type'] + '_' + button['addr']
        topic = config['mqtt']['topic'] + '/buttons/' + identifier
        payload = data_dict['data'][-2:]
        mqttc.publish(topic, payload=payload, retain=False)

    for blind in config['blinds']:
        if data_dict['type'] != 'ER' or data_dict['type'] != blind['type']:
            continue

        if data_dict['data'][0:2].lower() != blind['addr'].lower():
            continue

        identifier = blind['type'] + '_' + blind['addr']
        topic = config['mqtt']['topic'] + '/blinds/' + identifier + '/state'
        state = data_dict['data'][-2:].lower()
        payload = 'unknown'
        if state in ['01', '0e']:
            payload = 'open'
        elif state in ['02', '0f']:
            payload = 'closed'
        elif state in ['08', '0a']:
            payload = 'opening'
        elif state in ['09', '0b']:
            payload = 'closing'
        mqttc.publish(topic, payload=payload, retain=True)
