# 개발 가이드

## 준비물

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16
- mpd, mpc, ffmpeg (`sudo apt install mpd mpc ffmpeg`)

## 처음 한 번

```bash
# DB
sudo -u postgres psql -c "CREATE USER jukebox WITH PASSWORD 'jukebox' CREATEDB;"
sudo -u postgres createdb -O jukebox jukebox
sudo -u postgres createdb -O jukebox jukebox_test

# 의존성·마이그레이션
cd backend
cp ../.env.example .env            # DATABASE_URL 수정
uv sync
uv run alembic upgrade head

# 관리자 계정(웹으로는 만들 수 없음)
../scripts/jukebox-admin create --login-id admin --name 운영자
```

## 실행

터미널 두 개를 쓴다.

```bash
scripts/dev-mpd.sh                 # 1) null 출력 MPD + 테스트 음원 2곡(.dev/media)
mpc add /                          #    처음 한 번 대기열에 곡 추가(STEP 02 전까지는 수동)

cd backend && uv run uvicorn app.main:app --reload --port 8000   # 2) API
```

- 관리자 화면: http://127.0.0.1:8000/admin
- API 문서(OpenAPI): http://127.0.0.1:8000/docs

## 테스트

```bash
cd backend
uv run pytest                 # 전체(통합 테스트는 mpd·ffmpeg가 없으면 자동으로 건너뜀)
uv run pytest -m "not integration"
```

- 테스트 DB는 `TEST_DATABASE_URL` (기본 `.../jukebox_test`) 이며, 실행할 때마다 테이블을 새로 만든다.
- `tests/fake_mpd.py` 는 MPD 테스트 더블이다. 실제 MPD 동작은 `test_mpd_integration.py` 가 null 출력 mpd를 띄워서 검증한다.

## 구조 원칙

- **MPD 명령은 `app/services/player.py` 의 `PlayerController` 로만 보낸다.** API·스케줄러가 MPD에 직접 붙지 않는다.
- uvicorn 워커는 1개만 쓴다(컨트롤러의 연결·음소거 상태가 프로세스에 있음).
- 볼륨 상한은 `system_settings.max_volume` → 없으면 `DEFAULT_MAX_VOLUME`(40). 재생·이어 재생 직전에도 상한을 강제한다.
- 쓰기 API는 세션 쿠키 + `X-CSRF-Token` 헤더가 필요하다. CSRF 토큰은 `/api/auth/login` 또는 `/api/auth/me` 응답에 들어 있다.
- 에러 응답은 `{"error": {"code", "message"}}` 형식이다.

## 스키마 변경

```bash
cd backend
# app/models 수정 후
uv run alembic revision --autogenerate -m "설명"
# 생성된 파일을 반드시 검토(특히 DEFERRABLE 제약·CHECK·부분 인덱스는 수동 보완)
uv run alembic upgrade head
```
