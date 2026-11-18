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
- **자동화 테스트:** `.venv/bin/pytest -v backend/tests/test_core.py backend/tests/test_scenarios.py` → **11 passed in 3.46s** + 단독 `test_scenarios.py` + 실 API 키(`.env` 4종, 사전 DB 미생성) 기준 헤드리스 Chrome 전수 E2E(`0` 콘솔/HTTP 오류) 검증 완료.
- **구현 완료 모듈:**
  - **Backend API:** `POST /api/chat`, `GET/PUT /api/profiles/{session_id}`, CRUD `/api/plans` (`PUT`/`PATCH` 및 시간 중복 경고), `GET/POST analyze/DELETE /api/logs`, `GET /api/health`.
  - **Agents (`backend/app/agents/`):** `PlannerAgent`(`planner.py`), `WorkerAgent`(`worker.py`, 병렬 조회+실패 캐시), `NutritionValidatorAgent`(`validator.py`), `ResponderAgent`(`responder.py`), `LoggingSubAgent`(`logging_agent.py`), LangGraph 조립(`graph.py`).
  - **Tools (`backend/app/tools/`):** `base.py`(`http_get` 세션 풀), `nutrition_tool.py`, `price_tool.py`(TTL 캐시+에러 XML 방어+채널 필터), `local_tool.py`, `optimizer_tool.py`(모드 A/B/C+bulk 스왑+한국어 복합명사 매칭).
  - **Frontend (`frontend/`):** 프로필 역산(예산·탄단지 표시 포함), 채팅+실행 타임라인(재계획 강조·품목별 `[1:1 교체]` 버튼), 식단 계획 관리(로컬 날짜 기본값+세션 필터+CRUD), 실행 로그/F3020 이상 탐지 3탭 UI.

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

- **2026-11-18 (Clean-DB Live-API E2E & Swap/Scenario Fixes):** `planner.py`(스왑 시 `previous_combo` 실 API 품목명·`달걀` 계열 직접 매칭, 저장 프로필 `budget_krw` 초기화 동기화, 모드 C 추가답변·2회차 재계획 확장, 스왑 `meals_per_day` 보존), `optimizer_tool.py`(모드 A 유당불내증 다수량 쌍 탐색), `frontend/app.js`(빈 선택 프로필 `null`·세션 전환/전송 시 UI 상태 초기화), `test_scenarios.py`(F3019 무키 검증 환경 격리) 수정 및 사전 DB 없는 실 API(`.env`) Chrome E2E(`STEP 0~7`, 브라우저/HTTP 오류 `0`건)·회귀 11건 통과.
- **2026-11-18 (Ponytail Whole-Repo Audit Fixes #1~#11):** `validator.py`(bulk 스왑 총액 검증), `planner.py`(프로필 우선 병합·비식단 모드 전환 시 맥락 보존), `plans.py`(세션별 중복 경고 격리), `price_tool.py`(가격 API 에러 XML 비캐싱·Mock 채널 정렬·카탈로그 단일 비행 락), `nutrition_tool.py`(실시간 현미 도시락 `main` 분류), `database.py`(WAL `init_db` 1회 설정), `worker.py`·`state.py`(키 누락 선실행 방지·미사용 상태 정리) 수정 및 회귀 테스트 보강(`11 passed in 3.07s`).
- **2026-11-18 (Step 6 Integration & Log Samples):** `USE_MOCK=false` 무키 `MISSING_API_KEY` API 응답(`responder.py`, `chat.py`) 및 `tool_call_end` 출력 요약(`worker.py`) 보강, 대표 시나리오 5종+무키 검증(`11 passed`) 및 제출용 실행 로그 3종(`logs_sample/*.json`) 추출 완료.
- **2026-11-18 (Step 4&5 UI & 5-Scenario Verification):** `backend/tests/test_scenarios.py`(대표 시나리오 5종 자동 검증+로그 요약 출력) 신설, `responder.py`(부분 Tool 실패 `worker_escalations` 사유 노출), `planner.py`·`frontend/`(프로필 예산·탄단지 표시 및 품목별 `[1:1 교체]` 버튼) 보강 (`11 passed in 4.90s`).
- **2026-11-18 (Step 2&3 Compliance & Review Fixes #1~#5):** `optimizer_tool.py`(나트륨 1,500mg `HIGH_SODIUM` 검증, `maintain` 1.5g, ΔP·ΔK 스왑 근접 점수, M/F·별칭 호환, `_disliked_terms` 통합), `planner.py`(재계획 사유 분기 정확도·중복 제거), `worker.py`(`excluded_items` 동기화), `responder.py`(소액 `online_bulk` 대안 하한 보정) 수정 및 회귀 테스트 보강(`10 passed in 3.01s`).
- **2026-11-18 (Audit Fixes #1~#15 & Initial Build):** Ponytail 전수 감사 15건 일괄 수정(`10 passed`), 네이버 쇼핑 제거 → 한국소비자원 XML·Open Food Facts 연동 및 LangGraph 멀티 Agent 구축 완료.
