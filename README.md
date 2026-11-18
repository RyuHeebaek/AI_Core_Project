# 영양성분 선형 조합 + 가성비 역산 기반 하이브리드 식단 플래너

SSAFY AI 코어 프로젝트(3회차) 명세서(`F3001`~`F3021`, `NF3001`~`NF3007`)를 충족하는 **LangGraph 멀티 Agent 기반 하이브리드 식단 플래너**입니다.

---

## 1. 주요 기능

### 핵심 도메인 엔진
1. **초기 사용자 프로필 & 목표 역산 + 안전 가드레일 (`F3008`)**
   - BMR/TDEE 산출 후 목표 체중·기간($\Delta \text{kg} \times 7,700\text{kcal} \div \text{일수}$)을 반영해 일일·끼니별 목표 열량·탄수·단백질·지방을 역산합니다.
   - 남성 `1,400kcal/day`, 여성 `1,100kcal/day` 미만으로 역산되면 안전 가드레일 경고 및 권장 감량 기간을 역산 제시합니다.
2. **모드 A (`online_bulk`: 대량 장바구니 포트폴리오)**
   - 한국소비자원 생필품 가격정보(대형마트/할인점 최저가 우선) + 묶음 수량/중량 정규표현식 파서(`10팩`, `10+10`, `10팩+10팩`, `1kg`)로 **1회 섭취분 단가**를 환산합니다.
   - 설정 기간 총 끼니 수 기준 필요 묶음 수량, 총 결제 예상 금액, 참고 링크를 산출합니다.
3. **모드 B (`cvs_instant`: 편의점 즉시 한 끼 조합)**
   - 편의점 업태 기준가 우선 + 예산·단백질·칼로리 제약을 만족하는 `[메인 + 서브 + 음료]` 2~3개 품목 선형 조합을 탐색하고, 카카오 로컬 API로 반경 500m 내 지정 브랜드 매장 위치·지도 링크를 제공합니다.
   - *"조사 기준가 기반 역산 금액이며 매장별 1+1/2+1 행사 적용 시 실제 결제액이 달라질 수 있음"* 안내를 함께 제공합니다.
4. **모드 C (`cheating_recovery`: 치팅데이/외식 데미지 복구)**
   - 보정 타겟 $K_{\text{adjusted}} = K_{\text{target}} - \frac{\Delta K_{\text{over}}}{D_{\text{remain}}}$ 을 산출하고, 근손실 방지를 위한 최소 단백질($\text{체중} \times 1.6\text{g}$)은 고정한 채 탄수화물·지방만 감량합니다.
5. **고도화 기능**
   - 단백질 효율 스코어링(순수 단백질 밀도 %, 1,000원당 단백질 g) & **1:1 품목 스왑 (`item_swap`)**
   - 건강·기피 필터(유당불내증, 18시 이후 카페인 제한, 저당 $\le 10\text{g}$, 기피 식재료)

---

## 2. 멀티 Agent 아키텍처 (LangGraph)

- **`PlannerAgent` (`backend/app/agents/planner.py`)**: 의도/모드/제약 추출(`F3005`), 필수 조건 누락 시 되묻기, 세션 맥락 병합(`F3010`), 재계획(`F3009`, 상한 3회 `NF3004`).
- **`WorkerAgent` (`backend/app/agents/worker.py`, `F3021`)**: 정상 경로에서는 **LLM 호출 0회**로 Tool을 순차 실행하고 캐시를 재사용합니다. 검색 0건/단위 파싱 실패 등 보정 가능한 실패에만 `fixed_args`를 고정한 채 LLM으로 검색 키워드를 보정합니다.
- **`NutritionValidatorAgent` (`backend/app/agents/validator.py`)**:
  - 1단계(무 LLM): 품목 합계 재검산·오차 보정, 예산·단백질(85% 이상)·칼로리 상한(110% 이하)·건강 제약 검증.
  - 2단계(LLM 교차 검증): 상품명·영양 매칭 오류 시 `INVALID_FOOD_MATCH` 반려.
- **`ResponderAgent` (`backend/app/agents/responder.py`, `F3011`)**: 계산 근거, 제외 사유, 매장/장보기 요약과 건강 도메인 필수 면책 문구를 자동 포함합니다.
- **`LoggingSubAgent` (`backend/app/agents/logging_agent.py`, `F3012`~`F3014`, `F3020`)**: `EventBus`를 구독해 LLM 호출 없이 세션 이벤트를 SQLite에 기록하며, 미연결 시에도 메인 흐름은 정상 동작합니다. 세션 종료 후 규칙 기반으로 이상 징후(중복 호출, 재계획 상한 도달, 계획 외 호출, 수치 불일치, 이벤트 없음=`판정 불가`)를 판정하고 이상 시에만 LLM으로 3문장 이내 진단을 생성합니다.

---

## 3. 외부 API 출처 및 이용 조건 (`NF3007`)

| 구분 | 출처 | 주요 이용 조건 및 호출 한도 | 용도 |
|---|---|---|---|
| 식품영양성분 DB API | [공공데이터포털](https://www.data.go.kr/) (식품의약품안전처) | 개발계정 일 10,000건, 출처 표기 및 공공누리 조건 준수 | 후보 식품 영양성분(열량·탄단지·당류·나트륨) 조회 |
| 생필품 가격정보 API (1순위) | [공공데이터포털](https://www.data.go.kr/) (한국소비자원) | `DATA_GO_KR_API_KEY` 공용, XML 응답, 주간 조사 기준가 기반 | 업태별(`편의점` / `대형마트`) 상품 단가 조회 |
| Open Food Facts API (2순위 보조) | [Open Food Facts](https://world.openfoodfacts.org/) | API 키 불필요, ODbL 라이선스 및 User-Agent 명시 권장 | 소비자원 DB 미등재 가공식품 메타데이터 보완 |
| 카카오 로컬 API | [Kakao Developers](https://developers.kakao.com/docs/latest/ko/local/dev-guide) | 일 100,000회(월 3,000,000건), 지도/장소 링크 유지 의무 | 반경 500m 내 편의점 위치·주소·지도 링크 조회 |
| LLM API | OpenAI / [SSAFY GMS](https://gms.ssafy.io/web/) | 발급 토큰 한도 및 보안 정책(`NF3001`, `NF3002`) 준수 | 모호한 의도 보강, Worker 키워드 보정, 2단계 검증, F3020 진단 |

---

## 4. API 키 설정 및 실행 방법

### 1) 환경변수 설정 (`.env`)
```bash
cp .env.example .env
```
- `USE_MOCK=true`일 때는 키 미설정·외부 API 장애 시 로컬 모의 데이터로 동작하며, `USE_MOCK=false`이면 누락 키에 대해 `MISSING_API_KEY` 안내를 반환합니다(`F3019`).

### 2) 백엔드 실행 (포트 `8000`)
```bash
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
- API 문서: `http://127.0.0.1:8000/docs`

### 3) 프론트엔드 분리 실행 (포트 `5173`, `NF3006`)
```bash
python -m http.server 5173 --directory frontend
```
- 브라우저 접속: `http://127.0.0.1:5173`

### 4) 자동화 테스트 실행
```bash
.\.venv\Scripts\pytest.exe -v backend/tests/test_core.py
```
