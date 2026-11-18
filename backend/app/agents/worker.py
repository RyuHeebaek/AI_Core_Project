"""WorkerAgent: 정해진 task_list 실행(정상 경로 LLM 0회), 캐시 재사용, 실패 시 제한적 인자 보정 (F3007, F3021)."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agents.llm_client import safe_llm_json
from app.config import get_settings
from app.events.event_bus import AgentEvent, event_bus
from app.models.state import AgentState
from app.tools import TOOL_REGISTRY
from app.tools.base import FATAL_OR_CLARIFY_CODES, RETRYABLE_ERROR_CODES, ErrorCode


def _cache_key(tool_name: str, args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _decide_retry_args(
    tool_name: str,
    args: dict[str, Any],
    fixed_args: list[str],
    error_code: str,
    error_msg: str,
) -> tuple[str, dict[str, Any] | None, bool, str]:
    """F3021: 인자를 고칠 수 있는 실패 시에만 LLM에게 RETRY/SKIP/ESCALATE 판단을 묻는다. fixed_args는 절대 불변."""
    if "query" not in args or "query" in fixed_args:
        return "ESCALATE", None, False, "고정 인자이거나 수정 가능한 검색어가 없어 Planner로 에스컬레이션"

    prompt = (
        f"Tool '{tool_name}' 호출이 실패했습니다.\n"
        f"- 실패 코드: {error_code}\n- 실패 메시지: {error_msg}\n"
        f"- 현재 인자: {json.dumps(args, ensure_ascii=False)}\n"
        f"- 고정 인자(절대 변경 금지): {fixed_args}\n"
        "행동(RETRY 또는 ESCALATE)과, RETRY일 경우 한국어 식단 검색에 적합한 보정 query를 "
        "JSON {\"action\": \"RETRY\"|\"ESCALATE\", \"query\": \"...\", \"reason\": \"...\"}로만 답하세요."
    )
    parsed, called, _ = safe_llm_json(prompt, system_prompt="너는 Worker 에이전트 오류 복구 판단기다. JSON만 출력한다.")
    if parsed and called:
        action = parsed.get("action", "ESCALATE")
        new_query = str(parsed.get("query", "")).strip()
        if action == "RETRY" and new_query and new_query != args["query"]:
            return "RETRY", {**args, "query": new_query}, True, parsed.get("reason", "LLM 키워드 보정")
        return "ESCALATE", None, True, parsed.get("reason", "LLM 재계획 요청")

    # LLM 미설정 환경(오프라인 테스트)용 규칙 fallback — llm_called는 실제 호출 여부 그대로 유지
    fallback_query = "닭가슴살" if tool_name == "search_nutrition_db" else "계란"
    if fallback_query != args["query"]:
        return "RETRY", {**args, "query": fallback_query}, called, "대체 기본 키워드로 보정 재시도"
    return "ESCALATE", None, called, "대체 검색어 소진으로 Planner 재계획 요청"


_TRANSIENT_ERROR_CODES = frozenset({ErrorCode.API_TIMEOUT, ErrorCode.API_ERROR, ErrorCode.MISSING_API_KEY})


def _logged_args(tool_name: str, current_args: dict[str, Any]) -> dict[str, Any]:
    if tool_name != "optimize_meal_combo":
        return current_args
    return {
        "mode": current_args.get("mode"),
        "nutrition_count": len(current_args.get("nutrition_items") or []),
        "shopping_count": len(current_args.get("shopping_products") or []),
        "constraints": current_args.get("constraints"),
    }


def _execute_uncached_task(
    session_id: str,
    run_id: str,
    task_id: str,
    tool_name: str,
    spec: Any,
    args: dict[str, Any],
    fixed_args: list[str],
    max_retry: int,
    worker_llm_enabled: bool,
) -> dict[str, Any]:
    attempt = 0
    current_args = args
    events_out: list[AgentEvent] = []
    traces_out: list[dict[str, Any]] = []
    llm_delta = 0
    fatal_err: dict[str, Any] | None = None
    escalation: dict[str, Any] | None = None
    cacheable = False

    while True:
        t0 = time.perf_counter()
        events_out.append(
            AgentEvent(
                session_id,
                run_id,
                "tool_call_start",
                "WorkerAgent",
                "worker_execute",
                {
                    "tool_name": tool_name,
                    "args": _logged_args(tool_name, current_args),
                    "is_retry": attempt > 0,
                },
            )
        )
        result = spec.fn(**current_args)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        events_out.append(
            AgentEvent(
                session_id,
                run_id,
                "tool_call_end",
                "WorkerAgent",
                "worker_execute",
                {
                    "tool_name": tool_name,
                    "ok": result["ok"],
                    "error": result.get("error"),
                    "elapsed_ms": elapsed_ms,
                    "is_retry": attempt > 0,
                },
            )
        )
        traces_out.append(
            {
                "step": "tool_call",
                "tool_name": tool_name,
                "ok": result["ok"],
                "elapsed_ms": elapsed_ms,
                "is_retry": attempt > 0,
                "error_code": (result.get("error") or {}).get("code"),
            }
        )

        if result["ok"]:
            cacheable = True
            break

        err_obj = result.get("error") or {}
        err_code = err_obj.get("code", "")

        # F3021: 치명적 오류나 모호한 지역은 LLM 호출 없이 즉시 중단/전달
        if err_code in FATAL_OR_CLARIFY_CODES:
            fatal_err = {"code": err_code, "message": err_obj.get("message", ""), "tool_name": tool_name}
            break

        if err_code in RETRYABLE_ERROR_CODES and worker_llm_enabled and attempt < max_retry:
            action, new_args, llm_called, dec_reason = _decide_retry_args(
                tool_name, current_args, fixed_args, err_code, err_obj.get("message", "")
            )
            if llm_called:
                llm_delta += 1
            events_out.append(
                AgentEvent(
                    session_id,
                    run_id,
                    "llm_decision",
                    "WorkerAgent",
                    "worker_execute",
                    {"tool_name": tool_name, "action": action, "reason": dec_reason, "llm_called": llm_called},
                )
            )
            if action == "RETRY" and new_args is not None:
                attempt += 1
                current_args = new_args
                continue

        cacheable = err_code not in _TRANSIENT_ERROR_CODES
        escalation = {"task_id": task_id, "tool_name": tool_name, "reason": err_obj.get("message", "Tool 실패")}
        break

    return {
        "result": result,
        "events": events_out,
        "traces": traces_out,
        "llm_delta": llm_delta,
        "fatal_error": fatal_err,
        "escalation": escalation,
        "cacheable": cacheable,
    }


def worker_node(state: AgentState) -> dict[str, Any]:
    """Planner의 task_list를 실행하고 독립 조회 Tool은 병렬로 선실행해 앞선 Tool 출력을 연결한다."""
    session_id = state["session_id"]
    run_id = state["run_id"]
    task_list = state.get("task_list", [])
    worker_llm_enabled = state.get("worker_llm_enabled", True)
    max_retry = get_settings().max_worker_retry

    nutrition_cache = dict(state.get("nutrition_cache", {}))
    shopping_cache = dict(state.get("shopping_cache", {}))
    store_cache = dict(state.get("store_cache", {}))
    caches = {
        "search_nutrition_db": nutrition_cache,
        "search_product_price": shopping_cache,
        "search_nearby_store": store_cache,
    }

    worker_results: dict[str, Any] = {}
    escalations: list[dict[str, Any]] = []
    trace = list(state.get("execution_trace", []))
    llm_calls = int(state.get("llm_calls_count", 0))
    fatal_error = state.get("fatal_error")

    event_bus.publish(AgentEvent(session_id, run_id, "node_enter", "WorkerAgent", "worker_execute", {"task_count": len(task_list)}))

    # 독립 read-only 조회 태스크(depends_on == []) 중 캐시 미스 항목을 병렬 선실행
    prefetch_jobs: dict[str, tuple[str, Any, dict[str, Any], list[str], str]] = {}
    for task in task_list:
        tool_name = task["tool_name"]
        if task.get("depends_on") or tool_name not in caches:
            continue
        spec = TOOL_REGISTRY.get(tool_name)
        args = dict(task.get("args", {}))
        cache = caches[tool_name]
        ckey = _cache_key(tool_name, args)
        if spec and ckey not in cache and ckey not in prefetch_jobs:
            prefetch_jobs[ckey] = (task["task_id"], spec, args, list(task.get("fixed_args", [])), tool_name)

    prefetched: dict[str, dict[str, Any]] = {}
    if len(prefetch_jobs) > 1:
        keys = list(prefetch_jobs.keys())
        with ThreadPoolExecutor(max_workers=min(4, len(keys))) as pool:
            outcomes = list(
                pool.map(
                    lambda k: _execute_uncached_task(
                        session_id,
                        run_id,
                        prefetch_jobs[k][0],
                        prefetch_jobs[k][4],
                        prefetch_jobs[k][1],
                        prefetch_jobs[k][2],
                        prefetch_jobs[k][3],
                        max_retry,
                        worker_llm_enabled,
                    ),
                    keys,
                )
            )
        prefetched = dict(zip(keys, outcomes))

    collected_nutrition: list[dict[str, Any]] = []
    collected_shopping: list[dict[str, Any]] = []
    collected_stores: list[dict[str, Any]] = []
    basis_notes: list[str] = []
    is_estimated = False
    excluded_from_tools: list[dict[str, str]] = []

    for task in task_list:
        tool_name = task["tool_name"]
        args = dict(task.get("args", {}))
        fixed_args = list(task.get("fixed_args", []))
        spec = TOOL_REGISTRY.get(tool_name)
        if not spec:
            escalations.append({"task_id": task["task_id"], "tool_name": tool_name, "reason": "미등록 Tool"})
            continue

        # 앞선 Tool 결과를 optimizer 입력으로 전달 (F3007)
        if tool_name == "optimize_meal_combo" and args.get("mode") != "profile_calc":
            args["nutrition_items"] = collected_nutrition
            args["shopping_products"] = collected_shopping

        cache = caches.get(tool_name)
        ckey = _cache_key(tool_name, args)
        if cache is not None and ckey in cache:
            result = cache[ckey]
            event_bus.publish(
                AgentEvent(
                    session_id,
                    run_id,
                    "tool_cache_hit",
                    "WorkerAgent",
                    "worker_execute",
                    {"tool_name": tool_name, "cache_key": ckey[:100]},
                )
            )
            trace.append({"step": "tool_cache_hit", "tool_name": tool_name, "ok": result["ok"]})
        else:
            outcome = prefetched.get(ckey) or _execute_uncached_task(
                session_id,
                run_id,
                task["task_id"],
                tool_name,
                spec,
                args,
                fixed_args,
                max_retry,
                worker_llm_enabled,
            )
            for ev in outcome["events"]:
                event_bus.publish(ev)
            trace.extend(outcome["traces"])
            llm_calls += outcome["llm_delta"]
            result = outcome["result"]
            if outcome["fatal_error"]:
                fatal_error = outcome["fatal_error"]
            if outcome["escalation"]:
                escalations.append(outcome["escalation"])
            if cache is not None and outcome["cacheable"]:
                cache[ckey] = result

        worker_results[tool_name] = result
        if fatal_error:
            break

        if result and result["ok"]:
            data = result["data"]
            if data.get("is_estimated"):
                is_estimated = True
            if data.get("basis_note"):
                basis_notes.append(data["basis_note"])
            if tool_name == "search_nutrition_db":
                seen_ids = {i["food_id"] for i in collected_nutrition}
                collected_nutrition.extend(i for i in data.get("items", []) if i["food_id"] not in seen_ids)
                excluded_from_tools.extend(data.get("excluded_unverified", []))
            elif tool_name == "search_product_price":
                seen_pids = {p["product_id"] for p in collected_shopping}
                collected_shopping.extend(p for p in data.get("products", []) if p["product_id"] not in seen_pids)
                excluded_from_tools.extend(data.get("excluded_unparsed", []))
            elif tool_name == "search_nearby_store":
                collected_stores.extend(data.get("stores", []))
                if data.get("stock_disclaimer"):
                    basis_notes.append(data["stock_disclaimer"])

    candidate_combo: dict[str, Any] = {}
    opt_res = worker_results.get("optimize_meal_combo")
    if opt_res and opt_res.get("ok"):
        candidate_combo = dict(opt_res["data"])
        candidate_combo["stores"] = collected_stores
        candidate_combo["is_estimated"] = is_estimated
        candidate_combo["basis_notes"] = list(dict.fromkeys(basis_notes))
        candidate_combo["excluded_candidates"] = list(candidate_combo.get("excluded_candidates", [])) + excluded_from_tools

    event_bus.publish(
        AgentEvent(
            session_id,
            run_id,
            "node_exit",
            "WorkerAgent",
            "worker_execute",
            {"has_combo": bool(candidate_combo), "escalation_count": len(escalations), "fatal": bool(fatal_error)},
        )
    )

    return {
        "worker_results": worker_results,
        "worker_escalations": escalations,
        "nutrition_cache": nutrition_cache,
        "shopping_cache": shopping_cache,
        "store_cache": store_cache,
        "candidate_combo": candidate_combo,
        "fatal_error": fatal_error,
        "execution_trace": trace,
        "llm_calls_count": llm_calls,
    }
