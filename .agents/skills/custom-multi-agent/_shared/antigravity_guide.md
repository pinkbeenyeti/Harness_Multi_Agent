# 워커 실행 모드(execution_mode) 및 예산 운용 가이드

> 워커를 실제로 기동하기 직전에만 읽는다.

## execution_mode 3종 (`tasks/<task>/cost_tracker.json`)

| 값 | 의미 | 호출 경로 | 회계 |
|---|---|---|---|
| `api-routed` (기본) | `backends.json`의 역할별 이종 API 워커를 `call_worker.py`로 호출 | `call_anthropic`/`call_openai`/`call_google`(헤더 인증) | `usd_cost`: 호출 직전 worst-case reserve → 실비용 정산 |
| `cli-routed` | 로컬 CLI(`claude`/`codex`/`agy`)를 비대화형 모드로 서브프로세스 실행 | allowlist 통과 argv만 `subprocess.run`(`shell=True` 금지) | `cli_quota.history`: `{timestamp, cli, role, model, exit_code}` — **USD 필드 없음** |
| `host-native` | Antigravity SDK로 플랫폼 서브에이전트 구동(무료 쿼터) 또는 SDK 부재 시 오케스트레이터 위임 | 판단 기준 아래 참조 | 구독 쿼터 소모. `collect_metrics.py`로 수동 기록 |

**`usd_cost`와 `cli_quota`는 어떤 합계·비교 로직에서도 더하지 않는다.** CLI/host-native 사용량을 `$0`이나 "무료"로 표시하지 않는다 — API 비용은 안 들지만 구독 쿼터를 실제로 소비한다.

## host-native 판단 기준과 exit code 5

- **분기와 무관한 공통 원칙**: SDK 가용 여부(아래 두 분기 중 어느 쪽이든) 서브에이전트는 프로젝트 파일을 읽을 수 있으나 직접 수정하지 않는다. 코딩 워커는 `result.md`, 비평 워커는 `critic_report.md`에 기록하며, 오케스트레이터가 두 문서를 검토한 뒤 프로젝트 본 소스에 반영한다.
- `call_worker.py`는 Antigravity SDK(`google.antigravity`) 임포트 가능 여부로 host-native 처리 가능성을 판단한다.
  - **임포트 가능**: 기존 `call_antigravity_sdk`로 직접 구동, 무료 쿼터로 기록.
  - **임포트 불가능**(예: 이 스킬처럼 Claude Code가 오케스트레이터인 세션): **exit code 5**로 종료하고 "call_worker.py로 처리 불가. 오케스트레이터가 Agent 도구로 별도 서브에이전트를 기동해 `result.md`/`critic_report.md`를 직접 작성해야 함"이라는 안내만 출력한다.
- 이 판단은 워커를 실제로 불러본 뒤가 아니라 **브리프 작성 단계에서 오케스트레이터가 미리 알고 있어야 한다** — 지금 이 문서를 읽는 시점이 그 시점이다. Claude Code 세션에서 host-native 역할을 브리프하려면 애초에 `call_worker.py`를 거치지 말고 Agent 도구로 직접 서브에이전트를 기동한다.
- **host-native 비평의 격리 요건**: 구현과 동일한 대화/컨텍스트에서의 자기검사는 유효한 비평으로 인정하지 않는다. 반드시 별도 Agent 실행 + 별도 `result_<그룹>.md`/`critic_report_<그룹>.md` 산출물이어야 한다(`_shared/worker_system_prompt.md` 비평 워커 프롬프트 참조).

### `agy` CLI(cli-routed) vs `google.antigravity` SDK(host-native) — 별개 컴포넌트

`agy`는 PATH에 설치된 독립 실행 파일(`agy.exe`/`agy`, Antigravity CLI)이며 `cli-routed` 모드에서 `subprocess.run`으로 호출하는 대상이다. 이는 `call_antigravity_sdk()`가 임포트를 시도하는 `google.antigravity` **Python SDK**(host-native 전용, Antigravity 앱 내부에서만 임포트 가능)와는 **서로 다른 컴포넌트**다. SDK는 이 Claude Code 세션에서 여전히 임포트 불가로 exit code 5로 막히지만, `agy` CLI는 이 세션에서 실측으로 인증·호출이 확인됐다(`agy models`로 원격 인증 상태 조회 성공, `agy --model ... --mode plan --output-format json -p "..."`로 실제 응답 수신 성공). **"Antigravity를 쓴다"는 말이 곧 host-native를 의미하지 않는다** — `cli-routed`로 `agy` CLI를 호출하는 것도 Antigravity를 활용하는 방법이며, `backends.json`의 `critic-standard`/`critic-architecture`/`fallback-efficient` cli-routed route가 정확히 이 방식(`"cli": "agy"`)을 쓴다.

