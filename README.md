# token-metric-api-spec

사내 AI 서비스 **비용·성능 메트릭 수집 체계 정의서** — 모델별 GPU Hour(모델에 GPU가 매핑·할당된 시간)와 성능 메트릭(TTFT, ITL, Output TPS)을 수집하여 비용·효율·성능을 한 화면에서 보기 위한 스펙.

- 📄 구현 스펙 (서비스 담당자용): [docs/METRICS_COLLECTION_SPEC.md](docs/METRICS_COLLECTION_SPEC.md)
- 🧾 OpenAPI 계약: [token-metric-api.yaml](token-metric-api.yaml) (OpenAPI 3.1)
- ✅ 구현 자가 검사: [scripts/check_metrics_api.py](scripts/check_metrics_api.py) ([사용법·검사 항목](scripts/README.md))
- 📋 담당자 요약 (미팅용): [docs/METRICS_COLLECTION_SUMMARY.md](docs/METRICS_COLLECTION_SUMMARY.md)
- 📊 메타데이터 시트 양식: [docs/METADATA_SHEET_TEMPLATE.xlsx](docs/METADATA_SHEET_TEMPLATE.xlsx)
- 📝 의사결정 로그: [docs/internal/DECISIONS.md](docs/internal/DECISIONS.md)
- 🔧 수집기 설계 (운영자용): [docs/internal/COLLECTOR_DESIGN.md](docs/internal/COLLECTOR_DESIGN.md)
- 관련 스펙: [token-usage-api-spec](https://github.com/YoonsungNam/token-usage-api-spec) (기존 토큰 사용량 조회 API)
