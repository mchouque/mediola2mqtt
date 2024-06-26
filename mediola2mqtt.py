#!/usr/bin/env python
# (c) 2021 Andreas Böhler
# License: Apache 2.0

import os
import sys
import select
import socket
import time
import json
import yaml
import requests
import datetime
import paho.mqtt.client as mqtt

INTERVAL_REFRESH_AFTER_ACTION = 10
AFTER_ACTION_DURATION = 30
INTERVAL_BETWEEN_REFRESH = 60
last_refresh = 0
last_action = 0
subscribed = []

def call_mediola(payload, verbose=True):
    url = 'http://' + config['mediola']['host'] + '/command'
    i = 0
    sent = False
    result = None
    while i <= 3:
        try:
            response = requests.get(url, params=payload,
                                    headers={'Connection':'close'},
                                    timeout=(1, 2))
        except requests.exceptions.RequestException as e:
            print_log("Couldn't send request: ", e)
            i += 1
            continue

        if response.status_code == 200:
            sent = True
            if verbose:
                print_log('Got OK reponse: ', response)
            result = response
            break

        print_log('Got NOK reponse: ', response, 'retrying')
        i += 1

    if not sent:
        print_log("Failed to send Message: " + ', '.join([message.topic, str(message.qos), str(message.payload)]))

    return result

def print_log(*args, **kwargs):
    tstamp ='{:%Y-%m-%d %H:%M:%S} '.format(datetime.datetime.now())
    print(tstamp + " ".join(map(str, args)), **kwargs)

# Define MQTT event callbacks
def on_connect(client, userdata, flags, reason_code, properties):
    print_log("MQTT: " + reason_code.getName())
    print_log("Resubscribing to MQTT")
    for topic in subscribed:
        client.subscribe(topic)

def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if rc != 0:
        print_log("Unexpected disconnection")
    else:
        print_log("Disconnected")

def on_message(client, userdata, message):
    global last_action

    print_log("Sending Message: " + ', '.join([message.topic, str(message.qos), str(message.payload)]))
    # Here we should send a HTTP request to Mediola to open the blind
    dtype, addr = message.topic.split("_")
    dtype = dtype[dtype.rfind("/")+1:]
    addr = addr[:addr.find("/")]
    sub_identifier = None
    if '-' in addr:
        addr, sub_identifier = addr.split('-')
    for blind in config['blinds']:
        if dtype != blind['type'] or addr != blind['addr']:
            continue

        if sub_identifier:
            if sub_identifier == 'doubleup':
                data = "%02x" % int(addr) + "0A"
            elif sub_identifier == 'doubledown':
                data = "%02x" % int(addr) + "0B"
            else:
                return
        elif message.payload == b'open':
            if dtype == 'RT':
                data = "20" + addr
            elif dtype == 'ER':
                data = "%02x" % int(addr) + "01"
            else:
                return
        elif message.payload == b'close':
            if dtype == 'RT':
                data = "40" + addr
            elif dtype == 'ER':
                data = "%02x" % int(addr) + "00"
            else:
                return
        elif message.payload == b'stop':
            if dtype == 'RT':
                data = "10" + addr
            elif dtype == 'ER':
                data = "%02x" % int(addr) + "02"
            else:
                return
        else:
            print_log("Wrong command")
            return

        payload = {
          "XC_FNC" : "SendSC",
          "type" : dtype,
          "data" : data
        }
        call_mediola(payload)
        last_action = time.time()

def on_publish(client, userdata, mid, reason_code, properties):
    print_log("Pub: " + str(mid))

def on_subscribe(client, userdata, mid, reason_code_list, properties):
    if reason_code_list[0].is_failure:
        print(f"Broker rejected you subscription for {mid}: {reason_code_list[0]}")
    else:
        print(f"Broker granted the following QoS for {mid}: {reason_code_list[0].value}")

def on_log(client, userdata, paho_log_level, messages):
    print_log(messages)

