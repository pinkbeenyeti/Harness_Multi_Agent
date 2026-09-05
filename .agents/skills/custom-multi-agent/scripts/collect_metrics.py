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
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 토큰 근사와 분량 규칙은 common_utils가 단일 진실 원천이다.
# 계측(여기)과 검증(validate_result.py)이 서로 다른 계수를 쓰면
# "통과했는데 계측에선 초과"같은 모순이 생긴다.
from common_utils import (
    parse_log_line,
    evaluate_limits,
    load_validate_rules,
    approx_tokens,
    is_hangul,
    TOKENS_PER_HANGUL_CHAR,
    TOKENS_PER_ASCII_CHAR,
    archive_stale_log_entries,
    parse_workers_approved,
)

FENCE_RE = re.compile(r'^\s*```')

# 교정 루프 상한: 최초 1회 + 교정 3회. 이를 넘으면 워커를 계속 돌리지 말고
# 사용자에게 에스컬레이션해야 한다(실측 최악 사례: 워커 호출 24회 / 라운드트립 72회).
MAX_WORKER_CALLS = 4


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
    if name.startswith("draft"):
        # 문서 산출 태스크에서 워커가 쓰는 본문 초안. 이 태스크 유형에서는
        # 이것이 실질 납품물이자 최대 토큰 소비원이다.
        return "draft"
    if name in ("task.md", "context.md", "log.md") or name.startswith("design_spec") or name.startswith("change_spec"):
        return "orchestrator"
    return "other"


# artifact_tokens 합계에 포함되는 버킷.
# 예전에는 명명 규칙에 맞는 4종만 더해서, draft_*.md 같은 파일이 통째로
# 투명해졌다(실측 1건에서 총계를 3.2배 과소보고). 이제 모든 버킷을 더한다.
ARTIFACT_BUCKETS = ("brief", "worker", "critic", "draft", "orchestrator", "other")


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
ISO_LOG_RE = re.compile(
    r"^(\S+)\s+\[([A-Z_]+)\]\s*(.*)$")

