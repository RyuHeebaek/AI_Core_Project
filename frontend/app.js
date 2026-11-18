const API_HOST = window.location.hostname || "127.0.0.1";
const API_PROTO = window.location.protocol === "https:" ? "https:" : "http:";
const API_BASE = `${API_PROTO}//${API_HOST}:8000/api`;

let lastChatResult = null;
let selectedLogSession = null;
let profileSaved = false;

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function apiJson(url, options = {}) {
  const res = await fetch(url, options);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join(", ")
      : detail || `요청 실패 (HTTP ${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function showBanner(elId, msg, isWarn = false) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = msg;
  el.classList.toggle("warn", isWarn);
  el.classList.remove("hidden");
}

function getSessionId() {
  return document.getElementById("sessionIdInput").value.trim() || "demo-session-1";
}

function getProfilePayload() {
  return {
    gender: document.getElementById("profGender").value,
    weight_kg: Number(document.getElementById("profWeight").value),
    target_weight_kg: Number(document.getElementById("profTargetWeight").value),
    target_weeks: Number(document.getElementById("profWeeks").value),
    goal_type: document.getElementById("profGoal").value,
    meals_per_day: Number(document.getElementById("profMeals").value),
    lactose_free: document.getElementById("chkLactose").checked,
    low_sugar: document.getElementById("chkLowSugar").checked,
    no_caffeine_after_18: document.getElementById("chkNoCaff").checked,
  };
}

// 탭 전환
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "plans") loadPlans();
    if (btn.dataset.tab === "logs") loadLogSessions();
  });
});

// 초기 API 키 상태 조회 (실제 키 값 미노출 F3019, NF3002)
async function refreshHealth() {
  const bar = document.getElementById("apiStatusBar");
  try {
    const data = await apiJson(`${API_BASE}/health`);
    const k = data.api_keys || {};
    const missing = ["openai", "food_api", "consumer_price", "kakao"].filter((x) => !k[x]);
    bar.innerHTML =
      `OpenAI:${k.openai ? "✅" : "⚠️"} | 식약처:${k.food_api ? "✅" : "⚠️"} | 소비자원:${k.consumer_price ? "✅" : "⚠️"} | 카카오:${k.kakao ? "✅" : "⚠️"}` +
      (k.use_mock ? " (Mock 폴백 활성)" : " (실API 전용)");
    if (missing.length && !k.use_mock) {
      showBanner("chatErrorBanner", `미설정 API 키(${missing.join(", ")})가 있습니다. .env를 설정하거나 USE_MOCK=true로 실행하세요.`);
    }
  } catch {
    bar.textContent = `백엔드 연결 대기 중 (${API_BASE})`;
  }
}

function appendMessage(role, text) {
  const box = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

// 프로필 저장 & 목표 역산
document.getElementById("saveProfileBtn").addEventListener("click", async () => {
  const box = document.getElementById("profileResult");
  box.textContent = "역산 계산 중...";
  try {
    const data = await apiJson(`${API_BASE}/profiles/${encodeURIComponent(getSessionId())}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(getProfilePayload()),
    });
    profileSaved = true;
    const t = data.targets;
    box.innerHTML =
      `<div><strong>TDEE</strong>: ${t.tdee_kcal}kcal → <strong>일일 목표</strong>: ${t.target_daily_kcal}kcal</div>` +
      `<div><strong>1끼</strong>: ${t.target_meal_kcal}kcal (단백질 ${t.meal_protein_g}g)</div>` +
      (t.guardrail_triggered ? `<div class="alert warn">${esc(t.guardrail_warning)}</div>` : "");
  } catch (err) {
    box.innerHTML = `<span style="color:#dc2626">${esc(err.message)}</span>`;
  }
});

// 빠른 예시 버튼
document.querySelectorAll(".chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("chatInput").value = btn.dataset.prompt;
  });
});

