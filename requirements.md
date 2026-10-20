# AX2 Classroom Jukebox — 요구사항 정의서

- 문서 버전: v1.1
- 작성일: 2026-10-20 (v1.0 초안) / 개정: 2026-10-20 (v1.1)
- 프로젝트 유형: AX2반 로컬 스터디용 개발 프로젝트
- 우선 개발 환경: 기존 Ubuntu 강의실 서버 / 웹 기반 원격 제어
- 배포 범위: 강의실 내부 운영 및 허가된 관리자 원격 접속
- 개발 원칙: 최소 비용, 재현 가능성, 단계별 검증, 운영 안전성

### 변경 이력

| 버전 | 변경 내용 |
|---|---|
| v1.0 | 초안 |
| v1.1 | 인증 방식 확정(자체 회원가입 + 관리자 가입 승인 + CLI 전용 관리자 생성; Google OAuth는 검토 후 미채택, 부록 A), 신청 상태 `CANCELLED` 추가, Wish List 항목 단위 공개(`is_public`), 스케줄 컬럼 보강(페이드·재합류·재생 모드), 음원 이용 권리 상태 정의, `playlist_items` DEFERRABLE 제약, 전체 API 명세 확정, 운영 정책(수동/예약 충돌, 볼륨 상한 처리, 신청 한도 등) 확정 |

## 1. 프로젝트 개요

강의실 입실 시간에 맞춰 음악이 자동으로 재생되는 주크박스를 구축한다. AX2 학생은 YouTube URL을 이용해 음악을 신청하고 개인 Wish List를 관리하며, 관리자는 신청곡 승인·편성·스케줄·볼륨·재생을 원격으로 제어한다. 실제 오디오는 Ubuntu 강의실 서버에서 앰프로 출력한다.

### 1.1 목표

1. 지정 시간대(예: 08:00–09:00, 13:00–14:00, 18:00–19:00)에 음악을 자동 재생 및 종료한다.
2. 학생별 신청곡, Wish List, 승인 상태, 편성 리스트 및 재생 일정을 조회할 수 있다.
3. 관리자가 신청곡을 승인/반려하고 재생 목록 및 순서를 확정한다.
4. 로컬 파일을 우선 사용하여 네트워크 불안정에도 재생을 지속한다.
5. 관리자 웹 화면에서 현재 곡·재생 위치·남은 시간·볼륨·다음 곡·장애 상태를 확인하고 제어한다.
6. 기존 Ubuntu 서버 및 오픈소스로 추가 인프라 비용을 최소화한다.

### 1.2 범위와 비범위

**포함:** AX2 학생 계정(자체 회원가입 + 관리자 승인), 신청/승인/편성/스케줄, 로컬 오디오 라이브러리, 웹 관리자, 오디오 앰프 출력, 운영 상태 및 이력, 자동 복구.

**초기 제외:** 상업 스트리밍 서비스 연동, 결제, 공개 서비스, 모바일 네이티브 앱, 다중 강의실 동시 재생, 다중 출력 장치, 소셜 공유, 음성 인식.

## 2. 사용자 역할과 권한

| 역할 | 주요 권한 |
|---|---|
| 학생(STUDENT) | 본인 신청곡 등록/취소(`PENDING`일 때만), Wish List 관리 및 항목별 공개 설정, 전체 승인곡·편성·일정·현재 재생 조회, 타인의 공개 Wish List 항목 조회 |
| 관리자(ADMIN) | 가입 요청 승인/거절·사용자 비활성화, 신청 승인/반려, 음원 확보·이용 권리 상태 관리, 플레이리스트 편성, 스케줄/볼륨/재생 원격 제어, 운영 설정, 로그 조회 |
| 시스템(SYSTEM) | 일정 실행, 오디오 재생 상태 수집, 상태 동기화, 장애 복구, 감사 로그 |

- 모든 쓰기 API는 서버 측에서 권한을 검증한다.
- 학생은 다른 학생의 Wish List를 수정할 수 없고, `is_public = false`인 항목은 볼 수 없다. 공개 신청곡 목록의 신청자 노출 여부는 관리자 설정(기본: 비노출)을 따른다.
- 가입 승인/거절, 사용자 비활성화, 오디오 재생 제어, 운영 설정 변경은 감사 로그에 남긴다.

### 2.1 인증

- **주 인증: 자체 회원가입(아이디/비밀번호).** 외부 인증 서비스에 의존하지 않는다. Google OAuth는 검토 후 미채택했다(사유는 부록 A).
- **회원가입:** 아이디(`login_id`, 4–20자, 영소문자·숫자·`_`), 이름, 비밀번호, 가입 메모(선택, 반·학번 등 관리자가 본인 확인에 쓰는 정보)를 입력한다. 아이디 중복 시 409.
- **가입 승인 방식:** 가입한 계정은 `account_status = PENDING_APPROVAL`로 생성되고, 로그인하면 "승인 대기 중" 화면을 본다. 관리자가 이름·가입 메모를 확인해 승인하면 `ACTIVE`(STUDENT)가 되어 서비스를 사용할 수 있고, 거절하면 `REJECTED`가 되어 이용이 차단된다. 승인 대기 중인 계정은 `/api/auth/me`, `/api/auth/logout`만 호출할 수 있다(그 외 403 `ACCOUNT_PENDING`).
- **가입 요청 남용 방지:** 승인 대기 계정 수 상한(`max_pending_signups`, 기본 100), IP 기준 가입·로그인 rate limit, 회원가입 열기/닫기 설정(`signup_enabled`).
- **비밀번호:** 최소 10자, 아이디와 동일하거나 아이디를 포함하면 거부. argon2id 해시로 저장한다. 로그인 5회 실패 시 10분 잠금. 본인이 직접 변경할 수 있다.
- **비밀번호 분실:** 이메일을 받지 않으므로 "비밀번호 찾기"는 없다. 학생은 관리자에게 요청하고, 관리자가 웹에서 임시 비밀번호를 발급한다(화면에 1회만 표시, 다음 로그인 시 변경 강제 `must_change_password`). 관리자 계정의 비밀번호는 CLI로만 재설정한다.
- **관리자 계정은 CLI로만 생성/부여한다.** 웹 화면이나 API로는 ADMIN 권한을 부여·회수할 수 없다(권한 상승 경로 차단).
  - `scripts/jukebox-admin create --login-id <id>`: 관리자 생성(비밀번호는 프롬프트로 입력, `ACTIVE` 상태로 시작).
  - `scripts/jukebox-admin promote --login-id <id>` / `revoke --login-id <id>`: 기존 사용자 ADMIN 승격/회수.
  - `scripts/jukebox-admin reset-password --login-id <id>`: 관리자 비밀번호 재설정.
  - CLI 실행 기록과 모든 관리자 로그인은 감사 로그에 남긴다.
