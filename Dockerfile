ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip && \
    pip3 install --no-cache-dir --break-system-packages \
        pyserial==3.5 \
        paho-mqtt==1.6.1

COPY rootfs /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
