/* Shared browser protocol. ES5 deliberately: NR must not require modern syntax. */
(function (root) {
    'use strict';
    var serial = 0;
    function id() {
        serial += 1;
        if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
        var random = '';
        if (root.crypto && root.crypto.getRandomValues && root.Uint32Array) {
            var values = new Uint32Array(4); root.crypto.getRandomValues(values);
            for (var i = 0; i < values.length; i++) random += values[i].toString(16);
        } else random = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
        return Date.now().toString(36) + '-' + serial + '-' + random;
    }
    function typed(value) { return typeof value === 'string' && /^(private|group|legacy):[0-9]+$/.test(value); }
    function self(message) { return message.direction === 'outgoing'; }
    function request(method, url, body, callback, timeout) {
        var x = new XMLHttpRequest(), done = false;
        function finish() {
            if (done) return; done = true;
            var data = null;
            try { data = JSON.parse(x.responseText); } catch (ignore) {}
            callback(x.status || 0, data);
        }
        x.onreadystatechange = function () { if (x.readyState === 4) finish(); };
        x.onerror = x.ontimeout = x.onabort = finish;
        try {
            x.open(method, url, true); x.timeout = timeout || 10000;
            if (body !== null) x.setRequestHeader('Content-Type', 'application/json');
            x.send(body === null ? null : JSON.stringify(body));
        } catch (ignore) { finish(); }
        return x;
    }
    function Store() {
        this.messages = {}; this.revisions = {}; this.drafts = {}; this.outbox = {};
        this.order = []; this.seen = {}; this.seenOrder = [];
        this.cleared = {}; this.recalls = {};
    }
    Store.prototype.list = function (target) { return this.messages[target] || []; };
    Store.prototype.message = function (m, snapshot) {
        if (!m || !typed(m.target_id) || !m.message_id) return false;
        var revision = Number(m.revision || 0);
        if (this.cleared[m.target_id] !== undefined && revision <= this.cleared[m.target_id]) return false;
        if (!snapshot && revision < Number(this.revisions[m.target_id] || 0)) return false;
        var key = m.target_id + '/' + m.message_id;
        if (this.recalls[key] && revision <= Number(this.recalls[key].revision || 0)) m = this.recalls[key];
        if (m.quote && this.recalls[m.target_id + '/' + m.quote.id]) {
            m.quote = {id: m.quote.id, content: '[引用消息已撤回]', sender_name: '', is_recalled: true};
        }
        var list = this.messages[m.target_id] || (this.messages[m.target_id] = []);
        var fresh = !this.seen[key], i;
        if (fresh) {
            this.seen[key] = true; this.seenOrder.push(key);
            if (this.seenOrder.length > 4000) delete this.seen[this.seenOrder.shift()];
        }
        for (i = 0; i < list.length; i++) {
            if (String(list[i].message_id) === String(m.message_id)) {
                if (Number(m.revision || 0) >= Number(list[i].revision || 0)) list[i] = m;
                return false;
            }
        }
        list.push(m);
        if (list.length > 200) list.splice(0, list.length - 200);
        return fresh;
    };
    Store.prototype.history = function (d) {
        if (!typed(d.target_id)) return false;
        var revision = Number(d.revision || 0);
        if (revision < Number(this.revisions[d.target_id] || 0)) return false;
        var newer = this.list(d.target_id).filter(function (m) { return Number(m.revision || 0) > revision; });
        this.messages[d.target_id] = [];
        var items = (d.messages || []).concat(newer);
        for (var i = 0; i < items.length; i++) {
            if (items[i] && items[i].target_id === d.target_id) this.message(items[i], true);
        }
        this.revisions[d.target_id] = revision;
        return true;
    };
    Store.prototype.recall = function (d) {
        if (!d || !typed(d.target_id) || !d.message_id) return false;
        /* New servers publish full recalled/quote-updated records. A quote update is
           not a recall of the quoting message itself. Keep old event compatibility. */
        if (d.is_recalled === false || (d.direction && !d.is_recalled)) { this.message(d); return true; }
        var key = d.target_id + '/' + d.message_id, list = this.list(d.target_id), original = null, i;
        if (this.recalls[key] && Number(d.revision || 0) < Number(this.recalls[key].revision || 0)) return false;
        for (i = 0; i < list.length; i++) if (String(list[i].message_id) === String(d.message_id)) original = list[i];
        if (original && Number(d.revision || 0) < Number(original.revision || 0)) return false;
        var recalled = {}, source = original || d;
        for (var prop in source) if (Object.prototype.hasOwnProperty.call(source, prop)) recalled[prop] = source[prop];
        recalled.is_recalled = true; recalled.quote = null;
        recalled.content = '「' + (d.sender_name || '某人') + '」撤回了一条消息';
        recalled.segments = []; recalled.media_url = null; recalled.file_url = null; recalled.file_name = null; recalled.file_size = null;
        recalled.revision = d.revision;
        this.recalls[key] = recalled;
        this.message(recalled);
        for (i = 0; i < list.length; i++) {
            var m = list[i];
            if (m.quote && String(m.quote.id) === String(d.message_id)) m.quote = {id: d.message_id, content: '[引用消息已撤回]', sender_name: '', is_recalled: true};
        }
        if (this.drafts[d.target_id] && this.drafts[d.target_id].quote && String(this.drafts[d.target_id].quote.id) === String(d.message_id)) this.drafts[d.target_id].quote = null;
        for (i = 0; i < this.order.length; i++) {
            var item = this.outbox[this.order[i]];
            if (item.target_id === d.target_id && item.draft.quote && String(item.draft.quote.id) === String(d.message_id)) item.draft.quote = null;
        }
        return true;
    };
    Store.prototype.clear = function (d) {
        if (!d || !typed(d.target_id)) return false;
        var revision = Number(d.revision || 0);
        if (revision < Number(this.revisions[d.target_id] || 0) || revision < Number(this.cleared[d.target_id] || 0)) return false;
        this.cleared[d.target_id] = revision; this.revisions[d.target_id] = revision;
        this.messages[d.target_id] = this.list(d.target_id).filter(function (m) { return Number(m.revision || 0) > revision; });
        if (this.drafts[d.target_id]) this.drafts[d.target_id].quote = null;
        for (var i = 0; i < this.order.length; i++) {
            var item = this.outbox[this.order[i]];
            if (item.target_id === d.target_id) item.draft.quote = null;
        }
        return true;
    };
    Store.prototype.begin = function (target, type, draft) {
        if (!typed(target) || target.indexOf('legacy:') === 0) throw new Error('Read-only or invalid conversation');
        var requestId = id();
        var payload = {type: 'send_message', request_id: requestId, target_id: target, msg_type: type,
            content: draft.text || '', quote: draft.quote || null,
            file_local_path: draft.file ? draft.file.local_path : null,
            file_orig_name: draft.file ? draft.file.name : null,
            file_is_image: !!(draft.file && draft.file.is_image),
            file_upload_id: draft.file ? draft.file.upload_id : null};
        this.outbox[requestId] = {request_id: requestId, target_id: target, status: 'pending', draft: draft, payload: payload, started: Date.now()};
        this.order.push(requestId);
        /* Never evict unresolved operations or restore automatically. */
        for (var i = 0; this.order.length > 200 && i < this.order.length; i++) {
            if (this.outbox[this.order[i]].status === 'confirmed') { delete this.outbox[this.order[i]]; this.order.splice(i, 1); i--; }
        }
        return payload;
    };
    Store.prototype.result = function (d) {
        var item = this.outbox[d.request_id];
        if (!item || !/^(pending|confirmed|failed|unknown|partial)$/.test(d.status)) return null;
        if (/^(confirmed|failed|partial)$/.test(item.status) && d.status === 'pending') return item;
        if (item.status === 'confirmed' && d.status !== 'confirmed') return item;
        item.status = d.status; item.detail = d.content || ''; item.message_id = d.message_id || item.message_id;
        item.parts = d.parts || item.parts || [];
        return item;
    };
    Store.prototype.uncertain = function (age) {
        var changed = false, now = Date.now();
        for (var i = 0; i < this.order.length; i++) {
            var item = this.outbox[this.order[i]];
            if (item.status === 'pending' && now - item.started >= (age || 0)) {
                item.status = 'unknown'; item.detail = '连接中断或确认超时，请核实 QQ；不要重复发送'; changed = true;
            }
        }
        return changed;
    };
    Store.prototype.recoverable = function () {
        /* Keep only non-secret lookup identities in memory across expiry. Explicit
           logout and reload drop them; no message/attachment payload survives. */
        var next = new Store();
        for (var i = 0; i < this.order.length; i++) {
            var item = this.outbox[this.order[i]];
            if (item.status !== 'pending' && item.status !== 'unknown') continue;
            next.order.push(item.request_id);
            next.outbox[item.request_id] = {request_id: item.request_id, target_id: item.target_id,
                status: 'unknown', draft: {text: '过期会话中的发送请求'}, started: item.started,
                lookupOnly: true, detail: '重新登录后仅查询结果，不会重发'};
        }
        return next;
    };
    Store.prototype.refreshResults = function (connection) {
        for (var i = 0; i < this.order.length; i++) {
            var item = this.outbox[this.order[i]];
            if (item.status === 'pending' || item.status === 'unknown') connection.send({type: 'get_send_result', request_id: item.request_id});
        }
    };
    function Connection(options) {
        this.options = options; this.socket = null; this.timer = null; this.watch = null;
        this.active = false; this.attempt = 0; this.last = 0; this.state = 'closed';
        this.napcat = null; this.qq = null;
    }
    Connection.prototype.publish = function () { this.options.status(this); };
    Connection.prototype.open = function () {
        var that = this;
        this.active = true;
        clearTimeout(this.timer); clearInterval(this.watch);
        if (this.socket) {
            this.socket.onclose = this.socket.onmessage = this.socket.onopen = this.socket.onerror = null;
            try { this.socket.close(); } catch (ignore) {}
        }
        this.socket = null;
        this.state = 'connecting'; this.napcat = null; this.qq = null; this.publish();
        var socket;
        var wsURL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/client_ws';
        if (this.options.token) wsURL += '?token=' + encodeURIComponent(this.options.token);
        try { socket = new WebSocket(wsURL); }
        catch (ignore) { this.retry(); return; }
        this.socket = socket;
        this.last = Date.now();
        this.timer = setTimeout(function () {
            if (that.socket !== socket || that.state === 'open') return;
            that.timer = null; that.socket = null; socket.onclose = null;
            try { socket.close(); } catch (ignore) {}
            that.retry();
        }, 10000);
        socket.onopen = function () {
            if (that.socket !== socket) return;
            clearTimeout(that.timer); that.timer = null;
            that.state = 'open'; that.last = Date.now(); that.publish();
            that.watch = setInterval(function () {
                if (Date.now() - that.last > 65000) { socket.close(); return; }
                that.send({type: 'ping'});
                if (that.options.tick) that.options.tick();
            }, 25000);
            if (that.options.open) that.options.open();
        };
        socket.onmessage = function (event) {
            if (that.socket !== socket) return;
            that.last = Date.now();
            if (that.last - that.connectedAt > 10000) that.attempt = 0;
            var data; try { data = JSON.parse(event.data); } catch (ignore) { return; }
            if (data.type === 'status') {
                that.napcat = typeof data.napcat_connected === 'boolean' ? data.napcat_connected : null;
                that.qq = typeof data.qq_online === 'boolean' ? data.qq_online : null;
                if (Date.now() - that.connectedAt > 10000) that.attempt = 0;
                that.publish();
            }
            that.options.event(data);
        };
        this.connectedAt = Date.now();
        socket.onclose = function (event) {
            if (that.socket !== socket) return;
            that.socket = null; clearInterval(that.watch); clearTimeout(that.timer); that.timer = null;
            that.state = 'closed'; that.napcat = false; that.qq = false; that.publish();
            if (that.options.disconnected) that.options.disconnected();
            if (event.code === 1008 || event.code === 4001) { that.active = false; that.options.expired(); }
            else that.retry();
        };
        socket.onerror = function () { if (that.socket === socket) socket.close(); };
    };
    Connection.prototype.retry = function () {
        var that = this;
        this.state = 'closed'; this.publish();
        if (!this.active || this.timer) return;
        this.attempt += 1;
        this.timer = setTimeout(function () { that.timer = null; that.open(); }, Math.min(30000, 1000 * Math.pow(2, Math.min(this.attempt - 1, 5))));
    };
    Connection.prototype.send = function (payload) {
        if (!this.socket || this.socket.readyState !== 1) return false;
        try { this.socket.send(JSON.stringify(payload)); return true; } catch (ignore) { return false; }
    };
    Connection.prototype.stop = function () {
        this.active = false; clearTimeout(this.timer); clearInterval(this.watch); this.timer = null;
        if (this.socket) { this.socket.onclose = null; this.socket.onmessage = null; this.socket.onopen = null; try { this.socket.close(); } catch (ignore) {} }
        this.socket = null; this.state = 'closed'; this.napcat = false; this.qq = false; this.publish();
    };
    function status(c) {
        return 'Web：' + (c.state === 'open' ? '已连接' : (c.state === 'connecting' ? '连接中' : '断开，重连中')) +
            ' · NapCat：' + (c.state !== 'open' || c.napcat === null ? '未知' : (c.napcat ? '已连接' : '离线')) +
            ' · QQ：' + (c.state !== 'open' || !c.napcat || c.qq === null ? '未知' : (c.qq ? '在线' : '离线'));
    }
    function segments(m) {
        if (m.segments && m.segments.length) return m.segments;
        var list = [];
        if (m.media_url) list.push({type: 'image', url: m.media_url});
        if (m.file_name) list.push({type: 'file', url: m.file_url, name: m.file_name, size: m.file_size});
        var text = m.content || '';
        if (m.media_url) text = text.replace(/\[图片\]|\[表情包\]/g, '').trim();
        if (text && (!m.file_name || text !== '[文件]' + m.file_name)) list.push({type: 'text', text: text});
        return list;
    }
    function safeURL(url) { return typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/files\/[a-zA-Z0-9_.-]+(?:\?[^#]*)?$/.test(url)); }
    function renderSegments(parent, m) {
        var list = segments(m);
        for (var i = 0; i < list.length; i++) {
            var s = list[i] || {}, data = s.data || s, type = s.type;
            var el = document.createElement('div'); el.className = 'message-segment';
            if (type === 'text') el.appendChild(document.createTextNode(data.text || ''));
            else if (type === 'image') {
                var url = data.url || data.media_url || data.file;
                var holder = document.createElement('div'); holder.appendChild(document.createTextNode('[图片加载中…]'));
                el.appendChild(holder);
                if (safeURL(url)) {
                    var img = document.createElement('img');
                    img.className = 'max-w-full rounded-md object-contain max-h-[200px] md:max-h-60';
                    img.alt = '消息图片'; img.style.maxWidth = '100%'; img.style.maxHeight = '240px';
                    img.onload = (function (h) { return function () { if (h.parentNode) h.parentNode.removeChild(h); }; })(holder);
                    img.onerror = (function (h) { return function () { this.style.display = 'none'; h.textContent = '[图片无法加载：可能已过期或网络异常]'; }; })(holder);
                    img.src = url.indexOf('/files/') === 0 ? url : '/proxy_img?url=' + encodeURIComponent(url);
                    el.appendChild(img);
                } else holder.textContent = '[图片不可用]';
            } else if (type === 'file') {
                var name = data.name || data.file_name || '文件', fileURL = data.url || data.file_url;
                var a = document.createElement(safeURL(fileURL) ? 'a' : 'span'); a.className = 'filelink';
                a.appendChild(document.createTextNode(name));
                if (safeURL(fileURL)) {
                    a.href = fileURL.indexOf('/files/') === 0 ? fileURL : '/download?url=' + encodeURIComponent(fileURL) + '&name=' + encodeURIComponent(name);
                    a.download = name; a.target = '_blank'; a.rel = 'noopener noreferrer';
                } else a.appendChild(document.createTextNode(' [无可用下载地址]'));
                el.appendChild(a);
                if (data.size || data.file_size) el.appendChild(document.createTextNode(' (' + (data.size || data.file_size) + ' B)'));
            } else if (type === 'reply') {
                if (!m.quote) el.appendChild(document.createTextNode('[引用消息]'));
            } else el.appendChild(document.createTextNode(data.label || '[不支持的消息类型：' + (data.original_type || type || 'unknown') + ']'));
            parent.appendChild(el);
        }
    }
    var labels = {pending: '发送中', confirmed: '已确认', failed: '发送失败（可恢复草稿）', unknown: '结果未知，请核实，勿重复发送', partial: '部分成功，请核实，勿整条重发'};
    function renderOutbox(parent, store, target, restore) {
        for (var i = 0; i < store.order.length; i++) {
            var item = store.outbox[store.order[i]];
            if (item.target_id !== target) continue;
            var row = document.createElement('div'); row.className = 'send-state text-xs text-gray-500';
            row.setAttribute('data-request-id', item.request_id); row.setAttribute('data-status', item.status);
            row.appendChild(document.createTextNode((item.draft.text || (item.draft.file && item.draft.file.name) || '消息').slice(0, 80) + ' · ' + labels[item.status] + (item.detail ? '：' + item.detail : '')));
            if (item.status === 'failed' && !item.lookupOnly) {
                var button = document.createElement('button'); button.type = 'button'; button.textContent = '恢复草稿';
                button.onclick = (function (entry) { return function () { restore(entry); }; })(item);
                row.appendChild(button);
            }
            parent.appendChild(row);
        }
    }
    root.ChatCore = {id: id, typed: typed, self: self, request: request, Store: Store, Connection: Connection,
        status: status, segments: segments, renderSegments: renderSegments, renderOutbox: renderOutbox, labels: labels};
}(typeof window !== 'undefined' ? window : this));