- **세션:** 서버 측 세션 테이블 + `HttpOnly; SameSite=Lax` 쿠키(HTTPS 적용 시 `Secure` 추가), 쓰기 요청에 CSRF 토큰 검증. 세션 만료는 학생 7일, 관리자 12시간(마지막 활동 기준 갱신). 비밀번호 변경·초기화·비활성화 시 해당 사용자의 세션을 모두 폐기한다.
- **전송 보안(보류):** 강의실 같은 Wi-Fi 내부망에서 사용하는 것을 전제로, HTTPS 적용 여부와 방식은 **당분간 결정하지 않는다.** 초기에는 HTTP로 운영하며, 같은 Wi-Fi의 다른 기기가 비밀번호·세션을 엿볼 수 있다는 위험을 인지하고 수용한다. 앱은 HTTPS 전환에 대비해 설정값(`COOKIE_SECURE`, 기본 false)으로 쿠키 `Secure` 속성을 켤 수 있게 만들고, 학생에게는 다른 사이트와 같은 비밀번호를 쓰지 않도록 안내한다.
- **네트워크 단절 시:** 외부 인증 의존이 없으므로 강의실 내부망에서는 로그인·제어·예약 재생이 모두 정상 동작한다.

## 3. 핵심 사용자 흐름

1. 학생 회원가입 → 관리자 가입 승인 → 로그인 → YouTube URL 제출 → 유효성 검증 → 중복 확인 → 신청 `PENDING`.
2. 메타데이터(곡명/채널/길이/썸네일/원본 URL)를 조회하여 신청 내역에 표시.
3. 학생은 `PENDING` 상태의 본인 신청을 취소할 수 있다(`CANCELLED`).
4. 관리자가 신청을 검토 → `APPROVED` 또는 `REJECTED`(사유 선택).
5. 승인된 신청곡은 음원 확보 대기열로 이동 → 관리자가 **이용 권리 확인(`rights_status = CONFIRMED`)** → 권한 있는 소스에서 파일 등록/추출/변환 → 오디오 검증 → 로컬 보관(`READY`).
6. 관리자가 로컬 재생 가능한 곡으로 플레이리스트를 만들고 재생 순서를 확정.
7. 스케줄 시간이 되면 서버가 해당 재생 목록을 페이드 인으로 재생 → 앰프 출력 → 종료 시간이 되면 페이드아웃 후 정지.
8. 웹 대시보드 및 학생 조회 페이지에서 현재 재생 상태·다음 곡·일정을 확인.
9. 관리자는 수동 재생·일시정지·다음 곡·음소거·볼륨 변경·비상 정지 가능.

> **음원 이용 원칙:** 비상업적 스터디 프로젝트라도 일반 YouTube 영상을 임의로 MP3 다운로드·변환하는 행위가 자동으로 허용되지는 않는다. 신청곡의 YouTube URL은 **검색·식별·검토용**으로 사용한다. 다운로드/추출 기능은 권한 있는 직접 URL, 명시적으로 다운로드가 허용된 자료, 또는 운영자가 적법하게 보유한 파일에 한해 사용한다. 무단 다운로드나 기술적 제한 우회를 전제로 개발하지 않는다. 강의실 재생에 필요한 별도 이용 권리도 확인한다.

## 4. 기능 요구사항

우선순위: **P0** 필수(MVP), **P1** 2차, **P2** 선택.

| ID | 우선 | 기능 | 상세 요구사항 및 완료 기준 |
|---|---|---|---|
| FR-001 | P0 | 회원가입·로그인·권한 | 아이디/비밀번호 회원가입, 가입 승인 대기/승인/거절, 로그인 잠금, 비밀번호 변경·관리자 초기화, STUDENT/ADMIN 역할 구분(ADMIN은 CLI로만 부여), 비인가 재생 제어 요청은 403 |
| FR-002 | P0 | YouTube URL 신청 | URL 형식 검증, video ID 정규화, 신청 메모 등록, 동일 사용자의 진행 중 중복 신청 차단(409), 동시 `PENDING` 한도 적용 |
| FR-003 | P0 | 곡 메타데이터 | 곡명/채널/길이/썸네일/URL 저장; oEmbed로 기본 조회, 길이는 수동 입력 또는 Data API 키 설정 시 자동; 조회 실패 시 수동 입력 및 재시도 |
| FR-004 | P0 | 신청 상태 관리 | PENDING→APPROVED/REJECTED(관리자), PENDING→CANCELLED(본인); 처리자/시간/반려 사유 기록 |
| FR-005 | P0 | 로컬 음원 등록 | 이용 권리가 `CONFIRMED`인 곡에 한해 MP3/OGG/FLAC 파일 등록; 파일 유형·크기·오디오 재생 가능 여부 검증 |
| FR-006 | P0 | 플레이리스트 | 관리자만 생성/수정/순서 변경/활성화; `READY`가 아닌 곡은 편성 차단; 스케줄이 참조 중인 플레이리스트는 삭제 차단 |
| FR-007 | P0 | 자동 스케줄 | 요일, 시작/종료 시간, 타임존(Asia/Seoul), 플레이리스트, 볼륨, 페이드 인/아웃, 재합류 정책, 재생 모드, 종료 시 동작, 활성화 지원 |
| FR-008 | P0 | 로컬 재생 | Ubuntu MPD 서버에서 오디오 재생, 앰프 출력, 재생/정지/일시정지/다음 곡 |
| FR-009 | P0 | 원격 제어 | 인증된 관리자만 제어, 재생/정지/건너뛰기/볼륨 0–100(최대 상한으로 제한)/음소거/비상 정지 |
| FR-010 | P0 | 재생 현황 | 현재 곡, 진행 시간, 재생 상태, 볼륨, 다음 곡, 서버 연결 상태, 재생 원인(예약/수동) 조회 |
| FR-011 | P0 | 시간 종료 | 종료 시간에 페이드아웃(설정 가능) 및 자동 정지; 스케줄 없는 시간에는 정지(수동 재생 예외 규칙은 10장) |
| FR-012 | P0 | 상태/오류 처리 | 파일 없음, 오디오 장치 연결 실패, MPD 오류, 서버 재부팅, READY 곡 없음을 감지하고 표시 |
| FR-013 | P1 | 학생 Wish List | 추가/삭제/목록, 본인만 편집, 항목별 공개 설정 및 일괄 공개/비공개, 신청으로 바로 전환 |
| FR-014 | P1 | 신청곡 조회 | 본인 신청 이력/처리 상태, 승인곡 전체 목록, 검색/정렬/필터 |
| FR-015 | P1 | 스케줄 조회 | 오늘/이번 주 시간대와 플레이리스트 제목, 다음 재생 시간 표시 |
| FR-016 | P1 | 실시간 업데이트 | SSE로 관리자·학생 화면에 재생 상태 반영(MVP는 3~5초 폴링) |
| FR-017 | P1 | 음원 처리 작업 | 권한 확인된 소스만 비동기 다운로드/변환, 진행률/실패 사유/재시도/중복 파일 검사 |
| FR-018 | P1 | 기록과 통계 | 곡별 실제 재생 이력, 승인 이력, 수동 제어 이력, 스케줄 실행 결과 |
| FR-019 | P1 | 수동 우선권 | 수동 재생과 예약 재생 충돌 시 10장의 정책 적용, 관리자 화면에 원인·복귀 옵션(`resume_schedule`) 표시 |
| FR-020 | P1 | 운영 설정 | 최대 허용 볼륨, 수동 재생 자동 정지 시간, 신청 한도, 신청자 노출 여부, 비상 정지 |
| FR-021 | P1 | 휴강/공휴일 예외 | 날짜별 전체 또는 특정 스케줄 실행 제외 |
| FR-022 | P2 | 투표/추천 | 신청곡 좋아요, 중복 신청 통합, 추천 정렬 |
| FR-023 | P2 | 공지/알림 | 승인·반려 알림, 오디오 장치 장애 관리자 알림 |

