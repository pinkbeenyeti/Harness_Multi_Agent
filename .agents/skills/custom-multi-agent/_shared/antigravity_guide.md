# 워커 실행 모드 및 예산 운용 가이드

> 워커를 실제로 기동하기 직전에만 읽는다.

## worker_mode 3종 (`tasks/<task>/cost_tracker.json`)

| 값 | 의미 |
|---|---|
| `multi-api` (기본) | `backends.json`의 역할별 이종 API 워커를 `call_worker.py`로 호출 |
| `gemini-only` | 모든 역할을 `gemini`로 강제 통일 (비용 절감) |
| `antigravity` | 외부 API 키 없이 플랫폼 서브에이전트로 구동 (무료 쿼터) |

API Key 오류가 감지되거나 비용 절감이 필요하면 오케스트레이터가 자율적으로 모드를 변경할 수 있다.

## 예산 초과 처리

- `call_worker.py`가 **Exit Code 2**로 종료되면 즉시 사용자에게 "예산 초과로 중단되었습니다. 예산을 늘려 진행할까요?"라고 질문한다.
- 승인 시 `cost_tracker.json`의 `budget_limit`을 수동 증액한 뒤 재시도한다.
- 폴백 역할(`fallback_role`)이 남아 있으면 스크립트가 자동 전환하므로 별도 조치가 필요 없다.

## Antigravity 모드 운용

- 이 모드에서는 `call_worker.py`를 거치지 않고 플랫폼 고유의 서브에이전트 도구(`invoke_subagent` / Agent)로 직접 워커를 기동한다.
- 서브에이전트 정의(`define_subagent`) 시 `_shared/worker_system_prompt.md`의 해당 역할 프롬프트를 시스템 프롬프트로 사용한다.
- 서브에이전트는 프로젝트 파일을 읽을 수 있으나 직접 수정하지 않는다. 코딩 워커는 `result.md`, 비평 워커는 `critic_report.md`에 기록한다.
- 오케스트레이터가 두 문서를 검토한 뒤 프로젝트 본 소스에 반영한다.

### ⚠ 이 모드의 계측 공백

`call_worker.py`를 우회하므로 `cost_tracker.json`의 `accumulated_cost`/`history`가 갱신되지 않는다.
즉 **예산 통제가 동작하지 않는다.** 따라서 antigravity 모드로 진행한 태스크는
완료 시 반드시 `python scripts/collect_metrics.py <task-name>`을 실행해
라운드트립·소요시간·산출물 토큰을 기록해야 한다.

## 워커 브리프에 반드시 포함할 것

- 대상 파일 경로와 **수정 라인 범위** (워커가 프로젝트를 처음부터 재탐색하지 않도록)
- In Scope / Out of Scope
- 성능 핫스팟 여부
- 접촉하는 인터페이스 정의

전달하지 말 것: 오케스트레이터의 대화 히스토리, 이전 태스크들의 누적 요약, 브리프 본문을 프롬프트에 붙여넣기(경로만 전달).
