# check_metrics_api.py — `/v1/metrics` 구현 자가 검사

> 대상: 서비스 담당자 · 목적: 구현한 `GET /v1/metrics` 가 [구현 스펙](../docs/METRICS_COLLECTION_SPEC.md)·[OpenAPI 계약](../token-metric-api.yaml)대로 동작하는지 배포 전 스스로 확인
> 의존성: **파이썬 3.9+ 표준 라이브러리만** — 추가 설치 없음

## 사용법

```bash
# 기본: 어제(KST) 날짜로 검사
python3 scripts/check_metrics_api.py --base-url http://my-service.internal:8080

# 특정 날짜 지정
python3 scripts/check_metrics_api.py --base-url http://... --date 2026-08-18

# 동작 검사(당일·미래·보존초과·형식오류·멱등) 생략하고 스키마만
python3 scripts/check_metrics_api.py --base-url http://... --skip-behavior
```

- **종료 코드**: `0` = FAIL 없음 (통과) / `1` = FAIL 있음 / `2` = 인자 오류 — CI 에 그대로 물릴 수 있다.
- 스크립트는 **읽기만** 한다 (GET 호출 5~6회). 사내망에서 실행할 것.

## 실행 예시

### 통과하는 경우 (케이스 A — vLLM, serving 생략)

올바르게 구현된 서비스에 실행하면 다음과 같이 나온다 (종료 코드 `0`):

```
$ python3 scripts/check_metrics_api.py --base-url http://ds-assistant-vllm.internal:8080
# check_metrics_api — base=http://ds-assistant-vllm.internal:8080 date=2026-08-30

[PASS] A1     호출 성공 (HTTP 200)
[PASS] A2     Content-Type OK (application/json)
[PASS] A3     JSON 파싱 OK
[PASS] B1     최상위 필수 필드 존재 (date, serviceGroup, service, generatedAt, gpu, serving)
[PASS] B2     date 에코 일치
[PASS] B3     generatedAt OK (2026-08-31T01:30:00+09:00)
[PASS] B4     gpu 배열 확인 (2행)
[PASS] B9     serving: [] (케이스 A~C — 운영자 스크랩 경로)
[PASS] B11    engine 자기신고 OK (type=vllm)
[PASS] C4     같은 date 재호출 시 동일 결과 (멱등성)

[PASS] C1     당일 date(2026-08-31) → 400
[PASS] C1     오류 본문 형식 OK (code=invalid_date)
[PASS] C2     미래 date(2026-09-07) → 400
[PASS] C3     보존 기간 초과 date(2026-08-01) → 404
[PASS] C5     형식 오류 date(2026-13-99) → 400

결과: PASS 15 · WARN 0 · FAIL 0
→ 통과! 운영자측 검증(할당 대비 Σ검증, 모델명 정규화 등)은 수집 개시 후 자동으로 수행됩니다.
```

### 실패하는 경우 (규칙 위반이 섞인 구현)

계약을 어긴 구현에 실행하면 위반마다 FAIL 이 찍힌다 (종료 코드 `1`):

```
$ python3 scripts/check_metrics_api.py --base-url http://broken-svc.internal:8080
# check_metrics_api — base=http://broken-svc.internal:8080 date=2026-08-30

[PASS] A1     호출 성공 (HTTP 200)
[PASS] A2     Content-Type OK (application/json)
[PASS] A3     JSON 파싱 OK
[PASS] B1     최상위 필수 필드 존재 (date, serviceGroup, service, generatedAt, gpu, serving)
[PASS] B2     date 에코 일치
[FAIL] B3     generatedAt 은 ISO 8601 + '+09:00' 오프셋이어야 함 (현재: '2026-08-31T01:30:00Z')
[PASS] B4     gpu 배열 확인 (1행)
[FAIL] B6     gpu[0]: category=serving 에서 model "unknown" 금지 (test 만 허용)
[FAIL] B7     gpu[0]: gpuHours(120.0) > gpuCount×24(96) — 검증 규칙 위반
[PASS] B9     serving 배열 확인 (1행) — 경로 (a)
[FAIL] B9     serving[0].ttftMs percentile 이 비감소가 아님 (p50≤p90≤p95≤p99 여야 함): [900, 640, 850, 1450]
[WARN] B10    serving[0]: ttftMs/itlMs 중 하나만 있음 — 스트리밍 모델은 쌍으로 제공
[FAIL] B9     serving[0].outputTps 에 허용되지 않은 키 ['avg'] — p50만 허용 (avg·상위 percentile 없음)
[WARN] B11    engine 자기신고 없음 — 권장 (버전 수기 갱신을 없애줌)
[WARN] B12    스펙에 없는 최상위 필드: ['requests'] (토큰량·requests 는 보내지 말 것 — 이중 소스 금지)
[FAIL] C4     같은 date 재호출 결과가 다름 (2차: HTTP 200) — 재수집 안전성 위반

[PASS] C1     당일 date(2026-08-31) → 400
[PASS] C2     미래 date(2026-09-07) → 400
[PASS] C3     보존 기간 초과 date(2026-08-01) → 404
[PASS] C5     형식 오류 date(2026-13-99) → 400

결과: PASS 12 · WARN 3 · FAIL 6
→ FAIL 항목을 수정한 뒤 재실행하세요. 규칙 근거: docs/METRICS_COLLECTION_SPEC.md
```

