# STEP 01 결과 보고 — Ubuntu 오디오 출력 + MPD 연결 검증

- 일자: 2026-10-20
- 범위: 요구사항 15장 STEP 01 (1–6번)
- 검증 환경: 개발 컨테이너(Ubuntu 24.04, Python 3.13, PostgreSQL 16, MPD null 출력). **실제 사운드 장치 없음.**

## 1. 구현 내용

| 항목 | 내용 | 위치 |
|---|---|---|
| Player Controller | MPD 단일 연결·락 직렬화, 끊기면 1회 재연결, 상태 조회, 재생/일시정지/이어재생/정지/다음/볼륨/음소거/비상 정지 | `backend/app/services/player.py` |
| 볼륨 상한 | 요청값을 `max_volume`(기본 40)으로 자르고 `clamped` 반환. **재생·이어 재생 직전에도 상한 초과 볼륨을 낮춤** | 같은 파일 `_enforce_cap` |
| 인증 | 아이디/비밀번호 로그인(argon2id), 5회 실패 10분 잠금, 서버 측 세션(토큰 해시 저장), CSRF 헤더 검증 | `backend/app/auth/`, `app/api/auth.py` |
| 관리자 CLI | `create / promote / revoke / reset-password / list`, 마지막 관리자 회수 방지, 감사 로그 | `backend/app/cli.py`, `scripts/jukebox-admin` |
| API | `POST /api/auth/login·logout`, `GET /api/auth/me`, `GET /api/player/status`, `POST /api/player/control`, `POST /api/player/emergency-stop`, `GET /health/live·ready` | `backend/app/api/` |
| 관리자 화면 | 비상 정지(최상단), 현재 곡·진행률·남은 시간·다음 곡, 재생 버튼, 볼륨 슬라이더(상한까지만), 음소거, MPD·출력·대기열·오류 상태, 3초 폴링. 외부 CDN 없음(인터넷 없이 동작) | `backend/app/templates/`, `app/static/` |
| DB | `users`, `sessions`, `control_audit_logs`, `system_settings` + Alembic 초기 마이그레이션 | `backend/app/models/`, `backend/migrations/` |
| 배포 | MPD 설정(127.0.0.1 바인딩, `restore_paused`), systemd 유닛(워커 1개) | `deploy/` |

## 2. 자동화 테스트 — 55개 PASS

```
$ cd backend && uv run pytest -q
55 passed in 11.5s
```

| 파일 | 개수 | 주요 검증 |
|---|---|---|
| `test_player_api.py` | 24 | 재생 흐름, 볼륨 80→40 클램프(수용 테스트 4), 학생 제어 403(수용 테스트 10), CSRF 누락 403, 빈 대기열 409, MPD 중단 시 503·상태 표시, 재연결, 감사 로그, 재생 직전 상한 강제 |
| `test_auth.py` | 12 | HttpOnly 쿠키, 토큰 해시 저장, 존재하지 않는 아이디와 틀린 비밀번호 응답 동일, 5회 잠금·해제, 비활성 계정 차단, 세션 만료, CSRF 안정성 |
| `test_cli.py` | 11 | 관리자 생성·비밀번호 규칙·중복·아이디 규칙, 승격 시 세션 폐기, 마지막 관리자 보호, 학생 비밀번호는 CLI 재설정 불가 |
| `test_player_controller.py` | 7 | 끊긴 연결 투명 재시도, 연결 재사용, 음소거 중복·복원, 믹서 없는 출력 |
| `test_mpd_integration.py` | 1 | **실제 mpd 데몬**(null 출력)으로 재생→일시정지→이어재생→볼륨 클램프→음소거→다음 곡→비상 정지 |

## 3. 수동 검증 (개발 컨테이너)

| 시나리오 | 결과 |
|---|---|
| `scripts/mpd-smoke-test.sh` (mpc로 재생/일시정지/볼륨/다음/정지) | PASS |
| 미인증으로 `/admin` 접속 → `/login` 이동 | PASS |
| 틀린 비밀번호 → 오류 메시지 | PASS ([캡처](images/step-01/01-login-error.png)) |
| 로그인 → 대시보드, MPD 연결 "정상" | PASS |
| MPD 볼륨 100% 상태에서 재생 → 40%로 내려간 뒤 재생 | PASS ([캡처](images/step-01/02-playing.png)) |
| 볼륨 80 요청 → `applied=40, clamped=true` / 슬라이더 25 → 25% | PASS |
| 일시정지 → 이어 재생 → 다음 곡 → 음소거(0%) → 해제 | PASS |
| 비상 정지 → "정지" | PASS ([캡처](images/step-01/03-emergency-stopped.png)) |
| MPD 프로세스 종료 → status `connected=false, MPD_UNAVAILABLE`, 제어 503, `/health/ready` 503 | PASS |
| MPD 재시작 → 다음 조회에서 자동 재연결 | PASS |

## 4. 발견한 문제와 조치

| 문제 | 조치 |
|---|---|
| MPD는 기본 볼륨 100%로 시작해, 상한 40%를 설정해도 첫 재생이 100%로 나갈 수 있었음 | 재생·이어 재생 직전에 상한 초과 볼륨을 낮추도록 수정, 테스트 추가 |
| CSRF 토큰을 `/me` 호출마다 새로 발급하면 관리자 화면을 두 탭에서 열 때 먼저 연 탭이 실패 | 세션 토큰에서 파생하는 고정 CSRF 토큰으로 변경 |
| 개발 MPD 로그에 Avahi(zeroconf) 오류 | MPD 설정에 `zeroconf_enabled "no"` 추가 |

## 5. 현장에서 남은 확인 (강의실 서버)

컨테이너에는 사운드 장치가 없으므로 아래는 **미검증**이다. 절차는 [ubuntu-audio-setup.md](ubuntu-audio-setup.md).

- [ ] Ubuntu 버전, `aplay -l` 출력 장치, 앰프 연결 방식 확정
- [ ] 30초 테스트 음원으로 OS 레벨 재생(앰프에서 소리 확인)
- [ ] MPD ALSA 출력 `device` 지정 후 `scripts/mpd-smoke-test.sh` PASS
- [ ] PipeWire/PulseAudio 장치 점유 충돌 여부
- [ ] 웹 상한 40%에서 앰프 노브 현장 보정
- [ ] USB DAC 분리·MPD 중단·재부팅 시 대시보드 표시 확인

## 6. 범위 밖으로 넘긴 것

- 대기열 구성은 아직 `mpc add` 로 수동 → 플레이리스트·음원 등록 단계에서 앱이 관리
- 회원가입·가입 승인·학생 화면 → STEP 02
- `resume_schedule`, 페이드, 스케줄러 → STEP 03
- 로그인 rate limit(IP 기준) → STEP 02에서 회원가입과 함께
- 음소거 상태는 프로세스 메모리에만 있음. 앱이 재시작되면 볼륨 0이 유지되고 "음소거" 표시는 해제됨(소리가 커지는 방향의 위험은 없음)