def publish_button(button, sub_identifier=None, sub_name=None):
    identifier = button['type'] + '_' + button['addr']
    if sub_identifier:
        identifier += '-' + sub_identifier
    dtopic = config['mqtt']['discovery_prefix'] + '/switch/' + \
             identifier + '/config'
    topic = config['mqtt']['topic'] + '/buttons/' + identifier
    name = "Button"
    if 'name' in button:
        name += ' ' + button['name']
    if sub_name:
        name += ' ' + sub_name

    payload = {
      "command_topic" : topic + "/set",
      "optimistic" : True,
      "unique_id" : identifier,
      "name" : name,
      "device" : {
        "identifiers" : identifier,
        "manufacturer" : "Mediola",
        "name" : "Button",
        "suggested_area": button['name'],
      },
    }
    payload = json.dumps(payload)
    mqttc.subscribe(topic + "/set")
    subscribed.append(topic + "/set")
    mqttc.publish(dtopic, payload=payload, retain=True)

def publish_blind(blind):
    identifier = blind['type'] + '_' + blind['addr']
    dtopic = config['mqtt']['discovery_prefix'] + '/cover/' + \
             identifier + '/config'
    topic = config['mqtt']['topic'] + '/blinds/' + identifier
    name = "Blind"
    if 'name' in blind:
        name += ' ' + blind['name']

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
        "name" : "Blind",
        "suggested_area": blind['name'],
      },
    }
    if blind['type'] == 'ER':
        payload["state_topic"] = topic + "/state"
        payload["position_topic"] = topic + "/position"

    payload = json.dumps(payload)
    mqttc.subscribe(topic + "/set")
    subscribed.append(topic + "/set")
    mqttc.publish(dtopic, payload=payload, retain=True)

def get_states():
    payload = {
        "XC_FNC" : "GetStates",
    }
    response = call_mediola(payload, verbose=False)

    header = b'{XC_SUC}'
    if not response.content.startswith(header):
        print_log(f'Failed to get states: {response}')
        return

    return response.content[len(header):]

config_files = [
#        ['/data/options.json', 'Running in hass.io add-on mode'],
        ['/config/mediola2mqtt.yaml', 'Running in legacy add-on mode'],
        ['mediola2mqtt.yaml', 'Running in local mode'],
    ]
config = None

for config_file, comment in config_files:
    if not os.path.isfile(config_file):
        continue
    print_log(comment)
    with open(config_file, 'r') as fp:
        if config_file.endswith('.json'):
            config = json.load(fp)
            break
        if config_file.endswith('.yaml'):
            config = yaml.safe_load(fp)
            break
    break

if not config:
    print_log('Configuration file not found, exiting.')
    sys.exit(1)

# Setup MQTT connection
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

mqttc.on_connect = on_connect
mqttc.on_subscribe = on_subscribe
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message

if config['mqtt']['debug']:
    print_log("Debugging messages enabled")
    mqttc.on_log = on_log
    mqttc.on_publish = on_publish

if config['mqtt']['username'] and config['mqtt']['password']:
    mqttc.username_pw_set(config['mqtt']['username'], config['mqtt']['password'])
try:
    mqttc.connect(config['mqtt']['host'], config['mqtt']['port'], 60)
except:
    print_log('Error connecting to MQTT, will now quit.')
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
        publish_button(blind, sub_identifier='doubleup', sub_name='double up')
        publish_button(blind, sub_identifier='doubledown', sub_name='double down')

