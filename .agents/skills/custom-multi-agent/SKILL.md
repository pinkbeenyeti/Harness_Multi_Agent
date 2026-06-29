---
name: custom-multi-agent
description: 파일 기반 오케스트레이터-워커 멀티에이전트 시스템을 실행하고 제어하는 스킬입니다.
---

# Custom Multi-Agent Orchestration Skill

본 스킬은 Antigravity 또는 Claude Code 환경에서 파일 기반의 오케스트레이터-워커 구조를 사용하여 안전하고 투명하게 에이전트 작업을 진행하기 위한 규칙과 도구를 제공합니다.

## 1. 에이전트 행동 지침 (오케스트레이터 규칙)

당신은 이 시스템에서 **오케스트레이터(Orchestrator)** 역할을 수행합니다. 모든 작업은 파일 단위로 기록되며 아래의 워크플로우를 반드시 준수해야 합니다.

### 1.1 작업(Task) 생성 및 정박 규칙
* 새로운 태스크 요청이 들어오면, 직접 디렉토리를 생성하지 말고 `scripts/init_task.py` 도구를 호출하여 `tasks/<task-name>/` 구조와 기본 템플릿 파일들을 생성하십시오.
* 기존 작업을 이어받아 재진입하는 경우, 먼저 `tasks/<task-name>/` 폴더 내의 `task.md`, `context.md`, `log.md`를 읽어 현재 상태를 완전히 동기화(정박)한 후에만 행동을 개시하십시오.
* **실시간 진행 상태 표기 규칙**: 당신(오케스트레이터)이 새로운 단계(예: 계획 수립, 결과 검증, 코드 병합 등)를 시작할 때에는, 즉시 `task.md`의 `Status` 필드를 `in_progress (Gemini 3.5 Pro Orchestrator is planning...)` 등으로 업데이트하고, `log.md`에 `[DECISION]` 또는 `[VERIFICATION]` 등의 로그를 즉시 덧붙여 어떤 모델이 무엇을 하고 있는지 실시간으로 표시되도록 하십시오.

### 1.2 카파시 4원칙 준수
1. **추측 전 질문**: 요구사항이 모호하다면 사용자에게 질문하여 가정을 확인하십시오.
2. **단순함 우선**: 오버엔지니어링을 피하고 지시받은 내용만 정확하게 구현하십시오.
3. **외과수술식 변경**: 불필요한 코드 주변부를 수정하지 말고 변경이 필요한 라인만 정밀하게 타격하십시오.
4. **목표 기반 실행**: 작업이 끝난 후에는 반드시 검증(Verify) 체크리스트를 통과해야 합니다.

### 1.3 워커 호출 및 검증
* 오케스트레이터인 당신이 직접 복잡한 코드를 장시간 작성하는 대신, 별도의 서브에이전트(워커)를 활용하여 특정 분할 작업을 수행하십시오.
* 워커를 부르기 전에는 반드시 사용자에게 호출 승인을 받고 `task.md`의 `workers_approved` 목록에 승인 기록을 남겨야 합니다.
* 워커의 동작이 끝나면 `scripts/validate_result.py` 스크립트를 실행하여 결과를 검증하십시오. 검증 실패 시 출력되는 초과율(%) 및 트리밍 권장 수치를 참고하여 정밀하게 컨텍스트를 축소하십시오.
* **예산 및 토큰 통제 (worker_mode 활용)**:
  * 워커 호출 시 예산 초과로 인해 `call_worker.py`가 **Exit Code 2**로 종료되면, 즉시 사용자에게 "예산 초과로 중단되었습니다. 예산을 늘려 진행할까요?"라고 질문하십시오. 사용자가 승인하면 `tasks/<task-name>/cost_tracker.json`의 `budget_limit` 값을 직접 수동으로 증액 편집한 뒤 워커 호출을 재시도하십시오.
  * API Key 오류가 감지되거나 비용 절감이 필요한 상황이라면, `cost_tracker.json`의 `worker_mode`를 `"antigravity"` (무료 로컬 SDK 모드) 또는 `"gemini-only"` (구글 API 통일 모드)로 자율 변경하여 예산 문제를 우회할 수 있습니다. (기본값은 `"multi-api"` 입니다)

### 1.4 지식 그물 (Knot) 활용 규칙
* 여러 에이전트 간의 지식 공유가 필요하거나 사용자의 특정 프로젝트 문서를 학습해야 하는 경우 지식 그물(Knot) 시스템을 사용하십시오.
* 새로운 외부 문서는 `scripts/knot_manager.py save <경로>`와 `scripts/knot_manager.py ingest` 명령어를 통해 지식 위키(wiki/)로 가공해 보관하십시오.
* **Ingest 최적화**: Ingest 실행 시 시스템 내부에서 자율적으로 LLM 요약과 개념 연결(`[[키워드]]`)이 처리되므로, 사전에 본문을 수동 요약하느라 주의력을 낭비하지 마십시오.
* 위키 작성이 끝나면 주기적으로 `scripts/knot_manager.py lint`를 실행해 위키 내 링크 건강도(Broken Link 유무)를 모니터링하십시오.

---

## 2. 사용 가능한 스크립트 도구 (Scripts)

* **새 작업 초기화**: `python3 scripts/init_task.py <task-name>`
* **결과 유효성 검증 및 글자 수 체크**: `python3 scripts/validate_result.py <task-name> <brief_or_context_file>`
* **지식 그물 (Knot) 세컨드 브레인 관리**: `python3 scripts/knot_manager.py <save|ingest|lint> [args]`
* **이종 API 워커 연동 실행**: `python3 scripts/call_worker.py <worker_role> <brief_file_path> <result_file_path>`
