# AX2 Classroom Jukebox

강의실 입실 시간에 맞춰 음악을 자동으로 재생하는 주크박스. 학생은 YouTube URL로 곡을 신청하고, 관리자는 승인·편성·스케줄·볼륨·재생을 웹에서 제어한다. 오디오는 강의실 Ubuntu 서버의 MPD가 앰프로 출력한다.

- 요구사항: [requirements.md](requirements.md)
- 개발 가이드: [docs/development-guide.md](docs/development-guide.md)
- 강의실 서버 오디오 설정: [docs/ubuntu-audio-setup.md](docs/ubuntu-audio-setup.md)
- **노트북에서 먼저 해보기(Docker)**: [docs/laptop-test-guide.md](docs/laptop-test-guide.md)
- 진행 기록: [STEP 01](docs/step-01-report.md) · [STEP 02](docs/step-02-report.md) · [STEP 03](docs/step-03-report.md)

## 구성

```
브라우저 (같은 Wi-Fi) ── FastAPI ── PostgreSQL
                            │
                     PlayerController ── MPD(127.0.0.1) ── ALSA ── 앰프
```

## 노트북에서 바로 실행 (Docker)

```bash
docker compose up -d --build
docker compose exec app python -m app.cli create --login-id admin --name 운영자
# http://localhost:8080  → 관리자 로그인 / 다른 창에서 학생 회원가입
```

자세한 순서는 [노트북 테스트 가이드](docs/laptop-test-guide.md).

## 빠른 시작 (개발, 컨테이너 없이)

```bash
cd backend && cp ../.env.example .env && uv sync && uv run alembic upgrade head
../scripts/jukebox-admin create --login-id admin --name 운영자
../scripts/dev-mpd.sh &            # null 출력 MPD + 테스트 음원
mpc add /
uv run uvicorn app.main:app --reload --port 8000
# http://127.0.0.1:8000/admin
```

테스트: `cd backend && uv run pytest`

## 진행 상황

| 단계 | 상태 |
|---|---|
| STEP 01 오디오 출력 + MPD + 상태·제어 API + 관리자 화면 | 개발 환경 완료, 강의실 현장 검증 대기 |
| STEP 02 회원가입·가입 승인·곡 신청·음원 등록·대기열 + Docker | 완료(노트북 검증 대기) |
| STEP 03 플레이리스트·자동 스케줄·페이드·재합류 | 완료(노트북 검증, 강의실 드라이런 대기) |

## 원칙

- MPD 명령은 `PlayerController` 하나로만 보낸다.
- 관리자 권한은 CLI로만 부여한다.
- 볼륨은 서버에서 상한(기본 40%)으로 제한한다. 앰프 물리 볼륨은 현장에서 따로 맞춘다.
- `.env`·음원 파일·DB 덤프는 커밋하지 않는다.
- 음원은 이용 권리가 확인된 파일만 사용한다(요구사항 3장 음원 이용 원칙).
