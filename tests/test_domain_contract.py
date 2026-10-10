"""도메인 팩 계약 테스트. domains/ 아래 모든 도메인 팩에 공통으로 적용한다.

- 파일 계약: domain-spec.md, params.yaml, faults.yaml, dimensions.yaml의 형식
- 동작 계약: pack.py의 get_pack()이 반환하는 DomainPack의 반환 타입
  (pack.py가 아직 없으면 skip)
"""

import importlib
from numbers import Number
from pathlib import Path

import pytest
import yaml

from core.interfaces import DecisionRecord, Violation
from core.params import check_params

DOMAINS_DIR = Path(__file__).resolve().parent.parent / "domains"

REQUIRED_FILES = ["domain-spec.md", "params.yaml", "faults.yaml", "dimensions.yaml"]
SPEC_SECTIONS = ["목적", "필수 조건", "선호 조건", "판단 순서", "예외 처리", "사용 도구"]
DECISION_STATUSES = {"success", "failed", "blocked", "pending_approval"}


def discover_domains() -> list[str]:
    return sorted(
        p.name for p in DOMAINS_DIR.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    )


DOMAINS = discover_domains()


def load_yaml(domain: str, filename: str) -> dict:
    with open(DOMAINS_DIR / domain / filename, encoding="utf-8") as f:
        return yaml.safe_load(f)


def numeric_leaves(value) -> list[Number]:
    if isinstance(value, bool):
        return []
    if isinstance(value, Number):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in numeric_leaves(v)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numeric_leaves(v)]
    return []


def test_at_least_one_domain():
    assert DOMAINS, "domains/ 아래에 도메인 팩이 하나 이상 있어야 한다"


# --- 파일 계약 -------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize("filename", REQUIRED_FILES)
def test_required_file_exists(domain, filename):
    assert (DOMAINS_DIR / domain / filename).is_file()


@pytest.mark.parametrize("domain", DOMAINS)
def test_spec_has_fixed_sections_in_order(domain):
    text = (DOMAINS_DIR / domain / "domain-spec.md").read_text(encoding="utf-8")
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    assert headings == SPEC_SECTIONS


@pytest.mark.parametrize("domain", DOMAINS)
def test_params_version_and_bounds(domain):
    params = load_yaml(domain, "params.yaml")
    assert [k for k in params if k not in ("version", "overrides")], "파라미터 섹션이 있어야 한다"
    assert check_params(params, load_yaml(domain, "dimensions.yaml")) == []


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_param_has_docs(domain):
    """모든 파라미터에 설명(docs)이 있어야 한다: 화면과 개선 agent가 의미를 알 수 있게."""
    params = load_yaml(domain, "params.yaml")
    missing = [f"{name}.{key}" for name, section in params.items() if name not in ("version", "overrides")
               for key in section if key not in ("bounds", "docs") and key not in (section.get("docs") or {})]
    assert not missing, f"docs가 없는 파라미터: {missing}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_dimensions_schema(domain):
    dims = load_yaml(domain, "dimensions.yaml")
    assert dims.get("dimensions"), "분석 차원이 하나 이상 있어야 한다"
    for name, spec in dims["dimensions"].items():
        assert "label" in spec, f"차원 {name}에 label이 없다"
        assert "values" in spec or "format" in spec, f"차원 {name}에 values 또는 format이 없다"
    assert dims.get("reason_codes"), "사유 코드가 하나 이상 있어야 한다"
    assert dims.get("violation_rules"), "필수조건 규칙이 하나 이상 있어야 한다"


@pytest.mark.parametrize("domain", DOMAINS)
def test_faults_reference_declared_codes(domain):
    faults = load_yaml(domain, "faults.yaml")
    dims = load_yaml(domain, "dimensions.yaml")
    assert faults, "심을 패턴이 하나 이상 있어야 한다"
    for fid, fault in faults.items():
        for key in ("name", "generation", "expected", "answer"):
            assert key in fault, f"{fid}에 {key}가 없다"
        answer = fault["answer"]
        for code in answer.get("reason_codes", []):
            assert code in dims["reason_codes"], f"{fid}: 선언되지 않은 사유 코드 {code}"
        for dim, value in answer.get("dims", {}).items():
            assert dim in dims["dimensions"], f"{fid}: 선언되지 않은 차원 {dim}"
            allowed = dims["dimensions"][dim].get("values")
            if allowed is not None:
                values = value if isinstance(value, list) else [value]
                assert set(values) <= set(allowed), f"{fid}: {dim}에 선언되지 않은 값 {values}"
        if answer.get("resources"):   # 자원 단위 정답 (M12-d): 선언된 자원 종류와 정답표 키
            assert answer["resources"].get("kind") in (dims.get("resources") or {}), f"{fid}: 선언되지 않은 자원 종류"
            assert answer["resources"].get("truth_key"), f"{fid}: resources.truth_key가 없다"


