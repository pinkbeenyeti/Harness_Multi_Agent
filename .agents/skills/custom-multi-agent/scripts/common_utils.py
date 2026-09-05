import os
import json
import re
import subprocess

# ---------------------------------------------------------------------------
# log.md 항목 파서 (공용)
#
# 실사용 중 로그 포맷이 두 갈래로 드리프트했다. monitor_task.py와
# collect_metrics.py가 각자 정규식을 들고 있으면 한쪽만 조용히 못 읽는 사고가
# 반복되므로(실제로 발생함) 파서를 여기 한 곳에 둔다.
#   구형(템플릿): 2026-08-06 10:43 [DECISION] 내용
#   신형(실사용): - `[FACT]` 2026-08-08 19:05 — 내용   (시각 생략 가능)
# ---------------------------------------------------------------------------
LOG_RE_DATE_FIRST = re.compile(
    r'^\s*[-*]?\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?\s+`?\[([A-Z_]+)\]`?\s*(.*)$')
LOG_RE_TAG_FIRST = re.compile(
    r'^\s*[-*]?\s*`?\[([A-Z_]+)\]`?\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?\s*[—\-]?\s*(.*)$')

# 실사용 중 생긴 태그 변종을 표준 태그로 흡수한다.
LOG_TAG_ALIASES = {
    "WORKER": "WORKER_CALL",
    "WORKER_START": "WORKER_CALL",
    "GATE": "APPROVAL",
    "FACT": "DECISION",
}


def parse_log_line(line):
    """
    log.md 한 줄을 (tag, date, time_or_None, text)로 해석한다.
    로그 항목이 아니면(헤더, 들여쓴 부속 설명 등) None을 반환한다.
    """
    m = LOG_RE_DATE_FIRST.match(line)
    if m:
        date, hhmm, tag, text = m.group(1), m.group(2), m.group(3), m.group(4)
    else:
        m = LOG_RE_TAG_FIRST.match(line)
        if not m:
            return None
        tag, date, hhmm, text = m.group(1), m.group(2), m.group(3), m.group(4)

    if tag == "TAG":  # 템플릿 안내문의 예시 줄
        return None
    return LOG_TAG_ALIASES.get(tag, tag), date, hhmm, text


# ---------------------------------------------------------------------------
# 분량 한도 검사 (공용)
#
# validate_result.py가 사후 검증에, call_worker.py가 워커 기동 전 사전 검증에,
# collect_metrics.py가 준수 검사에 같은 규칙을 써야 한다. 각자 세면 규칙이
# 어긋나므로 한 곳에 둔다.
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r'^\s*```')

# 토큰 근사 계수. 실제 토크나이저를 부르지 않고 파일 내용만으로 추정한다.
# 한글은 글자당 1토큰을 넘는 경우가 흔하고, 코드/영문은 대략 4글자당 1토큰이다.
TOKENS_PER_HANGUL_CHAR = 1.15
TOKENS_PER_ASCII_CHAR = 0.27


def is_hangul(ch):
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def approx_tokens(text):
    """한글/비한글을 구분해 토큰 수를 근사한다."""
    hangul = sum(1 for ch in text if is_hangul(ch))
    other = len(text) - hangul
    return int(hangul * TOKENS_PER_HANGUL_CHAR + other * TOKENS_PER_ASCII_CHAR)


def count_korean_characters(text):
    return len(re.findall(r'[ㄱ-ㅎㅏ-ㅣ가-힣]', text))


def count_english_words(text):
    return len(re.findall(r'\b[a-zA-Z]+\b', text))


def strip_code_blocks(text):
    """마크다운 코드펜스 내부를 제거한다."""
    out = []
    in_code = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_code = not in_code
            continue
        if not in_code:
            out.append(line)
    return "".join(out)


def load_validate_rules():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules_path = os.path.join(base_dir, "validate_rules.json")
    if not os.path.exists(rules_path):
        return {}
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            return json.load(f).get("rules", {})
    except Exception:
        return {}