// 실행 타임라인 렌더링 (F3019: 재계획 지점 구분 표시)
function renderTimeline(data) {
  const box = document.getElementById("timelineBox");
  const badge = document.getElementById("runMetaBadge");
  const anomBox = document.getElementById("anomalyInlineBox");

  badge.textContent = `모드 ${data.mode} · 재계획 ${data.replan_count}회 · LLM ${data.llm_calls_count}회`;
  badge.className = `badge ${data.replan_count > 0 ? "warn" : "ok"}`;

  const trace = data.execution_trace || [];
  if (!trace.length) {
    box.innerHTML = `<p class="muted">실행 이력이 없습니다.</p>`;
  } else {
    box.innerHTML = trace
      .map((item, idx) => {
        const isReplan = item.step === "replan";
        const isFail = item.ok === false || item.passed === false;
        const cls = isReplan ? "replan" : isFail ? "fail" : "ok";
        let detail = "";
        if (item.step === "planner_analyze") detail = `모드=${item.mode}, 이유=${item.reason}`;
        else if (item.step === "planner_plan") detail = `계획 Tool: ${(item.tasks || []).join(" → ")}`;
        else if (item.step === "replan") detail = `재계획 #${item.replan_count}: ${item.reason}`;
        else if (item.step === "tool_call")
          detail = `Tool=${item.tool_name} (${item.ok ? "성공" : item.error_code}, ${item.elapsed_ms}ms)${item.is_retry ? " [재시도]" : ""}`;
        else if (item.step === "tool_cache_hit") detail = `캐시 재사용=${item.tool_name}`;
        else if (item.step === "validator_check")
          detail = item.passed ? "1·2단계 검증 통과" : `검증 반려: ${(item.stage1_errors || []).concat(item.stage2_errors || []).join("; ")}`;
        else if (item.step === "responder") detail = `응답 생성 완료 (로그 판정: ${item.anomaly_status || "-"})`;

        return `<div class="trace-item ${cls}">
          <strong>#${idx + 1} ${esc(item.step)}</strong>${isReplan ? ' <span class="badge warn">재계획 지점</span>' : ""}
          <div>${esc(detail)}</div>
        </div>`;
      })
      .join("");
  }

  const ar = data.anomaly_report || {};
  anomBox.innerHTML = `<strong>로깅 Agent(F3020):</strong> ${esc(ar.status || "-")}` +
    (ar.warnings?.length ? `<div>경고: ${ar.warnings.map((w) => esc(w.code)).join(", ")}</div>` : "") +
    (ar.diagnosis ? `<div>진단: ${esc(ar.diagnosis)}</div>` : "");
}

// 대화 전송
document.getElementById("chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  showBanner("chatErrorBanner", "");

  appendMessage("user", msg);
  const loadingEl = appendMessage("loading", "Agent가 계획을 수립하고 Tool을 실행 중입니다...");
  const sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;

  try {
    const data = await apiJson(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: getSessionId(),
        message: msg,
        profile: profileSaved ? getProfilePayload() : null,
      }),
    });
    loadingEl.remove();
    appendMessage("bot", data.reply);
    renderTimeline(data);
    lastChatResult = data;

    if (data.error_notice) showBanner("chatErrorBanner", `안내: ${data.error_notice}`);

    const saveBar = document.getElementById("savePlanBox");
    if (data.candidate_combo?.selected_items?.length && data.validation_result?.passed) {
      document.getElementById("planTitleInput").value = `모드${data.mode} 추천 식단`;
      document.getElementById("planDateInput").value = new Date().toISOString().slice(0, 10);
      saveBar.classList.remove("hidden");
    } else {
      saveBar.classList.add("hidden");
    }
  } catch (err) {
    loadingEl.remove();
    showBanner("chatErrorBanner", `요청 실패: ${err.message}`);
  } finally {
    sendBtn.disabled = false;
  }
});

