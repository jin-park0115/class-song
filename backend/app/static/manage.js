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
  const LOADERS = {
    signups: () => loadUsers(), requests: () => loadRequests(), library: () => loadLibrary(),
    playlists: () => loadPlaylists(), schedules: () => loadSchedules(),
  };
  document.querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      showTab(b.dataset.tab);
      run(() => Promise.all([LOADERS[b.dataset.tab](), counts()]));
    })
  );

  async function counts() {
    const [u, r, l, p] = await Promise.all([
      api.get("/api/admin/users?status=PENDING_APPROVAL"),
      api.get("/api/admin/requests?status=PENDING"),
      api.get("/api/admin/tracks"),
      api.get("/api/playlists"),
    ]);
    // 플레이리스트 배지: 재생 불가 곡이 섞인 플레이리스트 수
    $("c-playlists").textContent = p.items.filter((x) => x.unplayable_count).length || "";
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
      }, decision === "APPROVE" ? `'${t.title}' 승인 — 음원 라이브러리에서 이용 권리를 확인하고 음원을 준비하세요.` : `'${t.title}' 반려했어요.`);
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

      const removeBtn = el("button", { class: "btn small", "aria-label": `${t.title} 삭제`, onclick: () => {
        if (!confirm(`'${t.title}'을(를) 지울까요?\n대기열에서 빠지고, 음원 파일과 학생들의 신청 기록도 함께 지워져요.`)) return;
        run(async () => {
          await api.del(`/api/admin/tracks/${t.id}`);
          await refreshAll();
        }, `'${t.title}' 삭제했어요.`);
      } }, "삭제");

      return el("li", { class: `lib-item m-${t.media_status}` },
        el("div", { class: "lib-head" },
          thumb(t.thumbnail_url, t.title),
          el("div", { class: "tl-body" },
            el("p", { class: "tl-title" }, t.title),
            el("p", { class: "small muted" }, [t.artist_or_channel, fmtDur(t.duration_seconds)].filter(Boolean).join(" · ")),
            t.source_url ? el("a", { href: t.source_url, target: "_blank", rel: "noopener", class: "small" }, "YouTube ↗") : null),
          el("div", { class: "tl-side" }, badge(t.rights_status), badge(t.media_status), removeBtn)),
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


  // ── 플레이리스트 ───────────────────────────────────
  let selectedPl = null;

  async function loadPlaylists() {
    const res = await api.get("/api/playlists");
    const list = $("pl-list");
    if (!res.items.length) {
      list.replaceChildren(el("li", { class: "muted small" }, "아직 없어요."));
      $("pl-editor").replaceChildren(el("p", { class: "muted" }, "위에서 새 플레이리스트를 만드세요."));
      return;
    }
    if (!res.items.some((p) => p.id === selectedPl)) selectedPl = res.items[0].id;
    list.replaceChildren(...res.items.map((p) => el("li", {},
      el("button", {
        class: `pl-pick${p.id === selectedPl ? " on" : ""}`, "aria-current": p.id === selectedPl ? "true" : null,
        onclick: () => { selectedPl = p.id; run(loadPlaylists); },
      },
        el("b", {}, p.name),
        el("span", { class: "small muted" }, `${p.item_count}곡 · ${fmtTotal(p.total_seconds)}${p.is_active ? "" : " · 꺼짐"}`),
        p.unplayable_count ? el("span", { class: "small warn" }, `⚠ 재생 불가 ${p.unplayable_count}곡`) : null))));
    await renderPlaylist(selectedPl);
  }

  async function renderPlaylist(id) {
    const [p, ready] = await Promise.all([api.get(`/api/playlists/${id}`), api.get("/api/tracks?status=ready")]);
    const ids = p.items.map((i) => i.track.id);
    const save = (next, msg) => run(async () => {
      await api.put(`/api/playlists/${id}/items`, { track_ids: next });
      await loadPlaylists();
    }, msg);
    const move = (from, to) => {
      if (to < 0 || to >= ids.length || from === to) return;
      const next = [...ids];
      next.splice(to, 0, next.splice(from, 1)[0]);
      save(next);
    };

    const name = el("input", { value: p.name, maxlength: 100, "aria-label": "플레이리스트 이름" });
    const active = el("input", { type: "checkbox", checked: p.is_active });
    const head = el("div", { class: "row pl-head" }, name,
      el("label", { class: "small nowrap" }, active, " 사용"),
      el("button", { class: "btn small", onclick: () => run(async () => {
        await api.patch(`/api/playlists/${id}`, { name: name.value, is_active: active.checked });
        await loadPlaylists();
      }, "저장했어요.") }, "저장"),
      el("button", { class: "btn small", onclick: () => {
        if (!confirm(`'${p.name}' 플레이리스트를 지울까요? (곡 파일은 지워지지 않아요)`)) return;
        run(async () => {
          await api.del(`/api/playlists/${id}`);
          selectedPl = null;
          await loadPlaylists();
        }, "삭제했어요.");
      } }, "삭제"));

    const summary = el("p", { class: "small muted" }, `${p.item_count}곡 · 총 ${fmtTotal(p.total_seconds)}`,
      p.unknown_duration_count ? ` (길이를 모르는 곡 ${p.unknown_duration_count}개 빼고)` : "",
      p.unplayable_count ? el("span", { class: "warn" }, ` · ⚠ 재생 불가 곡 ${p.unplayable_count}개는 재생 때 건너뛰어요. 빼고 저장하세요.`) : "");

    // 끌어서 놓기(마우스) + ↑↓ 버튼(키보드·휴대폰)
    let dragFrom = null;
    const items = el("ol", { class: "pl-items" }, p.items.map((it, idx) => {
      const t = it.track;
      const bad = t.media_status !== "READY";
      const li = el("li", {
        draggable: "true", class: bad ? "bad" : null,
        ondragstart: (e) => { dragFrom = idx; e.dataTransfer.effectAllowed = "move"; li.classList.add("dragging"); },
        ondragend: () => li.classList.remove("dragging"),
        ondragover: (e) => { e.preventDefault(); li.classList.add("over"); },
        ondragleave: () => li.classList.remove("over"),
        ondrop: (e) => { e.preventDefault(); li.classList.remove("over"); if (dragFrom != null) move(dragFrom, idx); dragFrom = null; },
      },
        el("span", { class: "grip", "aria-hidden": "true" }, "⠿"),
        el("span", { class: "idx" }, idx + 1),
        thumb(t.thumbnail_url, t.title),
        el("span", { class: "pl-title" }, t.title, bad ? el("span", { class: `badge s-${t.media_status}` }, label(t.media_status)) : null),
        el("span", { class: "small muted" }, fmtDur(t.duration_seconds) || "—"),
        el("span", { class: "pl-ops" },
          el("button", { class: "icon", "aria-label": `${t.title} 위로`, disabled: idx === 0, onclick: () => move(idx, idx - 1) }, "↑"),
          el("button", { class: "icon", "aria-label": `${t.title} 아래로`, disabled: idx === ids.length - 1, onclick: () => move(idx, idx + 1) }, "↓"),
          el("button", { class: "icon", "aria-label": `${t.title} 빼기`, onclick: () => save(ids.filter((_, i) => i !== idx), "뺐어요.") }, "✕")));
      return li;
    }));
    if (!p.items.length) items.append(el("li", { class: "muted small" }, "아직 곡이 없어요. 아래에서 추가하세요."));

    const pick = el("select", { "aria-label": "추가할 곡" }, ready.items.length
      ? ready.items.map((t) => el("option", { value: t.id }, `${t.title}${t.duration_seconds ? ` (${fmtDur(t.duration_seconds)})` : ""}`))
      : el("option", { value: "" }, "재생 가능한 곡이 없어요 — 음원 라이브러리에서 먼저 준비하세요"));
    const add = el("div", { class: "row pl-add" }, pick,
      el("button", { class: "btn small primary", disabled: !ready.items.length, onclick: () => save([...ids, Number(pick.value)], "곡을 추가했어요.") }, "＋ 추가"));
    $("pl-editor").replaceChildren(head, summary, items, add);
  }

  $("pl-new").addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const p = await api.post("/api/playlists", { name: $("pl-new-name").value });
      $("pl-new-name").value = "";
      selectedPl = p.id;
      await loadPlaylists();
    }, "만들었어요. 이제 곡을 추가하세요.");
  });

  // ── 스케줄 ───────────────────────────────────────
  let schedules = [];
  let playlistOptions = [];
  let maxVolume = 40;
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };

  async function loadSchedules() {
    const [s, p, st] = await Promise.all([api.get("/api/schedules"), api.get("/api/playlists"), api.get("/api/player/status")]);
    schedules = s.items;
    playlistOptions = p.items;
    maxVolume = st.max_volume;
    $("sc-cap").textContent = maxVolume;
    renderGrid();
    renderScheduleList();
  }

  /** 주간 시간표: 요일별 세로 칸에 구간을 시각 비율로 놓는다. 겹침으로 막힌 스케줄은 빨갛게 표시한다. */
  function renderGrid(conflictIds = []) {
    const mins = schedules.flatMap((s) => [toMin(s.start_time), toMin(s.end_time)]);
    const from = Math.min(8 * 60, ...mins.map((m) => Math.floor(m / 60) * 60));
    const to = Math.max(18 * 60, ...mins.map((m) => Math.ceil(m / 60) * 60));
    const span = to - from;
    const pct = (m) => `${((m - from) / span) * 100}%`;
    const hours = [];
    for (let h = from; h < to; h += 60) hours.push(h);
    const grid = $("sc-grid");
    grid.style.setProperty("--hours", span / 60);
    grid.replaceChildren(
      el("div", { class: "tt-col tt-axis", "aria-hidden": "true" }, el("div", { class: "tt-day" }, ""),
        el("div", { class: "tt-body" }, hours.map((h) => el("span", { style: `top:${pct(h)}` }, `${String(h / 60).padStart(2, "0")}시`)))),
      ...[...WEEKDAYS].map((d, i) => el("div", { class: "tt-col" },
        el("div", { class: "tt-day" }, d),
        el("div", { class: "tt-body" }, schedules.filter((s) => s.weekdays.includes(i + 1)).map((s) =>
          el("button", {
            // 45분보다 짧은 구간은 칸이 낮아 이름만 보인다
            class: `tt-block${s.is_enabled ? "" : " off"}${conflictIds.includes(s.id) ? " conflict" : ""}${toMin(s.end_time) - toMin(s.start_time) < 45 ? " short" : ""}`,
            style: `top:${pct(toMin(s.start_time))};height:${pct(from + toMin(s.end_time) - toMin(s.start_time))}`,
            title: `${s.name} ${s.start_time}–${s.end_time}${s.is_enabled ? "" : " (꺼짐)"}`,
            "aria-label": `${d}요일 ${s.name} ${s.start_time}부터 ${s.end_time}까지${s.is_enabled ? "" : ", 꺼짐"}`,
            onclick: () => openForm(s),
          }, el("b", {}, s.name), el("span", {}, `${s.start_time}–${s.end_time}`)))))));
  }

  function renderScheduleList() {
    const list = $("sc-list");
    if (!schedules.length) return list.replaceChildren(el("li", { class: "muted" }, "아직 스케줄이 없어요. ＋ 새 스케줄로 만드세요."));
    list.replaceChildren(...schedules.map((s) => el("li", {},
      el("div", { class: "tl-body" },
        el("p", { class: "tl-title" }, s.name, s.is_enabled ? null : el("span", { class: "muted small" }, " · 꺼짐")),
        el("p", { class: "small" }, `${s.when} · ${s.playlist_name}`),
        el("p", { class: "small muted" },
          `볼륨 ${s.effective_volume}%${s.volume_clamped ? ` (설정 ${s.volume}% → 상한 적용)` : ""}`,
          ` · 페이드 ${s.fade_in_seconds}/${s.fade_out_seconds}초 · ${label(s.playback_mode)} · 끝나면 ${label(s.on_playlist_end)} · 재시작 시 ${label(s.rejoin_policy)}`)),
      el("div", { class: "tl-side" }, el("div", { class: "actions" },
        el("button", { class: "btn small", onclick: () => run(async () => {
          await api.patch(`/api/schedules/${s.id}`, { is_enabled: !s.is_enabled });
          await loadSchedules();
        }, s.is_enabled ? `'${s.name}'을(를) 껐어요.` : `'${s.name}'을(를) 켰어요.`) }, s.is_enabled ? "끄기" : "켜기"),
        el("button", { class: "btn small", onclick: () => openForm(s) }, "편집"))))));
  }

  function openForm(s = null) {
    const form = $("sc-form");
    if (!playlistOptions.length) return flash("먼저 플레이리스트 탭에서 플레이리스트를 만드세요.", true);
    const v = s || {
      name: "", weekdays: [1, 2, 3, 4, 5], start_time: "08:00", end_time: "09:00", playlist_id: playlistOptions[0].id,
      volume: Math.min(25, maxVolume), fade_in_seconds: 3, fade_out_seconds: 3,
      rejoin_policy: "RESTART", playback_mode: "SEQUENTIAL", on_playlist_end: "REPEAT", is_enabled: true,
    };
    const field = (text, input, hint) => el("label", { class: "sc-field" }, el("span", {}, text), input, hint ? el("small", { class: "muted" }, hint) : null);
    const choice = (value, options) => el("select", {}, options.map((o) => el("option", { value: o, selected: o === value }, label(o))));
    const num = (value, min, max) => el("input", { type: "number", min, max, value, inputmode: "numeric" });
    const f = {
      name: el("input", { value: v.name, maxlength: 100, required: true, placeholder: "예: 1교시 전" }),
      days: [...WEEKDAYS].map((d, i) => el("input", { type: "checkbox", value: i + 1, checked: v.weekdays.includes(i + 1) })),
      start: el("input", { type: "time", value: v.start_time, step: 60, required: true }),
      end: el("input", { type: "time", value: v.end_time, step: 60, required: true }),
      playlist: el("select", {}, playlistOptions.map((p) => el("option", { value: p.id, selected: p.id === v.playlist_id }, `${p.name} (${p.item_count}곡${p.is_active ? "" : ", 꺼짐"})`))),
      volume: num(v.volume, 0, 100),
      fadeIn: num(v.fade_in_seconds, 0, 30),
      fadeOut: num(v.fade_out_seconds, 0, 30),
      mode: choice(v.playback_mode, ["SEQUENTIAL", "SHUFFLE"]),
      endAction: choice(v.on_playlist_end, ["REPEAT", "STOP"]),
      rejoin: choice(v.rejoin_policy, ["RESTART", "SKIP"]),
      enabled: el("input", { type: "checkbox", checked: v.is_enabled }),
    };
    const err = el("p", { class: "alert", role: "alert", hidden: true });
    const body = () => ({
      name: f.name.value.trim(),
      weekdays: f.days.filter((c) => c.checked).map((c) => Number(c.value)),
      start_time: f.start.value, end_time: f.end.value, playlist_id: Number(f.playlist.value),
      volume: Number(f.volume.value), fade_in_seconds: Number(f.fadeIn.value), fade_out_seconds: Number(f.fadeOut.value),
      playback_mode: f.mode.value, on_playlist_end: f.endAction.value, rejoin_policy: f.rejoin.value, is_enabled: f.enabled.checked,
    });
    form.onsubmit = async (e) => {
      e.preventDefault();
      err.hidden = true;
      try {
        if (s) await api.patch(`/api/schedules/${s.id}`, body());
        else await api.post("/api/schedules", body());
        form.hidden = true;
        await loadSchedules();
        flash(s ? "저장했어요." : "스케줄을 만들었어요.");
      } catch (ex) {
        if (ex.status === 401) return (location.href = "/login");
        err.textContent = ex.code === "VALIDATION_FAILED"
          ? "입력값을 확인하세요: 이름·요일·시각(시작이 종료보다 빨라야 함)·볼륨 0–100·페이드 0–30초"
          : ex.message;
        err.hidden = false;
        renderGrid(((ex.detail && ex.detail.conflicts) || []).map((c) => c.id)); // 겹치는 스케줄을 시간표에 빨갛게
      }
    };
    form.replaceChildren(
      el("h3", {}, s ? `'${s.name}' 편집` : "새 스케줄"),
      field("이름", f.name),
      el("fieldset", { class: "sc-days" }, el("legend", {}, "요일"), f.days.map((c, i) => el("label", {}, c, WEEKDAYS[i]))),
      el("div", { class: "sc-row" }, field("시작", f.start), field("종료", f.end, "자정을 넘을 수 없어요")),
      field("플레이리스트", f.playlist),
      el("div", { class: "sc-row" },
        field("볼륨(%)", f.volume, `최대 ${maxVolume}%로 제한돼요`), field("페이드 인(초)", f.fadeIn, "0–30"), field("페이드 아웃(초)", f.fadeOut, "0–30")),
      el("div", { class: "sc-row" },
        field("재생 순서", f.mode), field("플레이리스트가 끝나면", f.endAction),
        field("구간 중 서버가 다시 켜지면", f.rejoin, "남은 시간이 2분 미만이면 재생하지 않아요")),
      el("label", { class: "sc-check" }, f.enabled, " 사용 (켜 둔 스케줄끼리는 겹칠 수 없어요)"),
      err,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "저장"),
        el("button", { type: "button", class: "btn", onclick: () => { form.hidden = true; renderGrid(); } }, "취소"),
        s ? el("button", { type: "button", class: "btn", onclick: () => {
          if (!confirm(`'${s.name}' 스케줄을 지울까요?`)) return;
          run(async () => {
            await api.del(`/api/schedules/${s.id}`);
            form.hidden = true;
            await loadSchedules();
          }, "삭제했어요.");
        } }, "삭제") : null));
    form.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
    f.name.focus();
  }
  $("sc-new").addEventListener("click", () => openForm());

  async function refreshAll() {
    await Promise.all([counts(), loadUsers(), loadRequests(), loadLibrary(), loadPlaylists(), loadSchedules()]);
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
      showTab(Object.keys(LOADERS).includes(location.hash.slice(1)) ? location.hash.slice(1) : "requests");
      await refreshAll();
      setInterval(() => run(counts), 15000);
    } catch (ex) {
      if (ex.status === 401) location.href = "/login";
      else flash(ex.message, true);
    }
  })();
})();
