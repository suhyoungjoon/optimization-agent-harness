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
    assert isinstance(params.get("version"), int)

    sections = {k: v for k, v in params.items() if k != "version"}
    assert sections, "params.yaml에 파라미터 섹션이 있어야 한다"
    for name, section in sections.items():
        assert isinstance(section, dict), f"{name}: 섹션은 매핑이어야 한다"
        tunable = {k: v for k, v in section.items() if k != "bounds" and numeric_leaves(v)}
        bounds = section.get("bounds", {})
        missing = set(tunable) - set(bounds)
        assert not missing, f"{name}: 허용 범위(bounds)가 없는 수치 파라미터 {sorted(missing)}"
        for key, (lo, hi) in bounds.items():
            assert key in section, f"{name}.bounds.{key}: 존재하지 않는 파라미터"
            assert lo <= hi, f"{name}.bounds.{key}: min > max"
            for v in numeric_leaves(section[key]):
                assert lo <= v <= hi, f"{name}.{key}={v}가 허용 범위 [{lo}, {hi}] 밖"


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

