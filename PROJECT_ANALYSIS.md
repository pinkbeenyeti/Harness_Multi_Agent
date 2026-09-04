# Harness Multi Agent 프로젝트 분석 보고서

> 분석일: 2026-09-04  
> 범위: 현재 작업 트리, Git 이력, 추적된 코드와 로컬 실행 환경  
> 주의: API 키의 실제 값은 열람하거나 이 문서에 기록하지 않았다.

## 1. 결론

이 저장소는 일반 애플리케이션이 아니라, AI 작업을 파일 단위로 오케스트레이션하는 Python 기반 개발 스킬 프로젝트다. 워커 호출, 비평 게이트, 비용 추적, 작업 상태 모니터링, 지식 위키 관리를 하나의 파일 중심 워크플로로 제공한다.

설계 의도와 운영 규칙은 비교적 구체적이지만, 현재 환경에서는 Python 런타임이 깨져 스크립트를 실행할 수 없다. 또한 의존성 명세, 자동화 테스트, CI, 실질적인 사용자 문서가 없어 새 환경에서 재현하거나 변경을 신뢰성 있게 검증하기 어렵다.

## 2. 프로젝트의 목적과 구조

### 목적

AI 에이전트가 수행하는 개발 작업을 다음 순서로 관리한다.

```text
사용자 요청
  → Tier 판정·승인 절차 (SKILL.md)
  → 작업 폴더·템플릿 초기화 (init_task.py)
  → 외부 API 또는 CLI 워커 실행 (call_worker.py)
  → 결과·비평 보고서 검증 (validate_result.py)
  → 비용·라운드트립·준수 여부 집계 (collect_metrics.py)
  → 진행 상태 모니터링 (monitor_task.py)
  → 선택적으로 문서 위키화 (knot_manager.py)
```

### 핵심 파일

| 영역 | 경로 | 책임 |
|---|---|---|
| 운영 규칙 | `.agents/skills/custom-multi-agent/SKILL.md` | Tier 0~2 분류, 승인, 비평, 병합, 비용 규칙 |
| 워커 라우팅 | `backends.json` | 역할별 API/CLI 모델 및 CLI allowlist |
| 워커 실행 | `scripts/call_worker.py` | API·CLI·host-native 실행, 결과 저장, 비용 정산 |
| 공통 기능 | `scripts/common_utils.py` | 로그 파싱, 토큰 추정, 규칙/설정/API 키 로딩 |
| 검증 | `scripts/validate_result.py` | 산출물 분량·문법·비평 보고서 존재 검사 |
| 측정 | `scripts/collect_metrics.py` | 작업 산출물, 비용, 로그, 준수 지표 집계 |
| 위키 | `scripts/knot_manager.py` | 문서 Inbox, LLM 요약, 링크 인덱스/검사 |

## 3. 확인된 강점

- 작업 절차를 Markdown 파일과 템플릿에 남겨 승인·비평·비용 관련 감사 추적을 지원한다.
- CLI 실행은 allowlist 검증과 `shell=False` 방식의 서브프로세스 호출을 사용한다.
- 병렬 워커 시 `result_<group>.md`와 `critic_report_<group>.md`의 쌍을 검사해 그룹별 비평 누락을 방지한다.
- API 비용과 CLI 사용 이력을 분리하여 예산 관리 책임을 명확히 했다.
- 작업 상태와 비용 파일 갱신에 파일 잠금을 사용해 일반적인 동시 쓰기 충돌을 줄이려 한다.
- Git 이력상 최근에는 Gemini CLI 의존을 제거하고 `agy` CLI로 라우팅을 표준화했다.
- Git 객체 무결성 검사에서 오류가 발견되지 않았다.

## 4. 주요 문제와 위험도

| 우선순위 | 항목 | 영향 |
|---|---|---|
| 높음 | Python 가상환경의 기반 인터프리터가 유효하지 않음 | 모든 Python 스크립트 실행과 검증이 불가 |
| 높음 | 테스트·CI·의존성 명세 없음 | 변경 안전성 및 신규 환경 재현 불가 |
| 높음 | 위키 ingest가 원본을 외부 LLM으로 보내고 성공 후 삭제 | 민감정보 유출 및 원본 손실 가능 |
| 중간 | README가 제목 한 줄뿐임 | 설치·설정·실행 방법을 알 수 없음 |
| 중간 | 운영 실패 시나리오 테스트 부족 | API/CLI/잠금/파일 쓰기 실패 시 신뢰도 저하 |
| 낮음 | 작업 트리에 사용자 변경과 미추적 설정 존재 | 분석·릴리스 기준점이 다소 불명확 |

### 4.1 실행 환경이 깨져 있음

`.venv/pyvenv.cfg`는 Microsoft Store Python 경로를 기반 인터프리터로 가리키지만, 해당 실행 파일을 실행할 수 없다. 다음 모두 실패했다.

- `.venv\\Scripts\\python.exe --version`
- `py -3.11 --version`
- 모든 Python 스크립트의 AST 문법 파싱

따라서 소스 문법 오류 여부를 독립적으로 확정할 수 없으며, 현 상태에서는 `init_task.py`, `call_worker.py`, `validate_result.py`, `collect_metrics.py` 등을 실행할 수 없다.

### 4.2 재현 가능한 개발 환경이 없음

저장소에는 다음 항목이 없다.

