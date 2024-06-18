ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN apk add --no-cache python3
RUN apk add py3-pip
RUN pip3 install --break-system-packages paho-mqtt requests PyYAML

COPY mediola2mqtt.py /
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