각 FAIL 의 수정 방법은 아래 [자주 나오는 FAIL](#자주-나오는-fail) 참조. 실제 터미널에서는 PASS/WARN/FAIL 이 색상(초록/노랑/빨강)으로 표시된다.

## 검사 항목

### A. 연결·기본

| ID | 검사 | 실패 시 의미 |
|---|---|---|
| A1 | GET 호출 성공 | URL·네트워크·서버 문제 |
| A2 | 상태 코드가 200/409/404 중 하나, 200 이면 Content-Type `application/json` | 응답 규칙(§2) 위반 |
| A3 | 본문 JSON 파싱 | 직렬화 문제 |

`409` 는 "집계 전 미확정"이라는 **유효한 응답**이다 — 이 경우 스키마 검사는 생략되므로, 확정된 날짜를 `--date` 로 지정해 재실행한다.

### B. 스키마 (200 응답)

| ID | 검사 |
|---|---|
| B1 | 최상위 필수 필드: `date` `serviceGroup` `service` `generatedAt` `gpu` `serving` (경로 (b)는 `serving: []` — 키 생략·null 불가) |
| B2 | `date` 에코가 요청 값과 일치 |
| B3 | `generatedAt` 이 ISO 8601 + `+09:00` 오프셋 |
| B4 | gpu 행 필수 필드: `model` `gpuType` `gpuCount` `gpuHours` `category` |
| B5 | `category` ∈ serving / standby / test |
| B6 | serving·standby 행에 `model: "unknown"` 금지 (test 만 허용) |
| B7 | `gpuCount` > 0, `gpuHours` ≥ 0, **`gpuHours` ≤ `gpuCount` × 24** |
| B8 | (경고) 동일 (model, gpuType, category) 중복 행 / serving 행 부재 |
| B9 | serving 행: `ttftMs`/`itlMs`/`e2eMs` 는 p50·p90·p95·p99 **4키 완비 + 비감소**, `outputTps` 는 **p50만**, `custom` 은 name·unit 필수 + 값 키(p50/p90/p95/p99) 최소 1개 |
| B10 | serving 행에 지표 최소 1개 / (경고) ttftMs·itlMs 는 쌍으로 · custom 만 있는 행 · 동일 model 중복 행 |
| B11 | `engine` 자기신고 형식 — 없으면 경고 (권장 필드) |
| B12 | (경고) 스펙에 없는 최상위 필드 — 토큰량·requests 를 보내고 있으면 여기서 걸린다 (이중 소스 금지) |

### C. 응답 규칙 동작 (`--skip-behavior` 로 생략 가능)

| ID | 검사 | 근거 |
|---|---|---|
| C1 | 당일 date → `400` | "0"과 "미확정" 구분 규칙 |
| C2 | 미래 date → `400` | 〃 |
| C3 | 30일 전 date → `404` (보존 7일 초과) — 200 이면 경고 | 보존 규칙 |
| C4 | 같은 date 2회 호출 → **동일 본문** | 멱등성 — 운영자 재수집 안전 |
| C5 | 형식 오류 date(`2026-13-99`) → `400` | 파라미터 검증 |

## 결과 해석

- **FAIL** — 계약 위반. 수정 전에는 수집이 실패하거나 틀린 값이 적재된다. 각 메시지에 위반한 규칙이 적혀 있다.
- **WARN** — 계약 위반은 아니지만 확인이 필요한 것 (권장 필드 누락, 중복 행, 스펙 외 필드 등).
- **PASS 여도 검사 범위 밖인 것** — 이 스크립트는 **형식·규칙 검사**다. 다음은 확인하지 못한다:
  - 값의 **정확성** (gpuHours 가 실제 점유와 맞는지, percentile 계산이 옳은지 — 특히 레플리카 로그를 모아 한 번에 계산했는지)
  - **모델명이 메타데이터 시트의 canonical 과 일치**하는지 (미등록 표기는 수집 개시 후 운영자 알림으로 잡힘)
  - **할당 대비 Σ gpuHours ≤ 할당량** (GPU 대시보드 데이터가 필요 — 운영자측 검증)
  - 02:00(KST) 이전 확정 여부 — 새벽에 크론으로 이 스크립트를 돌려보면 확인 가능

## 자주 나오는 FAIL

| 증상 | 원인·수정 |
|---|---|
| C1/C2 에서 200 반환 | 당일·미래 date 를 거르지 않고 빈 데이터를 200 으로 응답 — "실제 0"과 "미확정"이 구분되지 않아 **금지** |
| C4 멱등성 실패 | 호출 시점마다 재집계하면서 값이 흔들림 — 일자 확정 후 같은 응답을 반환하도록 캐시/스냅샷 |
| B7 gpuHours > gpuCount×24 | gpuCount 를 평균 장수로 적었거나, gpuHours 에 다른 날짜분이 섞임 — gpuCount 는 **그날 최대 장수** |
| B9 percentile 비감소 위반 | 레플리카별 p99 를 평균내는 등 잘못된 합산 — 전체 로그를 모아 한 번에 계산 |
| B6 unknown 금지 | 프로덕션 행에 model 미상 — canonical 표기를 붙이거나, 실험 GPU 라면 category 를 test 로 |
