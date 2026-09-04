import os
import sys
import shutil
from datetime import datetime

def init_task(task_name):
    # 경로 설정
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates_dir = os.path.join(base_dir, "templates")
    tasks_dir = os.path.join(base_dir, "tasks")
    target_task_dir = os.path.join(tasks_dir, task_name)
    
    if os.path.exists(target_task_dir):
        print(f"Error: Task '{task_name}' already exists at {target_task_dir}")
        sys.exit(1)
        
    os.makedirs(target_task_dir, exist_ok=True)
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1. task.md 생성 및 값 대체
    task_tpl_path = os.path.join(templates_dir, "task_template.md")
    task_out_path = os.path.join(target_task_dir, "task.md")
    if os.path.exists(task_tpl_path):
        with open(task_tpl_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("[태스크명 적기]", task_name)
        content = content.replace("[YYYY-MM-DD HH:MM]", current_time)
        with open(task_out_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    # 2. context.md 생성
    context_tpl_path = os.path.join(templates_dir, "context_template.md")
    context_out_path = os.path.join(target_task_dir, "context.md")
    if os.path.exists(context_tpl_path):
        shutil.copy(context_tpl_path, context_out_path)
        
    # 3. log.md 생성 및 값 대체
    log_tpl_path = os.path.join(templates_dir, "log_template.md")
    log_out_path = os.path.join(target_task_dir, "log.md")
    if os.path.exists(log_tpl_path):
        with open(log_tpl_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("[YYYY-MM-DD HH:MM]", current_time)
        with open(log_out_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    # 4. cost_tracker.json 생성 (템플릿 파일 복사 — 스키마 이중관리 제거)
    cost_tpl_path = os.path.join(templates_dir, "cost_tracker_template.json")
    cost_tracker_path = os.path.join(target_task_dir, "cost_tracker.json")
    if os.path.exists(cost_tpl_path):
        shutil.copy(cost_tpl_path, cost_tracker_path)

    print(f"Successfully initialized multi-agent task '{task_name}' at {target_task_dir}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python init_task.py <task_name>")
        sys.exit(1)
        
    try:
        from common_utils import check_for_updates
        check_for_updates()
    except Exception as e:
        print(f"[Warning] Failed to import or run check_for_updates: {e}")

    from common_utils import validate_task_name
    try:
        validate_task_name(sys.argv[1])
    except ValueError as e:
        print(f"[SECURITY] {e}")
        sys.exit(1)

    init_task(sys.argv[1])