def resolve_rule_key(filename, rules):
    """
    result_core.md, brief_fix_steps.md 처럼 접미사가 붙은 파일도 같은 규칙을
    적용받도록 접두사 매칭을 지원한다. 가장 긴(구체적인) 키를 우선한다.
    """
    if filename in rules:
        return filename
    stem = filename[:-3] if filename.endswith(".md") else filename
    best = None
    for key in rules:
        key_stem = key[:-3] if key.endswith(".md") else key
        if stem.startswith(key_stem) and (best is None or len(key_stem) > len(best[1])):
            best = (key, key_stem)
    return best[0] if best else None


def archive_stale_log_entries(log_path, keep_recent_rounds=2):
    """
    log.md가 계속 커지는 것을 막기 위해, WORKER_CALL 기준 라운드 중 최근
    keep_recent_rounds개를 제외한 나머지를 log_archive.md로 옮기고 원본에는
    요약 한 줄만 남긴다. parse_log_line/정규식/태그 별칭은 재사용만 한다.
    """
    if not os.path.exists(log_path):
        return {"archived_rounds": 0}

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    preamble = []       # 첫 WORKER_CALL 이전: 항상 보존
    rounds = []          # [{"entries": [{"date": str, "raw": [line, ...]}, ...]}]
    cur_entries = None   # 현재 라운드의 entries. None이면 아직 라운드 시작 전

    for line in lines:
        parsed = parse_log_line(line.rstrip("\n"))
        if parsed is None:
            # 연속(들여쓰기) 줄은 직전 항목에 붙인다.
            if cur_entries:
                cur_entries[-1]["raw"].append(line)
            else:
                preamble.append(line)
            continue
        tag, date, hhmm, text = parsed
        if tag == "WORKER_CALL":
            cur_entries = []
            rounds.append({"entries": cur_entries})
        if cur_entries is None:
            preamble.append(line)
        else:
            cur_entries.append({"date": date, "raw": [line]})

    if len(rounds) <= keep_recent_rounds:
        return {"archived_rounds": 0}

    n_archive = len(rounds) - keep_recent_rounds
    archived, kept = rounds[:n_archive], rounds[n_archive:]

    archive_path = os.path.join(os.path.dirname(log_path), "log_archive.md")
    need_header = not os.path.exists(archive_path)
    with open(archive_path, "a", encoding="utf-8") as af:
        if need_header:
            af.write("# Archived Log Entries (Append-Only)\n\n")
        for rnd in archived:
            for e in rnd["entries"]:
                af.writelines(e["raw"])

    new_lines = list(preamble)
    for rnd in archived:
        dates = [e["date"] for e in rnd["entries"]]
        first_date, last_date = min(dates), max(dates)
        new_lines.append(
            f"[ARCHIVED] {first_date}~{last_date} 라운드 {len(rnd['entries'])}건 "
            f"— 상세는 log_archive.md 참조\n")
    for rnd in kept:
        for e in rnd["entries"]:
            new_lines.extend(e["raw"])

    with open(log_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return {"archived_rounds": n_archive}


def evaluate_limits(file_path, rules=None):
    """
    파일 하나의 분량 한도 위반을 평가한다.
    반환: {"rule_key", "violations": [문자열...], "korean", "english", "bytes"}
    규칙이 없으면 violations는 빈 리스트다.
    """
    rules = load_validate_rules() if rules is None else rules
    filename = os.path.basename(file_path)
    rule_key = resolve_rule_key(filename, rules)
    file_rules = rules.get(rule_key, {}) if rule_key else {}

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    total_bytes = len(content.encode("utf-8"))
    counted = strip_code_blocks(content) if file_rules.get("exclude_code_blocks") else content
    korean = count_korean_characters(counted)
    english = count_english_words(counted)

    tokens = approx_tokens(counted)

    violations = []
    lim_b = file_rules.get("limit_bytes")
    lim_t = file_rules.get("limit_tokens")
    lim_k = file_rules.get("limit_korean")
    lim_e = file_rules.get("limit_english")

    if lim_b is not None and total_bytes > lim_b:
        violations.append(f"{filename}: {total_bytes} bytes > 상한 {lim_b}")
    # limit_tokens가 주 통제 수단이다. 한글/영어를 따로 AND로 걸면 언어에 따라
    # 한쪽만 터지는 비대칭이 생긴다(영어로 쓴 브리프는 영단어 한도에서 즉사).
    # 언어와 무관한 단일 예산으로 통제한다.
    if lim_t is not None and tokens > lim_t:
        violations.append(f"{filename}: 근사 {tokens}토큰 > 상한 {lim_t}토큰")
    if lim_k is not None and korean > lim_k:
        violations.append(f"{filename}: 한글 {korean}자 > 상한 {lim_k}자")
    if lim_e is not None and english > lim_e:
        violations.append(f"{filename}: 영단어 {english}개 > 상한 {lim_e}개")

    return {
        "rule_key": rule_key,
        "violations": violations,
        "korean": korean,
        "english": english,
        "tokens": tokens,
        "bytes": total_bytes,
        "exclude_code_blocks": bool(file_rules.get("exclude_code_blocks")),
        "rules": file_rules,
    }


def _load_backends_json():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base_dir, "backends.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_backend_config(role, execution_mode="api-routed"):
    """
    backends.json에서 워커 역할(role)×execution_mode(api-routed/cli-routed/host-native)에
    대응하는 provider/model(또는 cli/model) 설정을 읽어온다. call_worker.py와
    knot_manager.py가 이 함수를 공유하여, 모델명이 각 스크립트에 개별적으로
    하드코딩되어 backends.json 설정과 어긋나는 드리프트를 방지한다.
    """
    config = _load_backends_json()
    role = resolve_legacy_role(role, config)
    roles = config.get("roles", {})
    if role not in roles:
        raise KeyError(f"Role '{role}' is not defined in backends.json")
    routes = roles[role].get("routes", {})
    if execution_mode not in routes:
        raise KeyError(f"Role '{role}' has no route for execution_mode '{execution_mode}'")
    return routes[execution_mode]


def resolve_legacy_role(role, config):
    """구 역할명(claude-main 등)을 backends.json의 legacy_role_aliases로 신 역할명에 매핑한다."""
    if role in config.get("roles", {}):
        return role
    return config.get("legacy_role_aliases", {}).get(role, role)


_LEGACY_MODE_MAP = {"multi-api": "api-routed", "gemini-only": "api-routed", "antigravity": "host-native"}


def resolve_execution_mode(cost_data):
    """
    cost_tracker.json의 execution_mode(신규 스키마) 또는 worker_mode(레거시)를 읽어
    (execution_mode, google_only, warn_message) 튜플로 반환한다. 레거시 파일 자체는
    수정하지 않고, 읽는 시점에만 인메모리로 별칭을 해석한다.
    """
    if "execution_mode" in cost_data:
        return cost_data["execution_mode"], False, None
    wm = cost_data.get("worker_mode", "multi-api")
    mode = _LEGACY_MODE_MAP.get(wm, "api-routed")
    warn = f"[WARN] worker_mode='{wm}' -> execution_mode='{mode}'(별칭, 원본 미변경)." if wm == "antigravity" else None
    return mode, wm == "gemini-only", warn


_TASK_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_task_name(task_name):
    """task_name으로 경로를 구성하기 전에 경로 이탈(.., 절대경로, 구분자 등)을 차단한다."""
    if not task_name or not _TASK_NAME_RE.match(task_name):
        raise ValueError(f"Invalid task_name '{task_name}': 영숫자/-/_ 만 허용")
    return task_name


def cli_allowlist_check(cli_name, argv):
    """
    cli-routed 서브프로세스 실행 전 argv를 backends.json의 cli_allowlist와 대조한다.
    자유 텍스트(프롬프트)를 argv에 추가하기 전에 호출해야 한다 — 그 뒤에 검사하면
    코드블록·특수문자가 포함된 정상 프롬프트까지 metachar 스캔에 걸려 차단된다.
    """
    entry = _load_backends_json().get("cli_allowlist", {}).get(cli_name)
    if not entry:
        raise ValueError(f"CLI '{cli_name}' not in cli_allowlist")
    if not isinstance(argv, list) or not argv or argv[0] != entry["binary"]:
        raise ValueError(f"argv[0] mismatch: {argv}")
    for tok in argv:
        if not isinstance(tok, str) or any(c in tok for c in ";&|`$><\n"):
            raise ValueError(f"shell metachar in argv: {tok!r}")
    return True


def load_api_keys():
    # 1. 스크립트 실행 위치 및 오케스트레이터의 작업 폴더(cwd) 기준 여러 경로에서 api_keys.json 로드 시도
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_paths = [
        os.path.join(base_dir, "api_keys.json"),                # 스킬 폴더 내부
        os.path.join(os.path.dirname(base_dir), "api_keys.json"), # 워크스페이스 루트 폴더
        os.path.join(os.getcwd(), "api_keys.json")              # 현재 작업 중인 경로
    ]
    
    for p in search_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    keys = json.load(f)
                for k, v in keys.items():
                    # 템플릿용 기본 플레이스홀더 값은 주입하지 않음
                    if v and not v.startswith("your-") and v != "your_actual_key_here":
                        os.environ[k] = v
                print(f"[API Key] Loaded configurations from '{p}'")
                return True
            except Exception as e:
                print(f"[Warning] Failed to load keys from '{p}': {e}")
    return False


def _run_git_cmd(args, cwd, timeout=5):
    """
    git subprocess 호출을 위한 공용 헬퍼 함수.
    cwd를 명시적으로 지정하여, 이 스킬이 다른 프로젝트에 설치되었을 때
    호출자의 현재 작업 폴더(호스트 프로젝트 저장소)가 아닌
    스킬 자신의 설치 경로를 대상으로만 git 명령이 실행되도록 강제한다.
    """
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout
    )


