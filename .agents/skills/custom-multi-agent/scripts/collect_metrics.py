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

# 교정 루프 상한: 최초 1회 + 교정 3회. 이를 넘으면 워커를 계속 돌리지 말고
# 사용자에게 에스컬레이션해야 한다(실측 최악 사례: 워커 호출 24회 / 라운드트립 72회).
MAX_WORKER_CALLS = 4


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


METRICS_LINE_RE = re.compile(r'\[METRICS\]')


def measure_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # log.md는 이 스크립트가 [METRICS] 줄을 덧붙이는 대상이기도 하다. 그대로 재면
    # 측정할 때마다 log.md가 커져 다음 측정값이 달라진다(자기 참조). 계측이 남긴
    # 줄은 측정 대상에서 뺀다.
    if os.path.basename(path).lower() == "log.md":
        content = "".join(l for l in content.splitlines(keepends=True)
                          if not METRICS_LINE_RE.search(l))

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
            # [METRICS]는 이 스크립트 자신이 남긴 계측 기록이다. 세면 측정이
            # 자기 출력을 다시 측정하게 되어 재실행할 때마다 수치가 늘어난다.
            if tag == "METRICS":
                continue
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
# 규칙 준수 검사
#
# 규칙을 SKILL.md에만 적어두면 지켜지지 않는다는 것이 실측으로 확인됐다
# (35개 태스크 중 17개가 크리틱 검증 기록 0건, 21개가 승인 기록 0건).
# 여기서 로그를 근거로 위반을 적발한다.
# ---------------------------------------------------------------------------
def check_compliance(tags):
    worker_calls = tags.get("WORKER_CALL", 0)
    verifications = tags.get("VERIFICATION", 0)
    approvals = tags.get("APPROVAL", 0)

    violations = []
    if worker_calls > 0 and verifications == 0:
        violations.append(
            "CRITIC_MISSING: 워커가 실행됐으나 비평 워커 검증 기록이 0건입니다. "
            "비평 워커는 어떤 Tier에서도 생략할 수 없습니다.")
    if worker_calls > 0 and approvals == 0:
        violations.append(
            "APPROVAL_UNLOGGED: 워커가 실행됐으나 사용자 승인 기록이 0건입니다. "
            "승인 즉시 log.md에 [APPROVAL]을 남겨야 감사 추적이 됩니다.")
    if worker_calls > MAX_WORKER_CALLS:
        violations.append(
            f"CORRECTION_LOOP_EXCEEDED: 워커 호출 {worker_calls}회로 상한"
            f"({MAX_WORKER_CALLS}회 = 최초 1 + 교정 3)을 넘었습니다. "
            "교정을 반복하지 말고 사용자에게 에스컬레이션했어야 합니다.")

    return {
        "ok": not violations,
        "violations": violations,
        "critic_missing": worker_calls > 0 and verifications == 0,
        "approval_unlogged": worker_calls > 0 and approvals == 0,
        "correction_loop_exceeded": worker_calls > MAX_WORKER_CALLS,
    }


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
        "compliance": check_compliance(tags),
        "fixed_overhead": overhead,
        "artifacts": {k: {"bytes": v["bytes"], "total_tokens": v["total_tokens"],
                          "code_tokens": v["code_tokens"], "prose_tokens": v["prose_tokens"],
                          "files": [f["file"] for f in v["files"]]}
                      for k, v in sorted(artifacts.items())},
    }


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------
def compact_snapshot(metrics):
    """
    metrics_history에 쌓을 축약본. 전문을 매번 쌓으면 파일이 비대해지므로
    시계열 비교에 필요한 수치만 남긴다.
    """
    d = metrics["derived"]
    return {
        "measured_at": metrics["measured_at"],
        "log_entries": metrics["roundtrips"]["log_entries"],
        "worker_calls": metrics["roundtrips"]["worker_calls"],
        "verifications": metrics["roundtrips"]["verifications"],
        "user_approvals": metrics["roundtrips"]["user_approvals"],
        "elapsed_min": metrics["wallclock"]["elapsed_min"],
        "artifact_tokens": dict(metrics["artifact_tokens"]),
        "worker_code_share_pct": d["worker_code_share_pct"],
        "critic_share_pct": d["critic_share_pct"],
        "resident_overhead_tokens": d["resident_overhead_tokens"],
        "compliance_violations": len(metrics["compliance"]["violations"]),
    }


