import os
import sys
import re

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
    if filename == "context.md":
        limit_korean = 1500
        limit_english = 300
        
        if korean_cnt > limit_korean:
            diff = korean_cnt - limit_korean
            pct = (diff / limit_korean) * 100
            print(f"[FAIL] 'context.md' exceeds Korean character limit!")
            print(f"       - Limit: {limit_korean} chars, Current: {korean_cnt} chars ({pct:.1f}% exceeded)")
            print(f"       - Recommendation: Please trim the text by at least {diff} Korean characters.")
            is_valid = False
        else:
            print(f"[PASS] Korean character count within limit ({korean_cnt}/{limit_korean})")
            
        if english_cnt > limit_english:
            diff = english_cnt - limit_english
            pct = (diff / limit_english) * 100
            print(f"[FAIL] 'context.md' exceeds English word limit!")
            print(f"       - Limit: {limit_english} words, Current: {english_cnt} words ({pct:.1f}% exceeded)")
            print(f"       - Recommendation: Please trim the text by at least {diff} English words.")
            is_valid = False
        else:
            print(f"[PASS] English word count within limit ({english_cnt}/{limit_english})")
            
    elif filename == "brief.md":
        limit_korean = 1200
        limit_english = 240
        
        if korean_cnt > limit_korean:
            diff = korean_cnt - limit_korean
            pct = (diff / limit_korean) * 100
            print(f"[FAIL] 'brief.md' exceeds Korean character limit!")
            print(f"       - Limit: {limit_korean} chars, Current: {korean_cnt} chars ({pct:.1f}% exceeded)")
            print(f"       - Recommendation: Please trim the text by at least {diff} Korean characters.")
            is_valid = False
        else:
            print(f"[PASS] Korean character count within limit ({korean_cnt}/{limit_korean})")
            
        if english_cnt > limit_english:
            diff = english_cnt - limit_english
            pct = (diff / limit_english) * 100
            print(f"[FAIL] 'brief.md' exceeds English word limit!")
            print(f"       - Limit: {limit_english} words, Current: {english_cnt} words ({pct:.1f}% exceeded)")
            print(f"       - Recommendation: Please trim the text by at least {diff} English words.")
            is_valid = False
        else:
            print(f"[PASS] English word count within limit ({english_cnt}/{limit_english})")
            
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
