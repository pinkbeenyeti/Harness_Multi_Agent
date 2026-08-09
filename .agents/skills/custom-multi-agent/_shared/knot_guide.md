# 지식 그물 (Knot) 활용 가이드

> 여러 에이전트 간 지식 공유가 필요하거나 사용자의 프로젝트 문서를 학습해야 할 때만 읽는다.

- 새로운 외부 문서는 `scripts/knot_manager.py save <경로>` 후 `scripts/knot_manager.py ingest`로 지식 위키(`wiki/`)에 가공해 보관한다.
- **Ingest 최적화**: ingest 실행 시 내부에서 LLM 요약과 개념 연결(`[[키워드]]`)이 자동 처리된다. 사전에 본문을 수동 요약하느라 주의력을 낭비하지 말 것.
- 위키 작성 후에는 주기적으로 `scripts/knot_manager.py lint`로 링크 건강도(Broken Link)를 점검한다.
- 위키를 근거로 인용할 때는 `[[wiki/파일명]]` 형식으로 출처를 남긴다.
