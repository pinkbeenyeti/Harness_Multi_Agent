import os
import sys

# Windows cp949 인코딩 크래시 방지
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import re
import json
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import parse_log_line

# ---------------------------------------------------------------------------
# 토큰 근사 계수
#
# 실제 토크나이저를 호출하지 않고 파일 내용만으로 추정한다. API 모드가 아닌
# Agent(antigravity) 모드에서는 usage 값을 받을 수 없기 때문이다.
# 한글은 토크나이저에서 글자당 1토큰을 넘는 경우가 흔하고, 코드/영문은
# 대략 4글자당 1토큰이다. 절대값이 아니라 '어느 단계가 비싼가'의 비교용이다.
# ---------------------------------------------------------------------------
TOKENS_PER_HANGUL_CHAR = 1.15
TOKENS_PER_ASCII_CHAR = 0.27

FENCE_RE = re.compile(r'^\s*```')


def is_hangul(ch):
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def approx_tokens(text):
    """한글/비한글을 구분해 토큰 수를 근사한다."""
    hangul = sum(1 for ch in text if is_hangul(ch))
    other = len(text) - hangul
    return int(hangul * TOKENS_PER_HANGUL_CHAR + other * TOKENS_PER_ASCII_CHAR)


def split_code_prose(text):
    """마크다운 코드펜스를 기준으로 코드 영역과 산문 영역을 분리한다."""
    code_lines = []
    prose_lines = []
    in_code = False
    for line in text.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_code = not in_code
            code_lines.append(line)
            continue
        (code_lines if in_code else prose_lines).append(line)
    return "".join(code_lines), "".join(prose_lines)


def measure_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    code, prose = split_code_prose(content)
    return {
        "file": os.path.basename(path),
        "bytes": len(content.encode("utf-8")),
        "lines": content.count("\n") + 1,
        "code_tokens": approx_tokens(code),
        "prose_tokens": approx_tokens(prose),
        "total_tokens": approx_tokens(content),
    }


# ---------------------------------------------------------------------------
# 산출물 분류
# ---------------------------------------------------------------------------
def classify(filename):
    name = filename.lower()
    if name.startswith("critic_report"):
        return "critic"
    if name.startswith("result"):
        return "worker"
    if name.startswith("brief"):
        return "brief"
    if name in ("task.md", "context.md", "log.md") or name.startswith("design_spec") or name.startswith("change_spec"):
        return "orchestrator"
    return "other"


def collect_artifacts(task_dir):
    buckets = {}
    for path in sorted(glob.glob(os.path.join(task_dir, "*.md"))):
        m = measure_file(path)
        bucket = classify(m["file"])
        buckets.setdefault(bucket, {"files": [], "bytes": 0, "code_tokens": 0,
                                    "prose_tokens": 0, "total_tokens": 0})
        b = buckets[bucket]
        b["files"].append(m)
        for k in ("bytes", "code_tokens", "prose_tokens", "total_tokens"):
            b[k] += m[k]
    return buckets


# ---------------------------------------------------------------------------
# log.md 기반 라운드트립 / 소요시간 계측
# ---------------------------------------------------------------------------
def parse_log(log_path):
    entries = []
    if not os.path.exists(log_path):
        return entries
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = parse_log_line(line.rstrip("\n"))
            if not parsed:
                continue  # 연속 들여쓰기 줄은 직전 항목의 부속 내용이므로 건너뛴다
            tag, date, hhmm, text = parsed
            try:
                ts = datetime.strptime(f"{date} {hhmm}" if hhmm else date,
                                       "%Y-%m-%d %H:%M" if hhmm else "%Y-%m-%d")
            except ValueError:
                continue
            entries.append({"ts": ts, "tag": tag, "text": text, "has_time": bool(hhmm)})
    return entries


