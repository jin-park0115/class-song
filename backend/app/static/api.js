// 공통 API 호출. 쓰기 요청에는 X-CSRF-Token 을 붙인다(요구사항 2.1).
const api = (() => {
  let csrfToken = null;

  class ApiError extends Error {
    constructor(status, code, message) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function request(method, url, body) {
    const headers = { Accept: "application/json" };
    const isForm = body instanceof FormData;
    if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
    if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
    let res;
    try {
      res = await fetch(url, {
        method,
        headers,
        credentials: "same-origin",
        body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, "NETWORK", "서버에 연결할 수 없습니다.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const e = data.error || {};
      throw new ApiError(res.status, e.code || "UNKNOWN", e.message || `요청 실패 (${res.status})`);
    }
    if (data.csrf_token) csrfToken = data.csrf_token;
    return data;
  }

  /** 로그인 후 계정 상태에 맞는 첫 화면 */
  function homeFor(me) {
    if (me.must_change_password) return "/password";
    if (me.account_status === "PENDING_APPROVAL") return "/pending";
    if (me.account_status !== "ACTIVE") return "/login";
    return me.role === "ADMIN" ? "/admin" : "/me";
  }

  return {
    get: (url) => request("GET", url),
    post: (url, body = {}) => request("POST", url, body),
    patch: (url, body = {}) => request("PATCH", url, body),
    del: (url) => request("DELETE", url),
    upload: (url, formData) => request("POST", url, formData),
    homeFor,
    ApiError,
  };
})();

// 작은 DOM 헬퍼: el("li", { class: "x", onclick: fn }, "텍스트", 자식요소...)
// 텍스트는 항상 textContent 로 넣어 사용자 입력이 HTML로 해석되지 않게 한다.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
  return node;
}

const LABELS = {
  PENDING: "승인 대기", APPROVED: "승인", REJECTED: "반려", CANCELLED: "취소",
  NOT_AVAILABLE: "음원 없음", PENDING_ACQUISITION: "음원 준비 중", PROCESSING: "처리 중",
  READY: "재생 가능", FAILED: "실패", BLOCKED: "재생 불가",
  UNKNOWN: "권리 미확인", CONFIRMED: "권리 확인", DENIED: "사용 불가",
  DUPLICATE: "중복", INAPPROPRIATE: "부적절", NO_RIGHTS: "이용 권리 없음", TOO_LONG: "너무 김", OTHER: "기타",
  PENDING_APPROVAL: "승인 대기", ACTIVE: "사용 중", DISABLED: "비활성",
};
const label = (code) => LABELS[code] || code;
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "");
const fmtDur = (sec) => (sec ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` : "");
