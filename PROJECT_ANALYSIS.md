# Harness Multi Agent 프로젝트 분석 보고서

> 분석일: 2026-09-04  
> 범위: 현재 작업 트리, Git 이력, 추적된 코드와 로컬 실행 환경  
> 주의: API 키의 실제 값은 열람하거나 이 문서에 기록하지 않았다.

## 1. 결론

이 저장소는 일반 애플리케이션이 아니라, AI 작업을 파일 단위로 오케스트레이션하는 Python 기반 개발 스킬 프로젝트다. 워커 호출, 비평 게이트, 비용 추적, 작업 상태 모니터링, 지식 위키 관리를 하나의 파일 중심 워크플로로 제공한다.

최근 커밋(`9e1100c`)과 추가 개선을 통해 `pyproject.toml`과 확장 README가 구축되었으며, Python 3.11 런타임에서 안정적으로 동작한다. 토큰 상한 규제, 브리프 사전 차단, 교정 슬롯 제어, 승인 게이트, 원자적 예산 관리 등이 결합되어 파일 기반 멀티에이전트 제어 구조가 정착되었다.

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

| 우선순위 | 항목 | 영향 | 상태 |
|---|---|---|---|
| 완료 | 위키 ingest가 원본을 외부 LLM으로 보내고 성공 후 삭제 | 민감정보 유출 및 원본 손실 방지 (archive 이동/가드 구현) | **해결 완료** |
| 완료 | 자동화 단위 테스트 부재 | 20개 pytest 단위 테스트 구축 및 검증 완료 | **해결 완료** |
| 완료 | approval_scope 템플릿 부재 및 프롬프트 규칙 중복 | 기본 스키마 강제 및 worker_booster SSoT 일원화 | **해결 완료** |
| 중간 | 운영 실패 시나리오 테스트 확대 | 잠금 경합/파일 쓰기 실패 시 신뢰도 심층 검증 | 진행 중 |
| 완료 | Python 인터프리터 런타임 및 의존성 명세 | Python 3.11 검증 완료 및 `pyproject.toml` 구축 | **해결 완료** |
| 완료 | 설치 및 실행 안내 문서 | 확장된 `README.md` 작성 완료 | **해결 완료** |
| 완료 | 스킬 파일 Git 인덱스 추적 누락 (`H` 플래그) | `assume-unchanged` 해제하여 정상 추적 복구 | **해결 완료** |

### 4.1 실행 환경 및 패키지 명세 현황 (해결됨)

현재 환경은 Python 3.11 런타임이 정상 작동하며 모든 핵심 스크립트의 AST 문법 검사 및 `py_compile`이 성공한다. 또한 `pyproject.toml`과 상세 `README.md`가 추가되어 프로젝트의 목적, 설치 방법, CLI/API execution_mode 운용법이 문서화되었다.

### 4.2 자동화 단위 테스트 스위트 구축 (해결됨)

`tests/test_knot_manager.py`(8개 케이스) 및 `tests/test_templates.py`(12개 케이스)로 구성된 총 20개의 `pytest` 자동화 테스트 스위트가 구축되었다. Knot ingest 안전 가드(확장자/크기/민감도), archive 이동, 충돌 회피, 실패 복원 및 `cost_tracker_template.json`의 `approval_scope` 스키마 무결성이 상시 자동 검증된다.

### 4.3 Knot 위키 ingest 안전 가드 및 archive 보존 (해결됨)

`knot_manager.py`의 안전 트랜잭션이 전면 개정되었다.
1. 기존 `os.remove()` 삭제 로직을 전면 제거하고 `vault/archive/` 디렉터리로 원본을 이동 보존한다.
2. `ALLOWED_EXTENSIONS`({`.md`, `.txt`, `.json`, `.csv`}) 화이트리스트와 `MAX_FILE_SIZE`(1MB), `SENSITIVE_PATTERNS` 가드를 통해 부적격 및 보안 위험 파일을 사전 차단한다.
3. wiki 및 archive 대상 파일명 동명 충돌 시 `_1`, `_2` 순으로 넘버링하여 기존 데이터를 덮어쓰지 않는다.
4. 임시 파일 쓰기 또는 archive 이동 실패 시 원본은 inbox에 안전히 보존된다.

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
- HEAD: `9e1100c` — `feat(custom-multi-agent): streamline pipeline, runtime limit, and split timing metrics`
- 인덱스 상태: `.agents/skills/custom-multi-agent/` 추적 파일들의 `assume-unchanged(H)` 플래그 해제 완료.

## 7. 권장 개선 순서

### 즉시 (완료됨)
1. Python 3.11 런타임 및 `pyproject.toml` 구축 완료.
2. 상세 `README.md` 작성 및 execution_mode 가이드 추가 완료.
3. 태스크 생성 시 자동 업데이트 대화형 입력 제거 및 병렬 예산 원자적 잠금 처리 완료.

### 단기
1. `pytest`를 도입해 설정 로딩, 로그 파싱, 예산 원자적 예약, 승인 검증 자동화 테스트 구축.
2. 위키 ingest를 비파괴 기본값(삭제 대신 archive 이동)으로 전환하고 파일 크기 제한 추가.
3. GitHub Actions CI를 통해 JSON 스키마 검증 및 코드 린트 자동화.

### 중기
1. `call_worker.py`를 provider adapter, CLI runner, 비용 ledger, task state 모듈로 계층적 리팩터링.
2. API/CLI별 헬스체크 및 사전 검증 명령 추가.

## 8. 분석 한계

본 분석은 Python 3.11 환경에서 AST 문법 검사(`py_compile`) 및 스크립트 정적 점검을 마쳤다. 외부 유료 API 실호출 테스트는 예산 보호를 위해 모의(Mock) 범위 내에서 검증되었다.