- `pyproject.toml`, `requirements.txt`, lock 파일
- 테스트 디렉터리 및 테스트 러너 설정
- GitHub Actions 등 CI 설정
- 설치/실행용 Makefile 또는 스크립트
- 목적, 설치, 설정, 실행 방법을 담은 README

현재 의존성은 로컬 `.venv`에만 존재한다. 이 환경이 깨지면 복구 기준도 저장소에는 남아 있지 않다. `google-antigravity` 같은 선택적 런타임 의존성도 코드에는 나타나지만 설치 조건이 문서화되어 있지 않다.

### 4.3 Knot 위키 ingest는 보안·보존 위험이 큼

`knot_manager.py ingest`는 Inbox의 비-Markdown 파일 본문을 외부 LLM API에 전달한다. 파일 확장자 allowlist, 파일 크기 제한, 민감도 분류, 전송 전 사용자 확인이 없다.

처리가 성공하면 원본 Inbox 파일은 `os.remove()`로 삭제된다. 또한 대상 파일명은 `wiki/<원본 stem>.md`이므로 동일 stem의 문서가 기존 파일을 덮어쓸 수 있다.

권장 조치:

1. 기본 동작을 삭제가 아닌 archive 이동 또는 원본 유지로 변경한다.
2. 외부 전송 전에 명시적 확인, 확장자 allowlist, 크기 제한을 둔다.
3. 동일 이름 충돌 시 버전 또는 고유 파일명을 사용한다.
4. API 실패·대상 쓰기 실패·인덱스 실패 때 원본이 보존되는 자동 테스트를 추가한다.

### 4.4 검증 범위가 제한적임

`validate_result.py`는 대상 파일의 분량, 일부 파일 유형의 문법, 비평 보고서 존재 여부를 검사한다. 그러나 다음은 검증하지 않는다.

- API 응답 스키마와 모델별 파라미터의 호환성
- Claude, Codex, agy CLI의 설치 여부·버전·JSON 출력 호환성
- 동시 워커 실행 시 비용 예산과 파일 잠금 경쟁 조건
- 네트워크 타임아웃, 중간 쓰기 실패, 잘린 응답의 복구 절차
- 위키 ingest 뒤 원본·인덱스·링크의 무결성

파일 잠금은 30초 이상 지난 잠금 파일을 stale로 간주해 삭제한다. 일반적인 쓰기 작업에는 충분할 수 있으나, 느린 파일 시스템 또는 지연된 프로세스에서는 살아 있는 작업의 잠금을 제거할 위험이 있다.

## 5. 코드 품질 관찰

- Python 구현은 약 111KB이며, `call_worker.py`와 `collect_metrics.py`가 가장 큰 비중을 차지한다.
- `call_worker.py`는 provider API 호출, CLI 호출, 상태 변경, 잠금, 비용 정산을 함께 담당한다. 현재 규모에서는 동작할 수 있으나, 변경과 테스트 난도가 계속 높아질 구조다.
- `common_utils.py`도 로그 파싱, 분량 검사, 설정, API 키, Git 업데이트를 함께 담당하여 책임 경계가 넓다.
- `api_keys.json`은 `.gitignore`로 제외되어 Git에 추적되지는 않는다. 다만 코드가 작업 디렉터리의 키 파일을 자동 탐색해 환경변수로 주입하므로, 로그·백업·동기화 서비스에 노출되지 않도록 운영 규칙이 필요하다.

## 6. Git 및 작업 트리 상태

- 원격 저장소: `https://github.com/pinkbeenyeti/Harness_Multi_Agent`
- 현재 브랜치: `main`
- HEAD: `d7ed6e5` — `fix(custom-multi-agent): drop gemini CLI routing, standardize on agy`
- 작업 트리에는 `.agents/skills/structural-sop/SKILL.md`의 미커밋 변경과 `.claude/` 미추적 항목이 있다.
- 위 항목은 사용자 작업일 수 있으므로 분석 과정에서 변경하지 않았다.

## 7. 권장 개선 순서

### 즉시

1. 정상 Python을 설치하고 `.venv`를 재생성한다.
2. `pyproject.toml` 또는 잠긴 `requirements.txt`를 추가한다.
3. README에 프로젝트 목적, 설치, API 키 관리, 실행 예시, execution mode별 요구사항을 기록한다.

### 단기

1. `pytest`를 도입해 설정 로딩, 로그 파싱, 비용 계산, 파일 잠금, 결과 검증 테스트를 만든다.
2. 위키 ingest를 비파괴 기본값으로 전환하고, 외부 전송 보호 장치를 추가한다.
3. CI에서 JSON 검증, Python 문법 검사, 단위 테스트를 실행한다.

### 중기

1. `call_worker.py`를 provider adapter, CLI runner, 비용 ledger, task state 모듈로 분리한다.
2. API/CLI별 헬스체크와 명확한 오류 코드를 추가한다.
3. 파일 잠금을 OS 수준 잠금 또는 원자적 파일 교체 전략으로 강화한다.
4. 비용·응답·재시도 정책을 테스트 가능한 순수 로직으로 분리한다.

## 8. 분석 한계

이 보고서는 소스와 설정을 읽고 Git 상태를 점검한 결과다. 깨진 Python 런타임 때문에 실제 워커 호출, 단위 테스트, 문법 검증은 수행하지 못했다. 외부 모델 이름 및 API/CLI의 현재 호환성도 네트워크 호출 없이 검증하지 않았다.
