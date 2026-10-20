# 노트북 테스트용 MPD: 사운드 장치 대신 httpd 스트림으로 내보낸다(앱이 /api/player/monitor 로 중계).
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends mpd \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/mpd /run/mpd && chown -R mpd:audio /var/lib/mpd /run/mpd
COPY mpd.conf /etc/mpd.conf
RUN sed -i 's/\r$//' /etc/mpd.conf
USER mpd
EXPOSE 6600 8001
CMD ["mpd", "--no-daemon", "--stderr", "/etc/mpd.conf"]
