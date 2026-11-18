# AI Core Project — 하이브리드 식단 플래너 (`AI_Project`)

> **세션 연속성 단일 기록 파일 (Single Source of Truth)**
> 이 파일은 Antigravity 새 세션이 `AI_Project` 작업을 시작/수정할 때 자동 로드되는 프로젝트 기준서이자 진행상황·백로그 기록장이다.

---

## 0. 에이전트 세션 인계 규칙 (문서 증식 금지)

1. **작업 시작 전 확인:** 코드를 수정하기 전에 아래 **§1 고정 목표·규약**과 **§3 우선순위 백로그**를 먼저 확인해 기존 아키텍처·모드명·API 스펙이 흔들리지 않게 한다.
2. **단일 파일 갱신 원칙:** 진행상황, 결정사항, 이슈 기록을 위해 `TODO.md`, `PROGRESS.md`, `NOTES.md` 등 **새 마크다운 파일을 추가로 만들지 않는다.** 오직 이 파일(`AGENTS.md`)만 갱신한다.
3. **작업 완료 직후 업데이트:** 기능 추가·버그 수정·API 변경·테스트 완료 시 이 파일의 **§2 현재 구현 상태**, **§3 백로그 체크박스**, **§4 최근 마일스톤 로그**(최신 6건 이내 유지, 오래된 항목은 한 줄로 압축)를 실제 실행/관찰한 결과 기준으로 업데이트한다.
4. **보안·검증:** `.env` 실제 키 값을 절대 읽거나 출력/커밋하지 않는다. 수정 후 `.venv\Scripts\pytest.exe -v backend/tests/test_core.py`로 회귀 검증한다.

---

## 1. 고정 목표 & 아키텍처 기준 (Immutable Spec)

- **프로젝트:** SSAFY AI 코어 프로젝트(3회차) 명세(`F3001`~`F3021`, `NF3001`~`NF3007`) 기반 영양성분 선형 조합 + 가성비 역산 멀티 Agent 식단 플래너.
- **기술 스택:** Python 3.12 · FastAPI · SQLite(WAL) · LangGraph · Pydantic · Vanilla HTML/CSS/JS (프론트·백엔드 완전 분리 `NF3006`).
- **외부 API 4종 (`Naver Shopping` 영구 제거):**
  1. 식약처 식품영양성분 DB (`FoodNtrCpntDbInfo03`, `DATA_GO_KR_API_KEY`)
  2. 한국소비자원 생필품 가격정보 XML (`B551919/ProductPriceInfoService`, `DATA_GO_KR_API_KEY`) + 2순위 보조 Open Food Facts (무키 JSON)
  3. 카카오 로컬 API (`KAKAO_REST_API_KEY`)
  4. OpenAI / SSAFY GMS 호환 LLM (`OPENAI_API_KEY`)
- **표준 실행 모드명 (구 `"A"/"B"/"C"` 문자열 혼용 금지):**
  - `online_bulk` (모드 A: 기간·총 끼니 기반 대량 장바구니)
  - `cvs_instant` (모드 B: 편의점 2~3품목 조합 + 반경 500m 매장 안내)
  - `cheating_recovery` (모드 C: 초과 칼로리 복구 + 단백질 `1.6g/kg` 고정·탄지 감량)
  - `item_swap` (1:1 품목 핀셋 스왑), `profile_calc` (신체/목표 역산), `general_chat` (Tool 미호출 일반 안내)
- **핵심 불변 제약:**
  - `USE_MOCK=false`에서 API 키 누락 시 `MISSING_API_KEY` 반환 (`F3019`), 누락 영양성분 임의 `0` 대체 금지 (`F3003`).
  - 정상 경로에서 Worker·Logging Agent LLM 호출 `0회` 유지 (`F3013`, `F3020`, `F3021`).
  - 일일 칼로리 안전 가드레일 하한: 남성 `1,400kcal` / 여성 `1,100kcal` (`F3008`).
  - 재계획 상한 `MAX_REPLAN=3` (`NF3004`), Worker 재시도 상한 `MAX_WORKER_RETRY=2`, `fixed_args` 변경 금지.
  - 실행 로그는 추가/조회/삭제만 허용, 수정(PUT/PATCH) API 미제공 (`F3014`).

---

## 2. 현재 구현 상태 (Last Verified: 2026-11-18)

