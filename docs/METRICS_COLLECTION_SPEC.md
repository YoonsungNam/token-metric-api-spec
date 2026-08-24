# /v1/metrics 구현 스펙 — 서비스 담당자용

> 대상: 각 AI 서비스 담당자 · **구현에 필요한 최소 정보만** 담은 스펙
> 전체 맥락(배경·대시보드 설계·비용 배부 규칙): [상세 정의서 (old)](METRICS_COLLECTION_SPEC_OLD.md) · 미팅용: [담당자 요약](METRICS_COLLECTION_SUMMARY.md)
> 관련 스펙: [token-usage-api-spec](https://github.com/YoonsungNam/token-usage-api-spec) (기존 토큰 사용량 조회 API)
> 상태: 초안 — 규칙은 상세본 v2.1과 동일 (표현만 간소화)

---

## 0. 해야 할 일 (한눈에)

| 대상 | 할 일 |
|---|---|
| **모든 서비스** | ① 취합본(엑셀) 제출 (§2) ② GET `/v1/metrics` 구현 — gpu 블록 (§3, §5) |
| vLLM / SGLang / Prometheus 엔진 팀 | ③ `/metrics` URL 제출 + 중앙 Prometheus 네트워크 개방 → **serving 블록 생략** (§4 케이스 A~C) |
| 자체 구현 서빙 / 외부 API 연동 팀 | ③′ serving 블록 자체 집계 제공 (§4 케이스 D~E) |

**일정: ~9/18까지 `/v1/metrics` 구축 및 대시보드 연동. 그 전까지 수치는 수기(엑셀)로 취합.**

- 방향은 **pull**: 중앙 수집기가 매일 새벽 호출한다. 크론·재시도·장애 감지는 전부 중앙 책임 — 서비스는 상태 없는 GET만 제공.
- 토큰량·requests는 **제공하지 않는다** — 기존 토큰 API가 담당 (이중 소스 금지).

## 1. 식별자 — 기존 토큰 API와 완전 동일

- `serviceGroup > service > model` 계층, **제공 단위는 service**
- 기존 토큰 API와 **같은 문자열** 필수 (`claude` ≠ `Claude` — 불일치 시 JOIN 실패)
- 어느 하위 서비스에도 못 넣는 그룹 공통 GPU는 별도 service(예: `claude-shared`)로 등록
- `model`은 정규 표기(canonical) 필수. `"unknown"`은 **test 카테고리에서만 허용**

**모델 표기(canonical) 규칙**

- 소문자·하이픈: `{패밀리}{버전}-{크기}[-{변형}]` — 변형 접미: `-awq`/`-fp8`(양자화), `-ft-{용도}`(파인튜닝)
- **사내 파인튜닝은 반드시 `-ft-{용도}`로 분리** — 순정과 같은 id 사용 금지
- 나눔/합침 판단 3문항: ① 같은 웨이트인가 ② 파인튜닝 없는 순정인가 ③ 같은 정밀도·양자화인가 → 셋 다 "예"면 같은 canonical(다른 표기는 alias), 하나라도 "아니오"면 별도 canonical. **애매하면 나눈다**
- **기존에 쓰던 표기는 바꿀 필요 없음** — alias로 등록하면 중앙이 정규화한다. vLLM/SGLang은 `--served-model-name`을 canonical로 설정하면 그것도 불필요

## 2. 취합본(엑셀) 제출 항목

온보딩 시 1회 작성, 변경 시 갱신본 제출:

| # | 항목 | 내용 |
|---|---|---|
| ① | 서비스 표기 | serviceGroup · service · 담당자 |
| ② | 모델 표기 | canonical + alias 목록 + family + **체급(sizeClass: S ~15B / M ~40B / L 40B+, MoE는 활성 파라미터 기준)** |
| ③ | GPU 할당 매핑 | GPU 대시보드 할당 단위(프로젝트/네임스페이스 등) ↔ service |
| ④ | 소비 관계 | "우리 서비스 → 어느 플랫폼의 어느 모델" (사내 플랫폼 사용 시) |
| ⑤ | 서비스 계정 매핑 | (플랫폼 제공자만) 서비스 계정 ↔ 소비 서비스 — API 자기신고로 대체 가능 |
| ⑥ | `/metrics` URL | (케이스 A~C만) 스크랩 대상 URL + 엔진 종류 |

```yaml
# 한 서비스 분 예시 — 주석의 ①~⑥은 위 표의 항목 번호
serviceGroup: claude                                     # ① 서비스 표기
service: claude-cowork                                   # ①
owner: kim@company.com                                   # ① 담당자
models:                                                  # ② 모델 표기 (canonical·family·sizeClass·alias)
  - { canonical: opus, family: claude, sizeClass: L, aliases: ["anthropic/claude-opus", "opus-fp8"] }
gpuAllocationUnit: "k8s-ns:claude-cowork-prod"           # ③ GPU 할당 매핑 (GPU 대시보드 할당 단위)
consumes: []                                             # ④ 소비 관계 — 예: [{ platform: llm-gateway, model: llama3.3-70b }]
serviceAccounts: {}                                      # ⑤ 서비스 계정 매핑 (플랫폼 제공자만) — 예: { svc-key-a1: chat-assistant }
metricsUrl: "http://cowork-vllm.internal:8000/metrics"   # ⑥ /metrics URL (케이스 A~C만)
engine: { type: vllm }                                   # ⑥ 엔진 종류 — 버전은 API 자기신고로 수신 (수기 갱신 불필요)
```

**갱신이 끊겨도 중앙이 감지한다.** 취합본 최신화를 담당자의 기억에 맡기지 않는다 — 취합본과 실데이터가 어긋나면 시스템이 잡아서 알림을 보내고, 담당자는 그때 갱신하면 된다:

- 미등록 모델 표기 유입 → "미등록" 격리 + 알림 (모델 추가·교체가 자동 감지됨). "기존 canonical의 alias인지 / 새 canonical인지" 분류만 회신
- `engine` 자기신고 ↔ 취합본 불일치 → 알림 (엔진 교체 감지)
- gpu 블록 검증 규칙 위반(§3), GPU 할당 단위 ↔ 매핑 불일치 → 알림

## 3. gpu 블록 — 모델별 GPU Hour

model × gpuType × category 단위로 일별 제공:

| 필드 | 정의 | 역할 |
|---|---|---|
| `gpuType` | 단순 기종 표기 (예: `H100`, `A100`) | 단가표 키 |
| `gpuCount` | 해당일 그 모델에 매핑된 GPU 장수 (하루 중 증감 시 **최대** 기준) | **비용 계산에 미사용** — "몇 장 점유"의 표시·검증용 |
| `gpuHours` | GPU 수 × 모델에 매핑·할당된 시간. 예: H100 4장 × 24h = 96.0 | **비용의 유일한 근거** |
| `category` | 아래 3종 | 용도 분해 |

| category | 의미 | model 값 |
|---|---|---|
| `serving` | 프로덕션 서빙 | 실제 모델명 (`unknown` 금지) |
| `standby` | HA 대기 (failover) | 실제 모델명 (`unknown` 금지) |
| `test` | 테스트·실험 | 모델명 또는 `unknown` |

- **유휴는 제공하지 않는다** — 중앙이 `할당(GPU 대시보드) − Σ제공`으로 산출.
- 모델 교체·증설 이력은 daily 데이터에 자연 반영 — effective 필드 불필요.
- **검증 규칙** (위반 시 담당자 알림): ① 기종별 Σ제공 gpuHours ≤ 할당 GPU-hours ② 항목별 gpuHours ≤ gpuCount × 24 ③ serving/standby의 model `unknown` 금지

**비용 계산과 하루 중 변동 처리**

- 모델 비용은 중앙이 **`Σ기종 (gpuHours × 기종 단가)`** 로 계산한다. **단가의 단위는 원/GPU·hour** (GPU 1장의 시간당 TCO) — 장수는 gpuHours(= GPU 수 × 시간)에 이미 곱해져 있으므로, 단가를 곱하면 차원이 그대로 소거되어 원이 된다:

```
gpuHours [GPU·h] × 단가 [원/GPU·h] = 비용 [원]      ← GPU와 hour가 모두 소거
예: 96 GPU·h × 6,500원/GPU·h = 624,000원  (= 4장 × 24h × 6,500원)
```

- gpuCount는 비용에 쓰이지 않는다 — gpuCount × 단가로 계산하면 장수가 이중으로 곱해진다.

**장수 정보의 계층** — "GPU 몇 장?"은 수준별로 이렇게 답해진다:

| 질문 | 답 | 출처 |
|---|---|---|
| 서비스에 몇 장이 할당돼 있나 | 할당 장수 (기종별) | GPU 대시보드 (취합본 ③ 매핑) — 서비스가 제공할 필요 없음 |
| 이 모델이 몇 장을 점유하나 | `gpuCount` (모델×용도별) | 본 API |
| 몇 장이 놀고 있나 | 유휴 상당 = 할당 − Σ매핑 | 중앙 산출 |
| 하루 안에서 몇 시에 몇 장이었나 | **받지 않음 (의도적)** — 단가가 일 단위 상수라 비용은 gpuHours 적분값으로 정확. 시간대별 분석이 필요해지면 담당자 보고가 아닌 중앙 스크랩으로 (2단계) | — |
- **장수 증감**: gpuHours에 그대로 반영된다. 예: 2장으로 12h + 증설 후 4장으로 12h → `gpuHours: 72.0, gpuCount: 4`
- **기종 변경**: gpuType별로 행을 나눈다. 예: A100 4장으로 10h 운영 후 H100 4장으로 이전, 14h 운영 →

```json
{ "model": "llama3-70b", "gpuType": "A100", "gpuCount": 4, "gpuHours": 40.0, "category": "serving" },
{ "model": "llama3-70b", "gpuType": "H100", "gpuCount": 4, "gpuHours": 56.0, "category": "serving" }
```

**사내 플랫폼 관련**

- **플랫폼 제공자**: 자기 명의로 GPU 전량 제공 + 호출자를 소비 서비스별 API 키(서비스 계정)로 구분할 수 있어야 한다.
- **플랫폼 소비자**: 플랫폼 사용분은 GPU·토큰 모두 **제공하지 않는다** (이중 계상 방지) — 취합본에 소비 관계(④)만 등록. 자체 GPU가 있으면 그것만 위 규칙대로 제공.

## 4. 서비스 메트릭 — 내 케이스 찾기

| 지표 | 정의 | 단위 | 형태 |
|---|---|---|---|
| **TTFT** | 요청 수신 → 첫 토큰 | ms | p50 / p90 / p99 |
| **ITL** | 토큰 간 간격 | ms | p50 / p90 / p99 |
| **Output TPS** | 요청당 초당 생성 토큰 | tokens/s | avg / p50 |

| 케이스 | 경로 | 담당자 작업 | serving 블록 |
|---|---|---|---|
| A. vLLM | 중앙 스크랩 | `/metrics` URL 제출 + 네트워크 개방 | 생략 |
| B. SGLang | 중앙 스크랩 | 상동 (`--enable-metrics` 필요할 수 있음) | 생략 |
| C. 기타 Prometheus 엔진 (TGI 등) | 중앙 스크랩 우선 | 상동 + TTFT/ITL 메트릭명 매핑을 중앙과 확정 | 생략 |
| D. 자체 구현 서빙 | 자체 집계 | 스트리밍 시각 로깅 → daily p50/p90/p99 집계 → GET 제공 | 채움 |
| E. 외부 API 연동 (사내 GPU 없음) | 자체 집계 | `gpu: []`. 필요 시 게이트웨이에서 측정 (측정 위치를 취합본에 명시) | 선택 |
| F. 비스트리밍 | — | TTFT/ITL 해당 없음 | 생략 |

- (케이스 D) 측정: TTFT = 첫 청크 − 요청 수신, ITL = 청크 간 간격, TPS = 출력 토큰 ÷ (마지막 − 첫 청크). **레플리카가 여러 대면 전체 로그를 모아 한 번에 percentile 계산** (레플리카별 p99의 평균 ≠ 전체 p99).
- (케이스 A~C) 히스토그램은 중앙이 스크랩·집계하므로 구현 작업 없음. `--served-model-name`을 canonical로 설정 권장.

## 5. API 계약: GET /v1/metrics

```
GET https://{service-host}/v1/metrics?date=YYYY-MM-DD
```

- `date`: KST 기준 하루 (기본적으로 어제). 인증 없음 (사내망 전제).

**응답 규칙** (기존 토큰 API와 동일):

| 상황 | 응답 |
|---|---|
| 확정된 데이터 있음 | `200` + 본문 |
| 사용량·가동이 실제 0 | `200` + 빈 배열 (`gpu: []`) |
| 아직 집계 전 (미확정) | `409` — 빈 200 금지 ("0"과 "아직 없음" 구분) |
| 당일/미래 date | `400` |
| 보존 기간 초과 | `404` |

- 같은 date 재호출 시 동일 결과 반환 (중앙이 재수집해도 안전).

**예시 1 — vLLM 팀 (케이스 A): serving 생략**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "claude",
  "service": "claude-cowork",
  "generatedAt": "2026-08-19T01:30:00+09:00",
  "engine": { "type": "vllm", "version": "0.8.4" },
  "gpu": [
    { "model": "opus", "gpuType": "H100", "gpuCount": 4, "gpuHours": 96.0, "category": "serving" },
    { "model": "opus", "gpuType": "H100", "gpuCount": 1, "gpuHours": 24.0, "category": "standby" }
  ],
  "serving": []
}
```

**예시 2 — 자체 구현 팀 (케이스 D): serving 포함**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "search",
  "service": "doc-summary",
  "generatedAt": "2026-08-19T01:10:00+09:00",
  "engine": { "type": "custom" },
  "gpu": [
    { "model": "llama3-70b", "gpuType": "A100", "gpuCount": 2, "gpuHours": 48.0, "category": "serving" }
  ],
  "serving": [
    {
      "model": "llama3-70b",
      "ttftMs":    { "p50": 320, "p90": 640, "p99": 1450 },
      "itlMs":     { "p50": 28,  "p90": 45,  "p99": 95 },
      "outputTps": { "avg": 33.5, "p50": 35.2 }
    }
  ]
}
```

