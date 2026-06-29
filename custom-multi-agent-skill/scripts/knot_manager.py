import os
import sys
import shutil
import re
import json
import urllib.request
import urllib.error
import time
from pathlib import Path

def load_api_keys():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_paths = [
        os.path.join(base_dir, "api_keys.json"),
        os.path.join(os.path.dirname(base_dir), "api_keys.json"),
        os.path.join(os.getcwd(), "api_keys.json")
    ]
    for p in search_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    keys = json.load(f)
                for k, v in keys.items():
                    if v and not v.startswith("your-") and v != "your_actual_key_here":
                        os.environ[k] = v
                return True
            except Exception:
                pass
    return False

def get_wiki_prompt(body, filename):
    return f"""다음 문서는 지식 보관소(Wiki)에 등록할 외부 원본 문서입니다.
이 문서를 분석하여 지식 관리에 유용한 형태의 아름답고 정돈된 마크다운 위키 문서로 새로 작성해 주세요.

[요구사항]
1. 제목은 문서의 내용에 맞추어 명확하게 지어주세요.
2. 개요(1~2문장 요약), 주요 개념 및 상세 내용 요약, 그리고 연관 개념을 마크다운 포맷으로 작성해 주세요.
3. '연관 개념' 영역에는 문서 내 핵심 단어나 관련 개념을 위키링크 포맷인 [[개념명]] 형태로 작성해 주세요 (예: [[에이전트]], [[비용 통제]] 등).

---
원본 파일명: {filename}
원본 문서 내용:
{body}
---
"""

def call_llm_for_wiki(body, filename):
    load_api_keys()
    
    # 1. Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            prompt = get_wiki_prompt(body, filename)
            data = {"contents": [{"parts": [{"text": prompt}]}]}
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"[INGEST LLM WARNING] Gemini API call failed: {e}. Trying next provider...")

    # 2. OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json"
            }
            prompt = get_wiki_prompt(body, filename)
            data = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}]
            }
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[INGEST LLM WARNING] OpenAI API call failed: {e}. Trying next provider...")

    # 3. Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            prompt = get_wiki_prompt(body, filename)
            data = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]
            }
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["content"][0]["text"]
        except Exception as e:
            print(f"[INGEST LLM WARNING] Anthropic API call failed: {e}.")

    return None

# 설정 파일 및 환경 변수에서 Vault 경로를 읽어옵니다.
def get_vault_path():
    # 1. 환경변수 확인
    vault_env = os.environ.get("KNOT_VAULT")
    if vault_env:
        return Path(vault_env)
        
    # 2. ~/.config/knot/vault 파일 확인
    home_dir = Path.home()
    config_file = home_dir / ".config" / "knot" / "vault"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            path_str = f.read().strip()
            if path_str:
                return Path(path_str)
                
    # 3. 기본값으로 워크스페이스 내에 임시 vault 생성
    workspace_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default_vault = workspace_dir / "vault"
    return default_vault

def setup_vault(vault_path):
    inbox = vault_path / "inbox"
    wiki = vault_path / "wiki"
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(wiki, exist_ok=True)
    return inbox, wiki

# 1. SAVE: 원본 자료를 Inbox에 복사
def save_document(file_path):
    vault_path = get_vault_path()
    inbox, _ = setup_vault(vault_path)
    
    src = Path(file_path)
    if not src.exists():
        print(f"Error: Source file '{file_path}' does not exist.")
        sys.exit(1)
        
    dest = inbox / src.name
    shutil.copy(src, dest)
    print(f"[SAVE] Copied '{src.name}' into inbox: {dest}")

