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
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
    let res;
    try {
      res = await fetch(url, {
        method,
        headers,
        credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
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

  return {
    get: (url) => request("GET", url),
    post: (url, body = {}) => request("POST", url, body),
    ApiError,
  };
})();
