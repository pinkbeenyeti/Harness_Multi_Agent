import os
import sys
import re
import json

def count_korean_characters(text):
    # 한글 글자만 필터링하여 카운트
    korean_chars = re.findall(r'[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]', text)
    return len(korean_chars)

def count_english_words(text):
    # 영어 단어 패턴 추출
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    return len(words)

def validate_file(task_name, file_path):
    if not os.path.exists(file_path):
        print(f"Error: Target file does not exist at '{file_path}'")
        sys.exit(1)
        
    filename = os.path.basename(file_path)
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    total_len = len(content)
    korean_cnt = count_korean_characters(content)
    # 한글 및 공백, 기호 포함 대략적인 한글 텍스트 문자 수 계산
    # 엄밀하게는 한글 글자 수만 혹은 전체 글자 수로 판별 가능
    english_cnt = count_english_words(content)
    
    print(f"=== Validation Report for '{filename}' in Task '{task_name}' ===")
    print(f"Total Character Length: {total_len}")
    print(f"Korean Character Count: {korean_cnt}")
    print(f"English Word Count: {english_cnt}\n")
    
    # 규칙 검증
    is_valid = True
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules_path = os.path.join(base_dir, "validate_rules.json")
    
    if not os.path.exists(rules_path):
        print(f"Error: validate_rules.json not found at '{rules_path}'")
        sys.exit(1)
        
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_config = json.load(f)
    except Exception as e:
        print(f"Error: Failed to parse validate_rules.json: {e}")
        sys.exit(1)
        
    rules = rules_config.get("rules", {})
    
    # 1) 글자수 및 영어 단어수 한도 검사 (validate_rules.json 기반)
    if filename in rules:
        file_rules = rules[filename]
        limit_korean = file_rules.get("limit_korean")
        limit_english = file_rules.get("limit_english")
        
        if limit_korean is not None:
            if korean_cnt > limit_korean:
                diff = korean_cnt - limit_korean
                pct = (diff / limit_korean) * 100
                print(f"[FAIL] '{filename}' exceeds Korean character limit!")
                print(f"       - Limit: {limit_korean} chars, Current: {korean_cnt} chars ({pct:.1f}% exceeded)")
                print(f"       - Recommendation: Please trim the text by at least {diff} Korean characters.")
                is_valid = False
            else:
                print(f"[PASS] Korean character count within limit ({korean_cnt}/{limit_korean})")
                
        if limit_english is not None:
            if english_cnt > limit_english:
                diff = english_cnt - limit_english
                pct = (diff / limit_english) * 100
                print(f"[FAIL] '{filename}' exceeds English word limit!")
                print(f"       - Limit: {limit_english} words, Current: {english_cnt} words ({pct:.1f}% exceeded)")
                print(f"       - Recommendation: Please trim the text by at least {diff} English words.")
                is_valid = False
            else:
                print(f"[PASS] English word count within limit ({english_cnt}/{limit_english})")

    # 2) 파일 형식별 구문 유효성 검사 (독립된 if 블록으로 분리)
    if filename.endswith(".py"):
        import ast
        try:
            ast.parse(content)
            print("[PASS] Python syntax validation check passed!")
        except SyntaxError as e:
            print(f"[FAIL] Python syntax validation check failed!")
            print(f"       - File: {filename}")
            print(f"       - Line: {e.lineno}")
            print(f"       - Offset: {e.offset}")
            print(f"       - Error: {e.msg}")
            print(f"       - Code: {e.text.strip() if e.text else ''}")
            is_valid = False
            
    elif filename.endswith(".json"):
        try:
            json.loads(content)
            print("[PASS] JSON syntax validation check passed!")
        except json.JSONDecodeError as e:
            print(f"[FAIL] JSON syntax validation check failed!")
            print(f"       - File: {filename}")
            print(f"       - Line: {e.lineno}")
            print(f"       - Col: {e.colno}")
            print(f"       - Error: {e.msg}")
            is_valid = False
            
    elif filename.endswith(".js"):
        import subprocess
        try:
            result = subprocess.run(["node", "-c", file_path], capture_output=True, text=True, check=True)
            print("[PASS] JavaScript syntax validation check passed (via node -c)!")
        except FileNotFoundError:
            print("[INFO] Node.js not found. Skipping deep JavaScript syntax validation.")
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] JavaScript syntax validation check failed!")
            print(f"       - File: {filename}")
            print(f"       - Error: {e.stderr.strip()}")
            is_valid = False

    # critic_report.md 존재 체크 게이트
    # 오직 워커의 최종 결과 제안서인 result.md 검증 시에만 critic_report.md가 실존하는지 확인
    if filename == "result.md":
        task_dir = os.path.dirname(file_path)
        critic_report_path = os.path.join(task_dir, "critic_report.md")
        if not os.path.exists(critic_report_path):
            print("[FAIL] critic_report.md가 존재하지 않습니다. 비평 워커를 먼저 실행하십시오.")
            is_valid = False
        else:
            print("[PASS] critic_report.md 존재 확인")

    if is_valid:
        print("[PASS] Verification checklist rules satisfied!")
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python validate_result.py <task_name> <file_path>")
        sys.exit(1)
    validate_file(sys.argv[1], sys.argv[2])
