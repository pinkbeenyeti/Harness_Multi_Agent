import os
import sys

# Windows cp949 인코딩 크래시 방지
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import time
import re
from datetime import datetime

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_display_width(text):
    width = 0
    for char in text:
        # 한글 및 한글 자모 범위 (Unicode block)
        if 0x1100 <= ord(char) <= 0x11FF or 0xAC00 <= ord(char) <= 0xD7A3 or 0x3130 <= ord(char) <= 0x318F:
            width += 2
        else:
            width += 1
    return width

def pad_string(text, target_width):
    # ANSI 이스케이프 시퀀스 제거 후 폭 계산
    clean_text = re.sub(r'\033\[[0-9;]*m', '', text)
    current_width = get_display_width(clean_text)
    
    if current_width >= target_width:
        trimmed = ""
        temp_width = 0
        for char in text:
            # ANSI 이스케이프 문자는 그대로 포함하고 폭 계산에서 제외
            if char == '\033':
                # 단순 이스케이프 시퀀스 스킵
                pass
            char_w = 2 if (0x1100 <= ord(char) <= 0x11FF or 0xAC00 <= ord(char) <= 0xD7A3 or 0x3130 <= ord(char) <= 0x318F) else 1
            if temp_width + char_w > target_width - 3:
                trimmed += "..."
                temp_width += 3
                break
            trimmed += char
            temp_width += char_w
        return trimmed + " " * (target_width - temp_width)
    return text + " " * (target_width - current_width)

def parse_task_md(task_md_path):
    status = "unknown"
    goal = ""
    if not os.path.exists(task_md_path):
        return status, goal
    
    try:
        with open(task_md_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("* **Status**:"):
                status = line_str.replace("* **Status**:", "").strip()
        
        in_goal = False
        goal_lines = []
        for line in lines:
            if line.strip().startswith("## Goal"):
                in_goal = True
                continue
            if in_goal:
                if line.strip().startswith("##"):
                    break
                if line.strip().startswith("*"):
                    goal_lines.append(line.strip()[1:].strip())
        if goal_lines:
            goal = goal_lines[0]
            
    except Exception as e:
        status = f"Error: {e}"
        
    return status, goal

def read_last_logs(log_md_path, limit=10):
    logs = []
    if not os.path.exists(log_md_path):
        return logs
        
    try:
        with open(log_md_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        log_pattern = r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}'
        for line in lines:
            if re.match(log_pattern, line.strip()):
                logs.append(line.strip())
                
        return logs[-limit:]
    except Exception as e:
        return [f"Error reading logs: {e}"]

def main():
    if len(sys.argv) < 2:
        print("Usage: python monitor_task.py <task_name>")
        sys.exit(1)
        
    task_name = sys.argv[1]
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    task_dir = os.path.join(base_dir, "tasks", task_name)
    
    task_md_path = os.path.join(task_dir, "task.md")
    log_md_path = os.path.join(task_dir, "log.md")
    
    if not os.path.exists(task_dir):
        print(f"Error: Task '{task_name}' folder not found at '{task_dir}'")
        sys.exit(1)
        
    print(f"Starting real-time monitor for task '{task_name}'...")
    time.sleep(1)
    
    last_mtime_task = 0
    last_mtime_log = 0
    
    try:
        while True:
            mtime_task = os.path.getmtime(task_md_path) if os.path.exists(task_md_path) else 0
            mtime_log = os.path.getmtime(log_md_path) if os.path.exists(log_md_path) else 0
            
            if mtime_task != last_mtime_task or mtime_log != last_mtime_log:
                last_mtime_task = mtime_task
                last_mtime_log = mtime_log
                
                status, goal = parse_task_md(task_md_path)
                logs = read_last_logs(log_md_path)
                
                clear_screen()
                
                width = 80
                content_width = width - 6 # 좌우 마진 고려 74
                
                print("┌" + "─" * (width - 2) + "┐")
                print("│" + " MULTI-AGENT TASK MONITOR ".center(width - 2) + "│")
                print("├" + "─" * (width - 2) + "┤")
                
                # Status 색상 입히기
                status_color = f"\033[92m{status}\033[0m" if "completed" in status.lower() or "done" in status.lower() else f"\033[93m{status}\033[0m"
                
                # 패딩 맞추기
                status_line = f"  Status : {status_color}"
                status_pad = pad_string(status_line, content_width)
                print(f"│ {status_pad} │")
                
                goal_line = f"  Goal   : {goal}"
                goal_pad = pad_string(goal_line, content_width)
                print(f"│ {goal_pad} │")
                
                print("├" + "─" * (width - 2) + "┤")
                print("│" + " RECENT WORK LOGS (Last 10 entries):".ljust(width - 2) + "│")
                
                if logs:
                    for log in logs:
                        # 로그 표시 포맷 정리
                        log_line = f"  * {log}"
                        log_pad = pad_string(log_line, content_width)
                        print(f"│ {log_pad} │")
                else:
                    empty_pad = pad_string("  (No logs recorded yet)", content_width)
                    print(f"│ {empty_pad} │")
                    
                print("├" + "─" * (width - 2) + "┤")
                footer_pad = pad_string("  Press Ctrl+C to exit. Monitoring...", content_width)
                print(f"│ {footer_pad} │")
                print("└" + "─" * (width - 2) + "┘")
                
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        sys.exit(0)

if __name__ == "__main__":
    main()
