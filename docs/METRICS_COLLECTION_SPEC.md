# /v1/metrics 구현 스펙 — 서비스 담당자용

> 대상: 각 AI 서비스 담당자 · **해야 할 일 + 상황별 예시** 중심
> 설계 배경·근거: [상세 정의서(old)](METRICS_COLLECTION_SPEC_OLD.md) · [의사결정 로그](DECISIONS.md) · [미팅용 요약](METRICS_COLLECTION_SUMMARY.md)
> 관련 스펙: [token-usage-api-spec](https://github.com/YoonsungNam/token-usage-api-spec)

---

## 0. 해야 할 일

| 누가 | 무엇을 | 언제까지 |
|---|---|---|
| **모든 서비스** | [1] 메타데이터 시트(엑셀) 제출 (§1) | 온보딩 시 1회 |
| **모든 서비스** | [2] GET `/v1/metrics` 구현 — gpu 블록 (§2~3) | **~9/18** |
| vLLM / SGLang 팀 | [3a] `/metrics` URL 제출 + 네트워크 개방 — **이러면 끝** (serving 블록 불필요) | 온보딩 시 |
| 자체 구현 서빙 / 사외 AI 모델 API 팀 | [3b] serving 블록 자체 집계 (§4) | ~9/18 |

**역할 표기** — 이 문서는 두 역할로 구분해 쓴다:

| 표기 | 누구 | 하는 일 |
|---|---|---|
| **서비스 담당자** (이하 담당자) | 각 AI 서비스의 담당자 — **이 문서의 독자** | 메타데이터 시트 제출, `/v1/metrics` 구현, 알림 회신 |
| **대시보드 운영자** (이하 운영자) | 토큰 대시보드 개발·운영팀 | 매일 수집 호출, 정규화·검증·비용 산출, 알림 발송 |

- 운영자가 매일 새벽 pull 호출한다 — 서비스는 상태 없는 GET만 제공 (크론·재시도·장애 감지는 운영자 책임).
- 토큰량·requests는 보내지 않는다 — 기존 토큰 API 담당 (이중 소스 금지).

> **전제 — "기존 토큰 API"란?** 각 서비스가 일별 **토큰 사용량·요청 수**를 제공하는 사내 표준 GET `/v1/usage` 엔드포인트다 (스펙: [token-usage-api-spec](https://github.com/YoonsungNam/token-usage-api-spec)). 본 문서의 `/v1/metrics`는 그 **자매 API**로, 호출 방식(pull)·식별자·응답 규칙을 그대로 따른다.
>
> **`/v1/usage`를 아직 구현하지 않은 신규 서비스라면**: 두 API를 함께 온보딩한다 — [1] token-usage-api-spec대로 `/v1/usage` 구현 (토큰·requests) [2] 본 스펙대로 `/v1/metrics` 구현 (GPU·성능). 이때 두 API가 **같은 (serviceGroup, service, model) 표기**를 써야 한다 — 이 일치가 토큰↔GPU↔성능 데이터를 잇는(JOIN) 조건이므로, §1의 메타데이터 시트에 표기를 먼저 확정하고 두 API에 동일하게 적용하면 된다.

---

## 1. 메타데이터 시트(엑셀) 제출

**메타데이터 시트란?** 서비스의 기준 정보 — 식별자 표기, 모델 이름(canonical·alias), GPU 할당 매핑, 소비 관계, 담당자 연락처 — 를 **엑셀 양식으로 수기 제출·관리하는 문서**다. 운영자는 이 시트를 정본으로 삼아 모델명 정규화, 토큰↔GPU↔성능 데이터 JOIN, 검증, 알림 발송을 수행한다. (1단계는 시스템 없이 엑셀로 운영하고, 추후 등록 화면 전환을 검토한다.)

**제출 항목 6가지**:

| # | 항목 | 내용 |
|---|---|---|
| [1] | 서비스 표기 | serviceGroup · service · 담당자 — **기존 토큰 API와 같은 문자열** (`ds-assistant` ≠ `DS-Assistant`) |
| [2] | 모델 표기 | 서비스가 **사용하는 모든 모델** — 자체 서빙, 사내 플랫폼 경유, 사외 AI 모델 API 직접 호출 전부. canonical 정의(alias·family·sizeClass)는 **서빙 주체가 등재** — 사내 플랫폼 경유 모델은 이름만 참조로 적는다 |
| [3] | GPU 할당 매핑 | 운영자가 GPU 대시보드에서 이 서비스의 **할당량(고정 쿼터)을 조회할 때 쓰는 키** — 우리 GPU가 배정된 할당 단위(k8s 네임스페이스·프로젝트명 등)를 적는다. **일별 gpuHours를 적는 곳이 아님** (그건 API의 gpu 블록, §3) |
| [4] | 소비 관계 (consumes) | **models 중 외부에서 조달하는 모델의 출처** — 어떤 모델을 어떤 사내 플랫폼(다른 팀이 사내 GPU로 제공하는 LLM API) / 사외 AI 모델 API에서 쓰는지. **consumes에 없는 모델 = 자체 GPU 서빙** |
| [5] | 서비스 계정 매핑 | (플랫폼 제공자만) 서비스 계정 ↔ 소비 서비스 |
| [6] | `/metrics` URL | (케이스 A~C만) 스크랩 URL + 엔진 종류 |

**작성 예시 — 서비스 유형별 3가지** (주석의 [1]~[6]은 위 표의 항목 번호):

*유형 1 — LLM API만 쓰는 서비스 (자체 GPU 없음)*: 사내 플랫폼과 사외 AI 모델 API를 호출하는 챗봇

```yaml
serviceGroup: hr                              # [1]
service: hr-chatbot                           # [1]
owner: park@company.com                       # [1]
models:                                       # [2] 쓰는 모델 전부
  - canonical: claude-sonnet-4.5              #    사외 AI 모델 API 직접 호출 — canonical 정의 주체는 우리
    family: claude
    sizeClass: null                           #    사외 모델은 파라미터 비공개 — 생략 가능
    aliases: ["claude-sonnet-4-5-20250929", "claude-sonnet-4-5"]
  - canonical: llama3.3-70b                   #    사내 플랫폼 경유 — 정의는 플랫폼이 등재, 이름만 참조
gpuAllocationUnit: null                       # [3] GPU 할당 없음
consumes:                                     # [4] models 중 외부 조달 모델의 출처 — 없는 모델 = 자체 서빙
  - model: claude-sonnet-4.5
    provider: anthropic-api
    type: external                            #    external = 사외 AI 모델 API
  - model: llama3.3-70b
    provider: llm-gateway
    type: internal                            #    internal = 사내 플랫폼
serviceAccounts: {}                           # [5] 플랫폼 제공자가 아니므로 비움
metricsUrl: null                              # [6] 스크랩 대상 없음 (§4 케이스 E)
engine: null
```

*유형 2 — GPU 할당 받은 자체 AI 서비스*: vLLM으로 자체 서빙하는 DS Assistant

```yaml
serviceGroup: ds                              # [1]
service: ds-assistant                         # [1]
owner: kim@company.com                        # [1]
models:                                       # [2] 위 "작성 과정 예시"의 결과 그대로
  - canonical: llama3.3-70b
    family: llama3.3
    sizeClass: L
    aliases: ["meta-llama/Llama-3.3-70B-Instruct", "llama70b", "/models/llama33"]
gpuAllocationUnit: "k8s-ns:ds-assistant-prod" # [3] GPU 대시보드에서 우리 서비스의 할당 단위 (유형:식별자) — 할당량 조회 키
consumes: []                                  # [4] 비어 있음 = 전 모델 자체 서빙
serviceAccounts: {}
metricsUrl: "http://ds-assistant-vllm.internal:8000/metrics"   # [6] §4 케이스 A — 이것으로 serving 블록 생략
engine:                                       # [6] 버전은 API 자기신고로 수신 — 수기 갱신 불필요
  type: vllm
```

*유형 3 — GPU + LLM API 병행 (하이브리드)*: 일반 요청은 자체 GPU의 중형 모델, 대형 요청은 사내 플랫폼으로

```yaml
serviceGroup: search                          # [1]
service: doc-summary                          # [1]
owner: lee@company.com                        # [1]
models:                                       # [2] 쓰는 모델 전부
  - canonical: qwen3-32b                      #    자체 서빙
    family: qwen3
    sizeClass: M
    aliases: ["Qwen/Qwen3-32B"]
  - canonical: llama3.3-70b                   #    사내 플랫폼 경유 — 이름만 참조
gpuAllocationUnit: "k8s-ns:doc-summary-prod"  # [3] 자체 GPU 할당분
consumes:                                     # [4] llama3.3-70b만 외부 조달 (qwen3-32b는 자체 서빙) — 이 사용분은 gpu 블록에 쓰지 않음 (§3)
  - model: llama3.3-70b
    provider: llm-gateway
    type: internal
serviceAccounts: {}
metricsUrl: null                              # [6] 자체 구현 서빙 (§4 케이스 D) — serving 블록 직접 채움
engine:
  type: custom
```

- 플랫폼 **제공자**라면 [5]를 채운다 (발급한 API 키 ↔ 소비 서비스):

```yaml
serviceAccounts:
  svc-key-a1: hr-chatbot
  svc-key-b2: doc-summary
```

**모델 표기([2]) 작성법**

먼저 용어 4개 — 항목 [2]의 각 필드가 하는 일:

| 필드 | 뜻 | 역할 |
|---|---|---|
| `canonical` | 이 모델의 **공식 이름** (이번에 새로 정하는 것) | 대시보드에 표시되는 유일한 표기 — 모든 데이터가 이 이름으로 모여 집계된다 |
| `aliases` | 실데이터에 등장하는 이 모델의 **다른 이름 전부** — HF 경로, served-model-name, 기존 토큰 API에 보내던 문자열, 사외 AI 모델 API의 날짜 버전 등 | 운영자가 데이터에서 이 표기들을 만나면 canonical로 바꿔 집계한다. **여기 안 적힌 표기는 "미등록"으로 빠져 알림이 온다** |
| `family` | 변형들을 묶는 **상위 그룹명** | 대시보드에서 합쳐 보기(rollup)용 — 예: `llama3.3-70b`(순정)·`llama3.3-70b-awq`(양자화)·`llama3.3-70b-ft-cs`(파인튜닝)를 `llama3.3`으로 묶어 봄 |
| `sizeClass` | 모델 **체급**: S(~15B) / M(~40B) / L(40B+) — MoE는 활성 파라미터 기준 | 효율 비교·랭킹을 같은 체급끼리만 하기 위한 분류 |

작성 과정 예시 — *"HF에서 받은 Llama-3.3-70B를 vLLM으로 서빙 중. 기존 토큰 API에는 `llama70b`로 보내왔고, vLLM `--served-model-name`은 `/models/llama33`"* 인 팀이라면:

```yaml
- canonical: llama3.3-70b        # 공식 이름을 표기 형식에 맞게 새로 정함
  family: llama3.3
  sizeClass: L                   # 70B → L
  aliases: ["meta-llama/Llama-3.3-70B-Instruct", "llama70b", "/models/llama33"]
  # ↑ 순서대로: HF 경로 · 기존 토큰 API 표기 · served-model-name — 어디서든 쓰던 이름을 전부 나열
```

**상황별 예시**:

| 상황 | 이렇게 쓴다 |
|---|---|
| 팀마다 부르는 이름이 다름 — `meta-llama/Llama-3.3-70B-Instruct`와 `/models/llama33-70b` (같은 순정 모델) | canonical `llama3.3-70b` **하나** + 두 표기를 aliases에 |
| 사내 파인튜닝본 운영 | **반드시 별도** canonical: `llama3.3-70b-ft-cs` — 순정과 같은 id 금지 |
| AWQ/FP8 양자화 배포 | 별도 canonical: `llama3.3-70b-awq` |
| 사외 AI 모델 API의 날짜 버전 (`claude-sonnet-4-5-20250929`) | canonical `claude-sonnet-4.5` + 날짜 문자열은 alias |
| 같은 모델인지 애매함 | **나눈다** — 나눈 건 나중에 합쳐 볼 수 있지만, 합친 건 못 나눔 |

- 형식: 소문자·하이픈 `{패밀리}{버전}-{크기}[-{변형}]`. 판단 기준: [1] 같은 웨이트 [2] 순정 [3] 같은 정밀도 — 셋 다 "예"일 때만 같은 canonical.
- **기존에 쓰던 표기는 안 바꿔도 된다** — alias에 넣으면 운영자가 정규화. vLLM/SGLang은 `--served-model-name`을 canonical로 설정하면 그것도 불필요.

**제출 후 운영 — 갱신은 알림 기반 (기억할 필요 없음)**:

- 미등록 모델 표기가 데이터에 나타나면 운영자가 후보와 함께 알림 → **"기존 ○○의 alias" / "새 모델" 중 택일 회신**하면 끝 (사전 갱신 후 7일 내 소급 재처리. 날짜 접미 패턴은 자동 처리, 무응답 시 "미등록"으로 계속 표시)
- 엔진 교체·검증 위반·할당 매핑 불일치도 운영자가 감지해 알림
- **알림 채널**: 메타데이터 시트 [1]에 등록된 담당자(owner) 앞으로 사내 메신저·메일 발송 (채널 상세는 온보딩 안내 시 확정) — **owner가 바뀌면 메타데이터 시트 갱신 필수**

---

## 2. GET /v1/metrics 구현

```
GET https://{service-host}/v1/metrics?date=YYYY-MM-DD    (KST 기준, 사내망 전제 — 인증 없음)
```

**응답 규칙** (기존 토큰 API와 동일):

| 상황 | 응답 |
|---|---|
| 확정된 데이터 있음 | `200` + 본문 |
| 사용량·가동이 실제 0 | `200` + `gpu: []` |
| 아직 집계 전 (미확정) | `409` — 빈 200 금지 ("0"과 "아직 없음" 구분) |
| 당일/미래 date | `400` |
| 보존 기간(7일) 초과 | `404` |

- 같은 date 재호출 시 동일 결과. **02:00 이전에 전일 데이터 확정** (§5).

**응답 예시 1 — vLLM 팀 (케이스 A): serving 생략**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "ds",
  "service": "ds-assistant",
  "generatedAt": "2026-08-19T01:30:00+09:00",
  "engine": { "type": "vllm", "version": "0.8.4" },
  "gpu": [
    { "model": "llama3.3-70b", "gpuType": "H100", "gpuCount": 4, "gpuHours": 96.0, "category": "serving" },
    { "model": "llama3.3-70b", "gpuType": "H100", "gpuCount": 1, "gpuHours": 24.0, "category": "standby" }
  ],
  "serving": []
}
```

**응답 예시 2 — 자체 구현 팀 (케이스 D): serving 포함**

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

**응답 예시 3 — 사외 AI 모델 API 연동 (케이스 E): gpu 빈 배열**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "hr",
  "service": "hr-chatbot",
  "generatedAt": "2026-08-19T01:05:00+09:00",
  "gpu": [],
  "serving": [
    { "model": "claude-sonnet-4.5", "ttftMs": { "p50": 850, "p90": 1600, "p99": 3200 }, "itlMs": { "p50": 22, "p90": 38, "p99": 80 } }
  ]
}
```

**필드 정의**:

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `date` | string (YYYY-MM-DD) | O | KST 기준 집계 대상일 |
| `serviceGroup` / `service` | string | O | 메타데이터 시트 공식 표기와 일치 |
| `generatedAt` | string (ISO 8601, +09:00) | O | 집계 산출 시각 |
| `engine` | object {type, version?} | X (권장) | 엔진 자기신고 |
| `serviceAccounts` | object | X | 플랫폼만: 계정 ↔ 소비 서비스 매핑 자기신고 |
| `gpu[]` | array | O (빈 배열 허용) | §3 |
| `gpu[].model` | string | O | canonical 표기 (`unknown`은 test만) |
| `gpu[].gpuType` | string | O | 단순 기종 표기 (`H100`, `A100`) |
| `gpu[].gpuCount` | number | O | 해당일 매핑 장수 (증감 시 최대) — 비용 계산 미사용 |
| `gpu[].gpuHours` | number | O | 장수 × 매핑·할당 시간 (적분값) — 비용의 근거 |
| `gpu[].category` | enum | O | `serving` \| `standby` \| `test` |
| `serving[]` | array | X | 케이스 D~E만 |
| `serving[].model` | string | O | |
| `serving[].ttftMs` / `itlMs` | object {p50,p90,p99} | X | ms |
| `serving[].outputTps` | object {avg,p50} | X | tokens/s |

---

## 3. gpu 블록 — 상황별 작성 예시

원칙 2줄: **model × gpuType × category 단위로 행**을 쓴다. `gpuHours`(장수 × 시간의 적분)가 비용의 근거이고, `gpuCount`(그날 최대 장수)는 표시·검증용 — 비용은 운영자가 `gpuHours × 단가(원/GPU·h)`로 계산한다 (`[GPU·h] × [원/GPU·h] = [원]`, 장수는 gpuHours에 이미 포함).

| 상황 | gpu 블록에 이렇게 쓴다 |
|---|---|
| **기본**: H100 4장으로 llama3.3-70b를 하루 종일 서빙 | `{ "model": "llama3.3-70b", "gpuType": "H100", "gpuCount": 4, "gpuHours": 96.0, "category": "serving" }` |
| + HA 대기 1장 | 행 추가: `{ ..., "gpuCount": 1, "gpuHours": 24.0, "category": "standby" }` |
| + 실험용 A100 2장 (모델 유동적) | 행 추가: `{ "model": "unknown", "gpuType": "A100", "gpuCount": 2, "gpuHours": 48.0, "category": "test" }` |
| 낮에 증설: 2장 12h → 4장 12h | **한 행**: `gpuCount: 4, gpuHours: 72.0` (= 2×12 + 4×12) |
| 기종 이전: A100 4장 10h → H100 4장 14h | **두 행**: A100 `gpuHours: 40.0` + H100 `gpuHours: 56.0` |
| 주간 모델 X 12h + 야간 모델 Y 12h (같은 GPU 4장) | **두 행**: X `gpuHours: 48.0` + Y `gpuHours: 48.0` (gpuCount 각 4) |
| 다른 사내 플랫폼 API 사용분 | **행을 쓰지 않음** — GPU는 플랫폼이 보고. 메타데이터 시트 [4](consumes)에 출처만 등록 |
| 사내 GPU 없음 (사외 AI 모델 API만) | `"gpu": []` |

**하지 말 것**:

- `serving`/`standby`에 `model: "unknown"` — test에서만 허용
- `gpuCount × 단가`로 비용 계산 — 장수가 이중으로 곱해진다
- 여러 행의 gpuCount 합산 — 시간 분할 시 할당 초과로 보이는 착시 (장수 합산은 `Σ gpuHours ÷ 24`)

**운영자 검증** (위반 시 담당자에게 알림): [1] 기종별 Σ gpuHours ≤ 할당 GPU-hours [2] 행별 gpuHours ≤ gpuCount × 24 [3] 위 unknown 규칙

**참고** (서비스가 신경 쓸 필요 없는 것): 할당(비용 기준, 고정 쿼터)·유휴는 GPU 대시보드 기반으로 운영자가 산출한다. 하루 안의 시간대별 장수 변화는 받지 않는다 (비용은 적분값으로 정확). 오토스케일링이 도입돼도 같은 구조다 (gpuHours는 적분, gpuCount는 피크).

---

## 4. 서비스 메트릭 — 내 케이스 찾기

| 지표 | 정의 | 단위 | 형태 |
|---|---|---|---|
| **TTFT** | 요청 수신 → 첫 토큰 | ms | p50 / p90 / p99 |
| **ITL** | 토큰 간 간격 | ms | p50 / p90 / p99 |
| **Output TPS** | 요청당 초당 생성 토큰 | tokens/s | avg / p50 |

| 케이스 | 담당자 작업 | serving 블록 |
|---|---|---|
| A. vLLM | `/metrics` URL 제출 + 운영자측 Prometheus에 네트워크 개방 (끝) | 생략 |
| B. SGLang | 상동 (`--enable-metrics` 필요할 수 있음) | 생략 |
| C. 기타 Prometheus 엔진 (TGI 등) | 상동 + TTFT/ITL 메트릭명 매핑을 운영자와 확정 | 생략 |
| D. 자체 구현 서빙 | 아래 측정 예시대로 로깅·집계 → GET 제공 | 채움 |
| E. 사외 AI 모델 API 연동 | `gpu: []`. 원하면 게이트웨이에서 측정 (측정 위치를 메타데이터 시트에 명시) | 선택 |
| F. 비스트리밍 | TTFT/ITL 해당 없음 | 생략 |

**케이스 D 측정 예시** — 스트리밍 응답 코드에 시각 3종을 로깅:

```
요청 수신 10:00:00.000 · 첫 청크 10:00:00.320 · 마지막 청크 10:00:03.320 (출력 100 tokens)
→ TTFT = 320ms
→ Output TPS = 100 ÷ 3.0s = 33.3
→ ITL = 청크 간 간격들 (각각이 샘플)
하루치 전체 요청에서 p50/p90/p99 계산 → serving 블록에 기입
```

- **레플리카가 여러 대면 로그를 모아 한 번에 percentile 계산** — 레플리카별 p99의 평균 ≠ 전체 p99.

---

## 5. 운영 규칙

- 운영자가 **02:00(KST)부터** 전일 데이터 호출. `409`면 1시간 간격 재시도, **09:00까지 미확정이면 담당자에게 알림**.
- 최근 **7일 backfill** 가능 — 정정 시 담당자가 재집계 후 운영자에게 재수집 요청.
- 단위는 필드명에 명시 (`Ms`, `gpuHours`, `Tps`).

---

## 6. 체크리스트

- [ ] (serviceGroup, service, model) 공식 표기 확정 — 기존 토큰 API와 동일한가?
- [ ] GPU 대시보드 할당 단위 확인 + service 매핑을 메타데이터 시트에 제출했는가?
- [ ] 모델 표기를 canonical + alias + sizeClass로 정리해 제출했는가? 파인튜닝은 `-ft-{용도}`로 분리했는가?
- [ ] gpu 블록을 §3 상황별 예시대로 작성 가능한가? (serving·standby는 `unknown` 금지)
- [ ] 추론 엔진 확인 → §4 케이스 판별 + 응답에 `engine` 자기신고 포함하는가?
- [ ] (케이스 A~C) `/metrics` URL 제출 + 운영자측 Prometheus 접근 개방 가능한가?
- [ ] (케이스 D) 시각 3종 로깅 + 레플리카 통합 집계 가능한가?
- [ ] `/v1/metrics`를 02:00 이전에 전일 확정 상태로 제공 가능한가?
- [ ] (플랫폼 제공자) 소비 서비스별 API 키 발급 + 계정↔서비스 매핑 제출했는가?
- [ ] (사내 플랫폼 소비자) consumes(모델 출처) 제출 + 그 사용분의 GPU·토큰을 보내지 않는다는 것을 인지했는가?