def normalize_datetime(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def parse_log(log_path):
    entries = []
    if not os.path.exists(log_path):
        return entries
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            raw = line.rstrip("\n")
            iso = ISO_LOG_RE.match(raw)
            if iso:
                ts = normalize_datetime(iso.group(1))
                if ts and iso.group(2) != "METRICS":
                    entries.append({"ts": ts, "tag": iso.group(2),
                                    "text": iso.group(3), "has_time": True})
                continue
            parsed = parse_log_line(raw)
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
            entries.append({"ts": normalize_datetime(ts), "tag": tag,
                            "text": text, "has_time": bool(hhmm)})
    return entries


def analyze_log(entries):
    if not entries:
        return {
            "entries": 0, "tags": {}, "elapsed_min": 0, "elapsed_sec": 0.0,
            "time_by_tag_min": {}, "out_of_order": 0, "no_time_entries": 0,
            "timed_entries": 0,
        }

    tags = {}
    for e in entries:
        tags[e["tag"]] = tags.get(e["tag"], 0) + 1

    # 시각이 없는 항목(날짜만 기록)은 소요시간 집계에서 제외한다.
    timed = [e for e in entries if e["has_time"]]
    elapsed_sec = 0.0
    if len(timed) >= 2:
        ts = [e["ts"] for e in timed]
        elapsed_sec = (max(ts) - min(ts)).total_seconds()
    elapsed_min = int(elapsed_sec // 60)

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
        "elapsed_sec": elapsed_sec,
        "time_by_tag_min": {k: round(v, 1) for k, v in sorted(time_by_tag.items())},
        "out_of_order": out_of_order,
        "no_time_entries": len(entries) - len(timed),
        "timed_entries": len(timed),
    }


def union_duration(intervals):
    intervals = sorted((a, b) for a, b in intervals if a and b and b >= a)
    total, end = 0.0, None
    for start, stop in intervals:
        if end is None or start > end:
            total += (stop - start).total_seconds()
            end = stop
        elif stop > end:
            total += (stop - end).total_seconds()
            end = stop
    return round(total, 3)


def analyze_wait_intervals(entries):
    active, intervals, by_reason = {}, [], {}
    for entry in sorted(entries, key=lambda e: e["ts"]):
        match = re.search(r"reason=([\w-]+)", entry["text"])
        reason = match.group(1) if match else "unspecified"
        if entry["tag"] in ("WAIT_START", "EXCEPTION_GATE"):
            active[reason] = entry["ts"]
        elif entry["tag"] == "WAIT_END" and reason in active:
            interval = (active.pop(reason), entry["ts"])
            intervals.append(interval)
            by_reason.setdefault(reason, []).append(interval)
    return {
        "waiting_wallclock_sec": union_duration(intervals),
        "waiting_by_reason_sec": {k: union_duration(v) for k, v in by_reason.items()},
    }


def analyze_worker_runs(cost_data):
    runs = cost_data.get("worker_runs")
    if not isinstance(runs, list):
        return {
            "runs": None, "measurement_source": "legacy_log_estimate",
            "execution_wallclock_sec": None,
            "worker_process_sum_sec": None,
        }
    intervals, process_sum = [], 0.0
    for run in runs:
        start = normalize_datetime(run.get("started_at"))
        end = normalize_datetime(run.get("ended_at"))
        if start and end:
            intervals.append((start, end))
        process_sum += float(run.get("execution_wallclock_sec") or 0)
    return {
        "runs": runs, "measurement_source": "worker_runs_v2",
        "execution_wallclock_sec": union_duration(intervals),
        "worker_process_sum_sec": round(process_sum, 3),
    }


def pipeline_violations(runs):
    errors, groups = [], {}
    for run in runs:
        if run.get("stage") not in ("plan", "plan_critique") and run.get("outcome") != "running":
            groups.setdefault(run.get("group", "default"), []).append(run)
    for group, items in groups.items():
        expected = ("implement", 1)
        for run in items:
            actual = (run.get("stage"), int(run.get("attempt", 1)))
            if expected is None:
                errors.append(f"POST_TERMINAL_CALL: {group}")
                break
            if actual != expected:
                errors.append(f"PIPELINE_ORDER: {group} expected {expected}, got {actual}")
                break
            if run.get("outcome") != "success":
                expected = ("implement", 2) if actual[1] == 1 else None
            elif actual[0] == "implement":
                expected = ("critic", actual[1])
            elif run.get("verdict") == "PASS":
                expected = None
            elif actual[1] == 1:
                expected = ("implement", 2)
            else:
                errors.append(f"CRITIC_FAILED_TWICE: {group}")
                expected = None
    return errors


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
def check_brief_limits(task_dir):
    """브리프 분량 상한 위반을 적발한다. 상한은 있었으나 강제되지 않아
    실측에서 브리프가 워커 결과물의 2.7배까지 부푼 사례가 있었다."""
    rules = load_validate_rules()
    offenders = []
    for path in sorted(glob.glob(os.path.join(task_dir, "brief*.md"))):
        try:
            r = evaluate_limits(path, rules)
        except Exception:
            continue
        offenders.extend(r["violations"])
    return offenders


def check_worker_coverage(task_dir, cost_data):
    """task.md의 workers_approved에는 있으나 cost_tracker.json 어디에도
    성공 호출 기록이 없는 역할을 적발한다. verify_worker_approval()은 '지금 이
    호출이 승인됐는가'만 검사하는 편도 체크라서, 승인만 받고 끝내 한 번도
    부르지 않은 워커(예: 계획비평용 critic-standard)를 잡아내지 못한다
    (2026-09 audio-infrastructure 태스크 실측: critic-standard가 승인됐지만
    cli_quota.history/worker_runs 어디에도 등장하지 않았다).

    같은 역할이 서로 다른 stage(plan_critique/critic)로 각각 승인된 경우, 승인
    항목에 stage가 있으면 (role, stage) 단위로 커버리지를 추적한다 — 그래야
    "critic-architecture가 구조비평은 했지만 계획비평은 건너뛴" 것처럼 같은
    역할이 한쪽 stage만 실행된 경우도 잡아낸다. stage가 없는(레거시) 승인
    항목은 기존처럼 role 단위(어느 stage든 한 번이라도 실행되면 충족)로 취급한다."""
    approvals = parse_workers_approved(os.path.join(task_dir, "task.md"))
    if not approvals:
        return [], []

    latest_by_key = {}
    for a in approvals:
        role = a.get("worker")
        if not role:
            continue
        key = (role, a["stage"]) if a.get("stage") else role
        latest_by_key[key] = a  # 뒤에 나온 승인이 최신본

    executed_pairs = set()  # {(role, stage_or_None), ...}
    for item in cost_data.get("cli_quota", {}).get("history", []) or []:
        if item.get("exit_code") == 0 and item.get("role"):
            executed_pairs.add((item["role"], item.get("stage")))
    for item in cost_data.get("usd_cost", {}).get("history", []) or []:
        if item.get("role"):
            executed_pairs.add((item["role"], item.get("stage")))
    for item in cost_data.get("worker_runs", []) or []:
        if item.get("outcome") == "success" and item.get("role"):
            executed_pairs.add((item["role"], item.get("stage")))
    executed_roles_any_stage = {r for r, _ in executed_pairs}

    never_called = []
    for key in latest_by_key:
        if isinstance(key, tuple):
            covered = key in executed_pairs
        else:
            covered = key in executed_roles_any_stage
        if not covered:
            never_called.append(key)
    never_called.sort(key=lambda k: k if isinstance(k, tuple) else (k, ""))

    violations = []
    for key in never_called:
        a = latest_by_key[key]
        role, stage = key if isinstance(key, tuple) else (key, None)
        stage_desc = f"(stage={stage}) " if stage else ""
        violations.append(
            f"APPROVED_WORKER_NOT_CALLED: 승인된 워커 '{role}' {stage_desc}"
            f"({a.get('cli_or_provider', '?')}/{a.get('model', '?')})가 "
            f"승인({a.get('approved_at', '?')}) 이후 한 번도 성공 호출되지 않았습니다. "
            "승인만 받고 실행을 누락한 것인지, 더는 필요 없어 취소된 것인지 확인하십시오.")
    never_called_roles = sorted({(k[0] if isinstance(k, tuple) else k) for k in never_called})
    return violations, never_called_roles


def check_compliance(tags, groups=1, brief_violations=None, brief_tok=0,
                     worker_tok=0, worker_runs=None, task_dir=None, cost_data=None):
    """
    groups: 병렬 실행 그룹 수(= result*.md 개수). 워커를 파일 그룹별로 병렬
    기동하면 그룹 수만큼 호출이 늘어나는 것이 정상이므로, 교정 루프 상한도
    그룹 수에 비례해 확장한다. 상한이 통제하는 것은 '호출 총량'이 아니라
    '한 그룹을 몇 번이나 다시 고쳤는가'다.
    """
    groups = max(1, groups)
    call_limit = MAX_WORKER_CALLS * groups

    worker_calls = tags.get("WORKER_CALL", 0)
    verifications = tags.get("VERIFICATION", 0)
    approvals = tags.get("APPROVAL", 0)

    violations = []
    if worker_tok > 0 and brief_tok / worker_tok > 2.0:
        violations.append(
            f"BRIEF_RESULT_RATIO_EXCEEDED: 브리프({brief_tok}토큰)가 워커 결과물({worker_tok}토큰)의 "
            f"{brief_tok/worker_tok:.1f}배입니다. 브리프에 배경 설명·이전 라운드 요약이 섞여있지 않은지 확인하십시오.")
    if worker_calls > 0 and verifications == 0:
        violations.append(
            "CRITIC_MISSING: 워커가 실행됐으나 비평 워커 검증 기록이 0건입니다. "
            "비평 워커는 어떤 Tier에서도 생략할 수 없습니다.")
    if worker_calls > 0 and approvals == 0:
        violations.append(
            "APPROVAL_UNLOGGED: 워커가 실행됐으나 사용자 승인 기록이 0건입니다. "
            "승인 즉시 log.md에 [APPROVAL]을 남겨야 감사 추적이 됩니다.")

    state_errors = (pipeline_violations(worker_runs)
                    if worker_runs is not None else [])
    violations.extend(state_errors)
    legacy_exceeded = worker_runs is None and worker_calls > call_limit
    if legacy_exceeded:
        detail = f"{MAX_WORKER_CALLS}회 × {groups}그룹" if groups > 1 else f"{MAX_WORKER_CALLS}회 = 최초 1 + 교정 3"
        violations.append(
            f"CORRECTION_LOOP_EXCEEDED: 워커 호출 {worker_calls}회로 상한"
            f"({call_limit}회, {detail})을 넘었습니다. "
            "교정을 반복하지 말고 사용자에게 에스컬레이션했어야 합니다.")

    brief_violations = brief_violations or []
    if brief_violations:
        head = "; ".join(brief_violations[:3])
        more = f" (외 {len(brief_violations) - 3}건)" if len(brief_violations) > 3 else ""
        violations.append(
            f"BRIEF_OVER_LIMIT: 브리프 {len(brief_violations)}건이 분량 상한을 초과했습니다 — {head}{more}. "
            "브리프는 경로·라인 범위·지시만 담습니다.")

    never_called = []
    if task_dir is not None and cost_data is not None:
        coverage_violations, never_called = check_worker_coverage(task_dir, cost_data)
        violations.extend(coverage_violations)

    return {
        "ok": not violations,
        "violations": violations,
        "parallel_groups": groups,
        "worker_call_limit": call_limit,
        "critic_missing": worker_calls > 0 and verifications == 0,
        "approval_unlogged": worker_calls > 0 and approvals == 0,
        "correction_loop_exceeded": legacy_exceeded or bool(state_errors),
        "brief_over_limit": bool(brief_violations),
        "brief_result_ratio_exceeded": worker_tok > 0 and brief_tok / worker_tok > 2.0,
        "approved_worker_not_called": bool(never_called),
        "approved_workers_never_called": never_called,
    }


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def build_metrics(base_dir, task_name):
    task_dir = os.path.join(base_dir, "tasks", task_name)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"Task '{task_name}' not found at {task_dir}")

    artifacts = collect_artifacts(task_dir)
    log_entries = parse_log(os.path.join(task_dir, "log.md"))
    log_stats = analyze_log(log_entries)
    overhead = measure_fixed_overhead(base_dir)

    try:
        with open(os.path.join(task_dir, "cost_tracker.json"), encoding="utf-8") as f:
            cost_data = json.load(f)
    except (OSError, ValueError):
        cost_data = {}
    run_stats = analyze_worker_runs(cost_data)
    wait_stats = analyze_wait_intervals(log_entries)

    def bucket_tok(name):
        return artifacts.get(name, {}).get("total_tokens", 0)

    worker_tok = bucket_tok("worker")
    critic_tok = bucket_tok("critic")
    brief_tok = bucket_tok("brief")
    draft_tok = bucket_tok("draft")
    orch_tok = bucket_tok("orchestrator")
    other_tok = bucket_tok("other")
    artifact_tok = sum(bucket_tok(b) for b in ARTIFACT_BUCKETS)

    worker_code = artifacts.get("worker", {}).get("code_tokens", 0)

    # 병렬 실행 그룹 수 = 워커가 만든 result*.md 개수 (없으면 1로 본다)
    parallel_groups = max(1, len(artifacts.get("worker", {}).get("files", [])))

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
            "elapsed_wallclock_sec": log_stats["elapsed_sec"],
            "execution_wallclock_sec": run_stats["execution_wallclock_sec"],
            "worker_process_sum_sec": run_stats["worker_process_sum_sec"],
            "waiting_wallclock_sec": wait_stats["waiting_wallclock_sec"],
            "waiting_by_reason_sec": wait_stats["waiting_by_reason_sec"],
            "orchestration_overhead_sec": (
                None if run_stats["execution_wallclock_sec"] is None else
                max(0.0, round(log_stats["elapsed_sec"]
                    - run_stats["execution_wallclock_sec"]
                    - wait_stats["waiting_wallclock_sec"], 3))),
            "time_by_tag_min": log_stats["time_by_tag_min"],
            "out_of_order_entries": log_stats["out_of_order"],
            "entries_without_time": log_stats["no_time_entries"],
            "measurement_source": run_stats["measurement_source"],
        },
        "artifact_tokens": {
            "brief": brief_tok,
            "worker_result": worker_tok,
            "critic_report": critic_tok,
            "draft": draft_tok,
            "orchestrator_docs": orch_tok,
            "other": other_tok,
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
        "compliance": check_compliance(tags, groups=parallel_groups,
                                       brief_violations=check_brief_limits(task_dir),
                                       brief_tok=brief_tok, worker_tok=worker_tok,
                                       worker_runs=run_stats["runs"],
                                       task_dir=task_dir, cost_data=cost_data),
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
        "execution_wallclock_sec": metrics["wallclock"]["execution_wallclock_sec"],
        "waiting_wallclock_sec": metrics["wallclock"]["waiting_wallclock_sec"],
        "orchestration_overhead_sec": metrics["wallclock"]["orchestration_overhead_sec"],
        "measurement_source": metrics["wallclock"]["measurement_source"],
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
                       ("크리틱 report", "critic_report"), ("초안 draft", "draft"),
                       ("오케스트레이터 문서", "orchestrator_docs"), ("기타", "other")):
        val = a.get(key, 0)
        if val or key in ("brief", "worker_result", "critic_report"):
            print(f"  {label:<20}: {val:>8,}")
    print(f"  {'합계':<20}: {a['total']:>8,}")
    if a.get("draft"):
        print(f"  → 초안이 전체의 {a['draft'] / a['total'] * 100:.1f}%. 문서 산출형 태스크는 diff 핸드오프가 통하지 않는다.")

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

    from common_utils import validate_task_name
    if args[0] != "--all":
        try:
            validate_task_name(args[0])
        except ValueError as e:
            print(f"[SECURITY] {e}")
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
                if c.get("brief_over_limit"):
                    codes.append("브리프초과")
                if c.get("brief_result_ratio_exceeded"):
                    codes.append("브리프비율초과")
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

        task_dir = os.path.join(base_dir, "tasks", task_name)
        task_md_path = os.path.join(task_dir, "task.md")
        if os.path.exists(task_md_path):
            with open(task_md_path, "r", encoding="utf-8", errors="replace") as f:
                task_md_content = f.read()
            if "**Status**: done" in task_md_content:
                archive_result = archive_stale_log_entries(os.path.join(task_dir, "log.md"))
                if archive_result["archived_rounds"] > 0:
                    print(f"[Archive] {archive_result['archived_rounds']}개 라운드를 log_archive.md로 이동.")
    else:
        print("[Dry Run] 파일에 기록하지 않았습니다.")

    if strict and not metrics["compliance"]["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
