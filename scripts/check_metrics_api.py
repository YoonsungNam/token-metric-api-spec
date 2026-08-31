#!/usr/bin/env python3
"""
check_metrics_api.py — GET /v1/metrics 구현 자가 검사 스크립트 (서비스 담당자용)

사용법:
    python3 check_metrics_api.py --base-url http://my-service.internal:8080
    python3 check_metrics_api.py --base-url http://... --date 2026-08-18
    python3 check_metrics_api.py --base-url http://... --skip-behavior

의존성: 파이썬 3.9+ 표준 라이브러리만 사용 (추가 설치 불필요).
스펙: docs/METRICS_COLLECTION_SPEC.md · token-metric-api.yaml
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
PCT_KEYS = ["p50", "p90", "p95", "p99"]
CATEGORIES = {"serving", "standby", "test"}
EPS = 1e-6

results = []  # (level, check_id, message)


def report(level, check_id, message):
    results.append((level, check_id, message))
    mark = {"PASS": "\033[32m[PASS]\033[0m", "WARN": "\033[33m[WARN]\033[0m",
            "FAIL": "\033[31m[FAIL]\033[0m", "INFO": "[INFO]"}[level]
    print(f"{mark} {check_id:<6} {message}")


def http_get(base_url, date, timeout):
    """(status_code, headers, body_text) 반환. 연결 실패 시 (None, None, 오류문자열)."""
    url = f"{base_url.rstrip('/')}/v1/metrics?{urllib.parse.urlencode({'date': date})}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, headers, e.read().decode("utf-8", "replace")
    except Exception as e:  # 연결 자체가 실패 (DNS·거부·타임아웃)
        return None, None, f"{type(e).__name__}: {e}"


def check_error_body(check_id, body):
    """4xx 응답 본문이 {code, message} 형식인지 (token-metric-api.yaml Error 스키마)."""
    try:
        doc = json.loads(body)
        if isinstance(doc, dict) and isinstance(doc.get("code"), str) and isinstance(doc.get("message"), str):
            report("PASS", check_id, f"오류 본문 형식 OK (code={doc['code']})")
        else:
            report("WARN", check_id, "오류 본문에 code/message(string)가 없음 — Error 스키마 권장")
    except (json.JSONDecodeError, ValueError):
        report("WARN", check_id, "오류 본문이 JSON이 아님 — Error 스키마 {code, message} 권장")


def check_percentiles(path, obj, monotonic=True):
    """LatencyPercentiles: p50/p90/p95/p99 4키 완비·숫자·비감소, 그 외 키 금지."""
    ok = True
    if not isinstance(obj, dict):
        report("FAIL", "B9", f"{path} 가 객체가 아님")
        return False
    extra = set(obj) - set(PCT_KEYS)
    if extra:
        report("FAIL", "B9", f"{path} 에 허용되지 않은 키 {sorted(extra)} — p50/p90/p95/p99만 허용")
        ok = False
    missing = [k for k in PCT_KEYS if k not in obj]
    if missing:
        report("FAIL", "B9", f"{path} 에 {missing} 누락 — 4개 percentile 모두 필수")
        return False
    vals = []
    for k in PCT_KEYS:
        v = obj[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            report("FAIL", "B9", f"{path}.{k} 는 0 이상의 숫자여야 함 (현재: {v!r})")
            ok = False
        else:
            vals.append(v)
    if monotonic and len(vals) == 4 and any(vals[i] > vals[i + 1] + EPS for i in range(3)):
        report("FAIL", "B9", f"{path} percentile 이 비감소가 아님 (p50≤p90≤p95≤p99 여야 함): {vals}")
        ok = False
    return ok


def check_gpu_block(gpu):
    if not isinstance(gpu, list):
        report("FAIL", "B4", "gpu 는 배열이어야 함")
        return
    report("PASS", "B4", f"gpu 배열 확인 ({len(gpu)}행)")
    seen = {}
    for i, row in enumerate(gpu):
        p = f"gpu[{i}]"
        if not isinstance(row, dict):
            report("FAIL", "B4", f"{p} 가 객체가 아님")
            continue
        missing = [k for k in ("model", "gpuType", "gpuCount", "gpuHours", "category") if k not in row]
        if missing:
            report("FAIL", "B4", f"{p} 필수 필드 누락: {missing}")
            continue
        model, gtype, cat = row["model"], row["gpuType"], row["category"]
        cnt, hrs = row["gpuCount"], row["gpuHours"]
        if not isinstance(model, str) or not model:
            report("FAIL", "B4", f"{p}.model 은 비어있지 않은 문자열이어야 함")
        if not isinstance(gtype, str) or not gtype:
            report("FAIL", "B4", f"{p}.gpuType 은 비어있지 않은 문자열이어야 함")
        if not isinstance(cat, str) or cat not in CATEGORIES:
            report("FAIL", "B5", f"{p}.category={cat!r} — serving|standby|test 만 허용")
        elif cat in ("serving", "standby") and model == "unknown":
            report("FAIL", "B6", f"{p}: category={cat} 에서 model \"unknown\" 금지 (test 만 허용)")
        if not isinstance(cnt, (int, float)) or isinstance(cnt, bool) or cnt <= 0:
            report("FAIL", "B7", f"{p}.gpuCount 는 0보다 큰 숫자여야 함 (현재: {cnt!r})")
        if not isinstance(hrs, (int, float)) or isinstance(hrs, bool) or hrs < 0:
            report("FAIL", "B7", f"{p}.gpuHours 는 0 이상의 숫자여야 함 (현재: {hrs!r})")
        elif isinstance(cnt, (int, float)) and not isinstance(cnt, bool) and cnt > 0 and hrs > cnt * 24 + EPS:
            report("FAIL", "B7", f"{p}: gpuHours({hrs}) > gpuCount×24({cnt * 24}) — 검증 규칙 위반")
        key = (str(model), str(gtype), str(cat))
        if key in seen:
            report("WARN", "B8", f"{p}: 동일 (model, gpuType, category) 행 중복 — gpu[{seen[key]}] 와 합쳐서 한 행으로 제공 권장")
        seen[key] = i
    if not any(isinstance(r, dict) and r.get("category") == "serving" for r in gpu) and gpu:
        report("WARN", "B8", "serving 행이 없음 — standby/test 만 제공 중인지 확인")


def check_custom(path, arr):
    if not isinstance(arr, list):
        report("FAIL", "B9", f"{path} 는 배열이어야 함")
        return
    for j, m in enumerate(arr):
        p = f"{path}[{j}]"
        if not isinstance(m, dict):
            report("FAIL", "B9", f"{p} 가 객체가 아님")
            continue
        if not isinstance(m.get("name"), str) or not m.get("name"):
            report("FAIL", "B9", f"{p}.name 필수 (문자열)")
        if not isinstance(m.get("unit"), str) or not m.get("unit"):
            report("FAIL", "B9", f"{p}.unit 필수 (문자열, 단위 표기)")
        extra = set(m) - {"name", "unit", *PCT_KEYS}
        if extra:
            report("FAIL", "B9", f"{p} 에 허용되지 않은 키 {sorted(extra)} — name/unit/p50/p90/p95/p99만 허용")
        vals = [k for k in PCT_KEYS if k in m]
        if not vals:
            report("FAIL", "B9", f"{p}: 값 키(p50/p90/p95/p99) 최소 1개 필수 — 단일값 지표는 p50 하나로")
        for k in vals:
            if not isinstance(m[k], (int, float)) or isinstance(m[k], bool):
                report("FAIL", "B9", f"{p}.{k} 는 숫자여야 함")


def check_serving_block(serving, present):
    if serving is None:
        if present:
            report("FAIL", "B9", "serving 이 null — 배열이어야 함 (경로 (b)는 빈 배열 [])")
        # 키 자체가 없으면 B1 에서 이미 FAIL 처리됨
        return
    if not isinstance(serving, list):
        report("FAIL", "B9", "serving 은 배열이어야 함")
        return
    if not serving:
        report("PASS", "B9", "serving: [] (케이스 A~C — 운영자 스크랩 경로)")
        return
    report("PASS", "B9", f"serving 배열 확인 ({len(serving)}행) — 경로 (a)")
    allowed = {"model", "ttftMs", "itlMs", "outputTps", "e2eMs", "custom"}
    seen_models = set()
    for i, row in enumerate(serving):
        p = f"serving[{i}]"
        if not isinstance(row, dict):
            report("FAIL", "B9", f"{p} 가 객체가 아님")
            continue
        if not isinstance(row.get("model"), str) or not row.get("model"):
            report("FAIL", "B9", f"{p}.model 필수 (canonical 표기)")
        elif row["model"] == "unknown":
            report("FAIL", "B9", f"{p}.model 에 \"unknown\" 불가 — 성능은 canonical 모델 단위")
        extra = set(row) - allowed
        if extra:
            report("FAIL", "B9", f"{p} 에 허용되지 않은 키 {sorted(extra)}")
        m = row.get("model")
        if isinstance(m, str) and m:
            if m in seen_models:
                report("WARN", "B10", f"{p}: 동일 model 행 중복 — 모델당 1행으로 합칠 것")
            seen_models.add(m)
        standard = [k for k in ("ttftMs", "itlMs", "outputTps", "e2eMs") if k in row]
        if not standard and "custom" not in row:
            report("FAIL", "B10", f"{p}: 지표가 하나도 없음 — 스트리밍은 ttftMs·itlMs·outputTps, 비스트리밍은 e2eMs")
        elif not standard:
            report("WARN", "B10", f"{p}: 표준 지표 없이 custom 만 있음 — 비스트리밍(케이스 F)은 e2eMs 필수, custom 은 보조")
        if "ttftMs" in row:
            check_percentiles(f"{p}.ttftMs", row["ttftMs"])
        if "itlMs" in row:
            check_percentiles(f"{p}.itlMs", row["itlMs"])
        if "e2eMs" in row:
            check_percentiles(f"{p}.e2eMs", row["e2eMs"])
        if ("ttftMs" in row) != ("itlMs" in row):
            report("WARN", "B10", f"{p}: ttftMs/itlMs 중 하나만 있음 — 스트리밍 모델은 쌍으로 제공")
        if "outputTps" in row:
            tps = row["outputTps"]
            if not isinstance(tps, dict):
                report("FAIL", "B9", f"{p}.outputTps 는 객체여야 함")
            else:
                if "p50" not in tps or not isinstance(tps.get("p50"), (int, float)) or isinstance(tps.get("p50"), bool) or tps["p50"] < 0:
                    report("FAIL", "B9", f"{p}.outputTps.p50 필수 (0 이상의 숫자)")
                extra_t = set(tps) - {"p50"}
                if extra_t:
                    report("FAIL", "B9", f"{p}.outputTps 에 허용되지 않은 키 {sorted(extra_t)} — p50만 허용 (avg·상위 percentile 없음)")
        if "custom" in row:
            check_custom(f"{p}.custom", row["custom"])


def check_body(body, req_date):
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        report("FAIL", "A3", f"응답이 JSON 이 아님: {e}")
        return None
    report("PASS", "A3", "JSON 파싱 OK")
    if not isinstance(doc, dict):
        report("FAIL", "B1", "응답 본문은 객체여야 함")
        return None

    missing = [k for k in ("date", "serviceGroup", "service", "generatedAt", "gpu", "serving") if k not in doc]
    if missing:
        report("FAIL", "B1", f"최상위 필수 필드 누락: {missing} (serving 은 경로 (b)여도 빈 배열 [] 로 포함)")
    else:
        report("PASS", "B1", "최상위 필수 필드 존재 (date, serviceGroup, service, generatedAt, gpu, serving)")

    if doc.get("date") != req_date:
        report("FAIL", "B2", f"date 에코 불일치: 요청={req_date}, 응답={doc.get('date')!r}")
    else:
        report("PASS", "B2", "date 에코 일치")

    for k in ("serviceGroup", "service"):
        v = doc.get(k)
        if not isinstance(v, str) or not v:
            report("FAIL", "B1", f"{k} 는 비어있지 않은 문자열이어야 함 (메타데이터 시트 공식 표기)")

    gen = doc.get("generatedAt")
    if isinstance(gen, str) and re.search(r"\+09:00$", gen):
        try:
            datetime.fromisoformat(gen)
            report("PASS", "B3", f"generatedAt OK ({gen})")
        except ValueError:
            report("FAIL", "B3", f"generatedAt 이 ISO 8601 이 아님: {gen!r}")
    else:
        report("FAIL", "B3", f"generatedAt 은 ISO 8601 + '+09:00' 오프셋이어야 함 (현재: {gen!r})")

    if "gpu" in doc:
        check_gpu_block(doc["gpu"])
    check_serving_block(doc.get("serving"), "serving" in doc)

    if "engine" in doc and doc["engine"] is not None:
        eng = doc["engine"]
        if not isinstance(eng, dict) or not isinstance(eng.get("type"), str) or not eng.get("type"):
            report("FAIL", "B11", f"engine 은 {{type, version?}} 객체여야 함 (현재: {eng!r})")
        else:
            report("PASS", "B11", f"engine 자기신고 OK (type={eng['type']})")
    else:
        report("WARN", "B11", "engine 자기신고 없음 — 권장 (버전 수기 갱신을 없애줌)")

    known = {"date", "serviceGroup", "service", "generatedAt", "engine", "gpu", "serving"}
    extra = set(doc) - known
    if extra:
        report("WARN", "B12", f"스펙에 없는 최상위 필드: {sorted(extra)} (토큰량·requests 는 보내지 말 것 — 이중 소스 금지)")
    return doc


def behavior_checks(base_url, timeout):
    today = datetime.now(KST).date()

    status, headers, body = http_get(base_url, today.isoformat(), timeout)
    if status == 400:
        report("PASS", "C1", f"당일 date({today}) → 400")
        check_error_body("C1", body)
    elif status is None:
        report("FAIL", "C1", f"당일 date 호출 실패: {body}")
    else:
        report("FAIL", "C1", f"당일 date 는 400 이어야 함 (현재: {status}) — 미확정 데이터 구분 규칙")

    future = (today + timedelta(days=7)).isoformat()
    status, headers, body = http_get(base_url, future, timeout)
    if status == 400:
        report("PASS", "C2", f"미래 date({future}) → 400")
    elif status is None:
        report("FAIL", "C2", f"미래 date 호출 실패: {body}")
    else:
        report("FAIL", "C2", f"미래 date 는 400 이어야 함 (현재: {status})")

    old = (today - timedelta(days=30)).isoformat()
    status, headers, body = http_get(base_url, old, timeout)
    if status == 404:
        report("PASS", "C3", f"보존 기간 초과 date({old}) → 404")
    elif status == 200:
        report("WARN", "C3", f"30일 전 date 가 200 — 보존을 7일보다 길게 제공 중 (스펙상 초과분은 404)")
    elif status is None:
        report("FAIL", "C3", f"과거 date 호출 실패: {body}")
    else:
        report("FAIL", "C3", f"보존 기간 초과 date 는 404 여야 함 (현재: {status})")

    bad = "2026-13-99"
    status, headers, body = http_get(base_url, bad, timeout)
    if status == 400:
        report("PASS", "C5", f"형식 오류 date({bad}) → 400")
    elif status is None:
        report("FAIL", "C5", f"형식 오류 date 호출 실패: {body}")
    else:
        report("FAIL", "C5", f"형식 오류 date 는 400 이어야 함 (현재: {status})")


def main():
    ap = argparse.ArgumentParser(description="GET /v1/metrics 구현 자가 검사")
    ap.add_argument("--base-url", required=True, help="서비스 베이스 URL (예: http://my-svc.internal:8080)")
    ap.add_argument("--date", help="검사 대상 일자 YYYY-MM-DD (기본: 어제, KST)")
    ap.add_argument("--skip-behavior", action="store_true",
                    help="응답 규칙 동작 검사(C1~C5: 당일·미래·보존초과·형식오류·멱등) 생략")
    ap.add_argument("--timeout", type=float, default=10.0, help="HTTP 타임아웃 초 (기본 10)")
    args = ap.parse_args()

    date = args.date or (datetime.now(KST).date() - timedelta(days=1)).isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        print(f"--date 형식 오류: {date} (YYYY-MM-DD)")
        sys.exit(2)
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        print(f"--date 가 존재하지 않는 날짜: {date}")
        sys.exit(2)
    if d >= datetime.now(KST).date():
        print(f"--date 는 과거 날짜여야 함 (KST 기준 오늘: {datetime.now(KST).date().isoformat()}) — 스펙상 당일/미래는 400 대상")
        sys.exit(2)

    print(f"# check_metrics_api — base={args.base_url} date={date}\n")

    status, headers, body = http_get(args.base_url, date, args.timeout)
    if status is None:
        report("FAIL", "A1", f"연결 실패: {body}")
    else:
        report("PASS", "A1", f"호출 성공 (HTTP {status})")
        if status == 200:
            ct = (headers.get("content-type") or "").lower()
            if "application/json" in ct:
                report("PASS", "A2", f"Content-Type OK ({ct})")
            else:
                report("WARN", "A2", f"Content-Type 이 application/json 이 아님 ({ct or '없음'})")
            doc = check_body(body, date)
            # C4 멱등성: 같은 date 재호출 → 동일 본문
            if doc is not None and not args.skip_behavior:
                status2, _, body2 = http_get(args.base_url, date, args.timeout)
                try:
                    same = status2 == 200 and json.loads(body2) == doc
                except (json.JSONDecodeError, ValueError):
                    same = False
                if same:
                    report("PASS", "C4", "같은 date 재호출 시 동일 결과 (멱등성)")
                else:
                    report("FAIL", "C4", f"같은 date 재호출 결과가 다름 (2차: HTTP {status2}) — 재수집 안전성 위반")
        elif status == 409:
            report("PASS", "A2", "409 (집계 전 미확정) — 유효한 응답. 확정 후 재실행하거나 --date 로 확정된 날짜 지정")
            if headers and headers.get("retry-after"):
                report("INFO", "A2", f"Retry-After: {headers['retry-after']}초")
            check_error_body("A2", body)
        elif status == 404:
            report("WARN", "A2", "404 (보존 기간 초과) — 최근 날짜로 --date 를 지정해 재실행")
            check_error_body("A2", body)
        elif status == 400:
            report("FAIL", "A2", f"과거 유효 날짜({date})에 400 — date 파싱을 확인")
            check_error_body("A2", body)
        else:
            report("FAIL", "A2", f"예상 밖 상태 코드 {status} — 200/409(/404) 이어야 함")

    if not args.skip_behavior and status is not None:
        print()
        behavior_checks(args.base_url, args.timeout)

    n = {"PASS": 0, "WARN": 0, "FAIL": 0, "INFO": 0}
    for level, _, _ in results:
        n[level] += 1
    print(f"\n결과: PASS {n['PASS']} · WARN {n['WARN']} · FAIL {n['FAIL']}")
    if n["FAIL"]:
        print("→ FAIL 항목을 수정한 뒤 재실행하세요. 규칙 근거: docs/METRICS_COLLECTION_SPEC.md")
        sys.exit(1)
    print("→ 통과! 운영자측 검증(할당 대비 Σ검증, 모델명 정규화 등)은 수집 개시 후 자동으로 수행됩니다.")
    sys.exit(0)


if __name__ == "__main__":
    main()
