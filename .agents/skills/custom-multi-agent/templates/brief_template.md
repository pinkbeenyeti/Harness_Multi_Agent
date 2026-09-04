# Worker Brief: [작업명]

* **Sender (Orchestrator)**: `[agy-cli | codex-cli]`
* **Recipient (Worker)**: `[planner | implementer | critic-standard | critic-architecture]` (`[codex-cli | claude-code | agy-cli]`, model: `[...]`)
* **Tier**: [0/1/2] — 판정 근거: [파일 N개 / 변경 ~N줄 / 시그니처 불변 여부 / 핫스팟 여부]

## 1. 수정 대상 (Target)
* **파일 경로**: [대상 파일의 절대 경로]
* **수정 라인 범위**: [예: L120-L145]

> 코드 본문을 여기 붙여넣지 마십시오. 워커는 위 경로의 **실제 라이브 파일을 직접 읽습니다.**
> 브리프에 박제된 스냅샷은 낡아서 미고지 회귀를 일으킵니다.
> 앵커가 모호할 때만 식별용으로 1~3줄을 인용하십시오.

## 2. 관련 의존성 (Dependency Context)
* **접촉 인터페이스/타입**: [경로:라인 형태로 나열. 정의 본문 붙여넣기 금지]

## 3. 수정 지시 (What to Change)
* **변경 의도**: [무엇을 왜 바꾸는지 1~3문장]
* **구체적 지시**: [어떤 패턴으로 수정해야 하는지 명확하게]

## 4. 제약조건 (Constraints)
* **In Scope**: [반드시 구현할 것]
* **Out of Scope**: [건드리지 말 것]
* **성능 핫스팟 여부**: [예/아니오 — 예인 경우 성능 제약 상세]
* **코딩 컨벤션**: `_shared/coding_conventions.md` 참조

## 5. 출력 규약 (Output Protocol)
* 워커는 읽기 전용 샌드박스에서 실행되므로 파일 생성·수정 도구를 호출하지 마십시오.
* 최종 산출물 전체를 응답 텍스트(표준 출력)로 직접 반환하십시오. 실제 `result.md` 저장은 오케스트레이터(`call_worker.py`)가 담당합니다.
* 기존 코드 수정은 `unified diff` 또는 앵커 패치로 반환하십시오.
* 신규 파일은 파일 전문을 반환하십시오.
* 기획·분석 산출물은 문서 전문을 반환하십시오.
* 모든 출력은 `result.md` 기준 15,000 bytes, 코드 블록을 제외한 산문 1,200 토큰 이내입니다.
