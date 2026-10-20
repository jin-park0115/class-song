// 운영 콘솔(DJ 컨트롤러 화면). MVP는 3초 폴링으로 상태를 갱신하고(FR-016은 P1에서 SSE로 교체),
// 그 사이 진행 시간은 브라우저에서 보간한다.
(() => {
  const POLL_MS = 3000;
  const RING = 2 * Math.PI * 46; // jog-progress 원 둘레(viewBox 기준)
  const $ = (id) => document.getElementById(id);
  const STATE_LABEL = { play: "재생 중", pause: "일시정지", stop: "정지", unknown: "MPD 연결 끊김" };

  let status = null;
  let statusAt = 0; // status 를 받은 시각(ms)
  let queue = [];
  let queueVersion;
  let volumeEditing = false;

  // ── 표시 유틸 ───────────────────────────────────────
  const fmt = (sec) => {
    if (sec == null || isNaN(sec)) return "0:00";
    const s = Math.max(0, Math.floor(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  const clock = (ms) => new Date(ms).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  const songName = (s) => (s.artist ? `${s.artist} - ${s.title}` : s.title);

  function paintArt(el, song) {
    el.style.backgroundImage = "";
    el.classList.remove("has-img");
    if (!song) {
      el.textContent = "";
      el.style.removeProperty("--h");
      return;
    }
    const key = song.file || song.title || "";
    let h = 0;
    for (const ch of key) h = (h * 31 + ch.codePointAt(0)) % 360;
    el.style.setProperty("--h", h);
    el.textContent = (song.title || "?").trim().charAt(0).toUpperCase();
    if (song.thumbnail_url) {
      // 썸네일을 못 불러와도(인터넷 없음) 아래 그라데이션이 보인다
      el.style.backgroundImage = `url("${encodeURI(song.thumbnail_url)}"), linear-gradient(135deg, hsl(${h} 70% 58%), hsl(${h + 50} 65% 38%))`;
      el.classList.add("has-img");
    }
  }

  function showError(msg) {
    $("page-error").textContent = msg || "";
    $("page-error").hidden = !msg;
  }

  function handleAuth(ex) {
    if (ex.status === 401) {
      location.href = "/login";
      return true;
    }
    return false;
  }

  /** 지금 시점의 재생 위치(초). 재생 중이면 마지막 조회 이후 흐른 시간을 더한다. */
  function liveElapsed() {
    if (!status || status.elapsed == null) return 0;
    const extra = status.state === "play" ? (Date.now() - statusAt) / 1000 : 0;
    return Math.min(status.elapsed + extra, status.duration || Infinity);
  }

  // ── 렌더링 ─────────────────────────────────────────
  function renderStatus() {
    const st = status;
    const state = st.connected ? st.state : "unknown";
    $("device").dataset.state = state;
    $("state-text").textContent = STATE_LABEL[state] || state;

    // 왼쪽 덱: 현재 곡
    $("left-artist").textContent = st.current ? st.current.artist || "—" : "—";
    $("left-title").textContent = st.current ? st.current.title : "대기 중";
    paintArt($("left-art"), st.current);
    // 오른쪽 덱: 다음 곡
    $("right-title").textContent = st.next ? st.next.title : "없음";
    paintArt($("right-art"), st.next);

    // 볼륨
    $("max-volume").textContent = st.max_volume;
    if (!volumeEditing) setKnob(st.muted ? 0 : st.volume ?? 0, st.max_volume);
    $("volume-knob").setAttribute("aria-disabled", String(st.volume == null));
    document.querySelectorAll("[data-mute]").forEach((b) => {
      b.setAttribute("aria-pressed", String(st.muted));
      b.textContent = st.muted ? "MUTED" : "MUTE";
    });

    // 화면 하단 상태줄(색 + 글자)
    $("led-mpd").className = `led ${st.connected ? "ok" : "bad"}`;
    $("mpd-conn").textContent = st.connected ? "정상" : "연결 안 됨";
    const enabled = st.outputs.filter((o) => o.enabled);
    $("led-out").className = `led ${enabled.length ? "ok" : "bad"}`;
    const outText = enabled.length ? enabled.map((o) => o.name).join(", ") : "출력 장치 없음";
    $("outputs").textContent = outText;
    $("outputs-legend").textContent = outText;
    const err = $("player-error");
    err.hidden = !st.error;
    err.textContent = st.error ? `⚠ ${st.error.message} [${st.error.code}]` : "";
    $("updated").textContent = new Date(statusAt).toLocaleTimeString("ko-KR");

    renderQueue();
    tick();
  }

  function renderQueue() {
    const list = $("queue");
    $("queue-count").textContent = `${queue.length}곡`;
    if (!queue.length) {
      list.innerHTML = '<li class="empty">대기열이 비어 있습니다.</li>';
      return;
    }
    const cur = status && status.current ? status.current.position : null;
    const rows = queue.map((song) => {
      const li = document.createElement("li");
      li.dataset.pos = song.position;
      if (song.position === cur) li.className = "current";
      else if (cur != null && song.position < cur) li.className = "played";

      const idx = document.createElement("span");
      idx.className = "idx";
      idx.textContent = song.position + 1;
      const art = document.createElement("span");
      art.className = "art";
      paintArt(art, song);
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = songName(song);
      name.title = songName(song);
      const play = document.createElement("button");
      play.className = "row-play";
      play.textContent = "▶ Play";
      play.setAttribute("aria-label", `${songName(song)} 재생`);
      play.addEventListener("click", () => control({ action: "play_at", position: song.position }));
      const when = document.createElement("span");
      when.className = "when";
      const remove = document.createElement("button");
      remove.className = "row-remove";
      remove.textContent = "✕";
      remove.setAttribute("aria-label", `${songName(song)} 대기열에서 빼기`);
      remove.addEventListener("click", () => removeAt(song.position, songName(song)));
      li.append(idx, art, name, play, when, remove);
      return li;
    });
    list.replaceChildren(...rows);
  }

  /** 1초마다: 진행 링·남은 시간·예상 시작 시각 갱신 */
  function tick() {
    if (!status) return;
    const elapsed = liveElapsed();
    const dur = status.duration || 0;
    const pct = dur ? elapsed / dur : 0;
    $("left-ring").style.strokeDashoffset = String(RING * (1 - pct));
    $("left-time").textContent = status.current ? `-${fmt(dur - elapsed)}` : "-0:00";

    const playing = status.state === "play" && status.current;
    const cur = status.current ? status.current.position : null;
    let t = Date.now() + (dur - elapsed) * 1000; // 현재 곡이 끝나는 시각
    // 행마다 예상 시작 시각: 재생 중일 때만 현재 곡 이후를 누적 계산한다.
    document.querySelectorAll("#queue li[data-pos]").forEach((li) => {
      const pos = Number(li.dataset.pos);
      const when = li.querySelector(".when");
      if (cur != null && pos < cur) {
        when.textContent = ""; // 이미 지나간 곡
      } else if (pos === cur) {
        when.textContent = playing ? clock(Date.now() - elapsed * 1000) : STATE_LABEL[status.state] || "";
      } else if (playing) {
        when.textContent = clock(t);
        t += (queue[pos]?.duration || 0) * 1000;
      } else {
        when.textContent = "--:--";
      }
    });
    const nextRow = status.next ? document.querySelector(`#queue li[data-pos="${status.next.position}"] .when`) : null;
    $("right-time").textContent = playing && nextRow ? nextRow.textContent : "--:--";
  }

  // ── 서버 통신 ──────────────────────────────────────
  async function loadQueue() {
    queue = (await api.get("/api/player/queue")).items;
  }

  async function applyStatus(st) {
    const wasPlaying = status && status.state === "play";
    status = st;
    if (monitor && listening && st.state === "play" && (!wasPlaying || monitor.paused)) connectMonitor();
    statusAt = Date.now();
    if (st.connected && st.queue_version !== queueVersion) {
      queueVersion = st.queue_version;
      await loadQueue();
    }
    renderStatus();
  }

  async function refresh() {
    try {
      await applyStatus(await api.get("/api/player/status"));
      showError("");
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  }

  async function control(body) {
    try {
      const res = await api.post("/api/player/control", body);
      if (body.action === "volume" || body.action === "unmute") {
        $("volume-note").textContent = res.clamped ? `상한 ${res.applied_volume}%로 제한됨` : "";
      }
      await applyStatus(res.status);
      showError("");
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  }

  async function removeAt(position, name) {
    if (!confirm(`'${name}'을(를) 대기열에서 뺄까요?`)) return;
    try {
      await api.del(`/api/admin/queue/${position}`);
      await refresh();
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  }

  // ── LISTEN: MPD httpd 출력을 이 기기에서 듣기(노트북 테스트용) ─────
  const monitor = $("monitor");
  let listening = false;
  let retryTimer;

  function connectMonitor() {
    clearTimeout(retryTimer);
    monitor.src = `${monitor.dataset.src}${monitor.dataset.src.includes("?") ? "&" : "?"}t=${Date.now()}`;
    monitor.play().catch(() => {});
  }

  function setListening(on) {
    listening = on;
    document.querySelectorAll("[data-listen]").forEach((b) => b.setAttribute("aria-pressed", String(on)));
    if (on) connectMonitor();
    else {
      clearTimeout(retryTimer);
      monitor.pause();
      monitor.removeAttribute("src");
      monitor.load();
    }
  }

  if (monitor) {
    // 정지·일시정지하면 MPD가 스트림을 끊으므로, 다시 재생되면 자동으로 붙는다
    const retry = () => {
      if (listening) retryTimer = setTimeout(() => status && status.state === "play" ? connectMonitor() : retry(), 2000);
    };
    monitor.addEventListener("error", retry);
    monitor.addEventListener("ended", retry);
    monitor.addEventListener("stalled", retry);
    document.querySelectorAll("[data-listen]").forEach((b) => b.addEventListener("click", () => setListening(!listening)));
  }

  function toggle() {
    const state = status && status.connected ? status.state : "stop";
    control({ action: state === "play" ? "pause" : state === "pause" ? "resume" : "play" });
  }

  // ── 마스터 볼륨 노브(위아래로 드래그 · 휠 · 방향키) ───────
  const knob = $("volume-knob");
  const faders = [...document.querySelectorAll(".fader.vol")]; // CHAN 1·2 페이더도 같은 볼륨을 움직인다
  let knobValue = 0;
  let commitTimer;

  function setKnob(v, max) {
    knobValue = Math.max(0, Math.min(v, max));
    knob.style.setProperty("--v", knobValue / 100);
    knob.style.setProperty("--cap", max / 100);
    knob.setAttribute("aria-valuemax", String(max));
    knob.setAttribute("aria-valuenow", String(knobValue));
    knob.setAttribute("aria-valuetext", `${knobValue}%`);
    faders.forEach((f) => {
      f.querySelector(".fader-cap").style.setProperty("--p", 1 - knobValue / 100);
      f.setAttribute("aria-valuemax", String(max));
      f.setAttribute("aria-valuenow", String(knobValue));
      f.setAttribute("aria-valuetext", `${knobValue}%`);
    });
    $("volume-value").textContent = status && status.volume == null ? "—" : `${knobValue}%`;
  }

  function nudge(delta, commitDelay = 350) {
    if (!status || status.volume == null) return;
    volumeEditing = true;
    setKnob(Math.round(knobValue + delta), status.max_volume);
    clearTimeout(commitTimer);
    commitTimer = setTimeout(async () => {
      await control({ action: "volume", value: knobValue });
      volumeEditing = false;
    }, commitDelay);
  }

  let dragY = null;
  let dragStart = 0;
  knob.addEventListener("pointerdown", (e) => {
    if (!status || status.volume == null) return;
    knob.setPointerCapture(e.pointerId);
    knob.focus();
    dragY = e.clientY;
    dragStart = knobValue;
    volumeEditing = true;
    clearTimeout(commitTimer);
  });
  knob.addEventListener("pointermove", (e) => {
    if (dragY == null) return;
    setKnob(Math.round(dragStart + (dragY - e.clientY) / 3), status.max_volume); // 3px당 1%
  });
  const endDrag = async () => {
    if (dragY == null) return;
    dragY = null;
    await control({ action: "volume", value: knobValue });
    volumeEditing = false;
  };
  knob.addEventListener("pointerup", endDrag);
  knob.addEventListener("pointercancel", endDrag);

  // 페이더: 누른 높이가 곧 볼륨(위 = 100%, 상한에서 멈춤)
  let faderDrag = false;
  const faderAt = (f, e) => {
    const r = f.querySelector(".fader-slot").getBoundingClientRect();
    setKnob(Math.round((1 - (e.clientY - r.top) / r.height) * 100), status.max_volume);
  };
  faders.forEach((f) => {
    f.addEventListener("pointerdown", (e) => {
      if (!status || status.volume == null) return;
      f.setPointerCapture(e.pointerId);
      f.focus();
      faderDrag = true;
      volumeEditing = true;
      clearTimeout(commitTimer);
      faderAt(f, e);
    });
    f.addEventListener("pointermove", (e) => faderDrag && faderAt(f, e));
    const end = async () => {
      if (!faderDrag) return;
      faderDrag = false;
      await control({ action: "volume", value: knobValue });
      volumeEditing = false;
    };
    f.addEventListener("pointerup", end);
    f.addEventListener("pointercancel", end);
  });

  [knob, ...faders].forEach((el) => {
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      nudge(e.deltaY < 0 ? 1 : -1);
    }, { passive: false });
    el.addEventListener("keydown", (e) => {
      const step = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5 }[e.key];
      if (step) nudge(step);
      else if (e.key === "Home") nudge(-knobValue, 0);
      else if (e.key === "End") nudge(status.max_volume - knobValue, 0);
      else return;
      e.preventDefault();
    });
  });

  // ── 턴테이블: 잡고 JOG_DEG 만큼 돌리면 왼쪽 = 이전 곡, 오른쪽 = 다음 곡(한 번 잡을 때 한 곡) ──
  const JOG_DEG = 45; // 손맛에 맞게 조정
  document.querySelectorAll(".jog").forEach((jog) => {
    const action = jog.id === "jog-right" ? "next" : "previous";
    let rot = 0;
    let last = null;
    let turned = 0;
    const angle = (e) => {
      const r = jog.getBoundingClientRect();
      return (Math.atan2(e.clientY - r.top - r.height / 2, e.clientX - r.left - r.width / 2) * 180) / Math.PI;
    };
    jog.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return; // 가운데 NEXT/PREVIOUS 글자 버튼은 그대로 클릭
      jog.setPointerCapture(e.pointerId);
      last = angle(e);
      turned = 0;
    });
    jog.addEventListener("pointermove", (e) => {
      if (last == null) return;
      const a = angle(e);
      const d = ((a - last + 540) % 360) - 180; // ±180° 경계를 넘어도 이어지게
      last = a;
      rot += d;
      jog.style.setProperty("--rot", `${rot}deg`);
      if (turned === null) return; // 이번 잡기에서 이미 넘김
      turned += d;
      if (Math.abs(turned) >= JOG_DEG) {
        turned = null;
        jog.classList.add("fired");
        setTimeout(() => jog.classList.remove("fired"), 300);
        control({ action });
      }
    });
    const release = () => (last = null);
    jog.addEventListener("pointerup", release);
    jog.addEventListener("pointercancel", release);
  });

  // ── 버튼 연결 ──────────────────────────────────────
  document.querySelectorAll("[data-action]").forEach((b) =>
    b.addEventListener("click", () => control({ action: b.dataset.action }))
  );
  document.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", toggle));
  document.querySelectorAll("[data-mute]").forEach((b) =>
    b.addEventListener("click", () => control({ action: status && status.muted ? "unmute" : "mute" }))
  );
  document.querySelectorAll("[data-refresh]").forEach((b) =>
    b.addEventListener("click", async () => {
      queueVersion = undefined;
      await refresh();
    })
  );
  document.querySelectorAll("[data-scroll]").forEach((b) =>
    b.addEventListener("click", () => {
      const list = $("queue");
      list.scrollBy({ top: Number(b.dataset.scroll) * list.clientHeight * 0.8, behavior: "smooth" });
    })
  );

  $("emergency").addEventListener("click", async () => {
    try {
      const res = await api.post("/api/player/emergency-stop");
      await applyStatus(res.status);
      showError("");
    } catch (ex) {
      if (!handleAuth(ex)) showError(`비상 정지 실패: ${ex.message}`);
    }
  });

  $("logout").addEventListener("click", async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      location.href = "/login";
    }
  });

  // ── 시작 ───────────────────────────────────────────
  (async () => {
    try {
      const me = await api.get("/api/auth/me");
      if (me.role !== "ADMIN") {
        showError("관리자 계정만 사용할 수 있는 화면입니다.");
        document.querySelectorAll(".device button, .knob.big").forEach((el) => {
          el.disabled = true;
          el.setAttribute("aria-disabled", "true");
        });
        return;
      }
      $("who").textContent = `${me.name} (${me.login_id})`;
      await refresh();
      setInterval(refresh, POLL_MS);
      const updateTodo = async () => {
        try {
          const [u, r] = await Promise.all([
            api.get("/api/admin/users?status=PENDING_APPROVAL"),
            api.get("/api/admin/requests?status=PENDING"),
          ]);
          const n = u.items.length + r.items.length;
          $("manage-count").textContent = n || "";
          $("manage-count").title = n ? `가입 대기 ${u.items.length} · 신청 대기 ${r.items.length}` : "";
        } catch {}
      };
      updateTodo();
      setInterval(updateTodo, 15000);
      setInterval(tick, 1000);
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  })();
})();
