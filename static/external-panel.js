/* ============================================================
 * 外部会话面板（跨进程 SSH 观测）
 * 展示 ssh-monkeypatch（测试进程）通过 /ws/stream 推送的 SSH 事件：
 *   会话列表（左侧） + 事件流回放/实时（右侧）
 * 零依赖原生 JS，React 面板挂载 #external-panel-root 时初始化。
 * ============================================================ */
(function () {
  "use strict";
  if (window.__externalPanelLoaded) return;
  window.__externalPanelLoaded = true;

  var POLL_MS = 4000;
  var state = {
    root: null,
    sessions: [],
    activeSid: null,
    ws: null,
    pollTimer: null,
  };

  var css = [
    "#external-panel-root{display:flex;height:100%;min-height:0;gap:8px;font-size:12px;color:#c8d0e0}",
    ".ext-list{width:38%;min-width:150px;display:flex;flex-direction:column;border:1px solid #2a3040;border-radius:6px;overflow:hidden;background:#0d1220}",
    ".ext-list-head{padding:6px 8px;background:#161c2c;color:#8892b0;font-weight:600;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}",
    ".ext-list-head button{background:#e9456033;color:#ff6b81;border:1px solid #e9456055;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px}",
    ".ext-items{flex:1;overflow-y:auto}",
    ".ext-item{padding:6px 8px;cursor:pointer;border-bottom:1px solid #1a2030;transition:background .12s}",
    ".ext-item:hover{background:#161c2c}",
    ".ext-item.active{background:#e9456026;border-left:2px solid #e94560}",
    ".ext-item-title{display:flex;justify-content:space-between;gap:6px;font-weight:600;white-space:nowrap}",
    ".ext-item-host{overflow:hidden;text-overflow:ellipsis}",
    ".ext-item-badge{flex-shrink:0;font-size:10px;padding:1px 5px;border-radius:3px}",
    ".ext-badge-live{background:#52c41a33;color:#52c41a}",
    ".ext-badge-closed{background:#666;color:#aaa}",
    ".ext-item-sub{color:#6b7280;font-size:11px;margin-top:2px;display:flex;justify-content:space-between;gap:6px}",
    ".ext-empty{padding:16px;color:#6b7280;text-align:center;line-height:1.6}",
    ".ext-detail{flex:1;display:flex;flex-direction:column;border:1px solid #2a3040;border-radius:6px;overflow:hidden;background:#0d1220;min-width:0}",
    ".ext-detail-head{padding:6px 8px;background:#161c2c;color:#8892b0;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-shrink:0;white-space:nowrap}",
    ".ext-detail-head .meta{overflow:hidden;text-overflow:ellipsis}",
    ".ext-detail-head button{background:transparent;color:#8892b0;border:1px solid #2a3040;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px;flex-shrink:0}",
    ".ext-detail-head button:hover{color:#c8d0e0;border-color:#4a5268}",
    ".ext-events{flex:1;overflow-y:auto;padding:6px 8px;font-family:'Cascadia Code','JetBrains Mono',Consolas,monospace;font-size:12px;line-height:1.55;white-space:pre-wrap;word-break:break-all}",
    ".ext-ev-connect{color:#52c41a;font-weight:600;margin:4px 0;border-left:3px solid #52c41a;padding-left:6px;background:#52c41a0d}",
    ".ext-ev-command{color:#ffc107;font-weight:600;margin:4px 0;border-left:3px solid #ffc107;padding-left:6px;background:#ffc1070d}",
    ".ext-ev-output{color:#c8d0e0}",
    ".ext-ev-stderr{color:#ff6b81}",
    ".ext-ev-shell{color:#8bc8ea}",
    ".ext-ev-close{color:#ff6b81;font-weight:600;margin:4px 0;border-left:3px solid #e94560;padding-left:6px;background:#e9456012}",
    ".ext-ev-eof{color:#4b5563;margin:2px 0;border-top:1px dashed #2a3040;padding-top:2px}",
    ".ext-ev-time{color:#4b5563;font-size:10px;margin-right:6px}",
    ".ext-live{color:#52c41a;font-size:10px;flex-shrink:0}",
  ];
  function injectCss() {
    var s = document.createElement("style");
    s.textContent = css.join("\n");
    document.head.appendChild(s);
  }

  function fmtTime(ts) {
    try {
      var d = new Date(ts * 1000);
      var p = function (n) { return (n < 10 ? "0" : "") + n; };
      return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
    } catch (e) { return ""; }
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function evLine(ev) {
    var t = "<span class='ext-ev-time'>" + fmtTime(ev.ts) + "</span>";
    switch (ev.type) {
      case "connect":
        return "<div class='ext-ev-connect'>" + t + "SSH 连接建立: " + esc(ev.username || "") + "@" + esc(ev.host || "") + ":" + esc(ev.port || 22) + " <span style='opacity:.7'>[" + esc(ev.kind || "") + "]</span></div>";
      case "command":
        return "<div class='ext-ev-command'>" + t + "$ " + esc(ev.command) + "</div>";
      case "output":
        var cls = ev.stream === "stderr" ? "ext-ev-stderr" : (ev.stream === "shell" ? "ext-ev-shell" : "ext-ev-output");
        return "<div class='" + cls + "'>" + t + esc(ev.data) + "</div>";
      case "stream_eof":
        return "<div class='ext-ev-eof'>" + t + "—— " + esc(ev.stream || "") + " EOF ——</div>";
      case "close":
        return "<div class='ext-ev-close'>" + t + "SSH 连接已关闭</div>";
      default:
        return "<div class='ext-ev-output'>" + t + JSON.stringify(ev).slice(0, 200) + "</div>";
    }
  }

  function renderList() {
    var root = state.root;
    if (!root) return;
    var listEl = root.querySelector(".ext-items");
    if (!listEl) return;
    var sessions = state.sessions;
    listEl.innerHTML = "";
    if (!sessions.length) {
      var empty = document.createElement("div");
      empty.className = "ext-empty";
      empty.innerHTML = "暂无外部会话<br><span style='font-size:11px;opacity:.7'>测试进程安装 ssh-monkeypatch 并推送事件后，会话会出现在这里</span>";
      listEl.appendChild(empty);
      return;
    }
    sessions.forEach(function (s) {
      var item = document.createElement("div");
      item.className = "ext-item" + (s.session_id === state.activeSid ? " active" : "");
      var closed = !!s.closed;
      var badge = closed
        ? "<span class='ext-item-badge ext-badge-closed'>已关闭</span>"
        : "<span class='ext-item-badge ext-badge-live'>● 连接中</span>";
      var cmdCnt = s.command_count || 0;
      item.innerHTML =
        "<div class='ext-item-title'><span class='ext-item-host'>" + esc(s.username || "") + "@" + esc(s.host || "") + ":" + esc(s.port || 22) + "</span>" + badge + "</div>" +
        "<div class='ext-item-sub'><span>" + esc(s.kind || "") + " · " + cmdCnt + " 条命令</span><span>" + fmtTime(s.connected_at) + "</span></div>";
      item.addEventListener("click", function () {
        state.activeSid = s.session_id;
        renderList();
        openSession(s.session_id);
      });
      listEl.appendChild(item);
    });
  }

  function renderEvents(events) {
    var root = state.root;
    if (!root) return;
    var el = root.querySelector(".ext-events");
    if (!el) return;
    el.innerHTML = events.map(evLine).join("");
    el.scrollTop = el.scrollHeight;
  }

  function appendEvent(ev) {
    var root = state.root;
    if (!root) return;
    var el = root.querySelector(".ext-events");
    if (!el) return;
    var div = document.createElement("div");
    div.innerHTML = evLine(ev);
    el.appendChild(div.firstChild);
    // 贴底自动滚动
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) {
      el.scrollTop = el.scrollHeight;
    }
  }

  function setHead(meta, live) {
    var root = state.root;
    if (!root) return;
    var head = root.querySelector(".ext-detail-head .meta");
    var liveEl = root.querySelector(".ext-live");
    if (head && meta) {
      head.innerHTML = "<b>" + esc(meta.username || "") + "@" + esc(meta.host || "") + ":" + esc(meta.port || 22) + "</b> <span style='opacity:.7'>" + esc(meta.session_id || "") + " · " + esc(meta.kind || "") + "</span>";
    }
    if (liveEl) liveEl.textContent = live ? "● 实时" : "";
  }

  function closeWs() {
    if (state.ws) {
      try { state.ws.close(); } catch (e) { /* ignore */ }
      state.ws = null;
    }
  }

  function openSession(sid) {
    var root = state.root;
    if (!root) return;
    closeWs();
    var el = root.querySelector(".ext-events");
    if (el) { el.innerHTML = "<div class='ext-empty'>加载会话历史…</div>"; }
    setHead(null, false);
    fetch("/api/external-sessions/" + encodeURIComponent(sid) + "?limit=2000")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (state.activeSid !== sid) return;
        setHead(data.session, false);
        renderEvents(data.events || []);
        // 连接实时流
        var proto = location.protocol === "https:" ? "wss://" : "ws://";
        var ws = new WebSocket(proto + location.host + "/ws/external/" + encodeURIComponent(sid));
        state.ws = ws;
        ws.onmessage = function (e) {
          try {
            var ev = JSON.parse(e.data);
            if (state.activeSid !== sid) return;
            if (ev.type === "close") { setHead(null, false); appendEvent(ev); }
            else { setHead(null, true); appendEvent(ev); }
          } catch (err) { /* ignore */ }
        };
        ws.onclose = function () {
          if (state.ws === ws) state.ws = null;
        };
        ws.onerror = function () { try { ws.close(); } catch (e2) { /* ignore */ } };
        // 保活
        ws._ping = setInterval(function () {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
        }, 25000);
      })
      .catch(function (err) {
        var el2 = root.querySelector(".ext-events");
        if (el2) el2.innerHTML = "<div class='ext-empty'>加载失败: " + esc(err.message) + "</div>";
      });
  }

  function refresh() {
    fetch("/api/external-sessions")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.sessions = (data && data.sessions) || [];
        renderList();
        // 若无选中且存在会话，自动打开第一个
        if (!state.activeSid && state.sessions.length) {
          state.activeSid = state.sessions[0].session_id;
          renderList();
          openSession(state.activeSid);
        }
      })
      .catch(function () { /* 服务未就绪时静默 */ });
  }

  function clearView() {
    closeWs();
    state.sessions = [];
    state.activeSid = null;
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    var root = state.root;
    if (root) {
      root.innerHTML = "";
      state.root = null;
    }
  }

  function init(root) {
    state.root = root;
    root.innerHTML =
      "<div class='ext-list'>" +
      "<div class='ext-list-head'><span>会话</span><button id='ext-refresh'>刷新</button></div>" +
      "<div class='ext-items'><div class='ext-empty'>加载中…</div></div>" +
      "</div>" +
      "<div class='ext-detail'>" +
      "<div class='ext-detail-head'><span class='meta'>选择左侧会话查看事件流</span><span class='ext-live'></span><button id='ext-clear'>清空</button></div>" +
      "<div class='ext-events'><div class='ext-empty'>暂无外部会话事件</div></div>" +
      "</div>";
    root.querySelector("#ext-refresh").addEventListener("click", refresh);
    root.querySelector("#ext-clear").addEventListener("click", function () {
      var el = root.querySelector(".ext-events");
      if (el) el.innerHTML = "";
    });
    refresh();
    state.pollTimer = setInterval(refresh, POLL_MS);
  }

  // 挂载/卸载监听
  function watch() {
    var last = null;
    setInterval(function () {
      var el = document.getElementById("external-panel-root");
      if (el && last !== el) {
        last = el;
        init(el);
      } else if (!el && last) {
        last = null;
        clearView();
      }
    }, 800);
  }

  injectCss();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();