## 레거시 `worker_mode` 필드 (읽기 전용 별칭)

과거 53개 태스크(2026-09-03 실측, 본 태스크 제외)의 `cost_tracker.json`은 `execution_mode`가 아닌 `worker_mode`(`multi-api`/`gemini-only`/`antigravity`)를 쓴다. 이 파일들은 **절대 신규 스키마로 마이그레이션하지 않는다.**

세 값 모두 읽힐 때 별칭 매핑 공지 로그를 남긴다(원본 파일은 불변경). `antigravity`는 추가로 `[WARN]` 레벨 로그가 붙는다 — host-native로의 매핑이 무료 쿼터 소비·계측 공백 등 사용자가 주의해야 할 동작 변화를 수반하기 때문이다.

| 구 `worker_mode` | 신 `execution_mode` 해석 | 로그 |
|---|---|---|
| `multi-api` | `api-routed` | 공지 로그 |
| `gemini-only` | `api-routed` + Google allowlist 강제 | 공지 로그 |
| `antigravity` | `host-native` | 공지 로그 + `[WARN]` |

읽는 시점에만 인메모리로 별칭 해석하고, 해당 태스크에 대한 쓰기는 기존 필드(`accumulated_cost` 등)로 계속한다.

## 보안 유의사항

- **CLI allowlist**: `cli-routed` 서브프로세스는 사전 정의된 argv 패턴만 허용한다(`cli_allowlist_check()`). `shell=True`는 어떤 경우에도 쓰지 않는다.
- **Google 헤더 인증**: Gemini API 키는 URL 쿼리 파라미터가 아니라 `x-goog-api-key` HTTP 헤더로 전달한다. 쿼리 파라미터 방식은 로그·프록시에 키가 노출될 수 있어 금지한다.
- **`task_name` 경로 검증**: 사용자 입력(`task_name` 등)으로 파일 경로를 구성하는 모든 지점에서 `..`, 절대경로, 경로 구분자 포함 여부를 차단한다.
- **fallback 안전장치**: `route_history`에 이미 실패한 `route_id`(`<role>@<execution_mode>`)를 그대로 재사용하지 않는다. fallback은 1홉으로 제한하고, fallback을 거치며 위험도(effort)를 하향하지 않는다.

## 일괄 승인과 effort 적용

Tier 1/2는 실행 전에 경로·그룹·route·model·effort·예산·자동 병합 범위를
한 번에 승인한다. 승인 범위 안의 planner, implementer, critic 및 첫 FAIL
후 교정에는 추가 승인을 요구하지 않는다.

CLI 호출은 model과 effort를 별도 인자로 전달하고 `backends.json`의
`allowed_efforts` 밖 값은 호출 전에 거부한다. 승인된 route 또는
`scope_hash`가 달라지면 exit code 8로 중단한다.

`worker_runs`에는 성공·실패·절단을 모두 기록한다. API 예산 소진은
`BUDGET_EXHAUSTED`, CLI/SDK 장애는 실패 run으로 기록하며 CLI 사용량을
USD `$0`으로 표현하지 않는다.

## 예산 초과 처리

- `api-routed`에서 `call_worker.py`가 **Exit Code 2**로 종료되면 즉시 사용자에게 "예산 초과로 중단되었습니다. 예산을 늘려 진행할까요?"라고 질문한다.
- 승인 시 `cost_tracker.json`의 `budget_limit`을 수동 증액한 뒤 재시도한다.
- 폴백 역할(`fallback-efficient`)이 남아 있으면 스크립트가 자동 전환하므로 별도 조치가 필요 없다. 단, 위 fallback 안전장치(동일 route_id·순환·위험도 하향 금지)는 자동 전환에도 적용된다.
- `cli-routed`/`host-native`는 USD 예산 개념이 없으므로 Exit Code 2가 발생하지 않는다. 쿼터 소진은 CLI/SDK 자체 오류로 나타나며, 이 경우도 `fallback-efficient`로 1홉 대체를 시도한 뒤 안 되면 사용자에게 에스컬레이션한다.

## 워커 브리프에 반드시 포함할 것

- 대상 파일 경로와 **수정 라인 범위** (워커가 프로젝트를 처음부터 재탐색하지 않도록)
- In Scope / Out of Scope
- 성능 핫스팟 여부
- 접촉하는 인터페이스 정의

전달하지 말 것: 오케스트레이터의 대화 히스토리, 이전 태스크들의 누적 요약, 브리프 본문을 프롬프트에 붙여넣기(경로만 전달).
