"""M18-d 평가 스크립트: 가짜 AI로 S1~S3가 끝까지 돌고 표가 나온다 (키·비용 없음). S2는 첫 분석만 모델을 바꾼다."""

import json

import scripts.m18_eval as ev


def test_eval_runs_all_scenarios_with_fake_ai(tmp_path):
    results = ev.main(["all", "--fake", "--repeats", "1", "--out", str(tmp_path)])
    assert set(results["scenarios"]) == {"S1", "S2", "S3"}
    for rows in results["scenarios"].values():
        assert [r["mode"] for r in rows] == ["fixed", "supervisor"] and not any(r.get("error") for r in rows)
        sup = rows[1]
        assert sup["pick"] and sup["pick"]["confidence"] and sup["attempts"][:2] == ["run_analysis", "run_proposals"]
        assert "P4" in rows[0]["detected"]
    s3 = results["scenarios"]["S3"]
    assert all("on_time_rate" in r["pick"]["delta"] for r in s3)          # 제약 지표 변화도 본다
    md = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## S2 분석이 놓침" in md and "Supervisor가 고정 흐름과 다른 1번 안" in md
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["args"]["fake"] is True


def test_s2_forces_only_the_first_analysis_model(monkeypatch):
    made = []
    monkeypatch.setattr(ev, "make_client", lambda cfg: made.append((cfg.get("model"), cfg.get("effort"))) or cfg)
    args = ev.argparse.Namespace(fake=False, s2_model="gpt-4.1-mini")
    make = ev.client_factory(args, "S2")
    make("analysis"), make("analysis"), make("proposals")
    assert made[0] == ("gpt-4.1-mini", None) and made[1][0] != "gpt-4.1-mini"
    made.clear()
    s1 = ev.client_factory(args, "S1")
    s1("analysis")
    assert made[0][0] != "gpt-4.1-mini"


def test_cost_cap_stops_before_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "run_fixed", lambda w, make, salt: {"mode": "fixed", "cost_usd": 5.0, "detected": [],
                                                                 "pick": None, "seconds": 0})
    results = ev.main(["S1", "--fake", "--repeats", "2", "--max-usd", "1", "--out", str(tmp_path)])
    assert "비용 상한" in results["stopped"] and len(results["scenarios"]["S1"]) == 1
