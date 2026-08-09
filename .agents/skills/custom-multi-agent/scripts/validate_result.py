import os
import sys

# Windows cp949 인코딩 크래시 및 한글 깨짐 방지 (다른 스크립트와 동일 처리)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import re
import json

def count_korean_characters(text):
    # 한글 글자만 필터링하여 카운트
    korean_chars = re.findall(r'[ㄱ-ㅎㅏ-ㅣ가-힣]', text)
    return len(korean_chars)

def count_english_words(text):
    # 영어 단어 패턴 추출
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    return len(words)

_FENCE_RE = re.compile(r'^\s*```')

def strip_code_blocks(text):
    """
    마크다운 코드펜스 내부를 제거한다.
    result.md/critic_report.md는 코드(diff 포함)를 담는 것이 정상이므로,
    산문 분량만 한도로 통제하고 코드는 별도의 바이트 한도로 통제한다.
    """
    out = []
    in_code = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_code = not in_code
            continue
        if not in_code:
            out.append(line)
    return "".join(out)

def resolve_rule_key(filename, rules):
    """
    result_v2.md, critic_report_fix2.md 처럼 접미사가 붙은 파일도
    같은 규칙을 적용받도록 접두사 매칭을 지원한다.
    """
    if filename in rules:
        return filename
    stem = filename[:-3] if filename.endswith(".md") else filename
    for key in rules:
        key_stem = key[:-3] if key.endswith(".md") else key
        if stem.startswith(key_stem):
            return key
    return None

def validate_file(task_name, file_path):
    if not os.path.exists(file_path):
        print(f"Error: Target file does not exist at '{file_path}'")
        sys.exit(1)

    filename = os.path.basename(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 규칙 로드 (카운트 방식이 규칙에 따라 달라지므로 먼저 읽는다)
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
    rule_key = resolve_rule_key(filename, rules)
    file_rules = rules.get(rule_key, {}) if rule_key else {}

    total_len = len(content)
    total_bytes = len(content.encode("utf-8"))
    counted = strip_code_blocks(content) if file_rules.get("exclude_code_blocks") else content
    korean_cnt = count_korean_characters(counted)
    english_cnt = count_english_words(counted)

    print(f"=== Validation Report for '{filename}' in Task '{task_name}' ===")
    if rule_key and rule_key != filename:
        print(f"(Applying rule set of '{rule_key}')")
    print(f"Total Character Length: {total_len} ({total_bytes} bytes)")
    scope = " (code blocks excluded)" if file_rules.get("exclude_code_blocks") else ""
    print(f"Korean Character Count: {korean_cnt}{scope}")
    print(f"English Word Count: {english_cnt}{scope}\n")

    # 1) 글자수 및 영어 단어수 한도 검사 (validate_rules.json 기반)
    if file_rules:
        limit_korean = file_rules.get("limit_korean")
        limit_english = file_rules.get("limit_english")
        limit_bytes = file_rules.get("limit_bytes")

        if limit_bytes is not None:
            if total_bytes > limit_bytes:
                diff = total_bytes - limit_bytes
                pct = (diff / limit_bytes) * 100
                print(f"[FAIL] '{filename}' exceeds total byte limit!")
                print(f"       - Limit: {limit_bytes} bytes, Current: {total_bytes} bytes ({pct:.1f}% exceeded)")
                print(f"       - Recommendation: 전체 파일 재출력 대신 diff/앵커 패치로 전환하고 {diff} bytes 이상 줄이십시오.")
                is_valid = False
            else:
                print(f"[PASS] Total size within limit ({total_bytes}/{limit_bytes} bytes)")

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
            result = subprocess.run(["node", "--check", file_path], capture_output=True, text=True, check=True)
            print("[PASS] JavaScript syntax validation check passed (via node --check)!")
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
