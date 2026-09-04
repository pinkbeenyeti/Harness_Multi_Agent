# Routing — Tier별 실행 모드 세부 지침

본 문서는 `SKILL.md` §0에서 판정한 **Tier**에 따라 역할별 모델·effort 배정과 비용 정책을 정하는 세부 규칙이다.
Tier 판정 자체는 여기서 하지 않는다. **판정은 SKILL.md §0의 4개 객관 기준으로만 한다.**

Tier 2는 위험도에 따라 **일반**과 **고위험**으로 다시 나뉜다: SKILL.md §0의 4번 기준(성능 핫스팟·되돌리기 어려운 작업)을 위반해 Tier 2가 된 경우 **고위험**, 1~3번 기준(파일 수·변경 라인·시그니처)만으로 Tier 2가 된 경우 **일반**이다. 이 구분도 SKILL.md §0의 판정 결과에서 기계적으로 도출되며 별도 주관적 판단을 두지 않는다.

역할명(`planner`/`implementer`/`critic-standard`/`critic-architecture`/`fallback-efficient`)과 실행 방식(`execution_mode`: api-routed/cli-routed/host-native)은 `_shared/antigravity_guide.md` 참조. 본 문서는 **Tier×위험도별로 어떤 역할을 어떤 effort로 쓰는지**만 고정한다.

---

## Tier 0 — 질의 / 조회 / 기획 (소스 변경 없음)

* **해당**: 개념 질문, 코드 설명, 파일 위치 확인, 브레인스토밍, 태스크 기획.
* **비용**: $0. 워커를 호출하지 않는다.
* **동작**: 오케스트레이터 자체 추론으로 즉시 답한다. `tasks/` 폴더를 만들지 않는다.
* **주의**: 소스를 한 줄이라도 바꾸면 Tier 0이 아니다.

## Tier 1 — 국소 변경 (파일 ≤2, 변경 ≤50줄, 시그니처 불변, 비핫스팟)

* **해당**: 단일 파일 버그 수정, 소규모 기능 추가, 유닛 테스트 작성, 간단한 스크립트.
* **원칙**: 외과수술식 수정 — 인접 코드를 보존하고 변경 라인만 정밀 타격한다.
* **고정 라우팅**:

  | 역할 | 모델 | effort |
  |---|---|---|
  | `implementer` | Claude Sonnet 5 | medium |
  | `critic-standard` | Gemini Flash | medium |

* **비평 범위**: `result.md`의 **패치(diff)와 적용 지점**에 한정한다. 패치가 닿지 않는 코드의 전수 감사는 하지 않는다.
* **검증**: `validate_result.py`로 `brief.md`(한글 1200자)와 `result.md`(15,000 bytes / 산문 한글 1200자)를 린트한다.

## Tier 2 — 구조 변경

* **해당**: 다중 파일 재설계, 디렉토리 이주(Migration), 광범위한 레거시 정리, 대규모 아키텍처 설계, 성능 핫스팟 변경, 되돌리기 어려운 작업.
* **원칙**: 외과수술식 제약을 일시 완화하고 구조적 리팩토링을 허용한다.

### 2-A. Tier 2 일반

  | 역할 | 모델 | effort |
  |---|---|---|
  | `planner` | GPT-5.6 Sol | high |
  | `implementer` | Claude Sonnet 5 | high |
  | `critic-architecture` | Gemini 3.1 Pro | medium~high |

### 2-B. Tier 2 고위험

  | 역할 | 모델 | effort |
  |---|---|---|
  | `planner` | GPT-5.6 Sol | xhigh |
  | `critic-standard` (계획비평) | Gemini Flash | high |
  | `implementer` | Claude Sonnet 5 | high |
  | `critic-architecture` (구조비평) | Gemini 3.1 Pro | high |

* **1단계 (계획 및 설계)**: 오케스트레이터가 직접 기획하거나 마일스톤을 확정하지 않는다. 오케스트레이터는 브리프를 작성하고 사용자 승인을 받은 뒤 **`planner` 워커**를 기동한다. `planner` 역할은 코드 수정 전 `structural-sop` 관점을 반영하여 본질적 병목과 위험을 선제 검증하고, **요구사항 분석·개선 로드맵·마일스톤 분할·`design_spec.md` 초안**을 작성한다(일반=high, 고위험=xhigh). **고위험은 추가로** `critic-standard`(Gemini Flash, high)가 구현 착수 전 계획 자체를 비평(계획비평)한다. 사용자 승인 없이는 다음 단계(구현)로 넘어가지 않는다.
* **2단계 (구현 및 비평)**: `implementer`(Claude Sonnet 5, high)가 구현을 수령한 뒤, `critic-architecture`(Gemini 3.1 Pro)를 연속 가동해 구조 교차비평(Cross-Review)을 강제한다. 이종 모델 교차가 Tier 2의 핵심 안전장치다.
  * **3개 파일 이상이면 의존 그룹별로 워커를 병렬 기동한다** (그룹 판정 기준은 SKILL.md §1.3). 그룹 G마다 `result_G.md` → `critic_report_G.md` 쌍을 만들고, G의 result가 나오는 즉시 G의 비평을 시작한다. 전체 워커 완료를 기다리는 배리어를 두지 않는다 — 실측상 검증 단계가 벽시계 시간의 최대 67%를 차지했다.
  * 병합은 모든 그룹이 비평을 통과한 뒤 한 번에 한다.
* **3단계**: `validate_result.py` 검증 후 기존 파일을 아카이빙(백업)하고 신규 구조로 대체한다.

---

## 비용 요약

| Tier/위험도 | planner | implementer | critic-standard | critic-architecture |
|---|---|---|---|---|
| 0 | — | — | — | — |
| 1 | — | Sonnet5 (medium) | Gemini Flash (medium) | — |
| 2 일반 | Sol (high) | Sonnet5 (high) | — | Gemini 3.1 Pro (medium~high) |
| 2 고위험 | Sol (xhigh) | Sonnet5 (high) | Gemini Flash (high, 계획비평) | Gemini 3.1 Pro (high) |

`execution_mode`가 `host-native`이면 위 모델 지정 대신 오케스트레이터가 Agent 도구로 별도 서브에이전트를 기동하고 구독 쿼터를 소비한다(USD $0이 아니다 — `cli_quota`/쿼터 소모로 별도 기록). `cost_tracker.json` 자동 계측이 `api-routed`처럼 동작하지 않으므로 완료 시 `collect_metrics.py`를 반드시 실행한다. 상세는 `_shared/antigravity_guide.md` 참조.