### 4.1 스케줄 상세 규칙

- 기본 스케줄 예시(초깃값; 관리자가 변경 가능): 월~금 08:00–09:00(25%), 13:00–14:00(35%), 18:00–19:00(20%).
- 스케줄별 사용 여부, 반복 요일, 시작/종료, 볼륨, 플레이리스트, 페이드 인/아웃, 재합류 정책, 재생 모드, 종료 시 동작을 편집할 수 있다.
- 교차 자정 시간대는 MVP에서 금지하고 UI에서 명확히 안내한다(`start_time < end_time` 제약).
- MVP는 출력 장치를 1개로 고정하므로, **활성화된 모든 스케줄 간** 시간 겹침을 저장 시 차단한다(409 + 충돌 스케줄 정보).
- 서버 재시작 시 이미 재생 시간대이면 `rejoin_policy`에 따라 처리한다(10장).
- 종료 시 재생 중인 곡은 강제로 종료하며 `fade_out_seconds`(기본 3초) 동안 페이드아웃한다.
- 플레이리스트가 구간 종료 전에 끝나면 `on_playlist_end`(기본 `REPEAT`)에 따른다.
- 페이드는 Player Controller가 MPD 볼륨을 단계적으로 변경하여 구현한다.
- 주말·공휴일·휴강일 예외 일정은 P1(`schedule_exceptions`)로 둔다.

### 4.2 음악 파일 처리

- 원본 YouTube URL 및 메타데이터는 곡 식별의 기준으로 보관한다.
- 파일 상태(`media_status`): `NOT_AVAILABLE`, `PENDING_ACQUISITION`, `PROCESSING`, `READY`, `FAILED`, `BLOCKED`.
- 음원 확보 모듈은 `SourceProvider` 인터페이스로 분리한다.
- 첫 구현: `ManualUploadProvider`(권한 확인 후 업로드), `AuthorizedDownloadProvider`(명시적 다운로드 허용 URL). `yt-dlp`와 FFmpeg는 승인된 소스에 한해 어댑터 형태로 사용 가능하도록 설계한다.
- 표준 출력 형식: MP3(예: 192 kbps) 또는 원본 MPD 지원 형식 유지. 불필요한 재인코딩은 피한다.
- 저장 경로 예: `/srv/ax2-jukebox/media/<track-id>.<ext>`; 파일명을 사용자 입력으로 직접 구성하지 않는다.
- SHA-256 해시로 중복 감지, 다운로드 실패 시 부분 파일 정리, 대역폭/디스크 사용 제한, 파일 크기 상한 설정.
- 보안상 사용자가 지정한 임의 내부망 IP/localhost URL을 서버에서 다운로드하지 않는다(SSRF 방어, 허용 도메인/프로토콜 검증).

### 4.3 이용 권리 상태

곡을 로컬에 저장하고 강의실에서 재생해도 되는지 관리자가 확인한 결과를 기록한다.

| 값 | 의미 |
|---|---|
| `UNKNOWN` | 기본값. 미확인 |
| `CONFIRMED` | 관리자가 근거를 확인함 |
| `DENIED` | 사용 불가로 판단 |

- 근거(`rights_basis`): `OWNED_FILE`(운영자가 적법하게 보유), `LICENSED`(이용 허락/라이선스), `CC_LICENSE`, `PUBLIC_DOMAIN`, `OTHER`(메모 필수).
- 규칙:
  - `rights_status = CONFIRMED`가 아니면 `media_status`는 `READY`가 될 수 없다.
  - `UNKNOWN`인 곡은 어떤 Provider도 자동 다운로드하지 않는다.
  - `DENIED`로 변경하면 `media_status`는 `BLOCKED`가 되고, 해당 곡이 포함된 플레이리스트에 경고를 표시하며 재생 시 건너뛴다.
  - 권리 상태 변경은 확인자·시간과 함께 기록한다.

## 5. 권장 아키텍처

```text
[학생/관리자 브라우저]
         |
   HTTP (같은 Wi-Fi 내부망, 세션 쿠키; HTTPS는 보류)
         |
 [FastAPI Web/API] ------- [PostgreSQL]
         |                        |
         |                  사용자/세션/신청/승인/편성/일정/이력
         |
  [Scheduler Worker] ---- [Job/상태 저장]
         |
 [Player Controller] ---- [MPD daemon] ---- [Ubuntu 오디오 출력] ---- [앰프/스피커]
         |
 [Media Worker] --------- [/srv/ax2-jukebox/media]
     (권한 있는 음원만)
```

- MPD에 명령을 보내는 주체는 **Player Controller 하나로 일원화**한다. API와 스케줄러는 Player Controller를 통해서만 재생을 제어한다(명령 경합 및 페이드 충돌 방지).

### 5.1 기술 스택

