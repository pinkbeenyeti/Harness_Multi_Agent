import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import argparse
import hashlib
import re
import subprocess
import shutil
import uuid
from dataclasses import dataclass

# Windows cp949 인코딩 크래시 방지
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import urllib.request
import urllib.error
import time
import asyncio
from datetime import datetime, timezone
from common_utils import (
    load_api_keys,
    load_backend_config as _shared_load_backend_config,
    evaluate_limits,
    resolve_execution_mode,
    validate_task_name,
    cli_allowlist_check,
    verify_worker_approval,
    verify_cross_review_integrity,
)

# 워커 응답의 출력 토큰 상한.
MAX_OUTPUT_TOKENS = 8000
EXIT_CORRECTION_LIMIT = 6
EXIT_CROSS_REVIEW_VIOLATION = 7
EXIT_APPROVAL_VIOLATION = 8

@dataclass(frozen=True)
class PipelineKey:
    task: str
    group: str

class PipelineViolation(RuntimeError):
    pass

def parse_args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("role")
    p.add_argument("brief")
    p.add_argument("result")
    p.add_argument("legacy_effort", nargs="?")
    p.add_argument("--stage", choices=("plan", "plan_critique", "implement", "critic"))
    p.add_argument("--group", default="default")
    p.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    p.add_argument("--model")
    p.add_argument("--effort")
    a = p.parse_args(argv)
    if a.legacy_effort:
        print("[DEPRECATED] positional effort; use --effort")
        a.effort = a.effort or a.legacy_effort
    a.stage = a.stage or (
        "critic" if a.role.startswith("critic")
        else "plan" if a.role == "planner" else "implement")
    return a

def _default_effort(stage, tier):
    return "medium" if stage == "plan" or int(tier or 1) >= 2 else "low"

