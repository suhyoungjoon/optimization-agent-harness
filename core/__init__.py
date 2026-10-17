"""공통 코어 공개 API. 다른 레포에서 재사용할 이름을 여기서 다시 내보낸다 (원래 모듈 경로도 그대로 쓸 수 있다).

설명과 계약은 docs/handoff.md.
"""

from core.analysis.agent import (analyze, finalize_findings, grounding_problems, report_submit_tool, slice_problems,
                                 strip_bad_slice, submission_problems)
from core.analysis.aggregate_tools import Aggregator
from core.analysis.resources import resource_problems, resource_spec_errors, resources_text, strip_bad_resources
from core.analysis.perspectives import analyze_perspectives, merge_findings, perspective_errors
from core.evaluation.compare import compare, consistency
from core.evaluation.fault_scorer import apply_labels, matches, score
from core.evaluation.runner import create_ai_run, run_ai_agent, run_rule_agent
from core.harness.levels import Level, get_level, load_levels
from core.harness.runner import CORE_REASON_CODES, AbortRun, HarnessRunner, RunOutput
from core.harness.tracer import Tracer
from core.improvement.approval import write_params, write_spec
from core.improvement.changes import apply_params, apply_spec, params_errors, spec_errors, spec_sections
from core.improvement.constraints import constraint_errors, constraint_violations
from core.improvement.memory import analysis_memory_text, build_memory, collect_memory, proposal_memory_text
from core.improvement.proposer import finding_slices, propose
from core.improvement.simulate import simulate_params
from core.improvement.sweep import load_sweep_config, sensitivity, sweep_params
from core.improvement.history import change_card, change_record
from core.improvement.loop import allowed_steps, load_loop_config, loop_report, termination
from core.improvement.ranking import load_ranking_config, metric_specs, minimal_change, rank_proposals
from core.interfaces import DecisionRecord, DomainPack, ToolContext, TraceRecord, Violation
from core.llm.azure_openai import AzureOpenAIClient
from core.llm.client import AnthropicClient, LLMClient, LLMResponse, ResponseCache, Usage, load_config, make_client
from core.llm.embeddings import AzureEmbedder, Embedder, load_embedding_config, make_embedder
from core.llm.tool_loop import LoopResult, run_tool_loop, usage_dict
from core.params import (KINDS, apply_overrides, check_params, get_path, kind_errors, param_kind, params_view,
                         parse_path, path_errors, set_path)
from core.registry import knowledge_dir, list_domains, list_faults, load_faults, load_pack, load_params, load_perspectives
from core.retrieval import (Chunk, Index, citation_errors, load_knowledge, open_index, parse_markdown, prepare,
                            retrieval_meta, search_tool)
from core.storage.store import Store, dataset_id, to_jsonable

__all__ = [
    # 계약 (core.interfaces)
    "DecisionRecord", "DomainPack", "ToolContext", "TraceRecord", "Violation",
    # 파라미터: 경로 접근, 구간 조건, 허용 범위 검사 (core.params)
    "KINDS", "apply_overrides", "check_params", "get_path", "kind_errors", "param_kind", "params_view", "parse_path",
    "path_errors", "set_path",
    # 도메인 팩 로딩 (core.registry)
    "knowledge_dir", "list_domains", "list_faults", "load_faults", "load_pack", "load_params", "load_perspectives",
    # 하네스 (core.harness)
    "CORE_REASON_CODES", "AbortRun", "HarnessRunner", "Level", "RunOutput", "Tracer", "get_level", "load_levels",
    # LLM (core.llm)
    "AnthropicClient", "AzureOpenAIClient", "LLMClient", "LLMResponse", "LoopResult", "ResponseCache", "Usage",
    "load_config", "make_client",
    # 임베딩 (M15-C)
    "AzureEmbedder", "Embedder", "load_embedding_config", "make_embedder",
    "run_tool_loop", "usage_dict",
    # 실행·비교·채점 (core.evaluation)
    "apply_labels", "compare", "consistency", "create_ai_run", "matches", "run_ai_agent", "run_rule_agent", "score",
    # 분석 (core.analysis)
    "Aggregator", "analyze", "analyze_perspectives", "finalize_findings", "grounding_problems", "merge_findings",
    "perspective_errors", "report_submit_tool", "resource_problems", "resource_spec_errors", "resources_text",
    "slice_problems", "strip_bad_resources", "strip_bad_slice", "submission_problems",
    # 개선 루프 (core.improvement)
    "apply_params", "apply_spec", "constraint_errors", "constraint_violations", "finding_slices", "params_errors",
    "propose", "simulate_params", "analysis_memory_text", "build_memory", "collect_memory", "proposal_memory_text",
    "spec_errors", "spec_sections", "write_params", "write_spec",
    # 파라미터 가시화 (M13): 탐색·민감도, 변경 이력 카드
    "change_card", "change_record", "load_sweep_config", "sensitivity", "sweep_params",
    # 개선안 제시 기준·개선 루프 (M15-A·B·D)
    "load_ranking_config", "metric_specs", "minimal_change", "rank_proposals",
    "allowed_steps", "load_loop_config", "loop_report", "termination",
    # 문서 검색 (M15-C, core.retrieval)
    "Chunk", "Index", "citation_errors", "load_knowledge", "open_index", "parse_markdown", "prepare", "retrieval_meta",
    "search_tool",
    # 저장소 (core.storage)
    "Store", "dataset_id", "to_jsonable",
]