while True:
    readable, _, _ = select.select([sock], [], [], 1)
    if not readable:
        curtime = time.time()
        if curtime - last_refresh >= INTERVAL_BETWEEN_REFRESH:
            print_log('Refreshing after refresh timeout')
            last_refresh = time.time()
        elif last_action > 0:
            if curtime - last_action < INTERVAL_REFRESH_AFTER_ACTION:
                continue
            print_log('Refreshing after action')
            if curtime - last_action >= AFTER_ACTION_DURATION:
                last_action = 0
        else:
            continue

        data = get_states()
        refresh = True
        ip = port = 'N/A'
        if config['mqtt']['debug']:
            print_log(f'Got states: {data}')
    else:
        refresh = False
        if sock not in readable:
            continue
        data, (ip, port) = sock.recvfrom(1024)

        header = b'{XC_EVT}'
        if not data.startswith(header):
            print_log(f'Received something else than an event: {data}')
            continue

        data = data[len(header):]

    if config['mqtt']['debug']:
        print_log('Received message from %s:%d : %s' % (ip, port, data))
        mqttc.publish(config['mqtt']['topic'], payload=data, retain=False)

    try:
        all_data = json.loads(data)
    except ValueError as e:
        print_log("Couldn't load text as JSON: ", e)
        continue

    if isinstance(all_data, dict):
        all_data = [all_data]

    for data_dict in all_data:
        found = False
        # Ignore what seems to be Infra Red messages for now
        if data_dict['type'] == 'IR':
            continue

        # Ignore type EVENT
        if data_dict['type'] == 'EVENT':
            continue

        for button in config['buttons']:
            if data_dict['type'] != button['type']:
                continue

            key = None
            for tmpkey in ['data', 'state']:
                if tmpkey in data_dict:
                    key = tmpkey
                continue

            if not key:
                continue

            if 'adr' in data_dict:
                if '%02d' % int(data_dict['adr'], 16) != button['addr'].lower():
                    continue
            elif data_dict[key][0:-2].lower() != button['addr'].lower():
                continue

            identifier = button['type'] + '_' + button['addr']
            topic = config['mqtt']['topic'] + '/buttons/' + identifier
            payload = data_dict[key][-2:]
            print_log('%sing to %s: %s' % ('Refresh' if refresh else 'Publish', topic, payload))
            mqttc.publish(topic, payload=payload, retain=False)
            found = True
            break

        if found:
            continue

        for blind in config['blinds']:
            if data_dict['type'] != 'ER' or data_dict['type'] != blind['type']:
                continue

            key = None
            for tmpkey in ['data', 'state']:
                if tmpkey in data_dict:
                    key = tmpkey
                continue

            if not key:
                continue

            if 'adr' in data_dict:
                if '%02d' % int(data_dict['adr'], 16) != blind['addr'].lower():
                    continue
            elif '%02d' % int(data_dict[key][0:2], 16) != blind['addr'].lower():
                continue

            identifier = blind['type'] + '_' + blind['addr']
            topic = config['mqtt']['topic'] + '/blinds/' + identifier + '/state'
            position_topic = config['mqtt']['topic'] + '/blinds/' + identifier + '/position'
            state = data_dict[key][-2:].lower()
            payload = 'unknown'
            position = None
            if state in ['01', '0e']:
                payload = 'open'
                position = 100
            elif state in ['02', '0f']:
                payload = 'closed'
                position = 0
            elif state in ['08', '0a']:
                payload = 'opening'
            elif state in ['09', '0b']:
                payload = 'closing'
            elif state in ['0d', '05']:
                payload = 'stopped'
                position = 42
            elif state == '03':
                # intermediate position down
                payload = 'closed'
                position = 10
            elif state == '04':
                # intermediate position up (it seems)
                payload = 'open'
                position = 50
            else:
                print_log('Received unknown state from %s:%d : %s (state %s)' % (ip,
                    port, data, state))
            print_log('%sing to %s: %s' % ('Refresh' if refresh else 'Publish', topic, payload))
            mqttc.publish(topic, payload=payload, retain=True)
            if position is not None:
                print_log('%sing to %s: %s' % ('Refresh' if refresh else 'Publish', position_topic, position))
                mqttc.publish(position_topic, payload=position, retain=True)

            found = True
            break

        if found:
            continue

        if not refresh:
            print_log('Received unknown message from %s:%d : %s' % (ip, port,
                                                                    data_dict))
        else:
            print_log('Received unknown state: %s' % data_dict)
