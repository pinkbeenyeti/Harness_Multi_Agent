# Task: [태스크명 적기]

## Meta
* **Status**: pending  # pending, in_progress, waiting_<role>, reviewing, done
* **Created**: [YYYY-MM-DD HH:MM]
* **Updated**: [YYYY-MM-DD HH:MM]
* **Priority**: normal
* **Orchestrator**: [CLI명 (예: agy-cli, codex-cli, claude-code)]
* **Execution Mode**: [cli-routed | api-routed | host-native]

## Goal
* [이 작업의 궁극적인 목표를 한 문장으로 기술하십시오.]

## Constraints
* [작업 수행 시 반드시 지켜야 할 기술적 제약사항 및 Do NOT 규칙을 적으십시오.]

## Acceptance Criteria
* [ ] [작업 완료 여부를 판정할 구체적인 성공 기준 1]
* [ ] [작업 완료 여부를 판정할 구체적인 성공 기준 2]

## Worker Plan
* **planned_workers**:
  * [예: planner (codex-cli / gpt-5.6-sol), implementer (claude-code / claude-sonnet-5), critic (agy-cli / gemini-3.5-flash)]
* **workers_approved**:
  # - worker: 워커 역할 (planner | implementer | critic-standard | critic-architecture)
  #   cli_or_provider: 실행 CLI 또는 프로바이더 (예: codex-cli, claude-code, agy-cli, anthropic)
  #   model: 대상 모델명 (예: gpt-5.6-sol, claude-sonnet-5)
  #   approved_at: 승인 시각
  #   purpose: 호출 목적
  #   approved_by: user

## Planner Findings & Tradeoffs
* [Tier 2 작업 시 planner 워커의 design_spec.md 산출물 링크 및 주요 결정사항을 기록하십시오. (Tier 1은 선택 항목)]

## Rationale & Sources
* [태스크를 수행할 때 근거가 되는 코드 라인 링크 또는 지식/가이드라인 문서 경로를 기재하십시오.]
