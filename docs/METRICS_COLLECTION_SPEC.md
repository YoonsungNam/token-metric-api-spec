# /v1/metrics 구현 스펙 — 서비스 담당자용

> 대상: 각 AI 서비스 담당자 · **해야 할 일 + 상황별 예시** 중심
> 설계 배경·근거: [상세 정의서(old)](METRICS_COLLECTION_SPEC_OLD.md) · [의사결정 로그](DECISIONS.md) · [미팅용 요약](METRICS_COLLECTION_SUMMARY.md)
> 관련 스펙: [token-usage-api-spec](https://github.com/YoonsungNam/token-usage-api-spec)

---

## 0. 해야 할 일

| 누가 | 무엇을 | 언제까지 |
|---|---|---|
| **모든 서비스** | ① 취합본(엑셀) 제출 (§1) | 온보딩 시 1회 |
| **모든 서비스** | ② GET `/v1/metrics` 구현 — gpu 블록 (§2~3) | **~9/18** |
| vLLM / SGLang 팀 | ③ `/metrics` URL 제출 + 네트워크 개방 — **이러면 끝** (serving 블록 불필요) | 온보딩 시 |
| 자체 구현 서빙 / 외부 API 팀 | ③′ serving 블록 자체 집계 (§4) | ~9/18 |

- 중앙이 매일 새벽 pull 호출한다 — 서비스는 상태 없는 GET만 제공 (크론·재시도·장애 감지는 중앙 책임).
- 토큰량·requests는 보내지 않는다 — 기존 토큰 API 담당 (이중 소스 금지).

---

## 1. 취합본(엑셀) 제출

**제출 항목 6가지**:

| # | 항목 | 내용 |
|---|---|---|
| ① | 서비스 표기 | serviceGroup · service · 담당자 — **기존 토큰 API와 같은 문자열** (`claude` ≠ `Claude`) |
| ② | 모델 표기 | canonical + alias 목록 + family + 체급(sizeClass: S ~15B / M ~40B / L 40B+) |
| ③ | GPU 할당 매핑 | GPU 대시보드 할당 단위(프로젝트/네임스페이스 등) ↔ service |
| ④ | 소비 관계 | "우리 서비스 → 어느 플랫폼의 어느 모델" (사내 플랫폼 사용 시) |
| ⑤ | 서비스 계정 매핑 | (플랫폼 제공자만) 서비스 계정 ↔ 소비 서비스 |
| ⑥ | `/metrics` URL | (케이스 A~C만) 스크랩 URL + 엔진 종류 |

**작성 예시**:

```yaml
serviceGroup: claude                                     # ①
service: claude-cowork                                   # ①
owner: kim@company.com                                   # ①
models:                                                  # ②
  - { canonical: opus, family: claude, sizeClass: L, aliases: ["anthropic/claude-opus", "opus-fp8"] }
gpuAllocationUnit: "k8s-ns:claude-cowork-prod"           # ③
consumes: []                                             # ④ 예: [{ platform: llm-gateway, model: llama3.3-70b }]
serviceAccounts: {}                                      # ⑤ 예: { svc-key-a1: chat-assistant }
metricsUrl: "http://cowork-vllm.internal:8000/metrics"   # ⑥
engine: { type: vllm }                                   # ⑥ 버전은 API 자기신고로 수신 — 수기 갱신 불필요
```

**모델 표기(②) 작성법 — 상황별 예시**:

| 상황 | 이렇게 쓴다 |
|---|---|
| 팀마다 부르는 이름이 다름 — `meta-llama/Llama-3.3-70B-Instruct`와 `/models/llama33-70b` (같은 순정 모델) | canonical `llama3.3-70b` **하나** + 두 표기를 aliases에 |
| 사내 파인튜닝본 운영 | **반드시 별도** canonical: `llama3.3-70b-ft-cs` — 순정과 같은 id 금지 |
| AWQ/FP8 양자화 배포 | 별도 canonical: `llama3.3-70b-awq` |
| 외부 API 날짜 버전 (`gpt-4o-2024-11-20`) | canonical `gpt-4o` + 날짜 문자열은 alias |
| 같은 모델인지 애매함 | **나눈다** — 나눈 건 나중에 합쳐 볼 수 있지만, 합친 건 못 나눔 |

- 형식: 소문자·하이픈 `{패밀리}{버전}-{크기}[-{변형}]`. 판단 기준: ①같은 웨이트 ②순정 ③같은 정밀도 — 셋 다 "예"일 때만 같은 canonical.
- **기존에 쓰던 표기는 안 바꿔도 된다** — alias에 넣으면 중앙이 정규화. vLLM/SGLang은 `--served-model-name`을 canonical로 설정하면 그것도 불필요.

**제출 후 운영 — 갱신은 알림 기반 (기억할 필요 없음)**:

- 미등록 모델 표기가 데이터에 나타나면 중앙이 후보와 함께 알림 → **"기존 ○○의 alias" / "새 모델" 중 택일 회신**하면 끝 (사전 갱신 후 7일 내 소급 재처리. 날짜 접미 패턴은 자동 처리, 무응답 시 "미등록"으로 계속 표시)
- 엔진 교체·검증 위반·할당 매핑 불일치도 중앙이 감지해 알림

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

**응답 예시 3 — 외부 API 연동 (케이스 E): gpu 빈 배열**

```json
{
  "date": "2026-08-18",
  "serviceGroup": "hr",
  "service": "hr-chatbot",
  "generatedAt": "2026-08-19T01:05:00+09:00",
  "gpu": [],
  "serving": [
    { "model": "gpt-4o", "ttftMs": { "p50": 850, "p90": 1600, "p99": 3200 }, "itlMs": { "p50": 22, "p90": 38, "p99": 80 } }
  ]
}
```

**필드 정의**:

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `date` | string (YYYY-MM-DD) | O | KST 기준 집계 대상일 |
| `serviceGroup` / `service` | string | O | 취합본 공식 표기와 일치 |
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

원칙 2줄: **model × gpuType × category 단위로 행**을 쓴다. `gpuHours`(장수 × 시간의 적분)가 비용의 근거이고, `gpuCount`(그날 최대 장수)는 표시·검증용 — 비용은 중앙이 `gpuHours × 단가(원/GPU·h)`로 계산한다 (`[GPU·h] × [원/GPU·h] = [원]`, 장수는 gpuHours에 이미 포함).

| 상황 | gpu 블록에 이렇게 쓴다 |
|---|---|
| **기본**: H100 4장으로 opus를 하루 종일 서빙 | `{ "model": "opus", "gpuType": "H100", "gpuCount": 4, "gpuHours": 96.0, "category": "serving" }` |
| + HA 대기 1장 | 행 추가: `{ ..., "gpuCount": 1, "gpuHours": 24.0, "category": "standby" }` |
| + 실험용 A100 2장 (모델 유동적) | 행 추가: `{ "model": "unknown", "gpuType": "A100", "gpuCount": 2, "gpuHours": 48.0, "category": "test" }` |
| 낮에 증설: 2장 12h → 4장 12h | **한 행**: `gpuCount: 4, gpuHours: 72.0` (= 2×12 + 4×12) |
| 기종 이전: A100 4장 10h → H100 4장 14h | **두 행**: A100 `gpuHours: 40.0` + H100 `gpuHours: 56.0` |
| 주간 모델 X 12h + 야간 모델 Y 12h (같은 GPU 4장) | **두 행**: X `gpuHours: 48.0` + Y `gpuHours: 48.0` (gpuCount 각 4) |
| 다른 사내 플랫폼 API 사용분 | **행을 쓰지 않음** — GPU는 플랫폼이 보고. 취합본 ④에 소비 관계만 등록 |
| 사내 GPU 없음 (외부 API만) | `"gpu": []` |

**하지 말 것**:

- `serving`/`standby`에 `model: "unknown"` — test에서만 허용
- `gpuCount × 단가`로 비용 계산 — 장수가 이중으로 곱해진다
- 여러 행의 gpuCount 합산 — 시간 분할 시 할당 초과로 보이는 착시 (장수 합산은 `Σ gpuHours ÷ 24`)

**중앙 검증** (위반 시 알림): ① 기종별 Σ gpuHours ≤ 할당 GPU-hours ② 행별 gpuHours ≤ gpuCount × 24 ③ 위 unknown 규칙

**참고** (서비스가 신경 쓸 필요 없는 것): 할당(비용 기준, 고정 쿼터)·유휴는 GPU 대시보드 기반으로 중앙이 산출한다. 하루 안의 시간대별 장수 변화는 받지 않는다 (비용은 적분값으로 정확). 오토스케일링이 도입돼도 같은 구조다 (gpuHours는 적분, gpuCount는 피크).

---

## 4. 서비스 메트릭 — 내 케이스 찾기

| 지표 | 정의 | 단위 | 형태 |
|---|---|---|---|
| **TTFT** | 요청 수신 → 첫 토큰 | ms | p50 / p90 / p99 |
| **ITL** | 토큰 간 간격 | ms | p50 / p90 / p99 |
| **Output TPS** | 요청당 초당 생성 토큰 | tokens/s | avg / p50 |

| 케이스 | 담당자 작업 | serving 블록 |
|---|---|---|
| A. vLLM | `/metrics` URL 제출 + 네트워크 개방 (끝) | 생략 |
| B. SGLang | 상동 (`--enable-metrics` 필요할 수 있음) | 생략 |
| C. 기타 Prometheus 엔진 (TGI 등) | 상동 + TTFT/ITL 메트릭명 매핑을 중앙과 확정 | 생략 |
| D. 자체 구현 서빙 | 아래 측정 예시대로 로깅·집계 → GET 제공 | 채움 |
| E. 외부 API 연동 | `gpu: []`. 원하면 게이트웨이에서 측정 (측정 위치를 취합본에 명시) | 선택 |
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

- 중앙이 **02:00(KST)부터** 전일 데이터 호출. `409`면 1시간 간격 재시도, **09:00까지 미확정이면 담당자 알림**.
- 최근 **7일 backfill** 가능 — 정정 시 재집계 후 중앙에 재수집 요청.
- 단위는 필드명에 명시 (`Ms`, `gpuHours`, `Tps`).

---

## 6. 체크리스트

- [ ] (serviceGroup, service, model) 공식 표기 확정 — 기존 토큰 API와 동일한가?
- [ ] GPU 대시보드 할당 단위 확인 + service 매핑을 취합본에 제출했는가?
- [ ] 모델 표기를 canonical + alias + sizeClass로 정리해 제출했는가? 파인튜닝은 `-ft-{용도}`로 분리했는가?
- [ ] gpu 블록을 §3 상황별 예시대로 작성 가능한가? (serving·standby는 `unknown` 금지)
- [ ] 추론 엔진 확인 → §4 케이스 판별 + 응답에 `engine` 자기신고 포함하는가?
- [ ] (케이스 A~C) `/metrics` URL 제출 + 중앙 Prometheus 접근 개방 가능한가?
- [ ] (케이스 D) 시각 3종 로깅 + 레플리카 통합 집계 가능한가?
- [ ] `/v1/metrics`를 02:00 이전에 전일 확정 상태로 제공 가능한가?
- [ ] (플랫폼 제공자) 소비 서비스별 API 키 발급 + 계정↔서비스 매핑 제출했는가?
- [ ] (플랫폼 소비자) 소비 관계 제출 + 그 사용분의 GPU·토큰을 보내지 않는다는 것을 인지했는가?