def _same_measurement(a, b):
    """측정 시각만 다르고 수치가 동일하면 같은 측정으로 본다."""
    if not a or not b:
        return False
    ka = {k: v for k, v in a.items() if k != "measured_at"}
    kb = {k: v for k, v in b.items() if k != "measured_at"}
    return ka == kb


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

    # metrics는 최신본으로 덮어쓰되, 축약 이력은 append-only로 누적한다.
    # 덮어쓰기만 하면 개선 전/후 비교의 기준선이 재측정 시 사라진다.
    hist = data.get("metrics_history")
    if not isinstance(hist, list):
        hist = []
    snap = compact_snapshot(metrics)
    changed = not _same_measurement(hist[-1] if hist else None, snap)
    if changed:
        hist.append(snap)
    data["metrics_history"] = hist
    data["metrics"] = metrics

    with open(tracker_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # 수치가 그대로면 로그에 같은 줄을 또 남기지 않는다(재실행 시 로그 오염 방지).
    log_path = os.path.join(task_dir, "log.md")
    if changed and os.path.exists(log_path):
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

    return tracker_path, hist


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

    c = m["compliance"]
    print("\n[규칙 준수]")
    if c["ok"]:
        print("  ✅ 위반 없음")
    else:
        for v in c["violations"]:
            print(f"  ❌ {v}")
    print()


def print_delta(history):
    """최초 측정(베이스라인) 대비 최신 측정의 변화를 출력한다."""
    if len(history) < 2:
        return
    base, cur = history[0], history[-1]
    print(f"[베이스라인 대비] {base['measured_at']} → {cur['measured_at']}")

    def row(label, key, unit="", pct=False):
        b, c = base.get(key, 0), cur.get(key, 0)
        if b == c:
            return
        if pct:
            print(f"  {label:<22}: {b}{unit} → {c}{unit} ({c - b:+.1f}p)")
        else:
            delta_pct = ((c - b) / b * 100) if b else 0.0
            print(f"  {label:<22}: {b:,}{unit} → {c:,}{unit} ({delta_pct:+.1f}%)")

    row("라운드트립", "log_entries", "회")
    row("경과", "elapsed_min", "분")
    b_art, c_art = base.get("artifact_tokens", {}), cur.get("artifact_tokens", {})
    for label, key in (("워커 산출물", "worker_result"), ("크리틱 산출물", "critic_report"), ("산출물 합계", "total")):
        b, c = b_art.get(key, 0), c_art.get(key, 0)
        if b != c:
            print(f"  {label:<22}: {b:,} → {c:,} ({((c - b) / b * 100) if b else 0:+.1f}%)")
    row("코드블록 비중", "worker_code_share_pct", "%", pct=True)
    row("크리틱 비중", "critic_share_pct", "%", pct=True)
    row("상주 규칙문서", "resident_overhead_tokens", "토큰")
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
    raw = sys.argv[1:]
    dry = "--dry" in raw
    strict = "--strict" in raw
    args = [a for a in raw if not a.startswith("--") or a == "--all"]

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tasks_dir = os.path.join(base_dir, "tasks")

    if not args:
        print("Usage: python collect_metrics.py <task-name> [--dry] [--strict]")
        print("       python collect_metrics.py --all [--dry] [--strict]")
        print("  --dry    : 파일에 기록하지 않고 출력만 한다")
        print("  --strict : 규칙 위반이 있으면 exit code 1로 종료한다 (태스크 완료 시 권장)")
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

        offenders = [m for m in rows if not m["compliance"]["ok"]]
        if offenders:
            print(f"[규칙 준수] 위반 태스크 {len(offenders)}/{len(rows)}개")
            for m in offenders:
                codes = []
                c = m["compliance"]
                if c["critic_missing"]:
                    codes.append("크리틱누락")
                if c["approval_unlogged"]:
                    codes.append("승인미기록")
                if c["correction_loop_exceeded"]:
                    codes.append("교정루프초과")
                print(f"  ❌ {m['task'][:40]:<40} {', '.join(codes)}")
            print()
        if strict and offenders:
            sys.exit(1)
        return

    task_name = args[0]
    metrics = build_metrics(base_dir, task_name)
    print_report(metrics)
    if not dry:
        path, hist = persist(base_dir, task_name, metrics)
        print_delta(hist)
        print(f"[Saved] {path} — metrics 갱신, metrics_history {len(hist)}건 누적, log.md에 [METRICS] 추가.")
    else:
        print("[Dry Run] 파일에 기록하지 않았습니다.")

    if strict and not metrics["compliance"]["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