| 영역 | 권장 기술 | 비고 |
|---|---|---|
| 프론트엔드 | Bootstrap + Jinja2 또는 React | MVP는 서버 렌더링으로 개발량 최소화 |
| API | Python 3.12+ / FastAPI | OpenAPI 문서 기본 제공 |
| 인증 | argon2-cffi, 서버 측 세션 | 외부 인증 서비스 없음 |
| DB | PostgreSQL | 기존 DB 재사용 가능(전용 DB/스키마 권장) |
| 재생 | MPD + python-mpd2 | MPD는 로컬만 수신하도록 설정 |
| 스케줄 | APScheduler | 단일 전용 스케줄러 프로세스, 다중 실행 방지 |
| 변환 | FFmpeg | 허가된 파일에만 적용 |
| 다운로드 어댑터 | yt-dlp(선택) | 이용 권한이 확인된 소스에 한정 |
| 메타데이터 | YouTube oEmbed(기본), YouTube Data API(선택, 길이 조회) | API 키는 환경변수 |
| 상태 전달 | SSE 또는 3~5초 폴링 | MVP에서는 폴링 |
| 서비스 구동 | systemd, (선택) Caddy | 재시작; 역방향 프록시·TLS는 HTTPS 결정 시 도입 |
| 버전 관리 | GitHub | 비밀값·음악 파일 미커밋 |

### 5.2 강의실 하드웨어 검증

- Ubuntu 서버 → USB DAC/3.5mm/HDMI 중 실제 오디오 출력 경로 선택.
- 앰프 입력과 출력 포트 호환 확인(필요한 케이블/어댑터는 사전 실물 점검).
- ALSA/PipeWire/PulseAudio 충돌 여부와 MPD 서비스 계정 오디오 접근 권한 확인.
- OS 볼륨과 MPD 볼륨, 앰프 물리 볼륨을 구분해 설정.
- 강의 시작 시 즉시 끌 수 있는 물리적/웹 비상 정지 경로 제공.

## 6. 데이터 모델

| 테이블 | 주요 컬럼 |
|---|---|
| `users` | id, login_id(UNIQUE), name, signup_note, password_hash, must_change_password, role(`STUDENT`/`ADMIN`), account_status(`PENDING_APPROVAL`/`ACTIVE`/`REJECTED`/`DISABLED`), status_changed_by, status_changed_at, reject_note, failed_login_count, locked_until, last_login_at, created_at |
| `sessions` | id, user_id, token_hash(UNIQUE), csrf_token_hash, expires_at, created_at, last_seen_at, user_agent |
| `tracks` | id, youtube_video_id(UNIQUE), source_url, title, artist_or_channel, duration_seconds, thumbnail_url, media_status, media_path, sha256(UNIQUE, NULL 허용), rights_status, rights_basis, rights_note, rights_checked_by, rights_checked_at, created_at |
| `requests` | id, user_id, track_id, comment, status(`PENDING`/`APPROVED`/`REJECTED`/`CANCELLED`), reviewed_by, reviewed_at, reject_reason, reject_note, cancelled_at, created_at |
| `wishlists` | id, user_id, track_id, is_public(기본 false), created_at; UNIQUE(user_id, track_id) |
| `playlists` | id, name, description, is_active, created_by, created_at, updated_at |
| `playlist_items` | id, playlist_id, track_id, position; UNIQUE(playlist_id, position) **DEFERRABLE INITIALLY DEFERRED** |
| `schedules` | id, name, weekdays(`smallint[]`, ISO 1=월…7=일), start_time, end_time, timezone(기본 `Asia/Seoul`), playlist_id, volume, fade_in_seconds(기본 3), fade_out_seconds(기본 3), rejoin_policy(`RESTART`/`SKIP`), playback_mode(`SEQUENTIAL`/`SHUFFLE`), on_playlist_end(`REPEAT`/`STOP`), is_enabled, created_at, updated_at |
| `schedule_exceptions` (P1) | id, date, schedule_id(NULL=전체), reason, created_by, created_at |
| `playback_events` | id, track_id, schedule_id(NULL=수동), trigger(`SCHEDULE`/`MANUAL`/`REJOIN`), started_at, ended_at, result, error_code |
| `control_audit_logs` | id, actor_id, action, payload, created_at |
| `media_jobs` | id, track_id, provider, job_status, progress, error_message, started_at, completed_at |
| `system_settings` | key, value_json, updated_by, updated_at |

**제약 및 규칙**
- `tracks`는 음원 고유 엔티티, `requests`는 학생별 신청 기록으로 분리한다(중복 신청 허용 정책과 중복 음원 저장 방지).
- `requests`: 같은 (user_id, track_id)에 대해 `PENDING`/`APPROVED` 상태는 하나만 존재(부분 UNIQUE 인덱스).
- `schedules`: `start_time < end_time`, `0 ≤ volume ≤ 100`, `0 ≤ fade_* ≤ 30`, weekdays 값은 1–7 CHECK. 시간 겹침 검사는 애플리케이션에서 수행.
- `playlist_items` 순서 변경(`PUT /items`)은 단일 트랜잭션에서 일괄 갱신한다.
- `users`: CLI로 생성된 관리자는 `ACTIVE`로 시작한다. `role` 변경은 CLI만 수행한다.
- 세션 토큰·CSRF 토큰은 해시만 저장한다.
- 모든 시간 기록은 UTC 저장, 화면/반복 스케줄은 `Asia/Seoul` 기준으로 표시·실행한다.
- 승인(`APPROVED`)과 재생 가능(`READY`)은 서로 다른 상태다.

**`system_settings` 기본 키**

| key | 기본값 |
|---|---|
| `max_volume` | 40 |
| `manual_play_auto_stop_minutes` | 30 |
| `max_pending_requests_per_user` | 5 |
| `max_pending_signups` | 100 |
| `signup_enabled` | true |
| `max_track_duration_seconds` | 600 (초과 시 경고) |
| `rejoin_min_remaining_seconds` | 120 |
| `show_requester_names` | false |
| `output_device` | (MPD output 이름) |

## 7. API 명세

**공통 규칙**
- 경로 접두어 `/api`, JSON 요청/응답.
- 에러 형식: `{"error": {"code": "STRING_CODE", "message": "설명"}}`.
- 상태 코드: 401(미인증), 403(권한 없음/승인 대기·거절 계정), 404, 409(충돌·중복·상태 전이 불가), 422(검증 실패).
- 목록 API는 `?limit=&offset=` 페이지네이션, 응답에 `total` 포함.
- 쓰기 요청(POST/PUT/PATCH/DELETE)은 `X-CSRF-Token` 헤더 필수.

