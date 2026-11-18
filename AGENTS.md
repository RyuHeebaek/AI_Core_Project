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

- **원격 저장소:** `https://github.com/RyuHeebaek/AI_Core_Project.git`
- **자동화 테스트:** `pytest -v backend/tests/test_core.py` → **9 passed in 3.91s** (`USE_MOCK=true` + 실시간 XML/JSON/카카오 파서 Mock 검증).
- **구현 완료 모듈:**
  - **Backend API:** `POST /api/chat`, `GET/PUT /api/profiles/{session_id}`, CRUD `/api/plans` (`PUT`/`PATCH` 및 시간 중복 경고), `GET/POST analyze/DELETE /api/logs`, `GET /api/health`.
  - **Agents (`backend/app/agents/`):** `PlannerAgent`(`planner.py`), `WorkerAgent`(`worker.py`), `NutritionValidatorAgent`(`validator.py`), `ResponderAgent`(`responder.py`), `LoggingSubAgent`(`logging_agent.py`), LangGraph 조립(`graph.py`).
  - **Tools (`backend/app/tools/`):** `nutrition_tool.py`, `price_tool.py`, `local_tool.py`, `optimizer_tool.py`.
  - **Frontend (`frontend/`):** 프로필 역산 사이드바, 채팅+실행 타임라인(재계획 강조), 식단 계획 관리(CRUD+시간 겹침 경고), 실행 로그/F3020 이상 탐지 3탭 UI.

---

## 3. 남은 작업 / 감사 백로그 (Audit Baseline: 2026-11-18)

> Ponytail 전수 감사에서 확인된 개선 대상 (우선순위 순). 수정 완료 시 `[x]` 체크 후 §4에 요약한다.

### [P0 - Must Fix] 핵심 도메인·파싱·세션 버그
- [ ] **#1 Mode C `gender` 미입력 시 복구 계산 누락:** `optimizer_tool.py:L593-611` / `planner.py:L275-281` — 성별 없으면 `calculate_mode_c_targets` 미실행(UI 예시 칩 포함).
- [ ] **#2 Mode A 저장 프로필 + 다단백 목표 실패:** `optimizer_tool.py:L385-418` — 75kg 2끼(`60g/끼`)일 때 1인분 페어만 탐색해 3회 재계획 후 실패 (`1~2인분` 수량 탐색 필요).
- [ ] **#3 `item_swap` 이후 후속 턴 고착 및 bulk 스왑 손실:** `planner.py:L217-232` / `chat.py:L57-64` / `optimizer_tool.py:L544-570` — `base_mode` 분리 및 `swap_target` 단발성 유지 필요.
- [ ] **#4 소비자원 HTTP 200 에러 XML 영구 캐시 오염:** `price_tool.py:L376-417` — 빈 상품/매장 XML 응답을 모듈 캐시에 영구 저장 + TTL 부재.
- [ ] **#5 묶음 수량/중량 파싱·환산 오작동:** `price_tool.py:L31-34,L388-391` / `optimizer_tool.py:L180-195` — `"100+20g"` 역추적 오파싱(`102팩`), `"12팩"` 타이틀에서 `goodTotalCnt` 누락, `501~599g` 단품 가격·영양 불일치.
- [ ] **#6 `snack` 유입·중복 품목 조합·음식 오매칭:** `optimizer_tool.py:L168-177,L221-230,L447-462` / `nutrition_tool.py:L167-168` — `mains` 공백 시 동일 `food_id` 2회 포함, 조리법 토큰 과삭제로 닭가슴살 단가 통일, `"두부"`↔`"두부바"` 부분일치.
- [ ] **#7 목표 체중 선입력 시 현재 체중 오추출:** `planner.py:L16-17,L95-102` — `"목표 체중 65kg 현재 75kg"`에서 `weight_kg=65` 추출.
- [ ] **#8 지역 정규식 오탐:** `planner.py:L24,L157-158` — `"면역"`, `"서울지역"` 등 `~역` 일반 명사를 역 이름으로 인식해 매장 검색 실행.

### [P1 - Should Fix] 안정성·성능·테스트·정리
- [ ] **#9 실패 Tool 결과 미캐시 및 `DUPLICATE_TOOL_CALL` 오탐:** `worker.py:L131,L165-168` / `logging_agent.py:L121-147`.
- [ ] **#10 소비자원 라이브 조회 20회 순차 HTTP 병목 및 채널 정렬 전 5개 절단:** `price_tool.py:L427-476`.
- [ ] **#11 프론트엔드 UTC 날짜 기본값 버그:** `frontend/app.js:L211,L234` (한국 00~09시 사이 전날 날짜 저장).
- [ ] **#12 UI 세션 전환 시 `profileSaved` 누수 & `loadPlans` `session_id` 미필터링:** `frontend/app.js:L7,L116,L198,L264`.
- [ ] **#13 회귀 테스트 보강:** `test_core.py:L176-184,L356-391` (Mode C `mode_c_recovery` 검증, HTTP 200 에러 XML, `item_swap` 후속 턴, `mains` 공백 케이스).
- [ ] **#14 미사용 코드 정리:** `nutrition_tool.py` 미사용 `serving_base_g`, `price_tool.py:L564` 미사용 `openfoodfacts_items` 분기 정리, `planner.py:L346` 구 `"B"` 기본값 및 `L373-388` 도달 불가 가드 제거.

### [P2 - Nice to Have]
- [ ] **#15 외부 API HTTP 연결 재사용(`requests.Session`) 및 독립 검색 병렬화:** `worker.py:L87-136` / `price_tool.py:L439` / `local_tool.py:L116`.

---

## 4. 최근 마일스톤 로그 (최신 6건 유지)

- **2026-11-18 (Setup & Audit Baseline):** `AI_Project` 전수 감사(`/ponytail:ponytail-audit`) 완료(Must 8 / Should 6 / Nice 1), 단일 세션 기록 파일 `AGENTS.md` 신설, `.gitignore` WAL 패턴 추가, Git 초기화 및 `https://github.com/RyuHeebaek/AI_Core_Project.git` 최초 푸시.
- **2026-11-18 (API Migration):** 네이버 쇼핑 API 제거 → 한국소비자원 XML(`B551919`) + Open Food Facts 2순위 연동, 식약처 `FoodNtrCpntDbInfo03` 스키마 정렬, 표준 모드명(`online_bulk`/`cvs_instant`/`cheating_recovery`/`item_swap`/`profile_calc`) 통일, `pytest` 9/9 통과 및 실 API 키 단건 검증 완료.
- **2026-11-18 (Initial Build):** SSAFY AI 코어 3회차 LangGraph 멀티 Agent 식단 플래너 초안(프로필 역산, 모드 A/B/C, 1:1 스왑, F3020 로깅·이상탐지, 분리형 Vanilla JS UI) 구축.
