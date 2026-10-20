# 노트북에서 먼저 해보기 (Docker)

강의실 Ubuntu 서버에 옮기기 전에, 노트북(Windows/Mac)에서 **관리자·학생 흐름 전체**를 확인하는 방법이다.
PostgreSQL·MPD·앱이 컨테이너로 한 번에 뜬다. 소리는 앰프 대신 **관리자 콘솔의 LISTEN 버튼**으로 노트북 스피커에서 듣는다.

## 1. 준비 (처음 한 번)

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) 설치 후 실행 (Windows는 설치 중 WSL 2 사용 선택)
2. 이 저장소를 받는다: `git clone https://github.com/jin-park0115/class-song.git` (또는 zip을 `C:\jin_dev` 등에 풀기)
3. 터미널(PowerShell)에서 저장소 폴더로 이동: `cd C:\jin_dev\class-song`

## 2. 실행

```powershell
docker compose up -d --build        # 처음엔 이미지 빌드로 몇 분 걸린다
docker compose ps                   # app·db·mpd 가 Up, app 은 (healthy)
```

관리자 계정은 CLI로만 만든다(웹으로는 만들 수 없음). 비밀번호를 두 번 묻는다.

```powershell
docker compose exec app python -m app.cli create --login-id admin --name 운영자
```

브라우저에서 **http://localhost:8000** 을 연다.

## 3. 해볼 순서

| # | 누가 | 어디서 | 할 일 | 확인할 것 |
|---|---|---|---|---|
| 1 | 학생 | 시크릿 창(또는 다른 브라우저) → **회원가입** | 아이디·이름·비밀번호 입력 | "승인을 기다리는 중" 화면 |
| 2 | 관리자 | 일반 창 → 로그인(admin) | 운영 콘솔 상단 **관리** 배지 숫자 확인 → 관리 → **가입 승인** → 승인 | 학생 창이 10초 안에 학생 화면으로 넘어감 |
| 3 | 학생 | 학생 화면 → 곡 신청 | YouTube 주소 붙여넣기 → 미리보기 확인 → **이 곡 신청하기** | 내 신청곡에 "승인 대기" |
| 4 | 관리자 | 관리 → **신청 승인** | 승인 | 학생 화면에서 "승인", 음원 상태 "음원 준비 중" |
| 5 | 관리자 | 관리 → **음원 라이브러리** | ① 권리 근거 선택 → 확인 ② **본인이 적법하게 가진 MP3** 선택 → 올리기 ③ **대기열에 추가** | 배지 "권리 확인"·"재생 가능" |
| 6 | 관리자 | ← 운영 콘솔 | 화면 목록에서 **▶ Play** → 오른쪽 아래 **LISTEN** | 조그 휠 회전, 노트북 스피커에서 소리(몇 초 지연) |
| 7 | 관리자 | 운영 콘솔 | 일시정지·다음 곡·볼륨 노브(최대 40%)·MUTE·**비상 정지** | 상태 표시가 바로 바뀌고 소리도 따라 바뀜 |
| 8 | 학생 | 학생 화면 | — | "지금 재생 중"에 같은 곡 |

> YouTube 영상을 내려받아 올리지 않는다. 테스트에는 직접 만든 음원, 저작권이 만료됐거나 CC 라이선스가 붙은 음원처럼 이용 권리가 확인된 파일만 쓴다(요구사항 3장).

### 휴대폰으로도 보기

노트북과 휴대폰이 같은 Wi-Fi라면 휴대폰 브라우저에서 `http://<노트북 IP>:8000` 으로 접속한다.
노트북 IP는 PowerShell `ipconfig` 의 "IPv4 주소"다. 안 열리면 Windows 방화벽에서 Docker Desktop의 8000 포트를 허용한다.

## 4. 끄기·초기화

```powershell
docker compose stop          # 잠깐 끄기(데이터 유지)
docker compose up -d         # 다시 켜기
docker compose down          # 컨테이너 삭제(데이터 유지)
docker compose down -v       # 데이터까지 전부 삭제(계정·신청·음원 초기화)
docker compose logs -f app   # 앱 로그 보기
```

## 5. 노트북 구성과 강의실 서버의 차이

| 항목 | 노트북(Docker) | 강의실 Ubuntu 서버 |
|---|---|---|
| 소리 | MPD `httpd` 출력 → 콘솔 **LISTEN** 으로 브라우저 재생 | MPD `alsa` 출력 → 앰프 (`deploy/mpd/mpd.conf`) |
| 실행 | `docker compose up` | systemd 또는 같은 compose 파일에서 mpd만 ALSA로 교체 |
| 접속 | http://localhost:8000 | http://<서버 IP>:8000 (같은 Wi-Fi) |

노트북에서 흐름을 다 확인했으면 [ubuntu-audio-setup.md](ubuntu-audio-setup.md) 순서대로 강의실 서버에 옮긴다.

## 문제 해결

| 증상 | 확인 |
|---|---|
| `localhost:8000` 이 안 열림 | `docker compose ps` 에서 app 이 healthy 인지, `docker compose logs app` |
| 콘솔에 "MPD 연결 끊김" | `docker compose logs mpd` |
| LISTEN 을 눌러도 소리가 안 남 | 곡이 **재생 중**인지 확인(정지 중에는 스트림이 없다). 브라우저 탭 음소거·노트북 볼륨 확인 |
| 곡 미리보기에 제목이 안 나옴 | 노트북 인터넷 연결 확인. 안 되면 곡명을 직접 입력해 신청할 수 있다 |
| 업로드가 "재생할 수 있는 오디오 파일이 아니에요" | 확장자와 실제 형식이 같은 MP3/OGG/FLAC인지 확인, 50MB 이하 |