### 7.1 인증·사용자

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| POST | `/api/auth/signup` | 공개 | 회원가입 → `PENDING_APPROVAL`(아이디 중복 409, `signup_enabled = false`면 403) |
| POST | `/api/auth/login` | 공개 | 로그인(잠금 시 423), 세션 발급 |
| POST | `/api/auth/logout` | 로그인 | 세션 폐기 |
| GET | `/api/auth/me` | 로그인(승인 대기 포함) | 현재 사용자·역할·`account_status`·`must_change_password`·CSRF 토큰 |
| POST | `/api/auth/password` | 로그인 | 비밀번호 변경(현재 비밀번호 확인) |
| GET | `/api/admin/users?status=` | ADMIN | 사용자 목록(승인 대기함은 `status=PENDING_APPROVAL`) |
| POST | `/api/admin/users/{id}/approve` | ADMIN | 가입 승인 → `ACTIVE`(감사 로그) |
| POST | `/api/admin/users/{id}/reject` | ADMIN | 가입 거절 → `REJECTED`(`reject_note`, 감사 로그) |
| POST | `/api/admin/users/{id}/disable`, `/enable` | ADMIN | 비활성화/재활성화(STUDENT만, 세션 즉시 폐기) |
| POST | `/api/admin/users/{id}/reset-password` | ADMIN | 학생 임시 비밀번호 발급(STUDENT만, 1회 표시, 감사 로그) |

> ADMIN 권한 부여·회수 및 관리자 비밀번호 재설정 API는 두지 않는다(CLI 전용).

### 7.2 신청·곡

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| POST | `/api/youtube/preview` | 로그인 | URL → video_id·메타데이터·중복/기존 트랙 정보 |
| POST | `/api/requests` | STUDENT | 신청 생성(중복 409, 한도 초과 409) |
| GET | `/api/requests/me` | STUDENT | 내 신청 내역 |
| DELETE | `/api/requests/{id}` | STUDENT | 본인 `PENDING` 신청 취소 → `CANCELLED` |
| GET | `/api/admin/requests?status=` | ADMIN | 신청 승인함 |
| POST | `/api/admin/requests/{id}/review` | ADMIN | 승인/반려(`decision`, `reject_reason`, `reject_note`) |
| GET | `/api/tracks?status=approved\|ready&q=&sort=` | 로그인 | 승인/재생 가능 곡 조회 |
| GET | `/api/tracks/{id}` | 로그인 | 곡 상세 |
| PATCH | `/api/admin/tracks/{id}` | ADMIN | 메타데이터 수동 수정, 이용 권리 상태·근거 입력 |
| POST | `/api/admin/tracks/{id}/metadata/refresh` | ADMIN | 메타데이터 재조회 |

### 7.3 음원

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| POST | `/api/admin/tracks/{id}/media` | ADMIN | 권리 확인된 음원 파일 업로드(`rights_status ≠ CONFIRMED`면 409) |
| GET | `/api/admin/media-jobs?status=` | ADMIN | 음원 처리 작업 조회 |
| POST | `/api/admin/media-jobs` | ADMIN | 승인된 소스 다운로드/변환 작업 생성(P1) |
| POST | `/api/admin/media-jobs/{id}/retry` | ADMIN | 실패 작업 재시도(P1) |

### 7.4 Wish List

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| GET | `/api/wishlist` | STUDENT | 내 Wish List |
| POST | `/api/wishlist` | STUDENT | 추가(`url` 또는 `track_id`, `is_public`) |
| PATCH | `/api/wishlist/{track_id}` | STUDENT | 항목 공개 여부 변경 |
| POST | `/api/wishlist/visibility` | STUDENT | 전체 공개/비공개 일괄 변경 |
| DELETE | `/api/wishlist/{track_id}` | STUDENT | 삭제 |
| POST | `/api/wishlist/{track_id}/request` | STUDENT | 신청으로 전환 |
| GET | `/api/users/{id}/wishlist` | 로그인 | 해당 사용자의 공개 항목만 조회 |

### 7.5 편성·일정

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| GET | `/api/playlists`, `/api/playlists/{id}` | 로그인 | 조회(항목·총 길이·재생 불가 곡 경고 포함) |
| POST | `/api/playlists` | ADMIN | 생성 |
| PATCH | `/api/playlists/{id}` | ADMIN | 이름·설명·활성화 변경 |
| DELETE | `/api/playlists/{id}` | ADMIN | 삭제(스케줄 참조 시 409) |
| PUT | `/api/playlists/{id}/items` | ADMIN | 전체 순서 일괄 저장(`READY` 아닌 곡 포함 시 422) |
| GET | `/api/schedules` | 로그인 | 일정 목록 |
| GET | `/api/schedules/upcoming` | 로그인 | 오늘/이번 주 구간, 다음 재생 시각 |
| POST | `/api/schedules` | ADMIN | 등록(겹침 409 + 충돌 스케줄) |
| PATCH | `/api/schedules/{id}` | ADMIN | 변경·활성화/비활성화(겹침 409) |
| DELETE | `/api/schedules/{id}` | ADMIN | 삭제 |
| GET/POST/DELETE | `/api/admin/schedule-exceptions` | ADMIN | 휴강·공휴일 예외(P1) |

### 7.6 재생·운영

| HTTP | 엔드포인트 | 권한 | 목적 |
|---|---|---|---|
| GET | `/api/player/status` | 로그인 | 현재 곡, 진행, 상태, 볼륨, 다음 곡, 재생 원인, MPD/장치 상태 |
| POST | `/api/player/control` | ADMIN | `action`: `play`/`pause`/`resume`/`stop`/`next`/`volume`/`mute`/`unmute`/`resume_schedule`; 볼륨은 상한으로 제한 후 `applied_volume`, `clamped` 반환 |
| POST | `/api/player/emergency-stop` | ADMIN | 페이드 없이 즉시 정지 + 현재 구간 자동 재개 중지 |
| GET | `/api/player/stream` | 로그인 | SSE 상태 스트림(P1) |
| GET | `/api/admin/settings` | ADMIN | 운영 설정 조회 |
| PATCH | `/api/admin/settings` | ADMIN | 운영 설정 변경(감사 로그) |
| GET | `/api/admin/audit-logs` | ADMIN | 감사 로그 |
| GET | `/api/admin/playback-events` | ADMIN | 재생 이력 |
| GET | `/health/live` | 공개 | 프로세스 생존 |
| GET | `/health/ready` | 로컬/ADMIN | DB·MPD·스케줄러 준비 상태 |

## 8. 화면 요구사항

