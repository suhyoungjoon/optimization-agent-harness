---
area: distance
fictional: true
---
# 가상 운영 규정 — 출동 거리 (가상 문서, 실제 규정 아님)

## DIST-01 관할 밖 출동 거리 상한
<!-- meta: {"params": ["matching.area_extension_km"], "dims": {"area_zone": ["boundary", "core"]}} -->
작업자를 소속 관할 구역 밖으로 보내는 거리는 어떤 매칭 단계에서도 4km를 넘지 않는다.
4km를 넘는 출동은 이동 중 안전사고와 다른 지시서 지연 위험이 커서 허용하지 않는다.

## DIST-02 단계별 완화 순서
<!-- meta: {"params": ["matching.area_extension_km"]} -->
관할 밖 허용 거리는 1단계가 가장 좁고 단계가 올라갈수록 넓힌다. 앞 단계보다 좁은 값을 뒤 단계에 두지 않는다.
1단계는 관할 안(0km)을 원칙으로 한다.

## DIST-03 경계 지역 우선 완화
<!-- meta: {"params": ["matching.area_extension_km"], "dims": {"area_zone": ["boundary"]}} -->
관할 경계 근처 고객은 이웃 관할 작업자가 더 가까울 수 있으므로, 거리 완화는 경계 지역에 먼저 적용하고
관할 안쪽까지 넓히는 전역 완화는 그다음에 검토한다.

## DIST-04 완화 시 이동시간 점검
<!-- meta: {"params": ["matching.area_extension_km"]} -->
출동 거리를 넓히는 변경은 평균 이동시간과 희망시간 준수율의 변화를 함께 확인한 뒤 반영한다.
평균 이동시간이 2분 넘게 늘면 현장 관리자와 협의한다.

## DIST-05 이동 속도 가정 변경 금지
<!-- meta: {"params": ["travel.avg_speed_kmh", "travel.detour_factor"]} -->
이동 속도와 도로 우회계수는 실적으로 확인한 추정·고정값이다. 배정 성공률을 올리려고 이 값을 바꾸지 않는다.