# 2. INGEST: Inbox의 자료를 wiki로 가공 및 Index 업데이트
def ingest_documents():
    vault_path = get_vault_path()
    inbox, wiki = setup_vault(vault_path)
    
    inbox_files = list(inbox.glob("*.*"))
    if not inbox_files:
        print("[INGEST] No new documents in inbox to process.")
        return
        
    print(f"[INGEST] Processing {len(inbox_files)} files from inbox...")
    
    for f in inbox_files:
        # 가공 및 컴파일 시뮬레이션:
        # 실제 환경에서는 AI가 문서를 요약 및 마크다운 위키 형식으로 변환하여 wiki/ 폴더에 넣음.
        # 여기서는 기본 마크다운 이식 및 텍스트 파일 복사로 가공을 수행합니다.
        dest_filename = f.stem + ".md"
        dest_path = wiki / dest_filename
        
        # 이미 마크다운인 경우 바로 복사, 아닌 경우 텍스트 변환 복사
        if f.suffix.lower() == ".md":
            shutil.copy(f, dest_path)
        else:
            with open(f, "r", encoding="utf-8", errors="ignore") as src_file:
                body = src_file.read()
                
            print(f"[INGEST] Processing '{f.name}' through LLM for summarization and wiki generation...")
            llm_result = call_llm_for_wiki(body, f.name)
            
            with open(dest_path, "w", encoding="utf-8") as dest_file:
                if llm_result:
                    dest_file.write(llm_result)
                    print(f"[INGEST] Successfully generated LLM summary for '{f.name}'")
                else:
                    dest_file.write(f"# {f.stem}\n\nConverted from source inbox file (LLM Fallback).\n\n```text\n{body}\n```\n")
                    print(f"[INGEST] Fallback to raw copy for '{f.name}' (No LLM response)")
                
        # 가공 완료 후 inbox 원본 삭제 (또는 아카이빙)
        os.remove(f)
        print(f"[INGEST] Converted & compiled '{f.name}' -> 'wiki/{dest_filename}'")
        
    # 인덱스 파일(index.md) 자동 갱신
    update_wiki_index(wiki)

def update_wiki_index(wiki_path):
    index_file = wiki_path / "index.md"
    wiki_files = list(wiki_path.glob("*.md"))
    
    links = []
    for f in wiki_files:
        if f.name == "index.md":
            continue
        # 위키 내부 위키링크[[문서명]] 포맷 지원
        links.append(f"- [[{f.stem}]]")
        
    index_content = "# Knowledge Base Index\n\n지식 그물(Knot)에 등록된 위키 문서 목록입니다.\n\n"
    index_content += "\n".join(sorted(links))
    
    with open(index_file, "w", encoding="utf-8") as f:
        f.write(index_content)
    print(f"[INDEX] Automatically updated central index: {index_file}")

# 3. LINT: 끊어진 링크([[Broken Link]])를 탐색하는 검사기
def lint_wiki():
    vault_path = get_vault_path()
    _, wiki = setup_vault(vault_path)
    
    wiki_files = list(wiki.glob("*.md"))
    valid_doc_names = {f.stem for f in wiki_files}
    broken_links_found = False
    
    print(f"[LINT] Analyzing {len(wiki_files)} wiki files for link integrity...")
    
    for f in wiki_files:
        with open(f, "r", encoding="utf-8") as file:
            content = file.read()
            
        # [[링크문서명]] 패턴 탐색
        links = re.findall(r'\[\[(.*?)\]\]', content)
        for link in links:
            # 파이프라인 문자 분할 지원 (예: [[링크|보여줄이름]])
            link_target = link.split('|')[0].strip()
            if link_target not in valid_doc_names:
                print(f"[LINT WARNING] Broken Link found in '{f.name}': [[{link}]] (Target document '{link_target}' not found)")
                broken_links_found = True
                
    if not broken_links_found:
        print("[LINT PASS] All links are healthy and consistent!")
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python knot_manager.py <save|ingest|lint> [args]")
        sys.exit(1)
        
    cmd = sys.argv[1].lower()
    if cmd == "save":
        if len(sys.argv) < 3:
            print("Usage: python knot_manager.py save <file_path>")
            sys.exit(1)
        save_document(sys.argv[2])
    elif cmd == "ingest":
        ingest_documents()
    elif cmd == "lint":
        lint_wiki()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