def analyze_log(entries):
    if not entries:
        return {
            "entries": 0, "tags": {}, "elapsed_min": 0, "time_by_tag_min": {},
            "out_of_order": 0, "no_time_entries": 0, "timed_entries": 0,
        }

    tags = {}
    for e in entries:
        tags[e["tag"]] = tags.get(e["tag"], 0) + 1

    # 시각이 없는 항목(날짜만 기록)은 소요시간 집계에서 제외한다.
    timed = [e for e in entries if e["has_time"]]
    elapsed_min = 0
    if len(timed) >= 2:
        ts = [e["ts"] for e in timed]
        elapsed_min = int((max(ts) - min(ts)).total_seconds() // 60)

    # 각 항목 사이의 경과시간을 '앞선 항목의 태그'에 귀속시킨다.
    # 즉 WORKER_CALL 뒤 VERIFICATION까지 걸린 시간은 WORKER_CALL 몫으로 잡힌다.
    # 로그가 시간순이 아닌 구간(사후 추가 기록)은 음수가 되므로 제외하고 센다.
    time_by_tag = {}
    out_of_order = 0
    for prev, cur in zip(timed, timed[1:]):
        delta = (cur["ts"] - prev["ts"]).total_seconds() / 60.0
        if delta < 0:
            out_of_order += 1
            continue
        time_by_tag[prev["tag"]] = time_by_tag.get(prev["tag"], 0.0) + delta

    return {
        "entries": len(entries),
        "tags": tags,
        "elapsed_min": elapsed_min,
        "time_by_tag_min": {k: round(v, 1) for k, v in sorted(time_by_tag.items())},
        "out_of_order": out_of_order,
        "no_time_entries": len(entries) - len(timed),
        "timed_entries": len(timed),
    }


# ---------------------------------------------------------------------------
# 파이프라인 고정 오버헤드 (매 턴 상주하는 규칙 문서)
# ---------------------------------------------------------------------------
def measure_fixed_overhead(base_dir):
    result = {}
    skill_path = os.path.join(base_dir, "SKILL.md")
    if os.path.exists(skill_path):
        m = measure_file(skill_path)
        result["SKILL.md"] = {"bytes": m["bytes"], "tokens": m["total_tokens"]}
    result["resident_tokens"] = sum(v["tokens"] for v in result.values() if isinstance(v, dict))
    return result


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def build_metrics(base_dir, task_name):
    task_dir = os.path.join(base_dir, "tasks", task_name)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"Task '{task_name}' not found at {task_dir}")

    artifacts = collect_artifacts(task_dir)
    log_stats = analyze_log(parse_log(os.path.join(task_dir, "log.md")))
    overhead = measure_fixed_overhead(base_dir)

    worker_tok = artifacts.get("worker", {}).get("total_tokens", 0)
    critic_tok = artifacts.get("critic", {}).get("total_tokens", 0)
    brief_tok = artifacts.get("brief", {}).get("total_tokens", 0)
    orch_tok = artifacts.get("orchestrator", {}).get("total_tokens", 0)
    artifact_tok = worker_tok + critic_tok + brief_tok + orch_tok

    worker_code = artifacts.get("worker", {}).get("code_tokens", 0)

    tags = log_stats["tags"]
    worker_calls = tags.get("WORKER_CALL", 0)
    verifications = tags.get("VERIFICATION", 0)
    approvals = tags.get("APPROVAL", 0)

    # 산출물은 생성 1회 + 최소 1회 이상 재소비(크리틱/오케스트레이터 검토/병합)된다.
    # 실측 불가 구간이므로 '생성분'과 '재소비 추정분'을 분리해 보고한다.
    reconsumption = worker_tok * max(verifications, 1) + critic_tok

    return {
        "task": task_name,
        "measured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "토큰은 파일 내용 기반 근사치다. 단계 간 비교용이며 청구 토큰과 다르다.",
        "roundtrips": {
            "log_entries": log_stats["entries"],
            "worker_calls": worker_calls,
            "verifications": verifications,
            "user_approvals": approvals,
            "tags": tags,
        },
        "wallclock": {
            "elapsed_min": log_stats["elapsed_min"],
            "time_by_tag_min": log_stats["time_by_tag_min"],
            "out_of_order_entries": log_stats["out_of_order"],
            "entries_without_time": log_stats["no_time_entries"],
        },
        "artifact_tokens": {
            "brief": brief_tok,
            "worker_result": worker_tok,
            "critic_report": critic_tok,
            "orchestrator_docs": orch_tok,
            "total": artifact_tok,
        },
        "derived": {
            # B(diff 핸드오프) 판단용: result.md에서 코드블록이 차지하는 비중
            "worker_code_share_pct": round(worker_code / worker_tok * 100, 1) if worker_tok else 0.0,
            # C(크리틱 축소) 판단용: 워커 대비 크리틱의 토큰 비중
            "critic_share_pct": round(critic_tok / (worker_tok + critic_tok) * 100, 1) if (worker_tok + critic_tok) else 0.0,
            # D(SKILL.md 다이어트) 판단용: 상주 규칙문서를 로그 항목 수만큼 곱한 누적 고정비
            "resident_overhead_tokens": overhead.get("resident_tokens", 0),
            "resident_overhead_cumulative": overhead.get("resident_tokens", 0) * max(log_stats["entries"], 1),
            "estimated_reconsumption_tokens": reconsumption,
        },
        "fixed_overhead": overhead,
        "artifacts": {k: {"bytes": v["bytes"], "total_tokens": v["total_tokens"],
                          "code_tokens": v["code_tokens"], "prose_tokens": v["prose_tokens"],
                          "files": [f["file"] for f in v["files"]]}
                      for k, v in sorted(artifacts.items())},
    }


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------
def persist(base_dir, task_name, metrics):
    task_dir = os.path.join(base_dir, "tasks", task_name)

    tracker_path = os.path.join(task_dir, "cost_tracker.json")
    data = {}
    if os.path.exists(tracker_path):
        try:
            with open(tracker_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Warning] cost_tracker.json 파싱 실패, 새로 씁니다: {e}")
            data = {}
    if not isinstance(data, dict):
        data = {}

    data.setdefault("budget_limit", 2.0)
    data.setdefault("accumulated_cost", 0.0)
    data.setdefault("fallback_role", "gemini")
    data.setdefault("worker_mode", "multi-api")
    data.setdefault("history", [])
    data["metrics"] = metrics

    with open(tracker_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log_path = os.path.join(task_dir, "log.md")
    if os.path.exists(log_path):
        a = metrics["artifact_tokens"]
        w = metrics["wallclock"]
        r = metrics["roundtrips"]
        line = (f"\n{metrics['measured_at']} [METRICS] 라운드트립 {r['log_entries']}회"
                f"(워커 {r['worker_calls']} / 검증 {r['verifications']} / 승인 {r['user_approvals']}), "
                f"경과 {w['elapsed_min']}분, 산출물 근사 {a['total']}토큰"
                f"(워커 {a['worker_result']} / 크리틱 {a['critic_report']}), "
                f"코드블록 비중 {metrics['derived']['worker_code_share_pct']}%.")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

    return tracker_path


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def print_report(m):
    r, w, a, d = m["roundtrips"], m["wallclock"], m["artifact_tokens"], m["derived"]
    print(f"\n=== Task Metrics: {m['task']} ===")
    print(f"측정 시각: {m['measured_at']}")
    print(f"* 토큰은 파일 기반 근사치입니다 (한글 {TOKENS_PER_HANGUL_CHAR}/자, 그 외 {TOKENS_PER_ASCII_CHAR}/자).\n")

    print("[라운드트립]")
    print(f"  로그 항목 총계 : {r['log_entries']}")
    print(f"  워커 호출      : {r['worker_calls']}")
    print(f"  검증(크리틱)   : {r['verifications']}")
    print(f"  사용자 승인    : {r['user_approvals']}")
    if r["tags"]:
        print("  태그 분포      : " + ", ".join(f"{k}={v}" for k, v in sorted(r["tags"].items())))

    print("\n[소요시간]")
    print(f"  전체 경과      : {w['elapsed_min']}분")
    if w["time_by_tag_min"]:
        print("  단계별 점유    : " + ", ".join(f"{k} {v}분" for k, v in w["time_by_tag_min"].items()))
    if w["out_of_order_entries"]:
        print(f"  ⚠ 시간 역순 기록 {w['out_of_order_entries']}건은 집계에서 제외했습니다.")
    if w["entries_without_time"]:
        print(f"  ⚠ 시각(HH:MM) 없는 항목 {w['entries_without_time']}건은 소요시간 집계에서 제외했습니다.")

    print("\n[산출물 토큰 (근사)]")
    for label, key in (("브리프", "brief"), ("워커 result", "worker_result"),
                       ("크리틱 report", "critic_report"), ("오케스트레이터 문서", "orchestrator_docs")):
        print(f"  {label:<20}: {a[key]:>8,}")
    print(f"  {'합계':<20}: {a['total']:>8,}")

    print("\n[판단 지표]")
    print(f"  result.md 코드블록 비중 : {d['worker_code_share_pct']}%   → B(diff 핸드오프) 효과 상한")
    print(f"  크리틱 토큰 비중        : {d['critic_share_pct']}%   → C(크리틱 축소) 효과 상한")
    print(f"  상주 규칙문서           : {d['resident_overhead_tokens']:,}토큰/턴 "
          f"(누적 추정 {d['resident_overhead_cumulative']:,}) → D(SKILL.md 다이어트) 효과 상한")
    print(f"  산출물 재소비 추정      : {d['estimated_reconsumption_tokens']:,}토큰")
    print()


def print_summary_table(rows):
    if not rows:
        print("측정할 태스크가 없습니다.")
        return
    header = f"{'task':<36} {'RT':>4} {'분':>5} {'워커tok':>9} {'크리틱tok':>10} {'코드%':>7} {'크리틱%':>8}"
    print("\n=== 전체 태스크 베이스라인 ===")
    print(header)
    print("-" * len(header))
    for m in rows:
        print(f"{m['task'][:36]:<36} "
              f"{m['roundtrips']['log_entries']:>4} "
              f"{m['wallclock']['elapsed_min']:>5} "
              f"{m['artifact_tokens']['worker_result']:>9,} "
              f"{m['artifact_tokens']['critic_report']:>10,} "
              f"{m['derived']['worker_code_share_pct']:>6.1f}% "
              f"{m['derived']['critic_share_pct']:>7.1f}%")

    total_w = sum(m["artifact_tokens"]["worker_result"] for m in rows)
    total_c = sum(m["artifact_tokens"]["critic_report"] for m in rows)
    total_rt = sum(m["roundtrips"]["log_entries"] for m in rows)
    print("-" * len(header))
    print(f"{'TOTAL':<36} {total_rt:>4} {'':>5} {total_w:>9,} {total_c:>10,}")
    print()


def main():
    args = [a for a in sys.argv[1:]]
    dry = "--dry" in args
    args = [a for a in args if not a.startswith("--") or a == "--all"]

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tasks_dir = os.path.join(base_dir, "tasks")

    if not args:
        print("Usage: python collect_metrics.py <task-name> [--dry]")
        print("       python collect_metrics.py --all [--dry]")
        sys.exit(1)

    if args[0] == "--all":
        names = sorted(d for d in os.listdir(tasks_dir)
                       if os.path.isdir(os.path.join(tasks_dir, d)))
        rows = []
        for name in names:
            try:
                m = build_metrics(base_dir, name)
            except Exception as e:
                print(f"[Warning] '{name}' 측정 실패: {e}")
                continue
            rows.append(m)
            if not dry:
                persist(base_dir, name, m)
        print_summary_table(rows)
        return

    task_name = args[0]
    metrics = build_metrics(base_dir, task_name)
    print_report(metrics)
    if not dry:
        path = persist(base_dir, task_name, metrics)
        print(f"[Saved] {path} 의 metrics 필드에 기록했고 log.md에 [METRICS] 항목을 추가했습니다.")
    else:
        print("[Dry Run] 파일에 기록하지 않았습니다.")


if __name__ == "__main__":
    main()
