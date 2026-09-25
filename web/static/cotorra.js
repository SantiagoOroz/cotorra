/* Shared client helpers: a reconnecting websocket, a caption renderer and a
 * few DOM conveniences. Plain ES modules — no bundler, no dependencies.
 */

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c !== null && c !== undefined) node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

export const params = new URLSearchParams(location.search);

export function wsURL(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

/** A websocket that comes back after venue wifi drops, with capped backoff. */
export class Socket extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.backoff = 500;
    this.closed = false;
    this.connect();
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.backoff = 500;
      this.dispatchEvent(new CustomEvent("state", { detail: "open" }));
      // The server reads from the socket to detect a closed tab; a ping keeps
      // intermediaries from treating a quiet talk as an idle connection.
      clearInterval(this._ping);
      this._ping = setInterval(() => this.send({ type: "ping" }), 25000);
    };
    this.ws.onmessage = (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      this.dispatchEvent(new CustomEvent("message", { detail: data }));
      if (data.type) this.dispatchEvent(new CustomEvent(data.type, { detail: data }));
    };
    this.ws.onclose = () => {
      clearInterval(this._ping);
      this.dispatchEvent(new CustomEvent("state", { detail: "closed" }));
      if (this.closed) return;
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 10000);
    };
    this.ws.onerror = () => this.ws.close();
  }

  send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  close() {
    this.closed = true;
    clearInterval(this._ping);
    this.ws?.close();
  }
}

/** Appends captions, replacing a partial in place with its final. */
export class CaptionList {
  constructor(container, { max = 60, onAppend = null } = {}) {
    this.container = container;
    this.max = max;
    this.onAppend = onAppend;
    this.nodes = new Map(); // seq -> element
    this.partial = null;
  }

  clear() {
    this.container.replaceChildren();
    this.nodes.clear();
    this.partial = null;
  }

  add(caption) {
    const text = (caption.text || "").trim();
    if (!text) return;

    if (caption.kind === "partial") {
      if (!this.partial) {
        this.partial = el("p", { class: "line partial" });
        this.container.append(this.partial);
      }
      this.partial.textContent = text;
      this.partial.dataset.seq = caption.seq;
    } else {
      if (this.partial) {
        this.partial.remove();
        this.partial = null;
      }
      let node = this.nodes.get(caption.seq);
      if (node) {
        node.textContent = text;
      } else {
        node = el("p", { class: "line", "data-seq": caption.seq, text });
        this.nodes.set(caption.seq, node);
        this.container.append(node);
      }
      for (const [seq, n] of this.nodes) {
        n.classList.toggle("latest", seq === caption.seq);
      }
      this.trim();
    }
    this.onAppend?.();
  }

  trim() {
    while (this.nodes.size > this.max) {
      const oldest = Math.min(...this.nodes.keys());
      this.nodes.get(oldest)?.remove();
      this.nodes.delete(oldest);
    }
  }
}

/** Keeps the page pinned to the newest caption unless the reader scrolled up. */
export function autoScroller() {
  let pinned = true;
  const near = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 220;
  addEventListener("scroll", () => {
    pinned = near();
  }, { passive: true });
  return {
    get pinned() {
      return pinned;
    },
    follow() {
      if (pinned) {
        scrollTo({
          top: document.body.scrollHeight,
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        });
      }
    },
  };
}

export const LANG_LABEL = {
  es: "Español",
  en: "English",
  pt: "Português",
  fr: "Français",
  de: "Deutsch",
  it: "Italiano",
  ja: "日本語",
  zh: "中文",
  ko: "한국어",
  ca: "Català",
  auto: "auto",
};
export const langLabel = (c) => LANG_LABEL[c] || (c || "").toUpperCase();

/** Theme toggle persisted per browser. Dark is the default: rooms are dark. */
export function initTheme(buttonSelector = "#theme") {
  const saved = localStorage.getItem("cotorra-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = qs(buttonSelector);
  btn?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("cotorra-theme", next);
  });
}

/** Caption size control, persisted. Accessibility feature, not a gimmick. */
export function initFontSize(minusSel, plusSel, key = "cotorra-size") {
  const apply = (v) => {
    document.documentElement.style.setProperty("--caption-size", `${v}rem`);
    localStorage.setItem(key, v);
  };
  let size = parseFloat(localStorage.getItem(key) || "2");
  apply(size);
  qs(minusSel)?.addEventListener("click", () => apply((size = Math.max(1, size - 0.25))));
  qs(plusSel)?.addEventListener("click", () => apply((size = Math.min(5, size + 0.25))));
}

export const fmtAgo = (ts) => {
  if (!ts) return "—";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

export const fmtDur = (s) => {
  if (!s) return "0:00";
  const m = Math.floor(s / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};
