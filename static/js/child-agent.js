/**
 * child-agent.js — 子 Agent 对话模块。
 * sessionStorage 持久化状态，支持实验编辑与分析审阅两种模式。
 */
(function () {
  "use strict";

  // ---- State ----

  window._childState = null;
  window._childPreview = null;
  window._childLegacyData = null;

  function _childExpId() {
    return document.body.dataset.expId || window.location.pathname.split("/").pop();
  }

  function _childSessionKey() { return "exdiary_child_" + _childExpId(); }

  function loadChildSession() {
    try { var s = sessionStorage.getItem(_childSessionKey()); return s ? JSON.parse(s) : null; }
    catch (e) { return null; }
  }

  function saveChildSession() {
    try { if (window._childState) sessionStorage.setItem(_childSessionKey(), JSON.stringify(window._childState)); }
    catch (e) { /* noop */ }
  }

  window.clearChildSession = function () {
    try { sessionStorage.removeItem(_childSessionKey()); } catch (e) { /* noop */ }
  };

  function _childIsInternal(m) {
    if (m.role === "system") return true;
    if (m.role === "assistant" && m.tool_calls && (!m.content || !String(m.content).trim())) return true;
    if (m.role === "tool") return true;
    return false;
  }

  function _setChildInputEnabled(e) {
    document.getElementById("child-input").disabled = !e;
    document.getElementById("btn-child-send").disabled = !e;
  }

  function _appendChildMsg(role, content) {
    var c = document.getElementById("child-msgs");
    var d = document.createElement("div");
    d.className = "chat-msg " + role;
    var bubbleStyle = role === "agent"
      ? "background:var(--off-white);border:2px solid var(--black);padding:0.4rem 0.7rem;max-width:85%;font-size:0.85rem"
      : "background:var(--black);color:var(--white);padding:0.4rem 0.7rem;max-width:85%;font-size:0.85rem";
    var justify = role === "user" ? "justify-content:flex-end" : "";
    d.style.cssText = "display:flex;margin-bottom:0.4rem;" + justify;
    d.innerHTML = '<div style="' + bubbleStyle + '">' +
      (role === "agent" && typeof marked !== "undefined" ? marked.parse(content) : escHtml(content)) + "</div>";
    c.appendChild(d);
    c.scrollTop = c.scrollHeight;
  }

  function _showChildPreview(data) {
    window._childPreview = data;
    document.getElementById("child-preview").style.display = "";
    var c = document.getElementById("child-preview-content");
    c.innerHTML = '<h4 style="font-weight:700;text-transform:uppercase">Preview Changes</h4>';
    ["title", "status", "tags", "purpose", "conclusion"].forEach(function (f) {
      if (data[f]) c.innerHTML += '<div style="margin:0.3rem 0"><strong>' + f + "</strong>: " +
        escHtml(typeof data[f] === "string" ? data[f] : JSON.stringify(data[f])) + "</div>";
    });
    c.innerHTML += '<button onclick="confirmChildChange()" style="margin-top:0.5rem">Confirm</button>';
  }

  function _renderChildHistory(data) {
    var container = document.getElementById("child-msgs");
    container.innerHTML = "";
    var msgs = data.state ? data.state.history : [];
    var split = (data.state && data.state._child_initial_history_len) || 0;
    var hasHistory = false;
    for (var i = 0; i < msgs.length; i++) {
      var m = msgs[i];
      if (i === split && split > 0 && hasHistory) {
        var sep = document.createElement("div");
        sep.style.cssText = "text-align:center;margin:0.6rem 0;font-weight:700;font-size:0.7rem;opacity:0.5";
        sep.innerHTML = '<span style="background:var(--white);padding:0 0.5rem">—— Modification session ——</span>';
        container.appendChild(sep);
      }
      if (_childIsInternal(m)) continue;
      if (m.role === "user" && m.content) { _appendChildMsg("user", m.content); if (i < split) hasHistory = true; }
      else if (["assistant", "agent"].includes(m.role) && m.content) { _appendChildMsg("agent", m.content); if (i < split) hasHistory = true; }
    }
    container.scrollTop = container.scrollHeight;
  }

  // ---- 公开 API ----

  window.openChildAgent = function () {
    document.getElementById("child-modal").classList.add("active");
    document.getElementById("child-msgs").innerHTML = '<div style="text-align:center;opacity:0.5;padding:2rem">Loading...</div>';
    _setChildInputEnabled(false);
    var saved = loadChildSession();
    var body = { message: "" };
    if (saved) { body.state = saved; window._childState = saved; }
    var expId = _childExpId();
    fetch("/api/exp/" + expId + "/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.is_legacy) {
          document.getElementById("child-legacy").style.display = "";
          window._childLegacyData = data.exp_data;
          _setChildInputEnabled(true);
        } else {
          window._childState = data.state; saveChildSession();
          _renderChildHistory(data); _setChildInputEnabled(true);
        }
      })
      .catch(function () {
        document.getElementById("child-msgs").innerHTML = '<div style="text-align:center;color:var(--red);padding:2rem">Load failed</div>';
        _setChildInputEnabled(true);
      });
  };

  window.confirmLegacy = function () {
    document.getElementById("child-legacy").style.display = "none";
    document.getElementById("child-msgs").innerHTML = '<div style="text-align:center;opacity:0.5;padding:2rem">Loading...</div>';
    _setChildInputEnabled(false);
    var expId = _childExpId();
    fetch("/api/exp/" + expId + "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "Load experiment data for modification", is_legacy: true }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        window._childState = data.state; saveChildSession();
        _renderChildHistory(data); _setChildInputEnabled(true);
      });
  };

  window.closeChildAgent = function () {
    document.getElementById("child-modal").classList.remove("active");
    if (window._childState && window._childState.modified_values && Object.keys(window._childState.modified_values).length > 0) {
      setTimeout(function () { location.reload(); }, 1500);
    }
  };

  window.sendChildMsg = function () {
    var inp = document.getElementById("child-input"), msg = inp.value.trim();
    if (!msg) return; inp.value = "";
    _appendChildMsg("user", msg);
    var body = { message: msg };
    if (window._childState) body.state = window._childState;
    var expId = _childExpId();
    fetch("/api/exp/" + expId + "/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        window._childState = data.state; saveChildSession();
        if (data.type === "extract" && data.preview) { _showChildPreview(data.preview); }
        else { _renderChildHistory(data); }
      });
  };

  window.confirmChildChange = function () {
    var body = { preview: window._childPreview || {} };
    if (window._childState) body.state = window._childState;
    var expId = _childExpId();
    fetch("/api/exp/" + expId + "/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) { clearChildSession(); document.getElementById("child-preview").style.display = "none"; closeChildAgent(); }
      });
  };
})();
