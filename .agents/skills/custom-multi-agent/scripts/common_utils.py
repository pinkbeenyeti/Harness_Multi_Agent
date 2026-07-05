import os
import json

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