def _scope_hash(scope):
    value = {
        "tier": scope.get("tier"),
        "paths": sorted(os.path.normpath(str(p)).replace("\\", "/")
                        for p in scope.get("paths", [])),
        "groups": sorted(scope.get("groups", [])),
        "routes": scope.get("routes", {}),
        "auto_merge": bool(scope.get("auto_merge")),
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def scan_pipeline_attempts(task_dir, group):
    tracker = _read_json(os.path.join(task_dir, "cost_tracker.json"))
    if isinstance(tracker.get("worker_runs"), list):
        return [r for r in tracker["worker_runs"]
                if r.get("group", "default") == group]
    path = os.path.join(task_dir, "log.md")
    if not os.path.exists(path):
        return []
    pattern = re.compile(
        r"\[WORKER_ATTEMPT\].*?\bgroup=(\S+).*?\bstage=(plan|plan_critique|implement|critic)"
        r".*?\battempt=([12]).*?\boutcome=(\w+)(?:.*?\bverdict=(PASS|FAIL))?")
    runs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m and m.group(1) == group:
                runs.append({"group": group, "stage": m.group(2),
                    "attempt": int(m.group(3)), "outcome": m.group(4),
                    "verdict": m.group(5), "legacy": True})
    return runs

def expected_transitions(runs):
    """이 시점에서 합법적인 (stage, attempt) 목록을 반환한다. plan_critique는
    선택 단계라 같은 시점에 두 개의 합법적인 다음 수(계획 비평을 거치거나 곧장
    구현으로 넘어가거나)가 동시에 존재할 수 있어, 단일 expected 값이 아니라
    허용 목록으로 표현한다."""
    ended = [r for r in runs if r.get("outcome") != "running"]
    plans = [r for r in ended if r.get("stage") == "plan"]
    if len(plans) > 1:
        raise PipelineViolation("planner may run at most once")
    plan_crits = [r for r in ended if r.get("stage") == "plan_critique"]
    if len(plan_crits) > 1:
        raise PipelineViolation("plan_critique may run at most once")
    if plan_crits and not plans:
        raise PipelineViolation("plan_critique requires a prior plan")

    work = [r for r in ended if r.get("stage") not in ("plan", "plan_critique")]
    if not work:
        if plans and not plan_crits:
            return [("plan_critique", 1), ("implement", 1)]
        return [("implement", 1)]
    last = work[-1]
    stage, attempt = last.get("stage"), int(last.get("attempt", 1))
    if last.get("outcome") != "success":
        return [("implement", 2)] if attempt == 1 else []
    if stage == "implement":
        return [("critic", attempt)]
    if last.get("verdict") == "PASS":
        return []
    return [("implement", 2)] if attempt == 1 else []

def reserve_pipeline_slot(tracker_path, key, stage, attempt, run_id):
    legacy = scan_pipeline_attempts(os.path.dirname(tracker_path), key.group)
    def mutate(data):
        stored = data.get("worker_runs")
        runs = stored if isinstance(stored, list) else legacy
        group_runs = [r for r in runs if r.get("group", "default") == key.group]
        allowed = expected_transitions(group_runs)
        if stage == "plan":
            if any(r.get("stage") == "plan" for r in group_runs):
                raise PipelineViolation("planner already consumed")
        elif (stage, attempt) not in allowed:
            raise PipelineViolation(f"expected one of {allowed}, got {(stage, attempt)}")
        if not isinstance(stored, list):
            data["worker_runs"] = list(legacy)
        data["worker_runs"].append({
            "run_id": run_id, "group": key.group, "stage": stage,
            "attempt": attempt, "outcome": "running",
            "started_at": datetime.now(timezone.utc).isoformat()})
    _update_cost_tracker(tracker_path, tracker_path + ".lock", mutate)

def finish_pipeline_slot(tracker_path, run_id, **fields):
    finished = {}
    def mutate(data):
        for run in data.get("worker_runs", []):
            if run.get("run_id") == run_id:
                run.update(fields)
                run["ended_at"] = datetime.now(timezone.utc).isoformat()
                finished.update(run)
                return
    _update_cost_tracker(tracker_path, tracker_path + ".lock", mutate)
    if finished:
        with open(os.path.join(os.path.dirname(tracker_path), "log.md"),
                  "a", encoding="utf-8") as f:
            f.write("\n{} [WORKER_ATTEMPT] group={} stage={} attempt={} "
                    "outcome={} verdict={}".format(
                        datetime.now(timezone.utc).isoformat(),
                        finished["group"], finished["stage"], finished["attempt"],
                        finished.get("outcome"), finished.get("verdict") or "-"))

def critic_verdict(text):
    m = re.search(r"(?m)^## Verdict\s*$\s*^(PASS|FAIL)\s*$", text)
    return m.group(1) if m else None



def load_backend_config(role, execution_mode="api-routed"):
    try:
        return _shared_load_backend_config(role, execution_mode)
    except FileNotFoundError:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        print(f"Error: backends.json not found at '{os.path.join(base_dir, 'backends.json')}'")
        sys.exit(1)
    except KeyError as e:
        print(f"Error: {e}")
        sys.exit(1)

def call_anthropic(model, prompt, system_prompt=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable or configuration is missing.")
        
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    data = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    if system_prompt:
        data["system"] = system_prompt
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                input_tokens = res_data.get("usage", {}).get("input_tokens", 0)
                output_tokens = res_data.get("usage", {}).get("output_tokens", 0)
                truncated = res_data.get("stop_reason") == "max_tokens"
                return res_data["content"][0]["text"], input_tokens, output_tokens, truncated
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8', errors='ignore')
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] Anthropic API failed ({e.code}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Anthropic API Error (HTTP {e.code}): {err_msg}")
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] Connection error ({e}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Anthropic Connection Error: {e}")

def call_openai(model, prompt, system_prompt=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable or configuration is missing.")
        
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # OpenAI 계열은 모델 세대에 따라 출력 상한 파라미터명이 다르다(max_tokens /
    # max_completion_tokens). 잘못 보내면 400이 나므로 상한은 모델 기본값에 맡기고,
    # 대신 finish_reason으로 잘림을 감지한다.
    data = {
        "model": model,
        "messages": messages
    }

    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                input_tokens = res_data.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = res_data.get("usage", {}).get("completion_tokens", 0)
                truncated = res_data["choices"][0].get("finish_reason") == "length"
                return res_data["choices"][0]["message"]["content"], input_tokens, output_tokens, truncated
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8', errors='ignore')
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] OpenAI API failed ({e.code}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"OpenAI API Error (HTTP {e.code}): {err_msg}")
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] Connection error ({e}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"OpenAI Connection Error: {e}")

def call_google(model, prompt, system_prompt=None):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable or configuration is missing.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS}
    }
    if system_prompt:
        data["systemInstruction"] = {
            "parts": [{"text": system_prompt}]
        }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                usage = res_data.get("usageMetadata", {})
                input_tokens = usage.get("promptTokenCount", 0)
                output_tokens = usage.get("candidatesTokenCount", 0)
                truncated = res_data["candidates"][0].get("finishReason") == "MAX_TOKENS"
                return res_data["candidates"][0]["content"]["parts"][0]["text"], input_tokens, output_tokens, truncated
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8', errors='ignore')
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] Gemini API failed ({e.code}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Gemini API Error (HTTP {e.code}): {err_msg}")
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                print(f"[Warning] Connection error ({e}). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Gemini Connection Error: {e}")

def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def call_worker_cli(cli_name, *, model, effort, prompt, allowlist, system_prompt=None, cwd=None):
    entry = allowlist.get(cli_name)
    if not entry:
        raise ValueError(f"CLI '{cli_name}' not in allowlist")
    if effort not in entry.get("allowed_efforts", []):
        raise ValueError(f"unsupported effort '{effort}' for CLI '{cli_name}'")
    expand = lambda xs: [x.format(model=model, effort=effort) for x in xs]
    argv = ([entry["binary"]] + list(entry.get("args", []))
            + expand(entry["model_arg"]) + expand(entry["effort_args"][effort]))
    cli_allowlist_check(cli_name, argv)  # 자유 텍스트(프롬프트) 추가 전에 검증
    composed = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{prompt}" if system_prompt else prompt
    if cli_name == "agy":
        # 실측(agy --help): -p는 프롬프트를 인자 값으로 받는다(stdin은 부가 입력일 뿐,
        # 대체 아님). 빈 stdin을 넘겨 자식 프로세스가 터미널 stdin 대기로 멈추지 않게 한다.
        argv = argv + ["-p", composed]
        stdin_input = ""
    else:
        stdin_input = composed
    # Windows에서는 npm 전역 CLI가 확장자 없는 이름(예: "claude")으로 PATH에 없고
    # claude.cmd로만 존재한다. subprocess.run은 shell=True 없이는 PATHEXT를 뒤지지
    # 않아 WinError 2로 즉시 실패한다. cli_allowlist_check는 이미 원본 argv(확장자
    # 없는 이름)로 통과했으므로, 여기서 실행 직전에만 shutil.which로 실제 경로를
    # 찾아 치환한다(셸 호출 아님, PATH 검색만 수행 — 인젝션 위험 없음).
    resolved_bin = shutil.which(argv[0]) or argv[0]
    argv = [resolved_bin] + argv[1:]
    # text=True만 쓰면 Windows에서 로케일 기본 코드페이지(이 환경은 cp949)로
    # 자식 프로세스 stdin/stdout을 인코딩/디코딩한다. 브리프의 em-dash(—) 등
    # cp949로 표현 불가능한 문자가 있으면 UnicodeEncodeError로 즉시 실패한다.
    # encoding="utf-8"을 명시해 시스템 로케일과 무관하게 항상 UTF-8을 쓴다.
    # 실측: Tier2 high/xhigh effort 호출이 7~10분씩 걸려 기존 600초 상한에 위험하게
    # 근접해 조용히 TimeoutExpired로 실패하는 사례가 있었다(2026-09-04). 1200초로 완충.
    target_cwd = cwd or os.getcwd()
    proc = subprocess.run(argv, cwd=target_cwd, input=stdin_input, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1200)
    out, code = proc.stdout, proc.returncode
    if cli_name == "codex":
        messages = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
                if evt.get("type") == "item.completed":
                    item = evt.get("item", {})
                    if item.get("type") == "agent_message" and "text" in item:
                        messages.append(item["text"])
            except Exception:
                pass
        if messages:
            out = messages[-1]
    else:
        key = {"claude": "result", "agy": "response"}.get(cli_name)
        if key:
            try:
                data = json.loads(out)
                if cli_name == "agy" and data.get("denied_actions"):
                    print(f"[WARN] agy denied actions: {data.get('denied_actions')}")
                out = data.get(key, out)
            except Exception:
                pass
    if not out.strip() and proc.stderr:
        print(f"[WARN] {cli_name} returned empty output. Stderr: {proc.stderr.strip()[:500]}")
    return out, code

def _update_cost_tracker(path, lock_path, mutate_fn):
    with FileLock(lock_path):
        data = _read_json(path)
        if not isinstance(data, dict):
            data = {}
        mutate_fn(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

def _calc_cost(cfg, itok, otok):
    return round((itok * cfg.get("input_price_per_1m", 0.0) + otok * cfg.get("output_price_per_1m", 0.0)) / 1e6, 6)

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

_PROVIDER_FN = {"anthropic": call_anthropic, "openai": call_openai, "google": call_google}

def _append_route(d, route_id, outcome):
    rh = d.setdefault("route_history", [])
    rh.append({"route_id": route_id, "ts": _now(), "outcome": outcome})
    d["route_history"] = rh[-50:]

async def call_antigravity_sdk(prompt, system_prompt=None):
    try:
        from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
    except ImportError:
        raise RuntimeError("Google Antigravity SDK is not installed. Please install it using: pip install google-antigravity")
        
    sys_inst = system_prompt if system_prompt else "You are a specialized worker agent executing a subtask."
    config = LocalAgentConfig(
        system_instructions=sys_inst,
        capabilities=CapabilitiesConfig()
    )
    
    print("[SDK Call] Spawning local Antigravity Agent...")
    async with Agent(config) as agent:
        response = await agent.chat(prompt)
        content = ""
        async for token in response:
            content += token
            
        # 간이 토큰 계산 (1토큰 ~ 4글자 기준)
        input_tokens = len(prompt) // 4
        output_tokens = len(content) // 4
        # SDK 스트리밍은 종료 사유를 노출하지 않으므로 잘림 판정을 하지 않는다.
        return content, input_tokens, output_tokens, False

def safe_run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
        
    if loop and loop.is_running():
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)

def update_task_status(task_dir, new_status):
    import re
    task_md_path = os.path.join(task_dir, "task.md")
    if not os.path.exists(task_md_path):
        return
    try:
        with open(task_md_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # 띄어쓰기, 대소문자, 볼드 마크 및 불릿 형태에 구애받지 않고 Status 행 매칭
        pattern = re.compile(r'^(\s*[\-\*]\s*(?:\*\*)?status(?:\*\*)?\s*:\s*)(.*)$', re.IGNORECASE)
        
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                prefix = match.group(1)  # 기존 리스트 불릿 및 볼드 형식 보존
                remaining = match.group(2)
                comment = ""
                if "#" in remaining:
                    comment = "  #" + remaining.split("#", 1)[1].rstrip()
                lines[i] = f"{prefix}{new_status}{comment}\n"
                break
                
        with open(task_md_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        print(f"[Warning] Failed to update task.md status: {e}")

class FileLock:
    def __init__(self, lock_file_path, timeout=35.0, stale_limit=30.0):
        self.lock_file_path = lock_file_path
        self.timeout = timeout
        self.stale_limit = stale_limit
        self.is_locked = False
   
    def __enter__(self):
        if os.path.exists(self.lock_file_path):
            try:
                mtime = os.path.getmtime(self.lock_file_path)
                if time.time() - mtime > self.stale_limit:
                    print(f"[Lock] Stale lock detected before loop (aged {time.time() - mtime:.1f}s). Removing stale lock file.")
                    os.remove(self.lock_file_path)
            except OSError:
                pass

        start_time = time.time()
        while time.time() - start_time < self.timeout:
            try:
                # 30초 이상 경과된 Stale Lock 가로채기 및 제거
                if os.path.exists(self.lock_file_path):
                    try:
                        mtime = os.path.getmtime(self.lock_file_path)
                        if time.time() - mtime > self.stale_limit:
                            print(f"[Lock] Stale lock detected inside loop (aged {time.time() - mtime:.1f}s). Removing stale lock file.")
                            os.remove(self.lock_file_path)
                    except OSError:
                        pass
                
                fd = os.open(self.lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.is_locked = True
                break
            except FileExistsError:
                time.sleep(0.05)
        if not self.is_locked:
            raise RuntimeError(f"Could not acquire file lock on {self.lock_file_path} within {self.timeout} seconds")
        return self
   
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.is_locked:
            try:
                os.remove(self.lock_file_path)
            except OSError:
                pass

def main():
    args = parse_args(sys.argv[1:])
    role = args.role
    brief_path = args.brief
    result_path = args.result

    # base_dir 명시적 정의 (NameError 방지)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 0. API Key 설정 파일 사전 로드
    load_api_keys()

    # 0.1 태스크 디렉토리 검증 및 예산(budget)/execution_mode 설정 로드
    task_dir = os.path.dirname(os.path.abspath(brief_path))
    try:
        validate_task_name(os.path.basename(task_dir))
    except ValueError as e:
        print(f"[SECURITY] {e}")
        sys.exit(1)

    cost_tracker_path = os.path.join(task_dir, "cost_tracker.json")
    lock_path = cost_tracker_path + ".lock"

    with FileLock(lock_path):
        cost_data = _read_json(cost_tracker_path)
    budget_limit = cost_data.get("budget_limit", 2.0)
    fallback_role = cost_data.get("fallback_role", "fallback-efficient")

    scope = cost_data.get("approval_scope")
    tier = scope.get("tier", 1) if isinstance(scope, dict) else 1
    effort = args.effort or _default_effort(args.stage, tier)

    is_legacy_schema = "execution_mode" not in cost_data
    execution_mode, google_only, legacy_warn = resolve_execution_mode(cost_data)
    if legacy_warn:
        print(legacy_warn)
    if google_only and execution_mode == "api-routed":
        role = "critic-standard"

    last = (cost_data.get("route_history") or [{}])[-1]
    accumulated_cost = cost_data.get("usd_cost", {}).get("accumulated", cost_data.get("accumulated_cost", 0.0))
    route_id = f"{role}@{execution_mode}"
    retry_blocked = last.get("route_id") == route_id and last.get("outcome") == "fail"

    # 1. config 로드 및 execution_mode 가공, 1.1 예산 초과/직전 실패 재시도 시 자동 Fallback
    if (execution_mode == "api-routed" and accumulated_cost >= budget_limit) or retry_blocked:
        if role == fallback_role:
            print(f"[BUDGET ERROR] fallback '{fallback_role}' 불충족.")
            sys.exit(2)
        reason = "예산 한도 초과" if not retry_blocked else "직전 라운드 동일 route 실패"
        print(f"[FALLBACK] {reason}로 '{role}' -> '{fallback_role}' 전환.")
        log_path = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path):
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n{_now()} [DECISION] {reason}({role}@{execution_mode})로 폴백 워커 '{fallback_role}'로 자동 전환하여 호출합니다.")
        role, route_id = fallback_role, f"{fallback_role}@{execution_mode}"

    cli_allowlist = {}
    if execution_mode == "host-native":
        provider, model = "host-native", "agy-agent"
        worker_cfg = {"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}
    else:
        worker_cfg = load_backend_config(role, execution_mode)
        provider = worker_cfg.get("provider")
        model = args.model or worker_cfg["model"]
        if execution_mode == "cli-routed":
            cli_allowlist = _read_json(os.path.join(base_dir, "backends.json")).get("cli_allowlist", {})

    # 1.2 승인 일치 검증 게이트 (Approval Enforcement)
    approval_err = verify_worker_approval(task_dir, role, execution_mode, worker_cfg, stage=args.stage)
    if approval_err:
        print(f"\n[APPROVAL VIOLATION] {approval_err}")
        print("  → task.md의 workers_approved 승인 내역과 실제 실행 대상이 일치하지 않습니다.")
        log_path_pre = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path_pre):
            with open(log_path_pre, "a", encoding="utf-8") as lf:
                lf.write(f"\n{_now()} [ERROR] 승인 검증 실패로 워커 기동 차단: {approval_err}")
        sys.exit(EXIT_APPROVAL_VIOLATION)

    # 1.3 이종 모델 교차 비평 인터록 (Cross-Review Interlock)
    cross_err = verify_cross_review_integrity(task_dir, role, execution_mode, worker_cfg, stage=args.stage)
    if cross_err:
        print(f"\n[CROSS-REVIEW VIOLATION] {cross_err}")
        print("  → 비평 워커는 직전 구현자와 동일한 모델 계열(Vendor)일 수 없습니다.")
        log_path_pre = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path_pre):
            with open(log_path_pre, "a", encoding="utf-8") as lf:
                lf.write(f"\n{_now()} [ERROR] 교차 비평 인터록 차단: {cross_err}")
        sys.exit(EXIT_CROSS_REVIEW_VIOLATION)

    if isinstance(scope, dict):
        route_key = "critic" if args.stage in ("critic", "plan_critique") else role
        approved = scope.get("routes", {}).get(route_key, {})
        actual_route = worker_cfg.get("cli", provider)
        invalid = (
            (scope.get("scope_hash") and scope.get("scope_hash") != _scope_hash(scope))
            or (scope.get("groups") and args.group not in scope.get("groups", []))
            or (approved.get("cli_or_provider")
                and approved["cli_or_provider"] != actual_route)
            or (approved.get("model") and approved["model"] != model)
            or (approved.get("effort") and approved["effort"] != effort))
        if invalid:
            print("[APPROVAL VIOLATION] approval_scope/hash mismatch")
            sys.exit(EXIT_APPROVAL_VIOLATION)

    # 2. brief(지시서) 읽기
    if not os.path.exists(brief_path):
        print(f"Error: Brief file not found at '{brief_path}'")
        sys.exit(1)
        
    # 2.1 브리프 사전 검증 (기동 전 게이트)
    #
    # 브리프 상한은 규칙으로만 존재하고 강제되지 않아, 실측에서 브리프 11개가
    # 20,140토큰(워커 결과물의 2.7배)까지 부풀었다. 브리프는 워커 프롬프트로
    # 그대로 들어가므로 여기서 막는 것이 가장 싸다.
    brief_check = evaluate_limits(brief_path)
    if brief_check["violations"]:
        print(f"[BRIEF OVER LIMIT] '{os.path.basename(brief_path)}'가 분량 상한을 초과했습니다. 워커를 기동하지 않습니다.")
        for v in brief_check["violations"]:
            print(f"  - {v}")
        print("  → 브리프는 '경로 + 라인 범위 + 지시'만 담습니다. 코드 본문·배경 설명·이전 라운드 요약을 덜어내십시오.")

        log_path_pre = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path_pre):
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(log_path_pre, "a", encoding="utf-8") as lf:
                lf.write(f"\n{current_time} [ERROR] 브리프 '{os.path.basename(brief_path)}' 분량 상한 초과로 워커 기동을 차단했습니다. "
                         + "; ".join(brief_check["violations"]))
        sys.exit(4)

    with open(brief_path, "r", encoding="utf-8") as f:
        prompt = f.read()
    # worker_booster.md 로드 (system_prompt 용)
    worker_booster_path = os.path.join(base_dir, "_shared", "worker_booster.md")
    system_prompt = None
    if os.path.exists(worker_booster_path):
        try:
            with open(worker_booster_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
            print(f"[System Prompt] Loaded worker booster config from '{worker_booster_path}'")
        except Exception as e:
            print(f"[Warning] Failed to load worker booster from '{worker_booster_path}': {e}")

    # 상태 업데이트 및 로그 기록 (진행 중)
    status_msg = f"waiting_{role} ({model} is executing...)"
    update_task_status(task_dir, status_msg)
    
    log_path = os.path.join(task_dir, "log.md")
    if os.path.exists(log_path):
        cli_label = worker_cfg.get("cli", provider or "unknown")
        actor_desc = f"{cli_label}-cli/{role} ({model})"
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{_now()} [WORKER_START] [Actor: {actor_desc}] '{role}' ({model}) 호출 개시. (워커가 지시를 수행하고 결과를 작성 중...)")
            
    print(f"\n⚙️ [{model}] Worker '{role}'이(가) 지시를 수행하며 코드를 작성 중입니다...")
    print(f"[API Call] Running worker '{role}' via {provider} ({model})...")
    
    # 3. execution_mode별 워커 호출 (에러 캐치 루프 포함) — api-routed는 호출 전 worst-case 예산 예약
    result_text = ""
    input_tokens = 0
    output_tokens = 0
    truncated = False
    exit_code = 0

    run_id = str(uuid.uuid4())
    started_mono = time.monotonic()

    reserved_estimate = 0.0
    if not is_legacy_schema and execution_mode == "api-routed":
        reserved_estimate = round((MAX_OUTPUT_TOKENS * worker_cfg.get("output_price_per_1m", 0.0)
                                    + (len(prompt) // 4) * worker_cfg.get("input_price_per_1m", 0.0)) / 1e6, 6)
        def _reserve(d):
            uc = d.setdefault("usd_cost", {"accumulated": 0.0, "reserved": 0.0, "history": []})
            uc["reserved"] = round(uc.get("reserved", 0.0) + reserved_estimate, 6)
        _update_cost_tracker(cost_tracker_path, lock_path, _reserve)

    try:
        reserve_pipeline_slot(
            cost_tracker_path,
            PipelineKey(os.path.basename(task_dir), args.group),
            args.stage, args.attempt, run_id)
    except PipelineViolation as exc:
        print(f"[PIPELINE VIOLATION] {exc}")
        log_path_pre = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path_pre):
            with open(log_path_pre, "a", encoding="utf-8") as lf:
                lf.write(f"\n{_now()} [ERROR] 파이프라인 순서 위반으로 워커 기동 차단: {exc}")
        sys.exit(EXIT_CORRECTION_LIMIT)

    try:
        if execution_mode == "cli-routed":
            # base_dir(스킬 설치 경로)에서 2단계 상위로 올라가는 계산은 스킬이
            # <project>/.claude/skills/... 에 프로젝트 로컬로 설치된 경우만 맞는다.
            # 이 환경처럼 사용자 홈(~/.claude/skills/...)에 전역 설치된 경우
            # C:\Users\<user>\.claude로 잘못 해석되어, cli-routed 워커가 실제
            # Unity 프로젝트 파일에 전혀 접근하지 못하는 채로 기동되는 사고가 있었다
            # (2026-09-04, sound-effects-system Round 2). 환경변수로 오버라이드
            # 가능하게 하고, 미설정 시 이 스킬이 실사용되는 유일한 프로젝트 경로로 폴백한다.
            workspace_dir = os.environ.get(
                "MULTI_AGENT_PROJECT_DIR", r"C:\Users\rbgus\Dinosour cake")
            result_text, exit_code = call_worker_cli(
                worker_cfg["cli"], model=model, effort=effort, prompt=prompt,
                allowlist=cli_allowlist, system_prompt=system_prompt,
                cwd=workspace_dir
            )
            if exit_code != 0:
                raise RuntimeError(f"CLI exit code {exit_code}")
        elif execution_mode == "host-native":
            try:
                result_text, input_tokens, output_tokens, truncated = safe_run_async(call_antigravity_sdk(prompt, system_prompt))
            except RuntimeError as sdk_err:
                finish_pipeline_slot(
                    cost_tracker_path, run_id, role=role, outcome="error",
                    execution_wallclock_sec=round(time.monotonic()-started_mono, 3))
                print(f"[HOST-NATIVE] {sdk_err} — Agent 도구로 서브에이전트 기동 필요"
                      "(result.md/critic_report.md 직접 작성).")
                sys.exit(5)
        elif provider in _PROVIDER_FN:
            if effort:
                print(f"[WARN] effort='{effort}' requested but not yet wired to {provider} API — §4 미확정")
            result_text, input_tokens, output_tokens, truncated = _PROVIDER_FN[provider](model, prompt, system_prompt)
        else:
            print(f"Error: Unsupported provider '{provider}'")
            sys.exit(1)
    except Exception as e:
        finish_pipeline_slot(
            cost_tracker_path, run_id, role=role, outcome="error",
            execution_wallclock_sec=round(time.monotonic()-started_mono, 3))
        error_msg = str(e)
        print(f"[API ERROR] {error_msg}")

        # 에러 상태 업데이트
        update_task_status(task_dir, f"error (Worker '{role}' failed)")

        if not is_legacy_schema:
            def _fail(d):
                if reserved_estimate:
                    uc = d.setdefault("usd_cost", {"accumulated": 0.0, "reserved": 0.0, "history": []})
                    uc["reserved"] = round(max(uc.get("reserved", 0.0) - reserved_estimate, 0.0), 6)
                _append_route(d, route_id, "fail")
            _update_cost_tracker(cost_tracker_path, lock_path, _fail)

        # log.md에 상세 에러 내용 적재
        log_path = os.path.join(task_dir, "log.md")
        if os.path.exists(log_path):
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n{current_time} [ERROR] '{role}' ({model}) API 호출 실패. 원인: {error_msg}")
        sys.exit(1)

    # 3.05 출력 상한 도달(잘림) 감지
    if truncated:
        finish_pipeline_slot(
            cost_tracker_path, run_id, role=role, outcome="truncated",
            execution_wallclock_sec=round(time.monotonic()-started_mono, 3))
        banner = (f"<!-- TRUNCATED: 워커 '{role}'({model}) 응답이 출력 상한"
                  f"({MAX_OUTPUT_TOKENS} tokens)에 도달해 중간에 잘렸습니다. "
                  f"이 파일을 병합하지 마십시오. 브리프 범위를 쪼개 재호출하십시오. -->\n\n")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(banner + result_text)

        print(f"[TRUNCATED] 워커 응답이 출력 상한에 도달해 잘렸습니다. '{result_path}'는 불완전합니다.")
        print("[TRUNCATED] 브리프의 In Scope를 더 작게 쪼개어 재호출하십시오.")
        update_task_status(task_dir, f"error (Worker '{role}' output truncated)")

        if os.path.exists(log_path):
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n{current_time} [ERROR] '{role}' ({model}) 응답이 출력 상한"
                         f"({MAX_OUTPUT_TOKENS} tokens)에 도달해 잘렸습니다. result.md 병합 금지, 범위 분할 후 재호출 필요.")
        sys.exit(3)

    # 4. 결과 작성 및 트래커 정산 (usd_cost/cli_quota 분리, 레거시 스키마는 기존 방식 유지)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(result_text)

    finish_pipeline_slot(
        cost_tracker_path, run_id, role=role, model=model, effort=effort,
        outcome="success",
        verdict=critic_verdict(result_text) if args.stage in ("critic", "plan_critique") else None,
        execution_wallclock_sec=round(time.monotonic()-started_mono, 3))

    # 리뷰 진행 중으로 상태 업데이트
    update_task_status(task_dir, "reviewing (Orchestrator is verifying the output...)")

    print(f"[API Success] Worker response written to '{result_path}'")

    def _settle(d):
        d.setdefault("budget_limit", budget_limit)
        d.setdefault("fallback_role", fallback_role)
        if is_legacy_schema:
            d.setdefault("worker_mode", cost_data.get("worker_mode", "multi-api"))
            cost = _calc_cost(worker_cfg, input_tokens, output_tokens)
            d["accumulated_cost"] = round(d.get("accumulated_cost", 0.0) + cost, 6)
            print(f"[Cost] ${cost:.5f}")
            return
        d["execution_mode"] = execution_mode
        if execution_mode == "cli-routed":
            cq = d.setdefault("cli_quota", {"history": []})
            cq["history"].append({"timestamp": _now(), "cli": worker_cfg.get("cli"),
                "role": role, "model": model, "exit_code": exit_code, "stage": args.stage})
        elif execution_mode == "api-routed":
            cost = _calc_cost(worker_cfg, input_tokens, output_tokens)
            uc = d.setdefault("usd_cost", {"accumulated": 0.0, "reserved": 0.0, "history": []})
            uc["reserved"] = round(max(uc.get("reserved", 0.0) - reserved_estimate, 0.0), 6)
            uc["accumulated"] = round(uc.get("accumulated", 0.0) + cost, 6)
            print(f"[Cost] ${cost:.5f}")
        _append_route(d, route_id, "success")

    _update_cost_tracker(cost_tracker_path, lock_path, _settle)
    log_path = os.path.join(task_dir, "log.md")
    if os.path.exists(log_path):
        cli_label = worker_cfg.get("cli", provider or "unknown")
        actor_desc = f"{cli_label}-cli/{role} ({model})"
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{_now()} [WORKER_CALL] [Actor: {actor_desc}] '{role}' ({model}, {execution_mode}) 완료.")

if __name__ == "__main__":
    main()
