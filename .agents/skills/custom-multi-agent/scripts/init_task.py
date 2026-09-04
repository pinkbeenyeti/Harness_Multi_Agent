import os
import sys
import shutil
import json
from datetime import datetime

DEFAULT_APPROVAL_SCOPE = {
    "tier": 0,
    "paths": [],
    "groups": [],
    "routes": {},
    "auto_merge": False,
    "scope_hash": "",
}

def _load_cost_tracker_template(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Required cost tracker template not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as file:
            tracker = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid cost tracker template JSON: {exc}") from exc
    if not isinstance(tracker, dict):
        raise ValueError("Cost tracker template must be a JSON object")
    if tracker.get("approval_scope") != DEFAULT_APPROVAL_SCOPE:
        raise ValueError("Cost tracker template has invalid approval_scope")
    return tracker

def init_task(task_name):
    # 경로 설정
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates_dir = os.path.join(base_dir, "templates")
    tasks_dir = os.path.join(base_dir, "tasks")
    target_task_dir = os.path.join(tasks_dir, task_name)
    
    if os.path.exists(target_task_dir):
        print(f"Error: Task '{task_name}' already exists at {target_task_dir}")
        sys.exit(1)
        
    cost_tpl_path = os.path.join(templates_dir, "cost_tracker_template.json")
    expected_tracker = _load_cost_tracker_template(cost_tpl_path)

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
            
    # 4. cost_tracker.json 생성 (템플릿 파일 복사)
    cost_tracker_path = os.path.join(target_task_dir, "cost_tracker.json")
    shutil.copy(cost_tpl_path, cost_tracker_path)
    with open(cost_tracker_path, "r", encoding="utf-8") as file:
        copied_tracker = json.load(file)
    if copied_tracker.get("approval_scope") != expected_tracker["approval_scope"]:
        raise RuntimeError(
            "Copied cost tracker did not preserve approval_scope")

    print(f"Successfully initialized multi-agent task '{task_name}' at {target_task_dir}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python init_task.py <task_name>")
        sys.exit(1)
        

    from common_utils import validate_task_name
    try:
        validate_task_name(sys.argv[1])
    except ValueError as e:
        print(f"[SECURITY] {e}")
        sys.exit(1)

    init_task(sys.argv[1])
