---
area: time
fictional: true
---
# 가상 운영 규정 — 희망시간 (가상 문서, 실제 규정 아님)

## TIME-01 희망시간 허용 오차 상한
<!-- meta: {"params": ["matching.time_window_min"]} -->
고객 희망시각과 실제 도착 시각의 차이는 어떤 매칭 단계에서도 90분을 넘지 않는다.

## TIME-02 1단계는 희망시각 정시
<!-- meta: {"params": ["matching.time_window_min"]} -->
1단계 매칭은 희망시각에 맞춰 출동하는 것(허용 오차 0분)을 원칙으로 한다.

## TIME-03 오전 집중 시간대 운영
<!-- meta: {"dims": {"hour": ["09", "10"]}} -->
오전 9~10시에 지시서가 몰리는 지점은 인력 재배치나 희망시간 조정 안내를 먼저 검토하고,
허용 오차 완화는 그다음에 검토한다.

## TIME-04 희망시간 준수율 하락 한도
<!-- meta: {"params": ["matching.time_window_min", "matching.area_extension_km"]} -->
규칙 변경으로 희망시간 준수율이 3%p 넘게 떨어지면 고객 안내 계획을 함께 세운 경우에만 반영한다.

## TIME-05 작업소요 가정 변경 금지
<!-- meta: {"params": ["duration.base_min", "weights.difficulty", "weights.building"]} -->
작업소요 기본값과 가중치는 실적에서 나온 추정값이다. 일정을 맞추려고 작업이 빨리 끝난다고 가정하지 않는다.
