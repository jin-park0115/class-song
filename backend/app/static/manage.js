// 관리 화면: 가입 승인 · 신청 승인 · 음원 라이브러리
(() => {
  const $ = (id) => document.getElementById(id);
  const BASES = [
    ["OWNED_FILE", "운영자가 적법하게 보유한 파일"],
    ["LICENSED", "이용 허락/라이선스"],
    ["CC_LICENSE", "CC 라이선스"],
    ["PUBLIC_DOMAIN", "퍼블릭 도메인"],
    ["OTHER", "기타(메모 필수)"],
  ];
  const REASONS = ["DUPLICATE", "INAPPROPRIATE", "NO_RIGHTS", "TOO_LONG", "OTHER"];

  function flash(msg, isError = false) {
    $("page-error").hidden = !isError;
    $("page-ok").hidden = isError;
    (isError ? $("page-error") : $("page-ok")).textContent = msg;
    if (!isError) setTimeout(() => ($("page-ok").hidden = true), 4000);
  }

  async function run(fn, okMsg) {
    try {
      await fn();
      if (okMsg) flash(okMsg);
    } catch (ex) {
      if (ex.status === 401) location.href = "/login";
      else flash(ex.message, true);
    }
  }

  function thumb(url, title) {
    const box = el("span", { class: "thumb art" });
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

  // ── 탭 ─────────────────────────────────────────────
  function showTab(name) {
    document.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      $(`panel-${b.dataset.tab}`).hidden = !on;
    });
    history.replaceState(null, "", `#${name}`);
  }
  // 탭을 열 때마다 최신 목록을 다시 불러온다(그 사이 들어온 가입·신청 반영)
  const LOADERS = { signups: () => loadUsers(), requests: () => loadRequests(), library: () => loadLibrary() };
  document.querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      showTab(b.dataset.tab);
      run(() => Promise.all([LOADERS[b.dataset.tab](), counts()]));
    })
  );

  async function counts() {
    const [u, r, l] = await Promise.all([
      api.get("/api/admin/users?status=PENDING_APPROVAL"),
      api.get("/api/admin/requests?status=PENDING"),
      api.get("/api/admin/tracks"),
    ]);
    $("c-signups").textContent = u.items.length || "";
    $("c-requests").textContent = r.items.length || "";
    const todo = l.items.filter((t) => t.media_status !== "READY" && t.media_status !== "BLOCKED").length;
    $("c-library").textContent = todo || "";
  }

  // ── 회원 ───────────────────────────────────────────
  async function loadUsers() {
    const status = $("user-filter").value;
    const res = await api.get(`/api/admin/users${status ? `?status=${status}` : ""}`);
    const list = $("users");
    if (!res.items.length) return list.replaceChildren(el("li", { class: "muted" }, "해당하는 회원이 없어요."));
    list.replaceChildren(...res.items.map((u) => {
      const actions = [];
      const act = (text, path, cls, body, msg) =>
        el("button", { class: `btn small ${cls || ""}`, onclick: () => run(async () => {
          const out = await api.post(`/api/admin/users/${u.id}/${path}`, body ? body() : {});
          if (path === "reset-password") {
            alert(`${u.name}(${u.login_id})의 임시 비밀번호\n\n${out.temporary_password}\n\n이 창을 닫으면 다시 볼 수 없어요. 학생에게 직접 전달하세요.`);
          }
          await refreshAll();
        }, msg) }, text);
      if (u.role === "ADMIN") actions.push(el("span", { class: "muted small" }, "관리자(CLI 관리)"));
      else {
        if (["PENDING_APPROVAL", "REJECTED"].includes(u.account_status)) actions.push(act("승인", "approve", "primary", null, `${u.name}님을 승인했어요.`));
        if (u.account_status === "PENDING_APPROVAL") actions.push(act("거절", "reject", "", () => ({ note: prompt("거절 사유(선택)") || null }), `${u.name}님 가입을 거절했어요.`));
        if (u.account_status === "ACTIVE") {
          actions.push(act("임시 비밀번호", "reset-password", ""));
          actions.push(act("비활성화", "disable", "", null, `${u.name}님을 비활성화했어요.`));
        }
        if (u.account_status === "DISABLED") actions.push(act("다시 활성화", "enable", "", null, `${u.name}님을 다시 활성화했어요.`));
      }
      return el("li", {},
        el("span", { class: "thumb art avatar" }, (u.name || "?").charAt(0)),
        el("div", { class: "tl-body" },
          el("p", { class: "tl-title" }, `${u.name} `, el("span", { class: "muted small" }, `@${u.login_id}`)),
          el("p", { class: "small muted" }, `가입 ${fmtTime(u.created_at)}${u.last_login_at ? ` · 최근 로그인 ${fmtTime(u.last_login_at)}` : ""}`),
          u.signup_note ? el("p", { class: "small" }, `메모: ${u.signup_note}`) : null,
          u.reject_note ? el("p", { class: "small warn" }, `거절 사유: ${u.reject_note}`) : null),
        el("div", { class: "tl-side" }, badge(u.account_status), el("div", { class: "actions" }, actions)));
    }));
  }

  // ── 신청 ───────────────────────────────────────────
  async function loadRequests() {
    const status = $("req-filter").value;
    const res = await api.get(`/api/admin/requests?status=${status}`);
    const list = $("requests");
    if (!res.items.length) return list.replaceChildren(el("li", { class: "muted" }, "해당하는 신청이 없어요."));
    list.replaceChildren(...res.items.map((r) => {
      const t = r.track;
      const reason = el("select", { "aria-label": "반려 사유" }, REASONS.map((c) => el("option", { value: c }, label(c))));
      const note = el("input", { placeholder: "메모(기타일 때 필수)", maxlength: 300, "aria-label": "반려 메모" });
      const review = (decision) => run(async () => {
        await api.post(`/api/admin/requests/${r.id}/review`, decision === "APPROVE"
          ? { decision }
          : { decision, reject_reason: reason.value, reject_note: note.value || null });
        await refreshAll();
      }, decision === "APPROVE" ? `'${t.title}' 승인 — 음원 라이브러리에서 음원을 준비하세요.` : `'${t.title}' 반려했어요.`);
      const pendingActions = r.status === "PENDING" ? el("div", { class: "review" },
        el("button", { class: "btn small primary", onclick: () => review("APPROVE") }, "승인"),
        reason, note,
        el("button", { class: "btn small", onclick: () => review("REJECT") }, "반려")) : null;
      return el("li", {},
        thumb(t.thumbnail_url, t.title),
        el("div", { class: "tl-body" },
          el("p", { class: "tl-title" }, t.title),
          el("p", { class: "small muted" }, [t.artist_or_channel, fmtDur(t.duration_seconds)].filter(Boolean).join(" · ")),
          el("p", { class: "small" }, `신청: ${r.requester.name} (@${r.requester.login_id}) · ${fmtTime(r.created_at)}`,
            r.same_track_requests > 1 ? el("b", {}, ` · 같은 곡 신청 ${r.same_track_requests}명`) : null),
          r.comment ? el("p", { class: "small muted" }, `“${r.comment}”`) : null,
          r.reject_reason ? el("p", { class: "small warn" }, `반려: ${label(r.reject_reason)}${r.reject_note ? ` — ${r.reject_note}` : ""}`) : null,
          el("a", { href: t.source_url, target: "_blank", rel: "noopener", class: "small" }, "YouTube에서 열기 ↗"),
          pendingActions),
        el("div", { class: "tl-side" }, badge(r.status)));
    }));
  }

  // ── 음원 라이브러리 ─────────────────────────────────
  async function loadLibrary() {
    const res = await api.get("/api/admin/tracks");
    const list = $("library");
    if (!res.items.length) return list.replaceChildren(el("li", { class: "muted" }, "승인된 곡이 없어요. 신청 승인 탭에서 먼저 승인하세요."));
    list.replaceChildren(...res.items.map((t) => {
      const basis = el("select", { "aria-label": "권리 근거" },
        el("option", { value: "" }, "근거 선택…"),
        BASES.map(([v, text]) => el("option", { value: v, selected: t.rights_basis === v }, text)));
      const note = el("input", { value: t.rights_note || "", placeholder: "메모(출처·라이선스 등)", maxlength: 500, "aria-label": "권리 메모" });
      const setRights = (rights_status) => run(async () => {
        await api.patch(`/api/admin/tracks/${t.id}`, { rights_status, rights_basis: basis.value || null, rights_note: note.value || null });
        await refreshAll();
      }, rights_status === "CONFIRMED" ? `'${t.title}' 권리 확인 완료` : `'${t.title}' 사용 불가로 지정`);

      const file = el("input", { type: "file", accept: ".mp3,.ogg,.flac,audio/mpeg,audio/ogg,audio/flac", "aria-label": "음원 파일", disabled: t.rights_status !== "CONFIRMED" });
      const uploadBtn = el("button", { class: "btn small primary", disabled: t.rights_status !== "CONFIRMED", onclick: () => run(async () => {
        if (!file.files.length) throw new Error("파일을 선택하세요.");
        uploadBtn.disabled = true;
        uploadBtn.textContent = "올리는 중…";
        const fd = new FormData();
        fd.append("file", file.files[0]);
        try {
          await api.upload(`/api/admin/tracks/${t.id}/media`, fd);
        } finally {
          uploadBtn.textContent = "올리기";
        }
        await refreshAll();
      }, `'${t.title}' 음원 등록 완료 — 이제 대기열에 넣을 수 있어요.`) }, t.has_media ? "다시 올리기" : "올리기");

      const queueBtn = el("button", { class: "btn small primary", disabled: t.media_status !== "READY", onclick: () => run(
        () => api.post("/api/admin/queue", { track_id: t.id }), `'${t.title}'을(를) 재생 대기열 끝에 넣었어요.`) }, "＋ 대기열에 추가");

      return el("li", { class: `lib-item m-${t.media_status}` },
        el("div", { class: "lib-head" },
          thumb(t.thumbnail_url, t.title),
          el("div", { class: "tl-body" },
            el("p", { class: "tl-title" }, t.title),
            el("p", { class: "small muted" }, [t.artist_or_channel, fmtDur(t.duration_seconds)].filter(Boolean).join(" · ")),
            t.source_url ? el("a", { href: t.source_url, target: "_blank", rel: "noopener", class: "small" }, "YouTube ↗") : null),
          el("div", { class: "tl-side" }, badge(t.rights_status), badge(t.media_status))),
        el("ol", { class: "steps" },
          el("li", {}, el("b", {}, "① 이용 권리 "),
            el("div", { class: "step-row" }, basis, note,
              el("button", { class: "btn small", onclick: () => setRights("CONFIRMED") }, "확인"),
              el("button", { class: "btn small", onclick: () => setRights("DENIED") }, "사용 불가"))),
          el("li", {}, el("b", {}, "② 음원 파일 "),
            el("div", { class: "step-row" }, file, uploadBtn,
              t.rights_status !== "CONFIRMED" ? el("span", { class: "muted small" }, "권리 확인 후 올릴 수 있어요") : null)),
          el("li", {}, el("b", {}, "③ 재생 "), el("div", { class: "step-row" }, queueBtn))));
    }));
  }

  async function refreshAll() {
    await Promise.all([counts(), loadUsers(), loadRequests(), loadLibrary()]);
  }

  $("user-filter").addEventListener("change", () => run(loadUsers));
  $("req-filter").addEventListener("change", () => run(loadRequests));
  $("logout").addEventListener("click", async () => {
    try { await api.post("/api/auth/logout"); } finally { location.href = "/login"; }
  });

  (async () => {
    try {
      const me = await api.get("/api/auth/me");
      if (me.role !== "ADMIN") return (location.href = api.homeFor(me));
      $("who").textContent = `${me.name} (${me.login_id})`;
      showTab(["signups", "requests", "library"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "requests");
      await refreshAll();
      setInterval(() => run(counts), 15000);
    } catch (ex) {
      if (ex.status === 401) location.href = "/login";
      else flash(ex.message, true);
    }
  })();
})();