**예시 3 — 외부 API 연동 (케이스 E): gpu 빈 배열**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "hr",
  "service": "hr-chatbot",
  "generatedAt": "2026-08-19T01:05:00+09:00",
  "gpu": [],
  "serving": [
    {
      "model": "gpt-4o",
      "ttftMs": { "p50": 850, "p90": 1600, "p99": 3200 },
      "itlMs":  { "p50": 22,  "p90": 38,  "p99": 80 }
    }
  ]
}
```

**필드 정의**

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `date` | string (YYYY-MM-DD) | O | KST 기준 집계 대상일 |
| `serviceGroup` / `service` | string | O | 취합본 공식 표기와 일치 |
| `generatedAt` | string (ISO 8601, +09:00) | O | 집계 산출 시각 |
| `engine` | object {type, version?} | X (권장) | 엔진 자기신고 — 취합본과 불일치 시 중앙이 알림 |
| `serviceAccounts` | object | X | 플랫폼만: 서비스 계정 ↔ 소비 서비스 매핑 자기신고. 예: `{"svc-key-a1": "chat-assistant"}` |
| `gpu[]` | array | O (빈 배열 허용) | |
| `gpu[].model` | string | O | canonical 표기. `"unknown"`은 category=test만 |
| `gpu[].gpuType` | string | O | 단순 기종 표기 (`H100`, `A100`) |
| `gpu[].gpuCount` | number | O | 해당일 매핑 GPU 장수 (증감 시 최대 기준) — 비용 계산 미사용, 표시·검증용 (§3) |
| `gpu[].gpuHours` | number | O | GPU 수 × 매핑·할당 시간 (≤ gpuCount × 24) |
| `gpu[].category` | enum | O | `serving` \| `standby` \| `test` |
| `serving[]` | array | X | 케이스 D~E 팀만 |
| `serving[].model` | string | O | |
| `serving[].ttftMs` / `itlMs` | object {p50,p90,p99} | X | ms |
| `serving[].outputTps` | object {avg,p50} | X | tokens/s |

## 6. 운영 규칙

- 중앙이 **02:00(KST)부터** 전일 데이터 호출 시작. `409`면 1시간 간격 재시도, **09:00까지 미확정이면 담당자 알림** → `/v1/metrics`는 02:00 이전에 전일 데이터 확정 상태여야 한다.
- 최근 **7일까지 재수집(backfill)** 가능 — 정정 필요 시 재집계 후 중앙에 재수집 요청.
- 단위는 필드명에 명시 (`Ms`, `gpuHours`, `Tps`).

## 7. 체크리스트

- [ ] (serviceGroup, service, model) 공식 표기 확정 — 기존 토큰 API와 동일한가?
- [ ] GPU 대시보드 할당 단위 확인 + service 매핑을 취합본에 제출했는가?
- [ ] 모델 표기를 canonical + alias + 체급(sizeClass)으로 정리해 제출했는가? 사내 파인튜닝은 `-ft-{용도}`로 분리했는가?
- [ ] 모델별 GPU Hour(gpuCount·gpuHours)를 serving / standby / test로 분류·제공 가능한가? (유휴 제공 불필요 / serving·standby는 `unknown` 금지)
- [ ] 추론 엔진 확인 → §4 케이스 A~F 판별 + 응답에 `engine` 자기신고 포함하는가?
- [ ] (케이스 A~C) `/metrics` URL 제출 + 중앙 Prometheus 접근 개방 가능한가?
- [ ] (케이스 D) TTFT/ITL 측정 로깅 + 레플리카 통합 집계 가능한가?
- [ ] `/v1/metrics`를 02:00 이전에 전일 데이터 확정 상태로 제공 가능한가?
- [ ] (플랫폼 제공자) 호출 주체를 서비스 단위로 식별 가능한가? (서비스 계정 발급 + 매핑 제출)
- [ ] (플랫폼 소비자) 소비 관계를 제출했는가? 그 사용분의 GPU·토큰을 제공하지 않는다는 것을 인지했는가?
