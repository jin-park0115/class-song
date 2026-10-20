// 운영 대시보드. MVP는 3초 폴링으로 상태를 갱신한다(FR-016은 P1에서 SSE로 교체).
(() => {
  const POLL_MS = 3000;
  const $ = (id) => document.getElementById(id);
  const STATE_LABEL = { play: "재생 중", pause: "일시정지", stop: "정지", unknown: "알 수 없음" };

  let volumeDragging = false;

  function fmt(sec) {
    if (sec == null || isNaN(sec)) return "0:00";
    const s = Math.max(0, Math.floor(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function showError(msg) {
    const el = $("page-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function handleAuth(ex) {
    if (ex.status === 401) {
      location.href = "/login";
      return true;
    }
    return false;
  }

  function render(st) {
    const badge = $("state-badge");
    const state = st.connected ? st.state : "unknown";
    badge.textContent = st.connected ? STATE_LABEL[state] || state : "MPD 연결 끊김";
    badge.dataset.state = st.connected ? state : "error";

    $("current-title").textContent = st.current ? st.current.title : "—";
    $("current-artist").textContent = (st.current && st.current.artist) || " ";
    $("next-title").textContent = st.next ? st.next.title : "없음";

    const pct = st.duration ? Math.min(100, (100 * (st.elapsed || 0)) / st.duration) : 0;
    $("progress-bar").style.width = `${pct}%`;
    document.querySelector(".progress").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("elapsed").textContent = fmt(st.elapsed);
    $("remaining").textContent = `-${fmt((st.duration || 0) - (st.elapsed || 0))}`;

    $("max-volume").textContent = st.max_volume;
    const vol = $("volume");
    vol.max = String(st.max_volume);
    if (!volumeDragging && st.volume != null) vol.value = String(st.volume);
    vol.disabled = st.volume == null;
    $("volume-value").textContent = st.volume == null ? "—" : `${st.muted ? 0 : st.volume}%`;
    const mute = $("mute");
    mute.textContent = st.muted ? "음소거 해제" : "음소거";
    mute.setAttribute("aria-pressed", String(st.muted));

    $("mpd-conn").textContent = st.connected ? "정상" : "연결 안 됨";
    $("outputs").textContent = st.outputs.length
      ? st.outputs.map((o) => `${o.name} (${o.enabled ? "켜짐" : "꺼짐"})`).join(", ")
      : "없음";
    $("queue").textContent = st.queue_length == null ? "—" : `${st.queue_length}곡`;
    $("player-error").textContent = st.error ? `${st.error.message} [${st.error.code}]` : "없음";
    $("player-error").classList.toggle("text-danger", !!st.error);
    $("updated").textContent = new Date().toLocaleTimeString("ko-KR");
  }

  async function refresh() {
    try {
      render(await api.get("/api/player/status"));
      showError("");
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  }

  async function control(body) {
    try {
      const res = await api.post("/api/player/control", body);
      if (res.clamped) {
        $("volume-note").textContent = `요청한 볼륨이 최대 상한(${res.applied_volume}%)으로 제한되었습니다.`;
      } else if (body.action === "volume") {
        $("volume-note").textContent = "";
      }
      render(res.status);
      showError("");
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  }

  document.querySelectorAll("[data-action]").forEach((btn) =>
    btn.addEventListener("click", () => control({ action: btn.dataset.action }))
  );

  const vol = $("volume");
  vol.addEventListener("input", () => {
    volumeDragging = true;
    $("volume-value").textContent = `${vol.value}%`;
  });
  vol.addEventListener("change", async () => {
    await control({ action: "volume", value: Number(vol.value) });
    volumeDragging = false;
  });

  $("mute").addEventListener("click", () =>
    control({ action: $("mute").getAttribute("aria-pressed") === "true" ? "unmute" : "mute" })
  );

  $("emergency").addEventListener("click", async () => {
    try {
      const res = await api.post("/api/player/emergency-stop");
      render(res.status);
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

  (async () => {
    try {
      const me = await api.get("/api/auth/me");
      if (me.role !== "ADMIN") {
        showError("관리자 계정만 사용할 수 있는 화면입니다.");
        document.querySelectorAll("button, input").forEach((el) => {
          if (el.id !== "logout") el.disabled = true;
        });
        return;
      }
      $("who").textContent = `${me.name} (${me.login_id})`;
      await refresh();
      setInterval(refresh, POLL_MS);
    } catch (ex) {
      if (!handleAuth(ex)) showError(ex.message);
    }
  })();
})();
