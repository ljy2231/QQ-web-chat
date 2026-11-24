/* Lightweight adapter: ES5 syntax, XHR, no framework, no bearer persistence. */
var session = null, connection = null, myQQ = null, contacts = {}, currentId = null, currentType = null;
var pendingFile = null, currentXHR = null, quoteMsg = null, store = new ChatCore.Store();
var composing = false, historyTimer = null, sessionRequest = null, sessionGeneration = 0, logoutGeneration = 0, logoutPending = false;
var NR_BUILD = 'NR v9';
var nrView = {open: false, scale: 1, tx: 0, ty: 0}, nrDrag = null, NR_MIN_SCALE = 1, NR_MAX_SCALE = 5, NR_ZOOM_STEP = 1.35;
var nrStick = true;
var wsToken = null;
try { wsToken = window.sessionStorage.getItem('nr_ws_token'); } catch (ignore) {}
function $(id) { return document.getElementById(id); }
function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
var noticeTimer = null;
function notice(text, autoClear) {
    $('feedback').textContent = text; updateComposer();
    clearTimeout(noticeTimer);
    if (autoClear && text) noticeTimer = setTimeout(function () {
        if ($('feedback').textContent === text) { $('feedback').textContent = ''; updateComposer(); }
    }, 4000);
}
function updateStatus() {
    $('status').textContent = (connection ? ChatCore.status(connection) : 'Web：未连接 · NapCat：未知 · QQ：未知') + ' · ' + NR_BUILD + (window.WebSocket ? '' : ' · 不支持WebSocket');
    $('sendBtn').disabled = !currentId || currentId.indexOf('legacy:') === 0 || !connection || connection.state !== 'open' || !connection.napcat || !connection.qq;
}
function enterSession() {
    session = ChatCore.id(); $('pwd').value = ''; $('loginErr').textContent = '';
    $('login').style.display = 'none'; $('app').style.display = 'block'; connect();
}
function doLogin() {
    if (logoutPending) return;
    if (!$('pwd').value) { $('loginErr').textContent = '请输入密码'; return; }
    sessionGeneration += 1; var generation = sessionGeneration;
    if (sessionRequest) sessionRequest.abort();
    $('loginBtn').disabled = true; $('loginErr').textContent = '验证中…';
    sessionRequest = ChatCore.request('POST', '/api/login', {username: $('nrUser').value, password: $('pwd').value}, function(status, data) {
        if (generation !== sessionGeneration) return;
        sessionRequest = null; $('loginBtn').disabled = false;
        if (status === 200) {
            if (data && data.token) { wsToken = data.token; try { window.sessionStorage.setItem('nr_ws_token', wsToken); } catch (ignore) {} }
            enterSession();
        }
        else $('loginErr').textContent = status === 0 ? '登录超时或网络错误，请重试' : '登录失败，请检查密码或稍后重试';
    });
}
function clearSession(text, preserveLookups) {
    var nextStore = preserveLookups ? store.recoverable() : new ChatCore.Store();
    sessionGeneration += 1; session = null; wsToken = null;
    try { window.sessionStorage.removeItem('nr_ws_token'); } catch (ignore) {}
    if (sessionRequest) sessionRequest.abort(); sessionRequest = null;
    if (connection) connection.stop(); connection = null;
    clearTimeout(historyTimer); cancelFile(); quoteMsg = null; contacts = {}; store = nextStore;
    currentId = null; currentType = null; myQQ = null;
    $('contacts').innerHTML = ''; $('messages').innerHTML = ''; $('msgInput').value = ''; $('pwd').value = '';
    $('quotePreview').textContent = ''; $('chatTitle').textContent = '请选择联系人';
    $('app').style.display = 'none'; $('app').className = ''; $('login').style.display = 'block';
    $('loginBtn').disabled = false; $('loginErr').textContent = text || ''; updateStatus(); notice('');
}
function logout() {
    var generation = ++logoutGeneration;
    clearSession(); logoutPending = true; $('loginBtn').disabled = true; $('retryLogout').disabled = true;
    ChatCore.request('POST', '/api/logout', {}, function(status) {
        if (generation !== logoutGeneration || session) return;
        logoutPending = false; $('loginBtn').disabled = false; $('retryLogout').disabled = false;
        $('loginErr').textContent = status !== 200 && status !== 204 && status !== 401 ? '本页已退出，服务器注销未确认；请联网后点击“重试注销”。' : '';
    });
}
function connect() {
    if (!session) return;
    if (!window.WebSocket) { notice('浏览器不支持 WebSocket；NR 需要 WebSocket 支持'); return; }
    if (!connection) connection = new ChatCore.Connection({
        token: wsToken,
        status: updateStatus,
        open: function() { store.refreshResults(connection); if (currentId) requestHistory(); },
        event: handle,
        expired: function() { clearSession('登录已过期，请重新登录；未确认发送仅保留查询编号', true); },
        disconnected: function() { store.uncertain(); renderCurrent(); },
        tick: function() { if (store.uncertain(90000)) { notice('发送结果未知，请核实，勿重复发送'); renderCurrent(); } }
    });
    connection.open();
}
function handle(d) {
    if (d.type === 'status') { myQQ = d.self_id || myQQ; }
    else if (d.type === 'contact_add') addContact(d.contact);
    else if (d.type === 'message' || d.type === 'message_updated') {
        store.message(d); if (d.target_id === currentId) renderCurrent();
    } else if (d.type === 'history_data') {
        if (store.history(d) && d.target_id === currentId) {
            clearTimeout(historyTimer); var loading = $('history-loading'); if (loading && loading.parentNode) loading.parentNode.removeChild(loading);
            renderCurrent(true);
        }
    } else if (d.type === 'send_result') {
        var item = store.result(d);
        if (item) {
            var label = item.status === 'confirmed' ? '已发送'
                : (item.detail || ChatCore.labels[item.status] || item.status);
            notice(label, item.status === 'confirmed');
            renderCurrent();
        }
    }
    else if (d.type === 'toast') { notice(d.content); finishHistory('历史未完成或请求异常：' + d.content); }
    else if (d.type === 'message_recalled') {
        store.recall(d);
        if (d.target_id === currentId) {
            if ((d.is_recalled || !d.direction) && quoteMsg && String(quoteMsg.id) === String(d.message_id)) cancelQuote();
            renderCurrent();
        }
    } else if (d.type === 'clear_history') {
        if (store.clear(d) && d.target_id === currentId) { cancelQuote(); $('messages').innerHTML = ''; renderCurrent(); }
    }
}
function addContact(c) {
    if (!c || !ChatCore.typed(c.id)) return;
    contacts[c.id] = c;
    var el = $('contact-' + c.id);
    if (!el) {
        el = document.createElement('div'); el.className = 'contact'; el.id = 'contact-' + c.id;
        el.onclick = function() { switchChat(c.id); }; $('contacts').appendChild(el);
    }
    var html = '';
    if (c.avatar) html += '<img src="' + esc(c.avatar) + '" onerror="this.style.visibility=\'hidden\'">';
    el.innerHTML = html + '<div class="cinfo"><div class="cname">' + esc(c.name) + '</div><div class="cid">' + esc(c.type === 'group' ? '群 ' : 'QQ ') + esc(c.peer_id || c.id) + '</div></div>';
    if (c.id === currentId) $('chatTitle').textContent = c.name + (c.readonly || c.type === 'legacy' ? '（只读归档）' : '');
}
function saveDraft() {
    if (currentId) store.drafts[currentId] = {text: $('msgInput').value, quote: quoteMsg, file: pendingFile && pendingFile.local_path ? pendingFile : null};
}
function restoreDraft(draft) {
    cancelFile(); cancelQuote(); draft = draft || {text: ''};
    $('msgInput').value = draft.text || ''; quoteMsg = draft.quote || null;
    pendingFile = draft.file || null;
    updateComposer();
}
function updateComposer() {
    $('quotePreview').textContent = quoteMsg ? '引用：' + (quoteMsg.content || '') : '';
    $('cancelQuote').style.display = quoteMsg ? 'inline' : 'none';
    $('filePreview').textContent = pendingFile ? pendingFile.name + (pendingFile.local_path ? '（已上传）' : '（上传中）') : '';
    $('cancelFile').style.display = pendingFile ? 'inline' : 'none';
    var info = $('composerInfo'), show = !!(quoteMsg || pendingFile || $('feedback').textContent);
    if (info) info.style.display = show ? 'block' : 'none';
}
function switchChat(id) {
    var c = contacts[id]; if (!c) return;
    saveDraft(); cancelFile(); cancelQuote(); currentId = id; currentType = c.type;
    restoreDraft(store.drafts[id]); $('chatTitle').textContent = c.name + (c.readonly || c.type === 'legacy' ? '（只读归档）' : '');
    $('messages').innerHTML = '<div id="history-loading" role="status" style="color:#999;text-align:center;padding:20px;">加载中...</div>';
    var all = $('contacts').getElementsByTagName('div');
    for (var i = 0; i < all.length; i++) if (all[i].className === 'contact active') all[i].className = 'contact';
    var cur = $('contact-' + id); if (cur) cur.className = 'contact active';
    if (window.innerWidth <= 620) $('app').className = 'chat-open';
    renderCurrent(); requestHistory(); updateStatus(); notice('');
}
function finishHistory(text) { var el = $('history-loading'); if (el) el.textContent = text; }
function requestHistory() {
    clearTimeout(historyTimer); var target = currentId;
    if (!connection || !connection.send({type: 'get_history', target_id: target})) { finishHistory('Web 断开，重连后重新加载'); return; }
    historyTimer = setTimeout(function() { if (target === currentId) finishHistory('历史加载超时，请重新选择会话重试'); }, 12000);
}
var NR_AUTO_IMAGE_MS = 3600 * 1000;
function nrParseTime(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(text || ''));
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)).getTime();
}
function nrImageDeferred(m) {
    var t = nrParseTime(m.time);
    if (t === null) return true;
    return Date.now() - t > NR_AUTO_IMAGE_MS;
}
function nrOldImagePlan(m) {
    var list = ChatCore.segments(m), urls = [], out = [], i, s, d, url;
    for (i = 0; i < list.length; i++) {
        s = list[i] || {};
        if (s.type === 'image') {
            d = s.data || s; url = d.url || d.media_url || d.file;
            if (url) urls.push(url);
            out.push({type: 'text', text: '[图片]'});
        } else out.push(s);
    }
    return {urls: urls, segments: out};
}
function nrCopyMessage(m, segments) {
    var copy = {}, k;
    for (k in m) if (Object.prototype.hasOwnProperty.call(m, k)) copy[k] = m[k];
    copy.segments = segments;
    return copy;
}
function nrAppendDeferred(bubble, urls) {
    var box = document.createElement('div'), i, row, label, btn;
    box.className = 'nr-late-media';
    for (i = 0; i < urls.length; i++) {
        row = document.createElement('div'); row.className = 'nr-late-row';
        label = document.createElement('span'); label.className = 'nr-late-label'; label.textContent = '图片信息';
        btn = document.createElement('button'); btn.type = 'button'; btn.className = 'nr-late-download'; btn.textContent = '下载';
        btn.onclick = (function (url, rowEl, labelEl, btnEl, bubbleEl) {
            return function () {
                if (btnEl.disabled) return;
                btnEl.disabled = true; labelEl.textContent = '加载中…';
                var img = document.createElement('img');
                img.className = 'nr-late-img'; img.alt = '消息图片';
                img.style.maxWidth = '100%'; img.style.maxHeight = '240px'; img.style.display = 'block';
                img.onload = function () { if (rowEl.parentNode) rowEl.parentNode.removeChild(rowEl); if (nrStick) nrScrollBottom($('messages')); };
                img.onerror = function () { if (img.parentNode) img.parentNode.removeChild(img); btnEl.disabled = false; labelEl.textContent = '图片信息（加载失败，可重试）'; };
                bubbleEl.appendChild(img);
                img.src = url.indexOf('/files/') === 0 ? url : '/proxy_img?url=' + encodeURIComponent(url);
            };
        })(urls[i], row, label, btn, bubble);
        row.appendChild(label); row.appendChild(btn); box.appendChild(row);
    }
    bubble.appendChild(box);
}
function nrNearBottom(area) {
    if (!area || typeof area.clientHeight !== 'number') return false;
    return area.scrollHeight - area.scrollTop - area.clientHeight < 80;
}
function nrScrollBottom(area) {
    if (!area) return;
    area.scrollTop = area.scrollHeight;
    setTimeout(function () { if (area) area.scrollTop = area.scrollHeight; }, 0);
    setTimeout(function () { if (area && nrNearBottom(area)) area.scrollTop = area.scrollHeight; }, 300);
}
function nrReplaceNode(node, fresh) {
    var parent = node.parentNode;
    if (!parent) return;
    if (parent.replaceChild) { parent.replaceChild(fresh, node); return; }
    parent.removeChild(node); parent.appendChild(fresh);
}
function nrWatchImage(img, area) {
    var prevLoad = img.onload, prevError = img.onerror;
    img.__nrSrc = img.getAttribute('src') || img.src;
    img.onload = function () {
        if (prevLoad) { try { prevLoad.call(this); } catch (ignore) {} }
        if (nrStick) nrScrollBottom(area);
    };
    img.onerror = function () {
        if (prevError) { try { prevError.call(this); } catch (ignore) {} }
        var retries = img.__nrRetry || 0;
        if (retries < 2 && img.__nrSrc && img.parentNode) {
            img.__nrRetry = retries + 1;
            setTimeout(function () {
                if (!img.parentNode) return;
                img.style.display = '';
                img.src = img.__nrSrc + (img.__nrSrc.indexOf('?') >= 0 ? '&' : '?') + 'nr_retry=' + (retries + 1);
            }, 900 * (retries + 1));
        } else if (nrNearBottom(area)) nrScrollBottom(area);
    };
}
function nrWatchImages(area) {
    var imgs = area.getElementsByTagName('img'), i;
    for (i = 0; i < imgs.length; i++) {
        if (imgs[i].__nrWatched) continue;
        imgs[i].__nrWatched = true;
        nrWatchImage(imgs[i], area);
    }
}
function renderCurrent(force) {
    if (!currentId) return;
    var area = $('messages'), loading = $('history-loading');
    var stick = nrNearBottom(area), fromBottom = area.scrollHeight - area.scrollTop;
    if (force) {
        area.innerHTML = '';
        if (loading) area.appendChild(loading);
    }
    var list = store.list(currentId);
    for (var i = 0; i < list.length; i++) {
        var m = list[i], node = $('msg-' + m.message_id);
        if (!node) { appendMessage(m); continue; }
        var stale = Number(node.getAttribute('data-revision') || 0) < Number(m.revision || 0);
        var needsRecall = m.is_recalled && String(node.className || '').indexOf('recalled') < 0;
        if (stale || needsRecall) nrReplaceNode(node, buildMessage(m));
    }
    var states = $('send-states');
    if (states && states.parentNode) states.parentNode.removeChild(states);
    states = document.createElement('div'); states.id = 'send-states';
    ChatCore.renderOutbox(states, store, currentId, function(item) {
        if (($('msgInput').value || pendingFile || quoteMsg) && !confirm('恢复将替换当前草稿，是否继续？')) return;
        restoreDraft(item.draft); saveDraft();
    });
    area.appendChild(states);
    nrWatchImages(area);
    if (stick) { nrStick = true; nrScrollBottom(area); }
    else { nrStick = false; if (isFinite(fromBottom)) area.scrollTop = area.scrollHeight - fromBottom; }
}
function appendMessage(m) {
    if (m.target_id !== currentId || $('msg-' + m.message_id)) return;
    $('messages').appendChild(buildMessage(m));
}
function buildMessage(m) {
    var self = ChatCore.self(m), div = document.createElement('div');
    div.className = 'msg' + (self ? ' self' : '') + (m.is_recalled ? ' recalled' : ''); div.id = 'msg-' + m.message_id;
    div.setAttribute('data-revision', String(m.revision || 0));
    if (m.is_recalled) { div.innerHTML = '<div class="bubble">' + esc(m.content) + '</div>'; return div; }
    var meta = document.createElement('div'); meta.className = 'meta';
    meta.appendChild(document.createTextNode((self ? (myQQ || '我') : (m.sender_name || m.sender_id || '未知')) + ' · ' + (m.time || ''))); div.appendChild(meta);
    var bubble = document.createElement('div'); bubble.className = 'bubble';
    if (m.quote) {
        var q = document.createElement('div'); q.className = 'quote'; q.textContent = (m.quote.sender_name || '') + '：' + (m.quote.content || '[引用消息]');
        q.onclick = function() { var target = $('msg-' + m.quote.id); if (target) target.scrollIntoView(); else notice('引用消息已不在当前历史中'); };
        bubble.appendChild(q);
    }
    var deferred = nrImageDeferred(m) ? nrOldImagePlan(m) : null;
    if (deferred && deferred.urls.length) ChatCore.renderSegments(bubble, nrCopyMessage(m, deferred.segments));
    else ChatCore.renderSegments(bubble, m);
    div.appendChild(bubble);
    if (deferred && deferred.urls.length) nrAppendDeferred(bubble, deferred.urls);
    var actions = document.createElement('div'), reply = document.createElement('button'); reply.className = 'recall'; reply.type = 'button'; reply.textContent = '引用';
    reply.onclick = function() { if (currentId.indexOf('legacy:') === 0) { notice('归档仅供查看'); return; } quoteMsg = {id: m.message_id, sender_name: m.sender_name, content: m.content}; updateComposer(); $('msgInput').focus(); };
    actions.appendChild(reply);
    if (self) {
        var recall = document.createElement('button'); recall.className = 'recall'; recall.type = 'button'; recall.textContent = '撤回';
        recall.onclick = function() { if (confirm('撤回这条消息？')) recallMsg(m.message_id); }; actions.appendChild(recall);
    }
    div.appendChild(actions); return div;
}
function recallMsg(id) { if (!currentId || currentId.indexOf('legacy:') === 0) { notice('归档仅供查看'); return; } if (!connection || !connection.send({type: 'recall_message', target_id: currentId, message_id: id})) notice('Web 断开，撤回未提交'); }
function sendMsg() {
    if (!currentId || currentId.indexOf('legacy:') === 0) { notice('请选择可发送的联系人；归档仅供查看'); return; }
    if (!connection || connection.state !== 'open') { notice('Web 连接已断开，草稿保留'); return; }
    if (!connection.napcat || !connection.qq) { notice('NapCat 或 QQ 离线，草稿保留'); return; }
    var text = $('msgInput').value;
    if (!text && !pendingFile) return;
    if (pendingFile && (currentXHR || !pendingFile.local_path || pendingFile.target_id !== currentId)) { notice('附件上传中或会话不匹配，不能发送'); return; }
    saveDraft(); var payload = store.begin(currentId, currentType, store.drafts[currentId]);
    if (!connection.send(payload)) { store.result({request_id: payload.request_id, status: 'failed', content: '未写入连接，草稿保留'}); renderCurrent(); return; }
    $('msgInput').value = ''; cancelFile(); cancelQuote(); store.drafts[currentId] = {text: ''}; renderCurrent(); notice('已提交，等待服务器确认');
}
function cancelFile() {
    var old = currentXHR; currentXHR = null; pendingFile = null;
    if (old) { try { old.abort(); } catch (ignore) {} }
    $('imgInput').value = ''; $('fileInput').value = ''; updateComposer();
}
function cancelQuote() { quoteMsg = null; updateComposer(); }
function uploadFile(file, isImage) {
    if (!file || !currentId || currentId.indexOf('legacy:') === 0) { notice('请先选择联系人'); return; }
    if (file.size > 50 * 1024 * 1024) { notice('文件过大，限制 50MB'); return; }
    cancelFile(); var target = currentId, requestId = ChatCore.id(), x = new XMLHttpRequest();
    pendingFile = {name: file.name, size: file.size, is_image: isImage, target_id: target, upload_id: requestId, local_path: null};
    currentXHR = x; updateComposer();
    function valid() { return currentXHR === x && currentId === target && pendingFile && pendingFile.upload_id === requestId; }
    function fail(text) { if (!valid()) return; cancelFile(); notice(text); }
    var fd = new FormData(); fd.append('target_id', target); fd.append('upload_id', requestId); fd.append(isImage ? 'image' : 'file', file);
    x.open('POST', '/api/upload', true); x.timeout = 60000;
    x.setRequestHeader('X-Conversation-ID', target); x.setRequestHeader('X-Upload-ID', requestId);
    if (x.upload) x.upload.onprogress = function(e) { if (valid() && e.lengthComputable) $('filePreview').textContent = file.name + '（' + Math.round(e.loaded * 100 / e.total) + '%）'; };
    x.onload = function() {
        if (!valid()) return;
        if (x.status !== 200) { fail('上传失败，HTTP ' + x.status); return; }
        var d; try { d = JSON.parse(x.responseText); if (!d.local_path) throw new Error('Invalid upload'); } catch (ignore) { fail('上传响应无效'); return; }
        pendingFile.local_path = d.local_path; pendingFile.url = d.url; currentXHR = null; updateComposer(); notice('附件已上传，尚未发送');
    };
    x.onerror = function() { fail('上传网络错误'); }; x.ontimeout = function() { fail('上传超时'); };
    try { x.send(fd); } catch (ignore) { fail('此浏览器无法上传文件'); }
}
$('backBtn').onclick = function() { saveDraft(); cancelFile(); cancelQuote(); currentId = null; $('app').className = ''; updateStatus(); };
$('loginBtn').onclick = doLogin; $('logoutBtn').onclick = logout; $('retryLogout').onclick = logout;
$('pwd').onkeydown = function(e) { e = e || window.event; if ((e.keyCode || e.which) === 13 && !e.isComposing && e.keyCode !== 229) doLogin(); };
$('sendBtn').onclick = sendMsg;
$('msgInput').oncompositionstart = function() { composing = true; }; $('msgInput').oncompositionend = function() { composing = false; };
$('msgInput').onkeydown = function(e) { e = e || window.event; if ((e.keyCode || e.which) === 13 && !e.isComposing && !composing && e.keyCode !== 229) { if (e.preventDefault) e.preventDefault(); sendMsg(); } };
$('pickImg').onclick = function() { if (currentId) $('imgInput').click(); else notice('请先选择联系人'); };
$('pickFile').onclick = function() { if (currentId) $('fileInput').click(); else notice('请先选择联系人'); };
$('imgInput').onchange = function() { uploadFile(this.files[0], true); }; $('fileInput').onchange = function() { uploadFile(this.files[0], false); };
$('cancelFile').onclick = cancelFile; $('cancelQuote').onclick = cancelQuote;
if (window.addEventListener) window.addEventListener('online', function() { if (session && (!connection || connection.state !== 'open')) connect(); }, false);
try { localStorage.removeItem('webchat_token'); } catch (ignore) {}
(function restoreSession() {
    var generation = ++sessionGeneration;
    sessionRequest = ChatCore.request('GET', '/api/session', null, function(status, data) {
        if (generation !== sessionGeneration) return;
        sessionRequest = null;
        if (status === 200 && data && data.authenticated) {
            if (data.ws_token) { wsToken = data.ws_token; try { window.sessionStorage.setItem('nr_ws_token', wsToken); } catch (ignore) {} }
            enterSession();
        }
        else if (status === 401 && data && data.setup_required) $('loginErr').textContent = '请先在主站 /web 完成初始化设置';
        else if (status === 0) $('loginErr').textContent = '会话检查超时，请重新登录';
    });
}());

