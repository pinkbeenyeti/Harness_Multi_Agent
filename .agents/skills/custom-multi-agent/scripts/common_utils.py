import os
import json
import subprocess

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


def _run_git_cmd(args, timeout=5):
    """
    git subprocess 호출을 위한 공용 헬퍼 함수
    """
    return subprocess.run(
        ["git"] + args,
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
    try:
        # 1. 로컬 변경사항(unstaged/staged) 확인 (충돌 예방 안전장치)
        status_res = _run_git_cmd(["status", "--porcelain"], timeout=3)
        if status_res.stdout.strip():
            print("\n⚠️ [Warning] 로컬 저장소에 커밋되지 않은 변경사항이 존재합니다.")
            print("안전한 업데이트를 위해 작업을 먼저 커밋하거나 stashing 후 다시 실행하십시오.")
            return

        # 2. git fetch origin 수행 (타임아웃 3초)
        _run_git_cmd(["fetch", "origin"], timeout=3)
        
        # 3. 로컬 및 원격 해시 구하기
        local_hash = _run_git_cmd(["rev-parse", "HEAD"], timeout=3).stdout.strip()
        
        remote_hash = None
        try:
            remote_hash = _run_git_cmd(["rev-parse", "@{u}"], timeout=3).stdout.strip()
        except subprocess.CalledProcessError:
            try:
                remote_hash = _run_git_cmd(["rev-parse", "origin/main"], timeout=3).stdout.strip()
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
                        branch_name = _run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], timeout=3).stdout.strip()
                    except subprocess.CalledProcessError:
                        branch_name = "main"
                    
                    # git pull 실행 (타임아웃 10초 설정으로 행 방지)
                    try:
                        print(f"Applying updates from origin/{branch_name}...")
                        _run_git_cmd(["pull", "origin", branch_name], timeout=10)
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

