"""AI agent에 제공할 도구 선언. 실행 로직은 M3에서 추가한다."""

TOOL_SPECS = [
    {
        "name": "find_candidates",
        "description": "지시서의 매칭 단계(1~3)에서 필수 조건과 시간·지역 범위를 만족하는 후보 작업자 목록을 돌려준다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "stage": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["order_id", "stage"],
        },
    },
    {
        "name": "get_travel_time",
        "description": "작업자의 현재 위치에서 지시서 위치까지 이동시간(분)을 돌려준다.",
        "input_schema": {
            "type": "object",
            "properties": {"worker_id": {"type": "string"}, "order_id": {"type": "string"}},
            "required": ["worker_id", "order_id"],
        },
    },
    {
        "name": "get_worker_schedule",
        "description": "작업자의 가능시간과 이미 배정된 작업 일정을 돌려준다.",
        "input_schema": {
            "type": "object",
            "properties": {"worker_id": {"type": "string"}},
            "required": ["worker_id"],
        },
    },
    {
        "name": "check_assignment",
        "description": "지시서를 작업자에게 해당 시각에 배정할 때 필수 조건 위반이 있는지 확인한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "worker_id": {"type": "string"},
                "start_time": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
            },
            "required": ["order_id", "worker_id", "start_time"],
        },
    },
]
