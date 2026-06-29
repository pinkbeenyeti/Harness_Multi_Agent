import os
import sys
import json
import urllib.request
import urllib.error
import time
import asyncio
from datetime import datetime

def load_backend_config(role):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "backends.json")
    
    if not os.path.exists(config_path):
        print(f"Error: backends.json not found at '{config_path}'")
        sys.exit(1)
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    workers = config.get("workers", {})
    if role not in workers:
        print(f"Error: Role '{role}' is not defined in backends.json")
        sys.exit(1)
        
    return workers[role]

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
                return
            except Exception as e:
                print(f"[Warning] Failed to load keys from '{p}': {e}")

def call_anthropic(model, prompt):
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
        "max_tokens": 4000,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                input_tokens = res_data.get("usage", {}).get("input_tokens", 0)
                output_tokens = res_data.get("usage", {}).get("output_tokens", 0)
                return res_data["content"][0]["text"], input_tokens, output_tokens
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

def call_openai(model, prompt):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable or configuration is missing.")
        
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                input_tokens = res_data.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = res_data.get("usage", {}).get("completion_tokens", 0)
                return res_data["choices"][0]["message"]["content"], input_tokens, output_tokens
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

def call_google(model, prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable or configuration is missing.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {
        "Content-Type": "application/json"
    }
    
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
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
                return res_data["candidates"][0]["content"]["parts"][0]["text"], input_tokens, output_tokens
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

async def call_antigravity_sdk(prompt):
    try:
        from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
    except ImportError:
        raise RuntimeError("Google Antigravity SDK is not installed. Please install it using: pip install google-antigravity")
        
    config = LocalAgentConfig(
        system_instructions="You are a specialized worker agent executing a subtask.",
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
        return content, input_tokens, output_tokens

class FileLock:
    def __init__(self, lock_file_path, timeout=10.0):
        self.lock_file_path = lock_file_path
        self.timeout = timeout
        self.is_locked = False
   
    def __enter__(self):
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            try:
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
        print("Usage: python call_worker.py <worker_role> <brief_file_path> <result_file_path>")
        sys.exit(1)
        
    role = sys.argv[1]
    brief_path = sys.argv[2]
    result_path = sys.argv[3]
    
    # 0. API Key 설정 파일 사전 로드
    load_api_keys()
    
    # 0.1 태스크 디렉토리 및 예산(budget) 설정 로드
    task_dir = os.path.dirname(os.path.abspath(brief_path))
    cost_tracker_path = os.path.join(task_dir, "cost_tracker.json")
    lock_path = cost_tracker_path + ".lock"
    
    budget_limit = 2.0
    accumulated_cost = 0.0
    fallback_role = "gemini"
    worker_mode = "multi-api"
    cost_data = {}
    
    with FileLock(lock_path):
        if os.path.exists(cost_tracker_path):
            try:
                with open(cost_tracker_path, "r", encoding="utf-8") as f:
                    cost_data = json.load(f)
                    budget_limit = cost_data.get("budget_limit", 2.0)
                    accumulated_cost = cost_data.get("accumulated_cost", 0.0)
                    fallback_role = cost_data.get("fallback_role", "gemini")
                    worker_mode = cost_data.get("worker_mode", "multi-api")
            except Exception as e:
                print(f"[Warning] Failed to load cost_tracker.json: {e}")
                
        # 1. config 로드 및 worker_mode 가공
        if worker_mode == "antigravity":
            provider = "antigravity"
            model = "agy-agent"
            worker_cfg = {"input_price_per_1m": 0.0, "output_price_per_1m": 0.0}
            print("[Mode: Antigravity] Running via local Antigravity Python SDK (free quota).")
        else:
            if worker_mode == "gemini-only":
                print(f"[Mode: Gemini-Only] Overriding worker role '{role}' to force use 'gemini'.")
                role = "gemini"
            worker_cfg = load_backend_config(role)
            provider = worker_cfg["provider"]
            model = worker_cfg["model"]
        
        # 1.1 예산 초과 검사 및 자동 Fallback 처리
        if worker_mode != "antigravity" and accumulated_cost >= budget_limit:
            if role != fallback_role:
                print(f"[BUDGET EXCEEDED] Current accumulated cost (${accumulated_cost:.5f}) has exceeded budget limit (${budget_limit:.2f}).")
                print(f"[BUDGET EXCEEDED] Automatically switching worker role from '{role}' to fallback role '{fallback_role}'.")
                
                # log.md에 스위칭 결정 기록
                log_path = os.path.join(task_dir, "log.md")
                if os.path.exists(log_path):
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"\n{current_time} [DECISION] API 예산 한도(${budget_limit:.2f}) 초과(현재: ${accumulated_cost:.4f}). 워커 '{role}'에서 폴백 워커 '{fallback_role}'로 자동 전환하여 호출합니다.")
                
                role = fallback_role
                worker_cfg = load_backend_config(role)
                provider = worker_cfg["provider"]
                model = worker_cfg["model"]
            else:
                print(f"[BUDGET ERROR] Budget limit (${budget_limit:.2f}) exceeded and already using fallback role '{fallback_role}'. Terminating.")
                sys.exit(2)
        
        # 2. brief(지시서) 읽기
        if not os.path.exists(brief_path):
            print(f"Error: Brief file not found at '{brief_path}'")
            sys.exit(1)
            
        with open(brief_path, "r", encoding="utf-8") as f:
            prompt = f.read()
            
        print(f"[API Call] Running worker '{role}' via {provider} ({model})...")
        
        # 3. provider별 API 호출 (에러 캐치 루프 포함)
        result_text = ""
        input_tokens = 0
        output_tokens = 0
        
        try:
            if provider == "anthropic":
                result_text, input_tokens, output_tokens = call_anthropic(model, prompt)
            elif provider == "openai":
                result_text, input_tokens, output_tokens = call_openai(model, prompt)
            elif provider == "google":
                result_text, input_tokens, output_tokens = call_google(model, prompt)
            elif provider == "antigravity":
                result_text, input_tokens, output_tokens = asyncio.run(call_antigravity_sdk(prompt))
            else:
                print(f"Error: Unsupported provider '{provider}'")
                sys.exit(1)
        except Exception as e:
            error_msg = str(e)
            print(f"[API ERROR] {error_msg}")
            
            # log.md에 상세 에러 내용 적재
            log_path = os.path.join(task_dir, "log.md")
            if os.path.exists(log_path):
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n{current_time} [ERROR] '{role}' ({model}) API 호출 실패. 원인: {error_msg}")
            sys.exit(1)
            
        # 3.1 비용 계산 및 누적 업데이트
        input_price = worker_cfg.get("input_price_per_1m", 0.0)
        output_price = worker_cfg.get("output_price_per_1m", 0.0)
        call_cost = (input_tokens * input_price / 1000000.0) + (output_tokens * output_price / 1000000.0)
        new_accumulated_cost = accumulated_cost + call_cost
        
        # 4. 결과 작성 및 트래커 업데이트
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(result_text)
            
        print(f"[API Success] Worker response written to '{result_path}'")
        print(f"[Cost Report] Tokens used: {input_tokens} input, {output_tokens} output. Cost for this call: ${call_cost:.5f}")
        
        if os.path.exists(cost_tracker_path):
            cost_data["accumulated_cost"] = round(new_accumulated_cost, 6)
            if "history" not in cost_data:
                cost_data["history"] = []
            cost_data["history"].append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "role": role,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(call_cost, 6)
            })
            with open(cost_tracker_path, "w", encoding="utf-8") as f:
                json.dump(cost_data, f, indent=2, ensure_ascii=False)
                
            # log.md에 비용 및 워커 성공 기록
            log_path = os.path.join(task_dir, "log.md")
            if os.path.exists(log_path):
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n{current_time} [WORKER_CALL] '{role}' ({model}) 호출 완료. 소모 비용: ${call_cost:.5f} (누적: ${new_accumulated_cost:.5f}/{budget_limit:.2f})")

if __name__ == "__main__":
    main()