@pytest.mark.parametrize("domain", DOMAINS)
def test_resources_schema(domain):
    """자원 선언(선택): 종류마다 label, id_field, 속성마다 label."""
    from core.analysis.resources import resource_spec_errors

    assert resource_spec_errors(load_yaml(domain, "dimensions.yaml")) == []


# --- 동작 계약 (pack.py 구현 후 활성화) ------------------------------------

def load_pack(domain: str):
    if not (DOMAINS_DIR / domain / "pack.py").exists():
        pytest.skip(f"{domain}/pack.py 미구현")
    module = importlib.import_module(f"domains.{domain}.pack")
    return module.get_pack()


@pytest.fixture(params=DOMAINS)
def pack(request):
    return load_pack(request.param)


@pytest.fixture
def generated(pack):
    return pack.generate(seed=0, faults=[])


def test_pack_name_and_paths(pack):
    assert isinstance(pack.name, str) and pack.name
    assert Path(pack.spec_path()).name == "domain-spec.md"
    assert Path(pack.params_path()).name == "params.yaml"
    assert isinstance(pack.dimensions(), dict)


def test_generate_is_deterministic(pack):
    a, truth_a = pack.generate(seed=0, faults=[])
    b, truth_b = pack.generate(seed=0, faults=[])
    assert pack.items(a) == pack.items(b)
    assert truth_a == truth_b
    assert isinstance(truth_a, dict)


def test_items_are_unique_strings(pack, generated):
    instance, _ = generated
    items = pack.items(instance)
    assert items and all(isinstance(i, str) for i in items)
    assert len(items) == len(set(items))


def test_solve_returns_decision_records(pack, generated):
    instance, _ = generated
    params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
    dims = pack.dimensions()
    decisions = pack.solve(instance, params)
    assert {d.item_id for d in decisions} == set(pack.items(instance))
    for d in decisions:
        assert isinstance(d, DecisionRecord)
        assert d.status in DECISION_STATUSES
        if d.status != "success":
            assert d.reason_code in dims["reason_codes"]
        assert set(d.dims) <= set(dims["dimensions"])


def test_solve_has_no_violations(pack, generated):
    instance, _ = generated
    params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
    violations = pack.validate(instance, pack.solve(instance, params))
    assert violations == []


def test_validate_and_metrics_types(pack, generated):
    instance, _ = generated
    params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
    decisions = pack.solve(instance, params)
    for v in pack.validate(instance, decisions):
        assert isinstance(v, Violation)
        assert v.rule in pack.dimensions()["violation_rules"]
    metrics = pack.metrics(instance, decisions)
    assert metrics and all(isinstance(v, float) for v in metrics.values())


def test_tools_are_declared(pack, generated):
    instance, _ = generated
    tools = pack.tools(instance)
    assert tools
    for tool in tools:
        assert {"name", "description"} <= set(tool)



# --- [M3] AI agent 하네스용 계약 ------------------------------------------

def test_tools_have_schema_and_handler(pack, generated):
    instance, _ = generated
    for tool in pack.tools(instance):
        assert tool["input_schema"]["type"] == "object"
        assert callable(tool["handler"])


def test_decision_schema(pack):
    schema = pack.decision_schema()
    assert schema["type"] == "object" and schema.get("properties")


def test_item_dims_declared(pack, generated):
    instance, _ = generated
    declared = set(pack.dimensions()["dimensions"])
    item = pack.items(instance)[0]
    assert set(pack.item_dims(instance, item)) <= declared


def test_subset_keeps_only_requested_items(pack, generated):
    instance, _ = generated
    picked = pack.items(instance)[:5]
    sub = pack.subset(instance, picked)
    assert sorted(pack.items(sub)) == sorted(picked)


def test_approval_reasons_are_strings(pack, generated):
    instance, _ = generated
    params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
    decisions = pack.solve(instance, params)
    for record in decisions[:20]:
        reasons = pack.approval_reasons(instance, record, decisions)
        assert isinstance(reasons, list) and all(isinstance(r, str) for r in reasons)


# --- [M4] 분석 agent용 계약 ----------------------------------------------

def test_analysis_tools(pack, generated):
    instance, _ = generated
    params = yaml.safe_load(Path(pack.params_path()).read_text(encoding="utf-8"))
    decisions = pack.solve(instance, params)
    tools = pack.analysis_tools(instance, decisions)
    assert tools
    for tool in tools:
        assert {"name", "description", "input_schema"} <= set(tool) and callable(tool["handler"])
        assert tool["handler"]({}) is not None
