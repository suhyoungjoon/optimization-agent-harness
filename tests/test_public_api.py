"""공개 API (core, domains.dispatch의 __all__)가 모두 import되고 원래 모듈의 같은 객체인지."""

import importlib

import core
import domains.dispatch as dispatch


def test_core_public_api_resolves():
    assert len(core.__all__) == len(set(core.__all__))
    for name in core.__all__:
        obj = getattr(core, name)
        module = getattr(obj, "__module__", None)
        if module:                                   # 상수(CORE_REASON_CODES)는 모듈 정보가 없다
            assert getattr(importlib.import_module(module), name) is obj


def test_dispatch_public_api_resolves():
    for name in dispatch.__all__:
        obj = getattr(dispatch, name)
        module = getattr(obj, "__module__", None) or "domains.dispatch.pack"   # 상수(NAME)는 pack에 정의
        assert getattr(importlib.import_module(module), name) is obj
    pack = dispatch.get_pack()
    assert isinstance(pack, dispatch.DispatchPack) and pack.name == dispatch.NAME == "dispatch"
    assert core.load_pack("dispatch").name == "dispatch"