**공통**
- 로그인: 아이디/비밀번호 입력, 잠금 안내.
- 회원가입: 아이디·이름·비밀번호·가입 메모, 비밀번호 규칙 안내, 가입 후 승인 대기 안내.
- **승인 대기 화면**("관리자 승인을 기다리는 중", 상태 자동 새로고침), 거절/비활성 안내 화면.
- 비밀번호 변경(임시 비밀번호 로그인 시 강제 이동).

**학생 화면**
- 홈: 지금 재생 중인 음악, 오늘 일정, 다음 곡.
- 음악 신청: YouTube URL 입력, 메타데이터 미리보기, 중복 확인, 신청 제출, 남은 신청 한도 표시.
- 내 신청곡: 승인 대기/승인/반려/취소, 사유, `PENDING` 취소 버튼.
- Wish List: 개인 목록, 검색, 항목별 공개 토글, 전체 공개/비공개, 신청으로 전환.
- 선곡 리스트·일정: 확정된 플레이리스트, 재생 스케줄 조회.

**관리자 화면**
- 운영 대시보드: 서버·MPD·오디오 장치 상태, 현재 곡, 진행률, 볼륨, 다음 곡, 재생 원인, **비상 정지 버튼(항상 노출)**.
- 사용자 관리: **가입 승인함**(아이디·이름·가입 메모·요청 시각, 승인/거절), 학생 임시 비밀번호 발급, 사용자 목록, 비활성화/재활성화. 대시보드에 승인 대기 건수 배지.
- 신청 승인함: 신청곡 상세, 원본 URL 열기, 승인/반려.
- 음원 라이브러리: 이용 권리 상태·근거, 로컬 파일, 업로드/변환 작업 및 실패 처리.
- 플레이리스트 편집기: 드래그 정렬, 사용 가능 여부, 총 길이.
- 스케줄 편집기: 주간 시간표, 중복 경고, 볼륨·페이드·재합류·재생 모드, 예외 설정.
- 운영 설정: 최대 볼륨, 신청 한도 등 `system_settings`.
- 제어·운영 이력: 수동 조작, 재생 로그, 오류.

## 9. 비기능 요구사항

| ID | 항목 | 기준 |
|---|---|---|
| NFR-001 | 비용 | 서버/DB 기존 자원 활용, 월 고정 외부 서비스 비용 0원 목표(전기·네트워크·음원 권리·주변기기·도메인 비용 제외) |
| NFR-002 | 가용성 | 네트워크 단절 중에도 로컬 파일의 예약 재생 가능(서버/스케줄러/MPD 정상 시); 외부 인증 의존이 없어 내부망 로그인·제어 가능 |
| NFR-003 | 응답 | 로컬망에서 제어 명령 후 2초 이내 상태 변화 목표 |
| NFR-004 | 보안 | TLS(보류, 2.1 전송 보안), 비밀번호 argon2id 해시·로그인 잠금·rate limit, 가입 승인, CLI 전용 관리자 권한 부여, 역할 검사, 세션 쿠키 보호, CSRF 토큰, MPD 외부 포트 비노출 |
| NFR-005 | 장애 대응 | systemd 재시작, 상태 점검, 오류 원인 표시, 비정상 볼륨 증가 방지 |
| NFR-006 | 안전 | 최대 볼륨 상한(기본 40%, 현장 보정), 시작·종료 페이드, 비상 정지 |
| NFR-007 | 데이터 보호 | 환경변수/Secret으로 비밀값(세션 서명 키, DB 비밀번호 등) 관리, 사용자 입력 검증, 로그에 토큰 미출력 |
| NFR-008 | 백업 | DB 일일 백업, 플레이리스트/일정 복원 절차; 음원은 별도 백업 정책 |
| NFR-009 | 관측성 | 스케줄 실행/음원 누락/MPD 연결 실패/로그인 실패를 구조화 로그로 기록 |
| NFR-010 | 접근성 | 모바일 반응형, 키보드로 기본 조작, 상태를 색상 외 텍스트로도 표시 |

## 10. 운영 정책과 예외 상황

| 항목 | 정책 |
|---|---|
| **강의 중 방해 방지** | 스케줄 종료 시 정지는 수동 조작 여부와 무관하게 **항상 실행**한다. 수동 재생은 관리자만 허용 |
| **수동 vs 예약 (FR-019)** | 구간 내에서는 관리자 수동 조작이 우선(곡 변경·볼륨·일시정지 등). 구간 밖 수동 재생은 허용하되 `manual_play_auto_stop_minutes`(30분) 후 자동 정지. 수동 재생 중 예약 구간이 시작되면 예약 재생으로 전환 |
| **수동 정지 후** | 해당 구간은 정지 유지. `resume_schedule` 명령 또는 다음 구간 시작 시 재개 |
| **비상 정지** | 페이드 없이 즉시 정지, 현재 구간 자동 재개 중지, 감사 로그 기록 |
| **재부팅 재합류** | `rejoin_policy = RESTART`(기본): 플레이리스트 처음부터 페이드 인 재생. 남은 시간이 `rejoin_min_remaining_seconds`(2분) 미만이면 재합류하지 않음. `SKIP`: 해당 구간 건너뜀. 결과는 `playback_events`(trigger=`REJOIN`)와 로그에 기록 |
| **플레이리스트 소진** | `on_playlist_end = REPEAT`(기본)이면 처음부터 반복, `STOP`이면 페이드아웃 후 정지 |
| **라이브러리 부족** | 재생 가능한 `READY` 곡이 없으면 재생하지 않고 정지 상태 유지 + 관리자 대시보드 경고. 기본 제공 음악은 출처 확정 전까지 두지 않음 |
| **볼륨 상한** | 요청 범위는 0–100, 서버에서 `max_volume`(40)으로 제한 후 적용하고 `clamped: true` 반환. 상한 변경은 감사 로그 대상. 웹 볼륨 상한이 물리 앰프 설정을 대신하지 않으므로 현장에서 별도 조정 |
| **중복 신청** | 같은 사용자의 같은 곡이 `PENDING`/`APPROVED`이면 409. 다른 사용자의 신청은 허용하고 같은 트랙에 연결. 트랙 데이터는 video ID당 하나 |
| **신청 한도** | 학생 1인당 동시 `PENDING` 최대 `max_pending_requests_per_user`(5)건 |
| **곡 길이** | `max_track_duration_seconds`(10분) 초과 시 신청 화면에서 경고, 승인 시 관리자 확인 표시 |
| **신청 취소** | 본인의 `PENDING` 신청만 취소 가능. 레코드는 삭제하지 않고 `CANCELLED`로 보존 |
| **반려 사유** | `DUPLICATE` / `INAPPROPRIATE` / `NO_RIGHTS` / `TOO_LONG` / `OTHER`(메모 필수) |
| **신청자 노출** | 기본 비노출(관리자만 확인), `show_requester_names`로 변경 가능 |
| **Wish List 공개** | 항목 단위, 기본 비공개 |
| **가입 승인** | 신규 가입 계정은 `PENDING_APPROVAL`. 관리자가 승인해야 이용 가능. 거절된 계정은 관리자가 나중에 승인으로 바꿀 수 있음. 승인 대기 상한 초과 시 신규 가입 요청 일시 차단 |
| **비밀번호 분실** | 관리자가 학생 임시 비밀번호 발급(1회 표시, 다음 로그인 시 변경 강제). 관리자 비밀번호는 CLI로만 재설정 |
| **세션** | 학생 7일, 관리자 12시간 만료(활동 시 갱신). 사용자 비활성화 시 해당 세션 즉시 폐기 |
| **충돌** | 활성 스케줄 간 시간 겹침은 저장 시 차단 |
| **접속 범위** | 강의실 같은 Wi-Fi 내부망에서만 접속. 인터넷에 웹·MPD 포트를 노출하지 않음. 외부 원격 접속이 필요해지면 HTTPS와 VPN/Tailscale 등 안전한 연결을 함께 재검토 |
| **다운로드 실패** | 신청 및 승인 이력은 유지하되 해당 곡은 `READY`가 되기 전 편성 불가 |
| **이용 권리** | 출처/권한 정보가 없는(`UNKNOWN`) 음원은 관리자에게 경고하고 로컬 다운로드 대상으로 자동 처리하지 않음(4.3) |

