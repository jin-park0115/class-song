# Ubuntu 오디오 출력 + MPD 설정 (강의실 서버)

STEP 01의 1–3번(요구사항 15장)을 **실제 강의실 서버에서** 진행하는 절차다.
개발 컨테이너에는 사운드 장치가 없어서 null 출력으로만 검증했으므로, 아래 절차는 현장에서 확인해야 한다.

## 1. 서버와 출력 장치 조사

```bash
lsb_release -a                 # Ubuntu 버전
aplay -l                       # ALSA 재생 장치 목록 (card N, device M)
cat /proc/asound/cards
systemctl --user status pipewire pulseaudio 2>/dev/null   # 데스크톱 사운드 서버 사용 여부
```

| 앰프 연결 | `aplay -l` 에서 찾을 이름 | MPD `device` 예 |
|---|---|---|
| USB DAC | `USB Audio` | `hw:1,0` |
| 3.5mm 아날로그 | `ALC…` / `Analog` | `hw:0,0` |
| HDMI | `HDMI` | `hw:0,3` (번호는 장치마다 다름) |

> 카드 번호는 재부팅·USB 재연결 시 바뀔 수 있다. 바뀌면 `hw:CARD=Device,DEV=0` 처럼 **이름 기반**으로 지정한다(`aplay -L` 로 확인).

## 2. OS 레벨 재생 확인 (MPD 설치 전)

```bash
sudo apt install -y alsa-utils ffmpeg
scripts/make-test-tone.sh /tmp/tone          # 30초 사인파(저작권 문제 없음)
ffmpeg -i /tmp/tone/test-tone-440.mp3 -f wav - | aplay -D hw:1,0   # device 는 1단계 결과로
alsamixer -c 1                               # 소리가 안 나면 Master/PCM 음소거(MM) 해제
```

**먼저 앰프 볼륨을 최소로 내리고** 재생한 뒤 천천히 올린다.

## 3. MPD 설치·설정

```bash
sudo apt install -y mpd mpc
sudo mkdir -p /srv/ax2-jukebox/media
sudo cp deploy/mpd/mpd.conf /etc/mpd.conf     # device 를 1단계 결과로 수정
sudo usermod -aG audio mpd                    # mpd 계정의 ALSA 접근 권한
sudo chown -R mpd:audio /srv/ax2-jukebox/media
sudo chmod 2775 /srv/ax2-jukebox/media
sudo systemctl enable --now mpd
sudo systemctl status mpd
ss -ltnp | grep 6600                          # 127.0.0.1:6600 만 보여야 한다(외부 노출 금지)
```

### PipeWire/PulseAudio 충돌

데스크톱 세션의 PipeWire/PulseAudio가 장치를 잡고 있으면 MPD가 `Device or resource busy` 오류를 낸다.

- 서버(헤드리스)라면: 사용자 사운드 서버를 끈다 `systemctl --user mask pipewire pipewire-pulse wireplumber`
- 데스크톱을 같이 써야 한다면: MPD 출력을 `type "pipewire"` 로 바꾸는 방안을 검토한다(이때 MPD는 해당 사용자 세션으로 실행해야 함).

## 4. MPD 로컬 제어 검증

```bash
sudo cp /tmp/tone/*.mp3 /srv/ax2-jukebox/media/
scripts/mpd-smoke-test.sh
```

재생 → 일시정지 → 볼륨 30 → 다음 곡 → 정지가 차례로 출력되고 마지막에 `PASS` 가 나와야 한다.

## 5. 볼륨 3단계 구분 (요구사항 5.2)

| 단계 | 어디서 | 권장 |
|---|---|---|
| OS(ALSA) 믹서 | `alsamixer` | 100% 고정 후 `sudo alsactl store` 로 저장 |
| MPD 볼륨 | 웹 관리자(상한 40%) | `mixer_type "software"` 라 MPD가 직접 조절 |
| 앰프 물리 볼륨 | 앰프 노브 | 웹 상한 40%에서 강의실 적정 음량이 되도록 **현장 보정** 후 고정 |

웹 상한은 앰프 노브를 대신하지 못한다. 앰프 노브 위치를 표시해 두고 운영 런북에 기록한다.

## 6. 장애 확인 체크리스트

| 상황 | 확인 방법 | 기대 결과 |
|---|---|---|
| MPD 중단 | `sudo systemctl stop mpd` | 대시보드 "MPD 연결 끊김", 제어 503, `/health/ready` 503 |
| MPD 재시작 | `sudo systemctl start mpd` | 3초 안에 대시보드 "정상"으로 복귀(자동 재연결) |
| USB DAC 분리 | 케이블 분리 후 재생 | 대시보드 오류 칸에 MPD 오류 메시지 표시 |
| 출력 비활성 | `mpc disable 1` | 오류 칸 "활성화된 오디오 출력 장치가 없습니다" |
| 서버 재부팅 | `sudo reboot` | MPD가 스스로 재생을 재개하지 않음(`restore_paused`), 앱이 자동 기동 |
