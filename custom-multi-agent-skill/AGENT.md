# Claude Code Multi-Agent Orchestration Guide

본 프로젝트는 Antigravity 및 Claude Code 환경에서 파일 기반 협업 멀티에이전트 시스템을 구동하기 위한 커스텀 스킬 프로젝트입니다.

## 빌드 및 실행 커맨드 (Build & Run Commands)
* **스킬 검증 스크립트 실행**: `python3 scripts/validate_result.py <task_name> <file_path>`
* **새 작업 폴더 초기화**: `python3 scripts/init_task.py <task_name>`

## 에이전트 행동 지침 및 제약조건 (Agent Instructions & Rules)
* **상태 일관성**: 메모리 내의 변수나 상태는 신뢰하지 마십시오. 오직 `tasks/<task-name>/` 안의 `task.md`와 `context.md`, `log.md` 파일이 유일한 진실의 원천(Source of Truth)입니다.
* **이력 보존**: 작업 중 발생하는 모든 핵심 결정, 에이전트 호출, 검증 결과는 `log.md` 파일에 `[YYYY-MM-DD HH:MM] [TAG] 내용` 형식으로 **append-only** 방식으로만 기록해야 하며 기존 로그의 수정/삭제는 절대 금지됩니다.
* **컨텍스트 제한**: `context.md` 파일은 한글 1500자 이하, `brief.md` 파일은 1200자 이하로 관리되어야 합니다. 이 제한은 LLM의 주의력 낭비를 방지하기 위한 절대 제약사항입니다.
* **예산 인터랙티브 제어**: 워커 실행 시 예산 한도 도달로 스크립트가 `Exit Code 2`로 종료되면, 임의로 작업을 중단하지 말고 즉시 사용자에게 추가 과금 증액을 허락받기 위한 승인 질문을 채팅에 띄우십시오. 사용자가 승인하면 `cost_tracker.json`의 `budget_limit`을 직접 편집해 작업을 이어나가십시오.
* **외과수술식 수정**: 코드 수정 시 변경을 최소화하고 의도한 기능에 집중하며, 인접한 서식이나 무관한 코드를 임의로 편집하지 마십시오.