- **원격 저장소:** `https://github.com/RyuHeebaek/AI_Core_Project.git` (`main` 브랜치)
- **자동화 테스트:** `pytest -v backend/tests/test_core.py` → **10 passed in 4.00s** (`USE_MOCK=true` + 실시간 XML/JSON/카카오 파서 Mock + 감사 회귀 #1~#15 검증).
- **구현 완료 모듈:**
  - **Backend API:** `POST /api/chat`, `GET/PUT /api/profiles/{session_id}`, CRUD `/api/plans` (`PUT`/`PATCH` 및 시간 중복 경고), `GET/POST analyze/DELETE /api/logs`, `GET /api/health`.
  - **Agents (`backend/app/agents/`):** `PlannerAgent`(`planner.py`), `WorkerAgent`(`worker.py`, 병렬 조회+실패 캐시), `NutritionValidatorAgent`(`validator.py`), `ResponderAgent`(`responder.py`), `LoggingSubAgent`(`logging_agent.py`), LangGraph 조립(`graph.py`).
  - **Tools (`backend/app/tools/`):** `base.py`(`http_get` 세션 풀), `nutrition_tool.py`, `price_tool.py`(TTL 캐시+에러 XML 방어+채널 필터), `local_tool.py`, `optimizer_tool.py`(모드 A/B/C+bulk 스왑+한국어 복합명사 매칭).
  - **Frontend (`frontend/`):** 프로필 역산 사이드바(세션별 분리), 채팅+실행 타임라인(재계획 강조), 식단 계획 관리(로컬 날짜 기본값+세션 필터+CRUD), 실행 로그/F3020 이상 탐지 3탭 UI.

---

## 3. 남은 작업 / 감사 백로그 (Audit Baseline: 2026-11-18)

> Ponytail 전수 감사 15건 모두 수정·테스트 반영 완료 (`#1~#15` Done). 추가 요구사항이나 실 API 연동 이슈가 생기면 이 섹션에 `[ ]` 항목을 추가한다.

### [P0 - Must Fix] 핵심 도메인·파싱·세션 버그 (완료)
- [x] **#1 Mode C `gender` 필수 검증 및 직접 호출 가드:** `planner.py`, `optimizer_tool.py`, `index.html`.
- [x] **#2 Mode A 저장 프로필 + 다단백 목표 1~2회분 조합 탐색:** `optimizer_tool.py` (`_scale_serving_qty`, `_build_bulk_orders`).
- [x] **#3 `item_swap` 후속 턴 `base_mode` 복귀 및 bulk 스왑 유지:** `planner.py`, `chat.py`, `state.py`, `optimizer_tool.py`.
- [x] **#4 소비자원 HTTP 200 에러 XML 비캐싱 + 6h TTL 카탈로그 캐시:** `price_tool.py`.
- [x] **#5 `100+20g` 백트래킹 수정·`12팩`+중량 결합·`500g` 단품 임계치 통일:** `price_tool.py`, `optimizer_tool.py`.
- [x] **#6 `snack` 제외·중복 `food_id` 금지·조리법 유지·한국어 끝명사 매칭:** `nutrition_tool.py`, `optimizer_tool.py`.
- [x] **#7 목표 체중 선입력 시 `finditer` 스팬 체크로 현재 체중 보호:** `planner.py`.
- [x] **#8 비지명 `~역` 오탐 제거:** `planner.py` (`_NON_LOCATION_SUFFIXES`).

### [P1 - Should Fix] 안정성·성능·테스트·정리 (완료)
- [x] **#9 결정론적 실패 결과 재계획 캐시 & `optimize_meal_combo` 로그 구분:** `worker.py`.
- [x] **#10 소비자원 품목별 가격 1h 캐시·개별 예외 격리·최대 12개 채널 조기 종료:** `price_tool.py`.
- [x] **#11 프론트엔드 로컬 한국 날짜(`todayLocalDate()`) 적용:** `frontend/app.js`.
- [x] **#12 UI 세션별 `profileSavedSessionId` 격리 & `/api/plans?session_id=` 필터링:** `frontend/app.js`.
- [x] **#13 `#1~#10` 회귀 테스트 보강 (`10 passed`):** `backend/tests/test_core.py`.
- [x] **#14 미사용 `serving_base_g`·`openfoodfacts_items`·구 `"B"`·도달불가 가드 정리:** `nutrition_tool.py`, `price_tool.py`, `planner.py`.

### [P2 - Nice to Have] (완료)
- [x] **#15 `http_get` (`requests.Session` 풀) + 독립 read-only 조회 태스크 병렬 선실행:** `tools/base.py`, `worker.py`, `local_tool.py`, `nutrition_tool.py`, `price_tool.py`.

---

## 4. 최근 마일스톤 로그 (최신 6건 유지)

- **2026-11-18 (Audit Fixes #1~#15 Complete):** Ponytail 전수 감사 15건(Mode C/A 계산·스왑 후속 턴·XML 캐시 오염·정규식·매칭·병렬 조회·UTC 날짜·세션 격리·미사용 코드) 일괄 수정, 회귀 테스트 추가(`10 passed in 4.00s`), `AGENTS.md` 갱신 완료.
- **2026-11-18 (Setup & Audit Baseline):** `AI_Project` 전수 감사 완료(Must 8 / Should 6 / Nice 1), 단일 기록 파일 `AGENTS.md` 신설, `.gitignore` WAL 패턴 추가, Git 초기화 및 로컬 커밋(`a60121d`) 완료.
- **2026-11-18 (API Migration):** 네이버 쇼핑 API 제거 → 한국소비자원 XML(`B551919`) + Open Food Facts 2순위 연동, 식약처 `FoodNtrCpntDbInfo03` 스키마 정렬, 표준 모드명 통일.
- **2026-11-18 (Initial Build):** SSAFY AI 코어 3회차 LangGraph 멀티 Agent 식단 플래너 초안(프로필 역산, 모드 A/B/C, 1:1 스왑, F3020 로깅·이상탐지, 분리형 Vanilla JS UI) 구축.
