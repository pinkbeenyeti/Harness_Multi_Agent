# Harness Multi Agent

파일 기반 오케스트레이터-워커 멀티에이전트 시스템을 실행하고 제어하는 개발 스킬입니다.
AI 에이전트가 수행하는 코딩 작업을 Tier 분류 → 승인 → 워커 실행 → 비평 → 병합 순서로 관리합니다.

## 아키텍처

```
사용자 요청
  → Tier 판정 (0: 질의 / 1: 국소 변경 / 2: 구조 변경)
  → 작업 폴더 초기화 (init_task.py)
  → 워커 실행 — API·CLI·SDK (call_worker.py)
  → 결과·비평 보고서 검증 (validate_result.py)
  → 비용·소요시간 집계 (collect_metrics.py)
  → 진행 상태 모니터링 (monitor_task.py)
  → [선택] 지식 위키 관리 (knot_manager.py)
```

## 설치

### 요구사항

- **Python 3.11+**
- 오케스트레이터 CLI: [Antigravity CLI (`agy`)](https://cloud.google.com/antigravity) 권장
- 워커 CLI (선택): `claude`, `codex` — 구독 활성 상태여야 합니다

### 환경 구성

```bash
# 1. 가상환경 생성 및 활성화
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 2. 기본 설치 (표준 라이브러리만 사용, 추가 패키지 불필요)
pip install -e .

# 3. host-native 모드 사용 시 (Antigravity SDK)
pip install -e ".[sdk]"

# 4. 개발·테스트 환경
pip install -e ".[dev]"
```

### API 키 설정

프로젝트 루트에 `api_keys.json`을 생성합니다 (`.gitignore`로 제외됨):

```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "OPENAI_API_KEY": "sk-...",
  "GEMINI_API_KEY": "AI..."
}
```

> **주의**: `api-routed` 모드에서만 필요합니다. `cli-routed` 모드는 각 CLI의 자체 인증을 사용합니다.

## 실행 모드 (execution_mode)

| 모드 | 워커 호출 방식 | 비용 | 설정 |
|---|---|---|---|
| `api-routed` (기본) | HTTP API 직접 호출 | **USD 과금** | `api_keys.json` 필요 |
| `cli-routed` | 로컬 CLI subprocess | **구독 쿼터** ($0) | `claude`/`codex`/`agy` CLI 설치 필요 |
| `host-native` | Antigravity SDK 에이전트 | **구독 쿼터** | `pip install google-antigravity` |

태스크별 실행 모드는 `tasks/<task>/cost_tracker.json`의 `execution_mode` 필드로 설정합니다.

## 스킬 사용법

### Antigravity CLI (권장)

`.agents/skills/custom-multi-agent/` 경로에 스킬이 위치하면 `agy`가 자동으로 발견합니다.

```bash
agy  # 스킬이 자동 로드되어 오케스트레이터로 동작
```

### Claude Code

Junction 링크를 통해 스킬을 로드합니다:

```powershell
# Windows — Junction 생성 (관리자 권한)
mklink /J .agents\skills\custom-multi-agent C:\Users\<user>\.gemini\config\skills\custom-multi-agent
```

## 스크립트

| 용도 | 명령 |
|---|---|
| 새 작업 초기화 | `python scripts/init_task.py <task-name>` |
| 결과 검증 | `python scripts/validate_result.py <task-name> <file>` |
| 비용·소요시간 계측 | `python scripts/collect_metrics.py <task-name> [--strict]` |
| 워커 실행 | `python scripts/call_worker.py <role> <brief> <result>` |
| 실시간 모니터링 | `python scripts/monitor_task.py <task-name>` |
| 지식 위키 관리 | `python scripts/knot_manager.py <save\|ingest\|lint> [args]` |

> **참고**: 스크립트 경로는 스킬 디렉터리 기준입니다 (`.agents/skills/custom-multi-agent/scripts/`).

## 프로젝트 구조

```
.agents/
  AGENTS.md                          # 전역 에이전트 규칙
  skills/
    custom-multi-agent/              # 핵심 스킬
      SKILL.md                       # Tier 게이트·작업 흐름·절대 규칙
      backends.json                  # 역할별 모델·CLI 라우팅 설정
      validate_rules.json            # 산출물 분량 검증 기준
      scripts/                       # Python 자동화 스크립트 (7개)
      templates/                     # 작업 문서 템플릿 (6개)
      _shared/                       # 상세 규칙 문서 (8개)
    structural-sop/                  # 구조적 사고 SOP 스킬
```

## 역할별 모델 배정 (backends.json)

| 역할 | api-routed 모델 | cli-routed CLI |
|---|---|---|
| `planner` (설계) | GPT-5.6 Sol | `codex` |
| `implementer` (코딩) | Claude Sonnet 5 | `claude` |
| `critic-standard` (표준 비평) | Gemini 3.5 Flash | `agy` |
| `critic-architecture` (구조 비평) | Gemini 3.1 Pro | `agy` |
| `fallback-efficient` (폴백) | Gemini 3.5 Flash | `agy` |

## 라이선스

MIT