/* 图片查看器：单指拖动、按钮缩放，兼容旧 WebView */
function nrIsImage(el) { return !!(el && el.tagName && String(el.tagName).toLowerCase() === 'img'); }
function nrApplyView() {
    var img = $('nrViewerImg');
    if (!img) return;
    var t = 'translate(-50%,-50%) translate(' + nrView.tx + 'px,' + nrView.ty + 'px) scale(' + nrView.scale + ')';
    img.style.webkitTransform = t; img.style.transform = t;
}
function nrClampView() {
    var stage = $('nrViewerStage'), img = $('nrViewerImg');
    if (!stage || !img) return;
    var sw = stage.clientWidth, sh = stage.clientHeight, iw = img.clientWidth, ih = img.clientHeight;
    if (!(sw > 0 && sh > 0 && iw > 0 && ih > 0)) return;
    var mx = Math.max(0, (iw * nrView.scale - sw) / 2), my = Math.max(0, (ih * nrView.scale - sh) / 2);
    if (nrView.tx > mx) nrView.tx = mx; if (nrView.tx < -mx) nrView.tx = -mx;
    if (nrView.ty > my) nrView.ty = my; if (nrView.ty < -my) nrView.ty = -my;
}
function nrUpdateViewButtons() {
    var zin = $('nrZoomIn'), zout = $('nrZoomOut');
    if (zin) zin.disabled = nrView.scale >= NR_MAX_SCALE - 0.001;
    if (zout) zout.disabled = nrView.scale <= NR_MIN_SCALE + 0.001;
}
function nrSetScale(s) {
    if (s < NR_MIN_SCALE) s = NR_MIN_SCALE;
    if (s > NR_MAX_SCALE) s = NR_MAX_SCALE;
    nrView.scale = s; nrClampView(); nrApplyView(); nrUpdateViewButtons();
}
function nrZoomIn() { nrSetScale(nrView.scale * NR_ZOOM_STEP); }
function nrZoomOut() { nrSetScale(nrView.scale / NR_ZOOM_STEP); }
function nrOpenImage(src) {
    if (!src) return;
    nrView.open = true; nrView.scale = 1; nrView.tx = 0; nrView.ty = 0; nrDrag = null;
    var tip = $('nrViewerTip'); if (tip) tip.textContent = '单指拖动查看 · 点图片外区域关闭';
    var img = $('nrViewerImg');
    if (img) {
        img.onerror = function () { var t = $('nrViewerTip'); if (t) t.textContent = '图片加载失败，请检查网络'; };
        img.src = src;
    }
    var v = $('nrViewer');
    if (v) { v.style.display = 'block'; v.setAttribute('aria-hidden', 'false'); }
    nrApplyView(); nrUpdateViewButtons();
}
function nrCloseImage() {
    nrView.open = false; nrDrag = null;
    var v = $('nrViewer');
    if (v) { v.style.display = 'none'; v.setAttribute('aria-hidden', 'true'); }
    var img = $('nrViewerImg'); if (img) img.removeAttribute('src');
}
function nrStartDrag(cx, cy, target) {
    if (!nrView.open) return;
    nrDrag = {x: cx, y: cy, tx: nrView.tx, ty: nrView.ty, moved: false, onImage: nrIsImage(target)};
}
function nrMoveDrag(cx, cy) {
    if (!nrDrag || !nrView.open) return false;
    var dx = cx - nrDrag.x, dy = cy - nrDrag.y;
    if (!nrDrag.moved && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) nrDrag.moved = true;
    nrView.tx = nrDrag.tx + dx; nrView.ty = nrDrag.ty + dy;
    nrClampView(); nrApplyView();
    return true;
}
function nrEndDrag() {
    if (!nrDrag) return;
    var d = nrDrag; nrDrag = null;
    if (!d.moved && !d.onImage) nrCloseImage();
}
function nrBindViewer() {
    var box = $('messages'), stage = $('nrViewerStage');
    if (!box || !stage) return;
    box.addEventListener('scroll', function () { nrStick = nrNearBottom(box); }, false);
    box.addEventListener('click', function (e) {
        e = e || window.event;
        var t = e.target || e.srcElement;
        while (t && t !== box) {
            if (nrIsImage(t)) { nrOpenImage(t.getAttribute('src') || t.src); return; }
            t = t.parentNode;
        }
    }, false);
    stage.addEventListener('touchstart', function (e) {
        if (e.touches && e.touches.length !== 1) { nrDrag = null; return; }
        var t = e.touches ? e.touches[0] : e;
        nrStartDrag(t.clientX, t.clientY, e.target || e.srcElement);
        if (e.preventDefault) e.preventDefault();
    }, false);
    stage.addEventListener('touchmove', function (e) {
        if (!nrView.open || !e.touches || e.touches.length !== 1) return;
        if (e.preventDefault) e.preventDefault();
        nrMoveDrag(e.touches[0].clientX, e.touches[0].clientY);
    }, false);
    stage.addEventListener('touchend', nrEndDrag, false);
    stage.addEventListener('touchcancel', function () { nrDrag = null; }, false);
    stage.addEventListener('mousedown', function (e) {
        e = e || window.event;
        if (e.button !== undefined && e.button !== 0) return;
        nrStartDrag(e.clientX, e.clientY, e.target || e.srcElement);
    }, false);
    stage.addEventListener('mousemove', function (e) {
        e = e || window.event;
        if (e.buttons === 0) { nrDrag = null; return; }
        nrMoveDrag(e.clientX, e.clientY);
    }, false);
    if (document.addEventListener) document.addEventListener('mouseup', function () { nrEndDrag(); }, false);
    $('nrZoomIn').onclick = nrZoomIn;
    $('nrZoomOut').onclick = nrZoomOut;
    $('nrViewerClose').onclick = nrCloseImage;
}
nrBindViewer();

updateStatus(); updateComposer();