## 11. 단계별 개발 및 QA/CD 운영 기준

이 프로젝트는 로컬 스터디용이므로 작은 단계에서 검증하고, 마지막 단계만 실제 강의실 앰프로 검증한다. 단계마다 요구사항·테스트·배포 범위를 사전에 고정한다.

| 단계 | 구현 범위 | QA 기준 | 배포/운영 정책 |
|---|---|---|---|
| **1단계: 로컬 플레이어 MVP** | Ubuntu MPD, 파일 등록, 재생 제어, 관리자 웹(CLI로 만든 로컬 관리자 로그인), 볼륨 | MPD 연결, 재생/정지, 볼륨 상한, 앱 재시작, 모의 오디오 출력 테스트 PASS | 개발 서버 전용, 강의실 앰프 실제 출력은 필요시 승인 후 |
| **2단계: 학생 신청/승인** | 회원가입·가입 승인·비밀번호 관리, CLI 관리자 생성, 권한, URL 신청, 메타데이터, 승인/반려/취소 | 비밀번호 해시·잠금·rate limit, 승인 대기 계정 차단(403), 승인 후 이용 가능, 웹에서 ADMIN 승격 불가, API 권한 테스트, URL/중복 검증, 상태 전이 PASS | 로컬/테스트 환경, 외부 공개 금지 |
| **3단계: 편성/자동 스케줄** | 플레이리스트, 시간대, 페이드, 재합류, 상태 모니터링 | 경계시각, 겹침 방지, 재시작 복원, 정지 검증 PASS | 실제 강의실 연결 전 드라이런 필수 |
| **4단계: 통합/운영 강화** | Wish List, 이벤트(SSE), 미디어 워커, 이력/알림, 예외 일정 | E2E, 오류 주입, DB 백업/복구, 보안·권한 PASS | 제한 사용자 파일럿 운영 |
| **5단계: 강의실 실운영** | Ubuntu 앰프 출력, 원격 제어, 운영 런북 | 현장 볼륨/장치/재시작/스케줄/비상정지 실증 및 관리자 승인 | 승인된 시간대만 활성화, 롤백 준비 |

> MVP 범위는 **1~3단계**로 정의한다. 12장 수용 테스트는 1~3단계 완료 시점 기준이다.

### 11.1 각 단계 산출물

- 요구사항/수용 기준 체크리스트, 설계 변경 내역
- 코드 및 자동화 테스트, 실행 명령과 테스트 결과
- 화면 변경 시 변경 전/후 캡처(해당 기능에 한함)
- 배포/설정 변경 목록, 재현 가능한 롤백 절차
- GitHub PR의 CI 결과 및 리뷰 이력(저장소 사용 시)

### 11.2 배포 승인 조건

- 자동화 테스트 통과; 권한 우회 및 치명적 취약점 미해결 없음.
- 강의실 운영 영향이 있는 오디오 장치, 시간표, 최대 볼륨의 변경은 관리자 수동 승인 후 적용.
- 운영 DB 마이그레이션은 백업과 복구 경로 확인 후 진행.
- 배포 실패 시 직전 정상 버전과 스케줄 설정으로 복구 가능.

## 12. MVP 수용 테스트 시나리오

1. 관리자 로그인 후 권리 확인(`CONFIRMED`)한 로컬 테스트 MP3를 등록하면 `READY` 상태가 표시된다. 권리 미확인 곡은 업로드가 거부된다.
2. 플레이리스트에 곡을 추가하고 재생하면 Ubuntu 출력 장치에서 음악이 들린다.
3. 관리자가 웹에서 `pause/resume/next/stop`을 조작하면 플레이어가 반응하고 상태 화면도 갱신된다.
4. 관리자가 볼륨을 80으로 요청하면 상한(40)으로 적용되고 `clamped: true`가 반환된다.
5. 예를 들어 시작 시각 2분 전인 스케줄을 생성하면 정시에 페이드 인으로 재생하고 종료 시각에 페이드아웃 후 자동으로 정지한다.
6. 중복 스케줄은 저장되지 않고 충돌 안내가 표시된다.
7. 회원가입한 학생은 승인 대기 화면을 보고, 관리자가 승인한 뒤에야 YouTube URL 신청을 제출하고 `PENDING`을 확인할 수 있다. 승인 전 API 호출은 403이다. 웹/API로는 ADMIN 권한을 부여할 수 없다.
8. 학생이 `PENDING` 신청을 취소하면 `CANCELLED`로 표시되며, `APPROVED` 신청은 취소할 수 없다(409).
9. 관리자가 신청을 승인하면 `APPROVED`로 변경되지만 음원이 준비되지 않았으면 `READY`가 아닌 것으로 표시된다.
10. 학생 계정으로 `/api/player/control`을 호출하면 403이 반환된다.
11. 강의 시간대에 서버 서비스를 재시작했을 때 재합류 정책에 따라 복원되며 로그가 남는다.
12. MPD 중단 또는 오디오 출력 장치 연결 해제 시 관리자가 장애를 식별하고 비상 정지할 수 있다.
13. 인터넷이 끊긴 상태에서도 예약 재생은 지속되고, 내부망에서 관리자로 로그인해 비상 정지할 수 있다.
14. 로그인 5회 실패 시 계정이 10분 잠기고, 관리자가 발급한 임시 비밀번호로 로그인하면 비밀번호 변경 화면으로 이동한다.
15. 원격 관리자 화면에서 현재 재생 현황 및 다음 스케줄을 확인할 수 있다.