def check_for_updates():
    """
    깃허브 원격 저장소의 업데이트를 확인하고, 사용자 승인 시 자동 업데이트를 진행합니다.
    네트워크 문제, git 미설치 등으로 인한 모든 예외는 경고 메시지만 출력하고 무시됩니다.
    """
    # 스킬 자신의 설치 경로(scripts/의 상위 폴더)를 git 명령의 기준 디렉토리로 고정.
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        # 1. 로컬 변경사항(unstaged/staged) 확인 (충돌 예방 안전장치)
        status_res = _run_git_cmd(["status", "--porcelain"], skill_dir, timeout=3)
        if status_res.stdout.strip():
            print("\n⚠️ [Warning] 로컬 저장소에 커밋되지 않은 변경사항이 존재합니다.")
            print("안전한 업데이트를 위해 작업을 먼저 커밋하거나 stashing 후 다시 실행하십시오.")
            return

        # 2. git fetch origin 수행 (타임아웃 3초)
        _run_git_cmd(["fetch", "origin"], skill_dir, timeout=3)

        # 3. 로컬 및 원격 해시 구하기
        local_hash = _run_git_cmd(["rev-parse", "HEAD"], skill_dir, timeout=3).stdout.strip()

        remote_hash = None
        try:
            remote_hash = _run_git_cmd(["rev-parse", "@{u}"], skill_dir, timeout=3).stdout.strip()
        except subprocess.CalledProcessError:
            try:
                remote_hash = _run_git_cmd(["rev-parse", "origin/main"], skill_dir, timeout=3).stdout.strip()
            except subprocess.CalledProcessError:
                pass

        if not remote_hash:
            return

        # 4. 업데이트 여부 비교 및 y/n 대기 루프
        if local_hash != remote_hash:
            while True:
                user_input = input("\n💡 [Multi-Agent Update] 깃허브 원격 저장소에 새로운 업데이트가 존재합니다. 업데이트를 다운로드하여 로컬에 반영하시겠습니까? (y/n): ").strip().lower()
                if user_input in ["y", "yes"]:
                    # 현재 브랜치명 추출
                    try:
                        branch_name = _run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], skill_dir, timeout=3).stdout.strip()
                    except subprocess.CalledProcessError:
                        branch_name = "main"

                    # git pull 실행 (타임아웃 10초 설정으로 행 방지)
                    try:
                        print(f"Applying updates from origin/{branch_name}...")
                        _run_git_cmd(["pull", "origin", branch_name], skill_dir, timeout=10)
                        print("[Multi-Agent Update] 업데이트가 성공적으로 반영되었습니다. 변경사항을 적용하기 위해 프로그램을 재시작하십시오.")
                    except subprocess.CalledProcessError as pull_err:
                        print(f"\n⚠️ [Warning] git pull 과정에서 실패가 발생했습니다: {pull_err}")
                    break
                elif user_input in ["n", "no"]:
                    print("[Multi-Agent Update] 업데이트가 취소되었습니다.")
                    break
                else:
                    print("올바른 입력(y/n 또는 yes/no)을 입력해 주십시오.")

    except FileNotFoundError:
        print("\n⚠️ [Warning] 시스템에 'git' 프로그램이 설치되어 있지 않거나 경로(PATH)에 등록되어 있지 않아 업데이트 확인을 건너뜁니다.")
    except subprocess.TimeoutExpired:
        print("\n⚠️ [Warning] 업데이트 확인 중 네트워크 타임아웃이 발생하여 업데이트 검사를 건너뜁니다.")
    except Exception as e:
        print(f"\n⚠️ [Warning] 업데이트를 확인하는 도중 오류가 발생했습니다: {e}")


