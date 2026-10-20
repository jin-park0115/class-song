// 학생 화면: 지금 재생, 곡 신청, 내 신청곡, 승인된 곡
(() => {
  const $ = (id) => document.getElementById(id);
  let previewData = null;

  function showError(id, msg) {
    $(id).textContent = msg || "";
    $(id).hidden = !msg;
  }

  function guard(ex) {
    if (ex.status === 401) location.href = "/login";
    else if (ex.code === "ACCOUNT_PENDING") location.href = "/pending";
    else if (ex.code === "PASSWORD_CHANGE_REQUIRED") location.href = "/password";
    else return false;
    return true;
  }

  /** 썸네일(인터넷이 없으면 제목 첫 글자로 대체) */
  function thumb(url, title, cls = "thumb") {
    const box = el("span", { class: `${cls} art` });
    let h = 0;
    for (const ch of title || "") h = (h * 31 + ch.codePointAt(0)) % 360;
    box.style.setProperty("--h", h);
    box.textContent = (title || "?").trim().charAt(0).toUpperCase();
    if (url) {
      const img = el("img", { src: url, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.remove());
      box.append(img);
    }
    return box;
  }

  const badge = (code) => el("span", { class: `badge s-${code}` }, label(code));

  // ── 지금 재생 ───────────────────────────────────────
  async function refreshNow() {
    try {
      const st = await api.get("/api/player/status");
      const cur = st.current;
      const stateText = !st.connected ? "플레이어 연결 끊김" : { play: "지금 재생 중", pause: "일시정지", stop: "정지" }[st.state];
      $("np-state").textContent = stateText;
      $("np-dot").dataset.state = st.connected ? st.state : "error";
      $("np-title").textContent = cur ? cur.title : "재생 중인 곡이 없어요";
      $("np-artist").textContent = (cur && cur.artist) || " ";
      $("np-next").textContent = st.next ? st.next.title : "없음";
      $("np-art").replaceChildren(thumb(cur && cur.thumbnail_url, cur ? cur.title : "♪", "np-thumb"));
      const pct = st.duration ? Math.min(100, (100 * (st.elapsed || 0)) / st.duration) : 0;
      $("np-bar").style.width = `${pct}%`;
    } catch (ex) {
      if (!guard(ex)) $("np-state").textContent = ex.message;
    }
  }

  // ── 곡 신청 ────────────────────────────────────────
  async function preview() {
    showError("req-err", "");
    $("req-ok").hidden = true;
    const url = $("url").value.trim();
    if (!url) return;
    try {
      previewData = await api.post("/api/youtube/preview", { url });
      renderPreview();
    } catch (ex) {
      previewData = null;
      $("preview").hidden = true;
      if (!guard(ex)) showError("req-err", ex.message);
    }
  }

  function renderPreview() {
    const p = previewData;
    const box = $("preview");
    const notes = [];
    if (p.blocked) notes.push(el("p", { class: "warn" }, "강의실에서 재생할 수 없는 곡으로 지정되어 신청할 수 없어요."));
    if (p.my_request) notes.push(el("p", { class: "warn" }, `이미 신청한 곡이에요 (${label(p.my_request.status)}).`));
    for (const w of p.warnings) notes.push(el("p", { class: "warn" }, w));

    const manual = p.metadata_found ? null : el("div", {},
      el("label", { for: "manual-title" }, "곡명"),
      el("input", { id: "manual-title", maxlength: 300, placeholder: "곡 정보를 불러오지 못했어요. 곡명을 적어주세요", required: true }));
    const canSubmit = !p.blocked && !p.my_request;

    box.replaceChildren(
      el("div", { class: "preview-head" },
        thumb(p.thumbnail_url, p.title || p.video_id, "preview-thumb"),
        el("div", {},
          el("p", { class: "preview-title" }, p.title || "제목을 불러오지 못했어요"),
          el("p", { class: "muted small" }, p.channel || ""),
          el("a", { href: p.source_url, target: "_blank", rel: "noopener", class: "small" }, "YouTube에서 열기 ↗"))),
      ...notes,
      manual,
      el("label", { for: "comment" }, "한마디 ", el("span", { class: "muted" }, "(선택)")),
      el("input", { id: "comment", maxlength: 300, placeholder: "예: 월요일 아침에 듣고 싶어요" }),
      el("button", { type: "submit", class: "btn primary wide", disabled: !canSubmit }, "이 곡 신청하기"));
    box.hidden = false;
  }

  async function submit(e) {
    e.preventDefault();
    if (!previewData) return preview();
    showError("req-err", "");
    try {
      const res = await api.post("/api/requests", {
        url: previewData.source_url,
        comment: $("comment")?.value || null,
        title: $("manual-title")?.value || null,
      });
      $("req-ok").textContent = `신청했어요: ${res.track.title} — 관리자 승인을 기다리는 중이에요.`;
      $("req-ok").hidden = false;
      $("url").value = "";
      $("preview").hidden = true;
      previewData = null;
      loadMine();
    } catch (ex) {
      if (!guard(ex)) showError("req-err", ex.message);
    }
  }

  // ── 내 신청곡 ───────────────────────────────────────
  async function loadMine() {
    try {
      const res = await api.get("/api/requests/me");
      $("quota").textContent = `승인 대기 ${res.pending} / ${res.pending_limit}`;
      const list = $("mine");
      if (!res.items.length) return list.replaceChildren(el("li", { class: "muted" }, "아직 신청한 곡이 없어요."));
      list.replaceChildren(...res.items.map((r) => {
        const extra = [];
        if (r.status === "REJECTED") extra.push(el("p", { class: "small warn" }, `반려 사유: ${label(r.reject_reason)}${r.reject_note ? ` — ${r.reject_note}` : ""}`));
        if (r.status === "APPROVED") extra.push(el("p", { class: "small muted" }, `음원 상태: ${label(r.track.media_status)}`));
        if (r.comment) extra.push(el("p", { class: "small muted" }, `“${r.comment}”`));
        return el("li", {},
          thumb(r.track.thumbnail_url, r.track.title),
          el("div", { class: "tl-body" },
            el("p", { class: "tl-title" }, r.track.title),
            el("p", { class: "small muted" }, [r.track.artist_or_channel, fmtTime(r.created_at)].filter(Boolean).join(" · ")),
            ...extra),
          el("div", { class: "tl-side" },
            badge(r.status),
            r.status === "PENDING" ? el("button", { class: "btn small", onclick: () => cancel(r) }, "취소") : null));
      }));
    } catch (ex) {
      if (!guard(ex)) showError("page-error", ex.message);
    }
  }

  async function cancel(r) {
    if (!confirm(`'${r.track.title}' 신청을 취소할까요?`)) return;
    try {
      await api.del(`/api/requests/${r.id}`);
      loadMine();
    } catch (ex) {
      if (!guard(ex)) showError("page-error", ex.message);
    }
  }

  // ── 승인된 곡 ───────────────────────────────────────
  let searchTimer;
  async function loadApproved() {
    try {
      const q = $("q").value.trim();
      const res = await api.get(`/api/tracks?status=approved${q ? `&q=${encodeURIComponent(q)}` : ""}`);
      const list = $("approved");
      if (!res.items.length) return list.replaceChildren(el("li", { class: "muted" }, q ? "검색 결과가 없어요." : "아직 승인된 곡이 없어요."));
      list.replaceChildren(...res.items.map((t) =>
        el("li", {},
          thumb(t.thumbnail_url, t.title),
          el("div", { class: "tl-body" },
            el("p", { class: "tl-title" }, t.title),
            el("p", { class: "small muted" }, [t.artist_or_channel, fmtDur(t.duration_seconds)].filter(Boolean).join(" · "))),
          el("div", { class: "tl-side" }, badge(t.media_status)))));
    } catch (ex) {
      if (!guard(ex)) showError("page-error", ex.message);
    }
  }

  // ── 연결 ───────────────────────────────────────────
  $("preview-btn").addEventListener("click", preview);
  $("url").addEventListener("input", () => {
    previewData = null; // 주소를 고치면 다시 확인해야 신청할 수 있다
    $("preview").hidden = true;
  });
  $("url").addEventListener("change", preview);
  $("url").addEventListener("paste", () => setTimeout(preview, 0));
  $("url").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !previewData) {
      e.preventDefault();
      preview();
    }
  });
  $("req-form").addEventListener("submit", submit);
  $("q").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadApproved, 250);
  });
  $("logout").addEventListener("click", async () => {
    try { await api.post("/api/auth/logout"); } finally { location.href = "/login"; }
  });

  (async () => {
    try {
      const me = await api.get("/api/auth/me");
      const home = api.homeFor(me);
      if (home !== "/me" && home !== "/admin") return (location.href = home);
      $("who").textContent = `${me.name} (${me.login_id})`;
      refreshNow();
      loadMine();
      loadApproved();
      setInterval(refreshNow, 5000);
    } catch (ex) {
      guard(ex);
    }
  })();
})();