## 13. 권장 저장소 구조

```text
ax2-classroom-jukebox/
├── README.md
├── requirements.md
├── .env.example
├── .gitignore
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── auth/              # 회원가입/로그인, 비밀번호 해시, 세션/CSRF
│   │   ├── models/
│   │   ├── services/
│   │   │   ├── player.py
│   │   │   ├── scheduler.py
│   │   │   └── media_providers/
│   │   └── main.py
│   ├── migrations/
│   └── tests/
├── frontend/                  # SSR 사용 시 backend/templates 로 대체
├── deploy/
│   ├── systemd/
│   └── caddy/
├── docs/
│   ├── architecture.md
│   ├── development-guide.md
│   ├── ubuntu-audio-setup.md
│   ├── https-setup.md
│   ├── operation-runbook.md
│   └── qa-checklist.md
└── scripts/
    └── jukebox-admin          # 관리자 생성/승격/회수/비밀번호 재설정 CLI
```

`.gitignore`에 `.env`, 오디오 파일, DB 덤프, 임시 다운로드 파일을 제외한다.

## 14. 착수 전 확인 사항

- [ ] Ubuntu 배포판/버전과 서버 접속 방식(SSH/로컬)
- [ ] 앰프 연결 방식(USB DAC, 3.5mm, HDMI 등) 및 실제 사용 가능한 출력 장치
- [ ] 기존 PostgreSQL 설치 여부와 전용 DB/스키마 사용 여부
- [x] 사용자 계정 방식 → 자체 회원가입 + 관리자 가입 승인, 관리자 계정은 CLI로만 생성 (Google OAuth 미채택, 부록 A)
- [x] 호스팅 → 강의실 Ubuntu 서버 단일 구성(Vercel 분리 배포 미채택, 부록 A)
- [x] 접속 범위 → 강의실 같은 Wi-Fi 내부망
- [ ] (보류) HTTPS 적용 여부·방식: Caddy 내부 CA / Tailscale 인증서 / 자체 도메인. 외부 원격 접속이 필요해지거나 내부망 위험이 커지면 재검토
- [ ] 최초 관리자 계정 아이디 및 CLI 생성 담당자
- [ ] 로컬 음원 확보 방식 및 사용·보관·강의실 재생 권한 확인
- [x] 수동 재생/예약 재생 우선순위 정책 → 10장 확정
- [ ] 운영 기본 최대 볼륨(기본 40%) 현장 보정 및 관리 책임자

## 15. 권장 첫 개발 작업

**STEP 01 — Ubuntu 오디오 출력 + MPD 연결 검증**

1. Ubuntu 버전 및 오디오 출력 장치를 조사한다.
2. 권한을 확보한 30초 테스트 음원으로 서버 로컬 재생을 확인한다.
3. MPD를 설치·설정하고 로컬 제어 명령으로 재생/일시정지/정지/볼륨 변경을 검증한다.
4. 간단한 FastAPI 상태 조회·제어 API를 연결한다(CLI로 만든 로컬 관리자 로그인으로 보호, 볼륨 상한 적용).
5. 브라우저 관리자 페이지에 현재 곡, 상태, 재생 버튼, 볼륨 슬라이더, 비상 정지 버튼을 표시한다.
6. 정상/오류 상황을 문서에 기록한 뒤 STEP 02(회원가입·가입 승인·학생 신청·승인)로 진행한다.

---

**구현 메모:** 본 문서는 코딩 시작을 위한 기능 및 운영 요구사항이며, 외부 공개 서비스가 아닌 로컬 학습 프로젝트를 전제로 한다. 음원 추출·저작권·약관 준수는 개발 기능과 별도 검증 사항으로 다룬다.

---

## 부록 A. 결정 기록

### ADR-001 Google OAuth 로그인 미채택 → 자체 회원가입 + 관리자 승인

- **상태:** 결정 (v1.1)
- **검토안:** Google OAuth/OpenID Connect 로그인 + 첫 로그인 시 관리자 가입 승인.
- **미채택 사유:**
  1. **redirect URI 제약:** Google은 OAuth redirect URI로 공인 도메인의 HTTPS 주소만 허용하고, 사설 IP(`192.168.x.x`)·`.local` 주소는 허용하지 않는다. 내부망의 강의실 서버에는 그대로 적용할 수 없고 도메인·DNS·인증서 또는 터널 구성이 추가로 필요하다.
  2. **Vercel로 HTTPS를 확보하는 안의 한계:** Vercel은 요청 시에만 실행되는 서버리스 환경이라 MPD 제어, 상시 실행 스케줄러(APScheduler), 로컬 음원 저장, SSE 상시 연결을 수행할 수 없다. 채택하려면 시스템을 클라우드(웹/API + 클라우드 PostgreSQL)와 강의실 플레이어 에이전트로 분리해야 하며, 구성 요소와 개발량이 늘고 제어 응답 2초 목표(NFR-003)에 불리하다.
  3. **인터넷 의존:** 인터넷·Google 장애 시 신규 로그인이 불가하므로 비상용 로컬 관리자 계정을 별도로 유지해야 해 인증 경로가 둘이 된다.
  4. **비용:** 도메인 구입 시 월 고정 외부 비용 0원 목표(NFR-001)와 충돌할 수 있다.
- **결정:** 아이디/비밀번호 자체 회원가입 + 관리자 가입 승인. 관리자 계정은 CLI로만 생성한다. 호스팅은 강의실 Ubuntu 서버 단일 구성으로 유지한다.
- **감수하는 비용:** 비밀번호 해시 저장·잠금·rate limit을 직접 구현해야 하고, 비밀번호 분실 시 관리자가 수동으로 초기화해야 한다. HTTPS는 내부망 전제로 보류했다(2.1 전송 보안).
- **재검토 조건:** 외부 공개 도메인을 확보하거나 클라우드 분리 아키텍처로 전환할 때. 이 경우 `users`에 `auth_provider`, `google_sub` 컬럼을 추가해 기존 계정 체계를 유지한 채 Google 로그인을 추가할 수 있다.