# ---------------------------------------------------------------------------
# 실행 전 승인 검증 및 이종 교차 비평 인터록 (Approval & Cross-Review Gates)
# ---------------------------------------------------------------------------

def normalize_actor_name(name):
    """CLI 또는 프로바이더 명칭을 정규화한다 (예: 'codex-cli' -> 'codex', 'agy-cli' -> 'agy')."""
    if not name:
        return ""
    n = name.lower().strip()
    if n.endswith("-cli"):
        n = n[:-4]
    return n


def parse_workers_approved(task_md_path):
    """
    task.md의 '* **workers_approved**:' 블록을 파싱하여 승인된 워커 목록을 반환한다.
    반환: [{'worker': ..., 'cli_or_provider': ..., 'model': ..., ...}, ...]
    """
    if not os.path.exists(task_md_path):
        return []
    with open(task_md_path, "r", encoding="utf-8") as f:
        content = f.read()

    m_block = re.search(r'\*\s*\*\*workers_approved\*\*:(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if not m_block:
        return []

    block_text = m_block.group(1)
    results = []
    current = {}
    for line in block_text.splitlines():
        m_w = re.match(r'^\s*-\s*worker:\s*([^\s#]+)', line)
        if m_w:
            if current and "worker" in current:
                results.append(current)
            current = {"worker": m_w.group(1).strip()}
            continue
        m_c = re.match(r'^\s*cli_or_provider:\s*([^\s#]+)', line)
        if m_c and current:
            current["cli_or_provider"] = m_c.group(1).strip()
            continue
        m_m = re.match(r'^\s*model:\s*([^\s#]+)', line)
        if m_m and current:
            current["model"] = m_m.group(1).strip()
            continue
        m_e = re.match(r'^\s*effort:\s*([^\s#]+)', line)
        if m_e and current:
            current["effort"] = m_e.group(1).strip()
            continue
        m_s = re.match(r'^\s*stage:\s*([^\s#]+)', line)
        if m_s and current:
            current["stage"] = m_s.group(1).strip()
            continue
        m_at = re.match(r'^\s*approved_at:\s*(.+?)\s*$', line)
        if m_at and current:
            current["approved_at"] = m_at.group(1).strip()
            continue
    if current and "worker" in current:
        results.append(current)
    return results


def get_model_family(name):
    """모델 또는 CLI/프로바이더 이름으로부터 모델 벤더 가문('openai', 'anthropic', 'google')을 판별한다."""
    if not name:
        return "unknown"
    n = name.lower()
    if any(k in n for k in ["codex", "openai", "gpt"]):
        return "openai"
    if any(k in n for k in ["claude", "anthropic", "sonnet", "haiku", "opus"]):
        return "anthropic"
    if any(k in n for k in ["agy", "google", "gemini"]):
        return "google"
    return n


def verify_worker_approval(task_dir, role, execution_mode, worker_cfg, stage=None):
    """
    task.md의 workers_approved에 기록된 최신 승인 정보와 실제 실행 대상(CLI/provider, model)이
    일치하는지 검증한다. 불일치 시 에러 문자열을 반환하고, 정상이면 None을 반환한다.

    같은 역할(예: critic-architecture)이 서로 다른 stage(plan_critique/critic)에
    각각 다르게 승인될 수 있어, 승인 항목에 stage가 명시된 경우 요청 stage와
    일치하는 항목만 후보로 삼는다. stage가 전혀 명시되지 않은(레거시) 승인 항목은
    모든 stage에 대해 유효한 것으로 취급해 기존 task.md와 호환된다.
    """
    task_md_path = os.path.join(task_dir, "task.md")
    if not os.path.exists(task_md_path):
        return None

    approvals = parse_workers_approved(task_md_path)
    role_approvals = [a for a in approvals if a.get("worker") == role]
    if not role_approvals:
        return f"역할 '{role}'에 대한 사용자 승인(workers_approved) 내역이 task.md에 없습니다."

    tagged = [a for a in role_approvals if a.get("stage")]
    if stage and tagged:
        matching = [a for a in tagged if a.get("stage") == stage]
        if not matching:
            return (f"역할 '{role}'의 stage '{stage}'에 대한 승인(workers_approved)이 "
                     "task.md에 없습니다(다른 stage로만 승인됨).")
        role_approvals = matching

    latest = role_approvals[-1]
    approved_cli_or_prov = normalize_actor_name(latest.get("cli_or_provider", ""))
    approved_model = latest.get("model", "").strip().lower()

    if execution_mode == "cli-routed":
        actual_cli_or_prov = normalize_actor_name(worker_cfg.get("cli", ""))
    elif execution_mode == "api-routed":
        actual_cli_or_prov = normalize_actor_name(worker_cfg.get("provider", ""))
    else:
        actual_cli_or_prov = "host-native"
    actual_model = worker_cfg.get("model", "").strip().lower()

    if approved_cli_or_prov and actual_cli_or_prov != approved_cli_or_prov:
        return (f"실행 CLI/프로바이더('{actual_cli_or_prov}')가 "
                f"task.md 승인 내역('{approved_cli_or_prov}')과 불일치합니다.")

    if approved_model and actual_model != approved_model:
        return (f"실행 대상 모델('{actual_model}')이 "
                f"task.md 승인 내역('{approved_model}')과 불일치합니다.")

    return None


def verify_cross_review_integrity(task_dir, role, execution_mode, worker_cfg, stage=None):
    """
    비평 역할('critic-*') 기동 시, 비평 대상과 동일한 모델 계열(OpenAI/Anthropic/Google)의
    비평 모델이 사용되지 않도록 이종 교차 비평(Cross-Review) 원칙을 강제한다.
    stage='plan_critique'(계획 비평)면 planner를, 그 외(구조 비평)는 implementer를
    비평 대상으로 삼는다 — 계획 비평 시점엔 implementer가 아직 실행 전이라 그 벤더와
    비교하는 것은 의미가 없다.
    """
    if not role.startswith("critic"):
        return None

    target_role = "planner" if stage == "plan_critique" else "implementer"

    critic_actor = worker_cfg.get("cli") if execution_mode == "cli-routed" else worker_cfg.get("provider", "")
    critic_model = worker_cfg.get("model", "")
    critic_family = get_model_family(critic_actor) or get_model_family(critic_model)

    target_family = None
    cost_tracker_path = os.path.join(task_dir, "cost_tracker.json")
    if os.path.exists(cost_tracker_path):
        try:
            with open(cost_tracker_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            for item in reversed(cdata.get("cli_quota", {}).get("history", [])):
                if item.get("role") == target_role and item.get("exit_code") == 0:
                    target_family = get_model_family(item.get("cli")) or get_model_family(item.get("model"))
                    break
        except Exception:
            pass

    if not target_family:
        task_md_path = os.path.join(task_dir, "task.md")
        approvals = parse_workers_approved(task_md_path)
        for a in reversed(approvals):
            if a.get("worker") == target_role:
                target_family = get_model_family(a.get("cli_or_provider")) or get_model_family(a.get("model"))
                break

    if target_family and critic_family == target_family:
        return (f"동종 모델 교차 비평 차단: 직전 '{target_role}' 계열({target_family})과 "
                f"현재 비평자 계열({critic_family})이 동일합니다. 이종 모델 교차 비평(Cross-Review) 원칙에 따라 "
                f"기동이 차단됩니다. (예: Codex/OpenAI 코딩은 Gemini/Google 또는 Claude/Anthropic이 비평해야 함)")

    return None


