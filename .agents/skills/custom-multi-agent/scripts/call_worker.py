import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import subprocess
import shutil

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
from datetime import datetime
from common_utils import (
    load_api_keys,
    load_backend_config as _shared_load_backend_config,
    evaluate_limits,
    resolve_execution_mode,
    validate_task_name,
    cli_allowlist_check,
)

# 워커 응답의 출력 토큰 상한.
# 이전에는 anthropic 경로에만 4000이 걸려 있었고, 상한에 닿아도 아무 신호 없이
# 잘린 result.md가 그대로 저장됐다. 상한을 올리고, 상한에 닿았는지를
# provider별 종료 사유로 감지해 조용한 잘림을 에러로 승격한다.
MAX_OUTPUT_TOKENS = 8000


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

def call_worker_cli(cli_name, model, prompt, allowlist, effort=None, system_prompt=None):
    entry = allowlist.get(cli_name)
    if not entry:
        raise ValueError(f"CLI '{cli_name}' not in allowlist")
    argv = [entry["binary"]] + list(entry["args"]) + [model]
    if effort and cli_name == "claude":
        argv += ["--effort", effort]
    elif effort:
        print(f"[WARN] {cli_name} effort 미확인(§0), 무시")
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
    proc = subprocess.run(argv, input=stdin_input, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    out, code = proc.stdout, proc.returncode
    key = {"claude": "result", "agy": "response"}.get(cli_name)
    if key:
        try:
            out = json.loads(out).get(key, out)
        except Exception:
            pass
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
    if len(sys.argv) < 4:
        print("Usage: python call_worker.py <worker_role> <brief_file_path> <result_file_path> [effort]")
        sys.exit(1)

    role = sys.argv[1]
    brief_path = sys.argv[2]
    result_path = sys.argv[3]
    effort = sys.argv[4] if len(sys.argv) > 4 else None

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
        provider, model = worker_cfg.get("provider"), worker_cfg["model"]
        if execution_mode == "cli-routed":
            cli_allowlist = _read_json(os.path.join(base_dir, "backends.json")).get("cli_allowlist", {})

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
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{current_time} [WORKER_START] '{role}' ({model}) 호출 개시. (워커가 지시를 수행하고 결과를 작성 중...)")
            
    print(f"\n⚙️ [{model}] Worker '{role}'이(가) 지시를 수행하며 코드를 작성 중입니다...")
    print(f"[API Call] Running worker '{role}' via {provider} ({model})...")
    
    # 3. execution_mode별 워커 호출 (에러 캐치 루프 포함) — api-routed는 호출 전 worst-case 예산 예약
    result_text = ""
    input_tokens = 0
    output_tokens = 0
    truncated = False
    exit_code = 0

    reserved_estimate = 0.0
    if not is_legacy_schema and execution_mode == "api-routed":
        reserved_estimate = round((MAX_OUTPUT_TOKENS * worker_cfg.get("output_price_per_1m", 0.0)
                                    + (len(prompt) // 4) * worker_cfg.get("input_price_per_1m", 0.0)) / 1e6, 6)
        def _reserve(d):
            uc = d.setdefault("usd_cost", {"accumulated": 0.0, "reserved": 0.0, "history": []})
            uc["reserved"] = round(uc.get("reserved", 0.0) + reserved_estimate, 6)
        _update_cost_tracker(cost_tracker_path, lock_path, _reserve)

    try:
        if execution_mode == "cli-routed":
            result_text, exit_code = call_worker_cli(worker_cfg["cli"], model, prompt, cli_allowlist, effort=effort, system_prompt=system_prompt)
            if exit_code != 0:
                raise RuntimeError(f"CLI exit code {exit_code}")
        elif execution_mode == "host-native":
            try:
                result_text, input_tokens, output_tokens, truncated = safe_run_async(call_antigravity_sdk(prompt, system_prompt))
            except RuntimeError as sdk_err:
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
    # 잘린 응답을 정상 result.md로 저장하면 diff가 중간에 끊긴 채 병합되어
    # 소리 없이 깨진 코드가 반영된다. 부분 결과는 진단용으로 남기되 실패로 처리한다.
    if truncated:
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
                "role": role, "model": model, "exit_code": exit_code})
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
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{_now()} [WORKER_CALL] '{role}' ({model}, {execution_mode}) 완료.")

if __name__ == "__main__":
    main()