// 계획 저장 (F3015: 같은 날짜 시간 겹침 경고 표시)
document.getElementById("savePlanBtn").addEventListener("click", async () => {
  if (!lastChatResult?.candidate_combo?.selected_items?.length) return;
  const combo = lastChatResult.candidate_combo;
  showBanner("planOverlapNotice", "");

  const payload = {
    session_id: getSessionId(),
    title: document.getElementById("planTitleInput").value.trim() || "식단 계획",
    mode: lastChatResult.mode,
    plan_date: document.getElementById("planDateInput").value || new Date().toISOString().slice(0, 10),
    start_time: document.getElementById("planStartInput").value || null,
    end_time: document.getElementById("planEndInput").value || null,
    items: combo.selected_items,
    nutrition_summary: combo.nutrition_summary || {},
    total_price_krw: combo.total_price_krw || combo.nutrition_summary?.total_price_krw || 0,
    shopping_summary: combo.shopping_summary || {},
  };

  try {
    const data = await apiJson(`${API_BASE}/plans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.overlap_warnings?.length) {
      showBanner("planOverlapNotice", `저장 완료 (⚠️ 시간 중복 알림: ${data.overlap_warnings.map((w) => w.message).join(" / ")})`, true);
    } else {
      showBanner("planOverlapNotice", `식단 계획(#${data.plan.id})이 저장되었습니다.`, true);
    }
  } catch (err) {
    showBanner("chatErrorBanner", `계획 저장 실패: ${err.message}`);
  }
});

// 계획 목록 조회/수정/삭제 (F3015, F3016)
async function loadPlans() {
  const listEl = document.getElementById("plansList");
  showBanner("plansAlert", "");
  try {
    const plans = await apiJson(`${API_BASE}/plans`);
    if (!plans.length) {
      listEl.innerHTML = `<p class="muted">저장된 식단 계획이 없습니다.</p>`;
      return;
    }
    listEl.innerHTML = plans
      .map((p) => {
        const ns = p.nutrition_summary || {};
        return `<div class="plan-card" data-id="${p.id}">
          <div class="panel-head">
            <strong>#${p.id} ${esc(p.title)} <span class="badge">모드 ${esc(p.mode)}</span></strong>
            <span class="muted">생성: ${esc((p.created_at || "").slice(0, 19).replace("T", " "))}</span>
          </div>
          <div>일시: <strong>${esc(p.plan_date)}</strong> ${esc(p.start_time || "-")} ~ ${esc(p.end_time || "-")} | 총액: <strong>${Number(p.total_price_krw).toLocaleString()}원</strong></div>
          <div class="muted">영양: ${ns.total_kcal ?? 0}kcal · 단백질 ${ns.total_protein_g ?? 0}g · 탄수 ${ns.total_carbs_g ?? 0}g · 지방 ${ns.total_fat_g ?? 0}g</div>
          <div class="muted">품목: ${(p.items || []).map((i) => esc(i.name)).join(", ")}</div>
          <div class="plan-edit-grid">
            <input class="edit-title" value="${esc(p.title)}" />
            <input class="edit-date" type="date" value="${esc(p.plan_date)}" />
            <input class="edit-start" type="time" value="${esc(p.start_time || "")}" />
            <input class="edit-end" type="time" value="${esc(p.end_time || "")}" />
          </div>
          <div class="btn-group" style="margin-top:8px">
            <button class="btn primary btn-update-plan">수정 저장</button>
            <button class="btn danger btn-delete-plan">삭제</button>
          </div>
        </div>`;
      })
      .join("");

    listEl.querySelectorAll(".btn-update-plan").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const card = e.target.closest(".plan-card");
        const id = card.dataset.id;
        const body = {
          title: card.querySelector(".edit-title").value.trim(),
          plan_date: card.querySelector(".edit-date").value,
          start_time: card.querySelector(".edit-start").value || null,
          end_time: card.querySelector(".edit-end").value || null,
        };
        try {
          const d = await apiJson(`${API_BASE}/plans/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          await loadPlans();
          if (d.overlap_warnings?.length) {
            showBanner("plansAlert", d.overlap_warnings.map((w) => w.message).join(" / "), true);
          }
        } catch (err) {
          showBanner("plansAlert", `수정 실패: ${err.message}`, false);
        }
      });
    });

    listEl.querySelectorAll(".btn-delete-plan").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const id = e.target.closest(".plan-card").dataset.id;
        try {
          await apiJson(`${API_BASE}/plans/${id}`, { method: "DELETE" });
          await loadPlans();
        } catch (err) {
          showBanner("plansAlert", `삭제 실패: ${err.message}`, false);
        }
      });
    });
  } catch (err) {
    showBanner("plansAlert", `계획 목록 조회 실패: ${err.message}`, false);
  }
}
document.getElementById("refreshPlansBtn").addEventListener("click", loadPlans);

// 실행 로그 조회/분석/삭제 (F3014, F3020)
async function loadLogSessions() {
  const listEl = document.getElementById("logSessionsList");
  showBanner("logsAlert", "");
  try {
    const sessions = await apiJson(`${API_BASE}/logs`);
    if (!sessions.length) {
      listEl.innerHTML = `<p class="muted">저장된 실행 로그가 없습니다.</p>`;
      return;
    }
    listEl.innerHTML = sessions
      .map(
        (s) => `<div class="session-card ${selectedLogSession === s.session_id ? "selected" : ""}" data-sid="${esc(s.session_id)}">
          <div><strong>${esc(s.session_id)}</strong></div>
          <div class="muted">이벤트 ${s.event_count}건 · 재계획 ${s.replan_count}회 · 판정: ${esc(s.latest_anomaly_status || "미분석")}</div>
        </div>`
      )
      .join("");

    listEl.querySelectorAll(".session-card").forEach((card) => {
      card.addEventListener("click", () => selectLogSession(card.dataset.sid));
    });
  } catch (err) {
    showBanner("logsAlert", `로그 목록 조회 실패: ${err.message}`);
  }
}

async function selectLogSession(sid) {
  selectedLogSession = sid;
  document.getElementById("analyzeLogBtn").disabled = false;
  document.getElementById("deleteLogBtn").disabled = false;
  await loadLogSessions();

  try {
    const data = await apiJson(`${API_BASE}/logs/${encodeURIComponent(sid)}`);
    const eventsEl = document.getElementById("logEventsList");
    const anomEl = document.getElementById("anomalyDetailBox");

    if (data.latest_anomaly) {
      anomEl.classList.remove("hidden");
      anomEl.innerHTML = `<strong>최신 판정:</strong> ${esc(data.latest_anomaly.status)} (LLM 진단 호출: ${data.latest_anomaly.llm_called ? "예" : "아니오"})` +
        (data.latest_anomaly.diagnosis ? `<div>${esc(data.latest_anomaly.diagnosis)}</div>` : "");
    } else {
      anomEl.classList.add("hidden");
    }

    eventsEl.innerHTML = (data.events || [])
      .map(
        (ev) => `<div class="trace-item ${ev.event_type === "replan" ? "replan" : ""}">
          <strong>#${ev.seq} [${esc(ev.event_type)}] ${esc(ev.agent_name)}</strong> (${esc(ev.node_name || "-")})
          <div class="muted">${esc(JSON.stringify(ev.payload))}</div>
        </div>`
      )
      .join("");
  } catch (err) {
    showBanner("logsAlert", `로그 상세 조회 실패: ${err.message}`);
  }
}

document.getElementById("analyzeLogBtn").addEventListener("click", async () => {
  if (!selectedLogSession) return;
  try {
    const rep = await apiJson(`${API_BASE}/logs/${encodeURIComponent(selectedLogSession)}/analyze`, { method: "POST" });
    const anomEl = document.getElementById("anomalyDetailBox");
    anomEl.classList.remove("hidden");
    anomEl.innerHTML =
      `<strong>판정:</strong> ${esc(rep.status)} (LLM 호출: ${rep.llm_called ? "예" : "아니오"})` +
      (rep.warnings?.length ? `<div>경고: ${rep.warnings.map((w) => esc(w.message)).join(" / ")}</div>` : "") +
      (rep.diagnosis ? `<div>진단: ${esc(rep.diagnosis)}</div>` : "");
    await loadLogSessions();
  } catch (err) {
    showBanner("logsAlert", `이상 징후 분석 실패: ${err.message}`);
  }
});

document.getElementById("deleteLogBtn").addEventListener("click", async () => {
  if (!selectedLogSession) return;
  try {
    await apiJson(`${API_BASE}/logs/${encodeURIComponent(selectedLogSession)}`, { method: "DELETE" });
    selectedLogSession = null;
    document.getElementById("analyzeLogBtn").disabled = true;
    document.getElementById("deleteLogBtn").disabled = true;
    document.getElementById("logEventsList").innerHTML = `<p class="muted">로그가 삭제되었습니다.</p>`;
    document.getElementById("anomalyDetailBox").classList.add("hidden");
    await loadLogSessions();
  } catch (err) {
    showBanner("logsAlert", `로그 삭제 실패: ${err.message}`);
  }
});

document.getElementById("refreshLogsBtn").addEventListener("click", loadLogSessions);

refreshHealth();
