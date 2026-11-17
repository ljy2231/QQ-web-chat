let ws = null;
            let currentTargetId = null;
            let currentMsgType = null;
            let quoteMsg = null;
            let isNapcatOnline = false;
            let myQQ = null;
            const contacts = {};
            let contextMenuMsg = null;

            let authToken = null; // Session-generation marker only; never a bearer credential.
            let store = new ChatCore.Store();
            let connection = null, historyTimer = null, sessionRequest = null;
            let previewBlobURL = null, composing = false;
            try { localStorage.removeItem('webchat_token'); } catch (e) {}

            let pendingFile = null;
            let currentXHR = null;
            var unreadCounts = {};
            (function loadUnread() {
                try {
                    var raw = localStorage.getItem('webchat_unread');
                    if (raw) {
                        var d = JSON.parse(raw);
                        if (d && typeof d === 'object') { unreadCounts = d; }
                    }
                } catch (e) {}
            })();
            function saveUnread() {
                try { localStorage.setItem('webchat_unread', JSON.stringify(unreadCounts)); } catch (e) {}
            }


            let wsStatus = 'connecting';
            let reconnectTimeout = null;
            let countdownInterval = null;

            const loginOverlay = document.getElementById('login-overlay');
            const loginPwd = document.getElementById('login-pwd');
            const loginBtn = document.getElementById('login-btn');
            const loginError = document.getElementById('login-error');
            const logoutBtn = document.getElementById('logout-btn');
            const settingsBtn = document.getElementById('settings-btn');
            const refreshBgBtn = document.getElementById('refresh-bg-btn');
            const saveWallpaperBtn = document.getElementById('save-wallpaper-btn');
            (function enableCardAfterIntro() {
                const c = document.getElementById('login-card');
                if (!c) return;
                function reveal() {
                    c.style.pointerEvents = 'auto'; c.style.opacity = '1'; c.style.transform = 'none';
                    Array.prototype.forEach.call(c.children, function(child) { child.style.opacity = '1'; child.style.transform = 'none'; });
                }
                c.style.pointerEvents = 'auto';
                c.addEventListener('animationend', function() { c.style.pointerEvents = 'auto'; });
                if (!c.animate || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) reveal();
                else setTimeout(reveal, 4000);
            })();

            // 手写笔画动画：按笔画顺序逐笔画出标题（总时长 3 秒）
            (function animateTitleStrokes() {
                const svg = document.getElementById('login-title-svg');
                if (!svg) return;
                svg.style.opacity = '1';
                if ((window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) || !svg.animate) return;
                const TOTAL_MS = 3000, GAP = 25;
                const items = [...svg.querySelectorAll('path')]
                    .map((p) => {
                        let len = 0;
                        try { len = p.getTotalLength(); } catch (e) {}
                        return { p, len };
                    })
                    .filter((i) => i.len > 0);
                if (!items.length) return;
                const sum = items.reduce((a, i) => a + i.len, 0);
                const drawBudget = TOTAL_MS - GAP * (items.length - 1);
                setTimeout(() => svg.classList.add('title-pink'), TOTAL_MS + 120);
                setTimeout(() => { svg.classList.remove('title-pink'); svg.classList.add('title-bold'); }, TOTAL_MS + 1500);
                let t = 0;
                items.forEach((i) => {
                    i.p.style.strokeDasharray = i.len;
                    i.p.style.strokeDashoffset = i.len;
                    const dur = Math.max(50, drawBudget * i.len / sum);
                    if (typeof i.p.animate !== 'function') { i.p.style.strokeDashoffset = '0'; return; }
                    i.p.animate(
                        [{ strokeDashoffset: i.len }, { strokeDashoffset: 0 }],
                        { duration: dur, delay: t, easing: 'ease-in-out', fill: 'forwards' }
                    );
                    t += dur + GAP;
                });
            })();


            const sidebar = document.getElementById('sidebar');
            const chatArea = document.getElementById('chat-area');
            const backBtn = document.getElementById('back-btn');

            const statusDot = document.getElementById('status-dot');
            const statusText = document.getElementById('status-text');
            const contactList = document.getElementById('contact-list');
            const emptyContact = document.getElementById('empty-contact');
            const currentChatName = document.getElementById('current-chat-name');
            const messageArea = document.getElementById('message-area');
            const inputBox = document.getElementById('input-box');
            const sendBtn = document.getElementById('send-btn');
            const contextMenu = document.getElementById('context-menu');
            const ctxRecall = document.getElementById('ctx-recall');
            const quotePreview = document.getElementById('quote-preview');
            const quoteText = document.getElementById('quote-text');

            const filePreviewArea = document.getElementById('file-preview-area');
            const previewImg = document.getElementById('preview-img');
            const previewFileIcon = document.getElementById('preview-file-icon');
            const previewFilename = document.getElementById('preview-filename');
            const previewFilesize = document.getElementById('preview-filesize');
            const uploadProgressBar = document.getElementById('upload-progress-bar');
            const previewPercent = document.getElementById('preview-percent');

            const imageInput = document.getElementById('image-input');
            const fileInput = document.getElementById('file-input');
            const dropOverlay = document.getElementById('drop-overlay');

            document.addEventListener('dragover', (e) => { e.preventDefault(); });
            document.addEventListener('drop', (e) => { e.preventDefault(); });

            if (chatArea) {
                chatArea.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.add('show');
                });
                chatArea.addEventListener('dragleave', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.remove('show');
                });
                chatArea.addEventListener('drop', (e) => {
                    e.preventDefault();
                    dropOverlay.classList.remove('show');
                    if (!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                    if (!isNapcatOnline) { alert('NapCat 未连接，无法上传'); return; }
                    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                        const file = e.dataTransfer.files[0];
                        const isImage = file.type.startsWith('image/');
                        handleFileSelect(file, isImage);
                    }
                });
            }

            if (backBtn) {
                backBtn.onclick = () => {
                    chatArea.classList.add('hidden');
                    chatArea.classList.remove('flex');
                    sidebar.classList.remove('hidden');
                    saveDraft(); cancelFile(); cancelQuote(); currentTargetId = null; updateStatusUI();
                };
            }

            if (inputBox) {
                inputBox.addEventListener('input', function() {
                    this.style.height = 'auto';
                    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                });
            }

            document.getElementById('btn-image').onclick = () => {
                if(!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                if(!isNapcatOnline) { alert('NapCat 未连接'); return; }
                imageInput.click();
            };
            document.getElementById('btn-file').onclick = () => {
                if(!currentTargetId) { alert('请先在左侧选择联系人'); return; }
                if(!isNapcatOnline) { alert('NapCat 未连接'); return; }
                fileInput.click();
            };

            function formatSize(bytes) {
                if (bytes < 1024) return bytes + ' B';
                else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
                else return (bytes / 1048576).toFixed(1) + ' MB';
            }

            function handleFileSelect(file, isImage) {
                if (!file || !currentTargetId || currentTargetId.indexOf('legacy:') === 0) { showToast('只读归档不能上传或发送'); return; }
                if (file.size > 50 * 1024 * 1024) {
                    alert('文件过大，限制 50MB');
                    return;
                }

                cancelFile();
                const uploadTarget = currentTargetId;
                const uploadId = ChatCore.id();
                pendingFile = { name: file.name, size: file.size, is_image: isImage, local_path: null, target_id: uploadTarget, upload_id: uploadId };
                previewFilename.innerText = file.name;
                previewFilesize.innerText = formatSize(file.size);

                if (isImage) {
                    try { if (window.URL && URL.createObjectURL) { previewBlobURL = URL.createObjectURL(file); previewImg.src = previewBlobURL; } } catch (e) { previewBlobURL = null; }
                    previewImg.classList.toggle('hidden', !previewBlobURL);
                    previewFileIcon.classList.toggle('hidden', !!previewBlobURL);
                } else {
                    previewImg.classList.add('hidden');
                    previewFileIcon.classList.remove('hidden');
                }

                uploadProgressBar.style.width = '0%';
                previewPercent.innerText = '准备中...';
                previewPercent.classList.remove('hidden');
                filePreviewArea.classList.remove('hidden');

                const xhr = new XMLHttpRequest();
                currentXHR = xhr;
                function validUpload() { return currentXHR === xhr && currentTargetId === uploadTarget && pendingFile && pendingFile.upload_id === uploadId; }

                xhr.open('POST', '/api/upload', true);

                xhr.setRequestHeader('X-Conversation-ID', uploadTarget);
                xhr.setRequestHeader('X-Upload-ID', uploadId);
                const formData = new FormData();
                formData.append('target_id', uploadTarget); formData.append('upload_id', uploadId);
                formData.append(isImage ? 'image' : 'file', file);

                xhr.timeout = 60000;

                if (xhr.upload) xhr.upload.onprogress = function(e) {
                    if (!validUpload()) return;
                    if (e.lengthComputable) {
                        const percent = Math.round((e.loaded / e.total) * 100);
                        uploadProgressBar.style.width = percent + '%';
                        previewPercent.innerText = percent + '%';
                    }
                };

                xhr.onload = function() {
                    if (!validUpload()) return;
                    if (xhr.status === 200) {
                        try {
                            const data = JSON.parse(xhr.responseText);
                            if (!data.local_path) throw new Error('Missing upload path');
                            pendingFile.url = data.url;
                            pendingFile.local_path = data.local_path;
                            currentXHR = null;
                            uploadProgressBar.style.width = '100%';
                            previewPercent.innerText = '完成';
                            setTimeout(() => {
                                if (!pendingFile || pendingFile.upload_id !== uploadId) return;
                                uploadProgressBar.style.width = '0%';
                                previewPercent.classList.add('hidden');
                            }, 1000);
                        } catch (err) {
                            alert('上传响应无效，请重新选择附件');
                            cancelFile();
                        }
                    } else {
                        let errMsg = '上传失败，HTTP 状态码: ' + xhr.status;
                        try {
                            const errData = JSON.parse(xhr.responseText);
                            if (errData.error) errMsg += '\n具体原因: ' + errData.error;
                        } catch(e) {}
                        alert(errMsg);
                        cancelFile();
                    }
                };

                xhr.onerror = function() { if (!validUpload()) return; alert('上传网络错误或跨域被拦截'); cancelFile(); };
                xhr.ontimeout = function() { if (!validUpload()) return; alert('上传超时！服务器可能未响应'); cancelFile(); };

                try { xhr.send(formData); } catch (e) { if (validUpload()) { cancelFile(); showToast('此浏览器无法上传附件'); } }
            }

            imageInput.onchange = (e) => handleFileSelect(e.target.files[0], true);
            fileInput.onchange = (e) => handleFileSelect(e.target.files[0], false);

            document.getElementById('cancel-file').onclick = cancelFile;
            function cancelFile() {
                const oldXHR = currentXHR; currentXHR = null;
                if (oldXHR) { try { oldXHR.abort(); } catch (e) {} }
                if (previewBlobURL) { try { if (URL.revokeObjectURL) URL.revokeObjectURL(previewBlobURL); } catch (e) {} previewBlobURL = null; }
                pendingFile = null;
                filePreviewArea.classList.add('hidden');
                previewImg.src = '';
                imageInput.value = '';
                fileInput.value = '';
            }

            window.addEventListener('paste', async function(e) {
                if (!currentTargetId || !isNapcatOnline) return;
                const clipboardData = e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData);
                let blob = null;

                if (clipboardData && clipboardData.items) {
                    for (let i = 0; i < clipboardData.items.length; i++) {
                        if (clipboardData.items[i].type.indexOf('image') !== -1) {
                            blob = clipboardData.items[i].getAsFile();
                            break;
                        }
                    }
                }
                if (blob) {
                    e.preventDefault();
                    blob.name = 'paste.png';
                    handleFileSelect(blob, true);
                }
            });

            document.addEventListener('click', hideContextMenu);
            function hideContextMenu() { contextMenu.classList.remove('show'); contextMenuMsg = null; }

            function showToast(msg) {
                let toast = document.createElement('div');
                toast.className = 'toast';
                toast.innerText = msg;
                document.body.appendChild(toast);
                setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2000);
            }

            // 粉色粒子连线背景（canvas-nest 原版算法）
            (function canvasNest() {
                const host = document.getElementById('login-bg');
                const overlay = document.getElementById('login-overlay');
                if (!host || !overlay) return;

                function l() {
                    return { l: 0, z: 0, o: 0.55, c: '244,114,182', n: 150 };
                }
                function k() {
                    r = u.width = host.clientWidth;
                    n = u.height = host.clientHeight;
                }
                function b() {
                    if (overlay.style.display === 'none') return;
                    requestAnimationFrame(b);
                    if (!r || !n) { k(); return; }
                    e.clearRect(0, 0, r, n);
                    var w = [f].concat(t);
                    var x, v, A, B, z, y;
                    t.forEach(function(i) {
                        i.x += i.xa;
                        i.y += i.ya;
                        i.xa *= i.x > r || i.x < 0 ? -1 : 1;
                        i.ya *= i.y > n || i.y < 0 ? -1 : 1;
                        e.fillStyle = 'rgba(' + s.c + ',0.9)';
                        e.fillRect(i.x - 0.5, i.y - 0.5, 1, 1);
                        for (v = 0; v < w.length; v++) {
                            x = w[v];
                            if (i !== x && null !== x.x && null !== x.y) {
                                B = i.x - x.x;
                                z = i.y - x.y;
                                y = B * B + z * z;
                                if (y < x.max) {
                                    if (x === f && y >= x.max / 2) {
                                        i.x -= 0.03 * B;
                                        i.y -= 0.03 * z;
                                    }
                                    A = (x.max - y) / x.max;
                                    e.beginPath();
                                    e.lineWidth = 0.8 + A * 0.6;
                                    e.strokeStyle = 'rgba(' + s.c + ',' + (A + 0.2) + ')';
                                    e.moveTo(i.x, i.y);
                                    e.lineTo(x.x, x.y);
                                    e.stroke();
                                }
                            }
                        }
                        w.splice(w.indexOf(i), 1);
                    });
                }

                var u = document.createElement('canvas');
                var s = l();
                var e; try { e = u.getContext('2d'); } catch (ignore) { return; }
                if (!e || !window.requestAnimationFrame || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
                var r, n;
                var f = { x: null, y: null, max: 20000 };
                var a = Math.random;
                u.id = 'c_n' + s.l;
                u.style.cssText = 'position:absolute;top:0;left:0;z-index:0;opacity:' + s.o;
                host.appendChild(u);
                k();
                window.addEventListener('resize', k);
                window.addEventListener('mousemove', function(i) {
                    i = i || window.event;
                    f.x = i.clientX;
                    f.y = i.clientY;
                });
                window.addEventListener('mouseout', function() {
                    f.x = null;
                    f.y = null;
                });
                for (var t = [], p = 0; s.n > p; p++) {
                    var h = a() * r;
                    var g = a() * n;
                    var q = 2 * a() - 1;
                    var d = 2 * a() - 1;
                    t.push({ x: h, y: g, xa: q, ya: d, max: 6000 });
                }
                window.__nestStart = function() { requestAnimationFrame(b); };
                setTimeout(function() { b(); }, 100);
            })();

            function updateWallpaperView() {
                if (!uiCfg) return;
                const img = document.getElementById('login-bg-img');
                if (img) {
                    if (uiCfg.use_image_host && wallpaperUrl) {
                        if (img.getAttribute('src') !== wallpaperUrl) {
                            clearTimeout(wallpaperRevealTimer);
                            img.src = wallpaperUrl;
                        }
                        const wait = Math.max(0, 3000 - performance.now());
                        clearTimeout(wallpaperRevealTimer);
                        if (wait > 0) {
                            const shownUrl = wallpaperUrl;
                            wallpaperRevealTimer = setTimeout(function() {
                                if (uiCfg.use_image_host && wallpaperUrl === shownUrl) img.style.opacity = '1';
                            }, wait);
                        } else {
                            img.style.opacity = '1';
                        }
                    } else {
                        clearTimeout(wallpaperRevealTimer);
                        img.style.opacity = '0';
                        img.removeAttribute('src');
                    }
                }
                if (uiCfg.chat_background && wallpaperUrl) {
                    document.body.classList.add('chat-bg-on');
                    if (document.body.getAttribute('data-chat-background') !== wallpaperUrl) {
                        document.body.style.setProperty('--chat-wallpaper', 'url("' + wallpaperUrl + '")');
                        document.body.setAttribute('data-chat-background', wallpaperUrl);
                    }
                } else {
                    document.body.classList.remove('chat-bg-on');
                    document.body.style.removeProperty('--chat-wallpaper');
                    document.body.removeAttribute('data-chat-background');
                }
                const visible = !!authToken && loginOverlay.style.display === 'none';
                saveWallpaperBtn.hidden = !visible;
                saveWallpaperBtn.disabled = !(visible && uiCfg.chat_background && wallpaperBlob);
                document.body.classList.toggle('wallpaper-save-visible', visible);
                refreshBgBtn.disabled = !!wallpaperRequest;
            }

            function ensureWallpaper(forceRefresh = false) {
                if (!uiCfg.use_image_host && !uiCfg.chat_background) return Promise.resolve(false);
                if (wallpaperRequest) return wallpaperRequest;
                if (wallpaperBlob && !forceRefresh) {
                    updateWallpaperView();
                    return Promise.resolve(true);
                }
                const variant = window.matchMedia && window.matchMedia('(max-width: 768px)').matches ? 'mobile' : 'desktop';
                const tip = document.getElementById('bg-error');
                if (tip) tip.classList.remove('show');
                wallpaperRequest = (async function() {
                    let candidateUrl = null;
                    try {
                        const response = await fetch('/api/wallpaper?variant=' + variant, {cache: 'no-store'});
                        if (!response.ok) throw new Error('Wallpaper request failed');
                        const blob = await response.blob();
                        if (!blob.size || blob.type.indexOf('image/') !== 0) throw new Error('Invalid wallpaper');
                        candidateUrl = URL.createObjectURL(blob);
                        await new Promise(function(resolve, reject) {
                            const image = new Image();
                            image.onload = resolve;
                            image.onerror = function() { reject(new Error('Wallpaper decode failed')); };
                            image.src = candidateUrl;
                        });
                        const oldUrl = wallpaperUrl;
                        wallpaperBlob = blob;
                        wallpaperUrl = candidateUrl;
                        candidateUrl = null;
                        updateWallpaperView();
                        if (oldUrl) URL.revokeObjectURL(oldUrl);
                        return true;
                    } catch (e) {
                        if (candidateUrl) URL.revokeObjectURL(candidateUrl);
                        if (!wallpaperBlob && uiCfg.use_image_host && tip) tip.classList.add('show');
                        if (forceRefresh) showToast('壁纸加载失败，已保留当前壁纸');
                        return false;
                    } finally {
                        wallpaperRequest = null;
                        updateWallpaperView();
                    }
                })();
                updateWallpaperView();
                return wallpaperRequest;
            }

            function saveCurrentWallpaper() {
                if (!authToken || !uiCfg.chat_background || !wallpaperBlob || saveWallpaperBtn.disabled) return;
                const types = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/avif': 'avif'};
                const downloadUrl = URL.createObjectURL(wallpaperBlob);
                const link = document.createElement('a');
                link.href = downloadUrl;
                link.download = 'QQ-Web-Chat-wallpaper-' + Date.now() + '.' + (types[wallpaperBlob.type] || 'img');
                link.style.display = 'none';
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(function() { URL.revokeObjectURL(downloadUrl); }, 60000);
            }

            function enterSession() {
                authToken = ChatCore.id();
                loginPwd.value = ''; loginError.classList.add('hidden');
                loginOverlay.style.display = 'none'; updateWallpaperView();
                resetSettingsCache(); loadSettings(); connect();
            }
            function doLogin() {
                if (!loginPwd.value) { showToast('请输入密码'); return; }
                if (logoutPending) return;
                const marker = ChatCore.id(); loginBtn.dataset.request = marker;
                if (sessionRequest) { sessionRequest.abort(); sessionRequest = null; }
                loginBtn.disabled = true; loginBtn.innerText = '验证中...';
                sessionRequest = ChatCore.request('POST', '/api/login', {password: loginPwd.value}, function(status, data) {
                    if (loginBtn.dataset.request !== marker) return;
                    sessionRequest = null; loginBtn.disabled = false; loginBtn.innerText = '登录';
                    if (status === 200) enterSession();
                    else { loginError.classList.remove('hidden'); loginError.innerText = status === 0 ? '登录超时或网络错误，请重试' : '登录失败，请检查密码或稍后重试'; }
                });
            }
            function clearSession(message, preserveLookups) {
                const nextStore = preserveLookups ? store.recoverable() : new ChatCore.Store();
                authToken = null; loginBtn.dataset.request = '';
                if (sessionRequest) { sessionRequest.abort(); sessionRequest = null; }
                if (connection) connection.stop(); connection = null; ws = null;
                clearTimeout(historyTimer); cancelFile(); cancelQuote(); hideContextMenu(); resetSettingsCache();
                store = nextStore; currentTargetId = null; currentMsgType = null;
                myQQ = null; isNapcatOnline = false; wsStatus = 'closed'; unreadCounts = {}; saveUnread();
                Object.keys(contacts).forEach(key => delete contacts[key]);
                contactList.innerHTML = ''; messageArea.innerHTML = ''; inputBox.value = '';
                currentChatName.innerText = '请选择联系人';
                loginPwd.value = ''; loginBtn.disabled = false; loginBtn.innerText = '登录';
                loginOverlay.style.display = 'flex'; updateWallpaperView();
                loginError.innerText = message || ''; loginError.classList.toggle('hidden', !message);
                const card = document.getElementById('login-card');
                if (card) { card.style.pointerEvents = 'auto'; card.style.opacity = '1'; }
                updateStatusUI();
            }
            let logoutGeneration = 0, logoutPending = false;
            function doLogout() {
                const generation = ++logoutGeneration;
                clearSession(); logoutPending = true; loginBtn.disabled = true;
                const retry = document.getElementById('retry-logout'); retry.hidden = false; retry.disabled = true;
                ChatCore.request('POST', '/api/logout', {}, function(status) {
                    if (generation !== logoutGeneration || authToken) return;
                    retry.disabled = false; logoutPending = false; loginBtn.disabled = false;
                    const failed = status !== 200 && status !== 204 && status !== 401;
                    retry.hidden = !failed;
                    loginError.classList.toggle('hidden', !failed);
                    loginError.innerText = failed ? '本页已退出，但服务器会话注销未确认。请联网后点击“重试注销”。' : '';
                });
            }
            document.getElementById('retry-logout').onclick = doLogout;
                        if (logoutBtn) logoutBtn.addEventListener('click', doLogout);
            if (settingsBtn) settingsBtn.addEventListener('click', openSettings);
            if (refreshBgBtn) refreshBgBtn.addEventListener('click', async function() {
                if (!uiCfg.chat_background) { showToast('请先在设置中开启聊天背景'); return; }
                if (wallpaperRequest) return;
                if (await ensureWallpaper(true)) showToast('壁纸已刷新');
            });
            if (saveWallpaperBtn) saveWallpaperBtn.addEventListener('click', saveCurrentWallpaper);
            loginBtn.addEventListener('click', doLogin);
            loginPwd.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) doLogin(); });

            function restoreSession() {
                const marker = ChatCore.id(); loginBtn.dataset.request = marker;
                sessionRequest = ChatCore.request('GET', '/api/session', null, function(status, data) {
                    if (loginBtn.dataset.request !== marker) return;
                    sessionRequest = null;
                    if (status === 200 && data && data.authenticated) enterSession();
                    else if (status === 0) { loginError.classList.remove('hidden'); loginError.innerText = '会话检查超时，请重新登录'; }
                });
            }
            function connect() {
                if (!authToken) return;
                if (!connection) connection = new ChatCore.Connection({
                    status: function(c) { ws = c.socket; wsStatus = c.state; isNapcatOnline = c.napcat && c.qq; updateStatusUI(); },
                    open: function() { ws = connection.socket; store.refreshResults(connection); if (currentTargetId) requestHistory(); },
                    event: handleEvent,
                    disconnected: function() { store.uncertain(); renderCurrent(); },
                    tick: function() { if (store.uncertain(90000)) renderCurrent(); },
                    expired: function() { clearSession('登录已过期，请重新登录；未确认发送仅保留查询编号', true); }
                });
                connection.open(); ws = connection.socket;
            }
            function handleEvent(data) {
                if (data.type === 'status') { if (data.self_id) myQQ = data.self_id; }
                else if (data.type === 'contact_add') addContact(data.contact);
                else if (data.type === 'message' || data.type === 'message_updated') {
                    const fresh = store.message(data);
                    if (fresh && data.type === 'message' && !ChatCore.self(data)) {
                        playNotifySound();
                        if (data.target_id !== currentTargetId || document.hidden) {
                            unreadCounts[data.target_id] = (unreadCounts[data.target_id] || 0) + 1;
                            updateUnreadBadge(data.target_id); saveUnread();
                        }
                    }
                    if (data.target_id === currentTargetId) renderCurrent();
                } else if (data.type === 'history_data') {
                    if (store.history(data) && data.target_id === currentTargetId) { clearTimeout(historyTimer); const loading = document.getElementById('history-loading'); if (loading) loading.remove(); renderCurrent(); }
                } else if (data.type === 'send_result') {
                    if (store.result(data)) renderCurrent();
                } else if (data.type === 'message_recalled') {
                    store.recall(data);
                    if ((data.is_recalled || !data.direction) && quoteMsg && String(quoteMsg.message_id) === String(data.message_id) && data.target_id === currentTargetId) cancelQuote();
                    if (contextMenuMsg && contextMenuMsg.target_id === data.target_id) hideContextMenu();
                    if (data.target_id === currentTargetId) renderCurrent();
                } else if (data.type === 'clear_history') {
                    if (store.clear(data) && data.target_id === currentTargetId) { cancelQuote(); hideContextMenu(); renderCurrent(); }
                } else if (data.type === 'toast') {
                    showToast(data.content); finishHistory('历史可能未加载完成：' + data.content);
                }
            }
            function updateStatusUI() {
                const online = !!(connection && connection.state === 'open' && connection.napcat && connection.qq);
                statusText.innerText = connection ? ChatCore.status(connection) : 'Web：未连接 · NapCat：未知 · QQ：未知';
                statusText.title = statusText.innerText;
                statusDot.className = 'w-3 h-3 rounded-full mr-2 shadow-sm shrink-0 ' + (online ? 'bg-green-500' : 'bg-yellow-500');
                sendBtn.disabled = !online || !currentTargetId || currentTargetId.indexOf('legacy:') === 0;
                inputBox.disabled = !currentTargetId;
            }
            function isSelfSender(message) { return ChatCore.self(message); }
            function saveDraft() {
                if (currentTargetId) store.drafts[currentTargetId] = {text: inputBox.value, quote: quoteMsg ? {id: quoteMsg.message_id, sender_name: quoteMsg.sender_name, content: quoteMsg.content} : null, file: pendingFile && pendingFile.local_path ? Object.assign({}, pendingFile) : null};
            }
            function restoreDraft(draft) {
                draft = draft || {text: ''}; inputBox.value = draft.text || '';
                cancelQuote(); cancelFile();
                if (draft.quote) { quoteMsg = {message_id: draft.quote.id, sender_name: draft.quote.sender_name, content: draft.quote.content}; quoteText.innerText = '引用：' + draft.quote.content; quotePreview.classList.remove('hidden'); }
                if (draft.file) {
                    pendingFile = Object.assign({}, draft.file, {target_id: currentTargetId});
                    previewFilename.innerText = pendingFile.name; previewFilesize.innerText = formatSize(pendingFile.size || 0);
                    previewFileIcon.classList.remove('hidden'); filePreviewArea.classList.remove('hidden');
                    previewPercent.innerText = '已上传'; previewPercent.classList.remove('hidden');
                }
            }
            function finishHistory(text) {
                const node = document.getElementById('history-loading');
                if (node) { node.innerText = text; node.setAttribute('data-failed', 'true'); }
            }
            function requestHistory() {
                clearTimeout(historyTimer);
                const target = currentTargetId;
                if (!connection || !connection.send({type: 'get_history', target_id: target})) { finishHistory('Web 连接断开，恢复后重试加载'); return; }
                historyTimer = setTimeout(function() { if (currentTargetId === target) finishHistory('历史加载超时，请重新选择会话重试'); }, 12000);
            }
            function renderCurrent() {
                if (!currentTargetId) return;
                const loading = document.getElementById('history-loading');
                messageArea.innerHTML = '';
                if (loading) messageArea.appendChild(loading);
                store.list(currentTargetId).forEach(m => appendMessage(m, false));
                const states = document.createElement('div'); states.id = 'send-states';
                ChatCore.renderOutbox(states, store, currentTargetId, function(item) {
                    if ((inputBox.value || pendingFile || quoteMsg) && !confirm('恢复将替换当前编辑草稿，是否继续？')) return;
                    restoreDraft(item.draft); saveDraft(); inputBox.focus();
                });
                messageArea.appendChild(states);
                messageArea.scrollTop = messageArea.scrollHeight;
            }
            window.addEventListener('online', function() { if (authToken && (!connection || connection.state !== 'open')) connect(); });
            window.addEventListener('beforeunload', function() { cancelFile(); if (wallpaperUrl) URL.revokeObjectURL(wallpaperUrl); });
            setTimeout(restoreSession, 0);


                        function addContact(contact) {
                if (!contact || !ChatCore.typed(contact.id)) return;
                contacts[contact.id] = contact;
                if (unreadCounts[contact.id] === undefined) unreadCounts[contact.id] = 0;
                if (emptyContact) emptyContact.style.display = 'none';

                let div = document.getElementById(`contact-${contact.id}`);
                if (!div) {
                    div = document.createElement('div');
                    div.className = 'flex items-center p-3 rounded-lg cursor-pointer hover:bg-gray-100 transition-colors relative';
                    div.id = `contact-${contact.id}`;
                    div.onclick = () => switchChat(contacts[contact.id]);
                    contactList.appendChild(div);
                }

                const avatarText = escapeHTML((contact.name || '?').charAt(0));
                const avatarHTML = contact.avatar
                    ? `<img src="${escapeHTML(contact.avatar)}" alt="" referrerpolicy="no-referrer" class="w-10 h-10 rounded-full object-cover bg-gray-200" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"><div class="w-10 h-10 rounded-full bg-blue-500 text-white items-center justify-center font-bold hidden">${avatarText}</div>`
                    : `<div class="w-10 h-10 rounded-full bg-blue-500 text-white flex items-center justify-center font-bold">${avatarText}</div>`;

                div.innerHTML = `
                    <div class="relative shrink-0 mr-3">
                        ${avatarHTML}
                        <span id="unread-${contact.id}" class="hidden absolute -top-1 -right-1 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full border-2 border-white shadow-sm"></span>
                    </div>
                    <div class="flex-1 overflow-hidden">
                        <div class="text-sm font-semibold truncate text-gray-700">${escapeHTML(contact.name)}</div>
                        <div class="text-xs text-gray-400 truncate">ID: ${escapeHTML(contact.id)}</div>
                    </div>
                `;
                updateUnreadBadge(contact.id);
                if (contact.id === currentTargetId) currentChatName.innerText = contact.name || '';
            }

            function updateUnreadBadge(contactId) {
                const badge = document.getElementById(`unread-${contactId}`);
                if (badge) {
                    const count = unreadCounts[contactId] || 0;
                    if (count > 0) {
                        badge.innerText = count > 99 ? '99+' : count;
                        badge.classList.remove('hidden');
                    } else {
                        badge.classList.add('hidden');
                    }
                }
            }

            function switchChat(contact) {
                saveDraft(); cancelFile(); cancelQuote(); hideContextMenu();
                currentTargetId = contact.id;
                currentMsgType = contact.type;
                currentChatName.innerText = contact.name + (contact.readonly || contact.type === 'legacy' ? '（只读归档）' : '');
                restoreDraft(store.drafts[contact.id]);

                unreadCounts[contact.id] = 0;
                updateUnreadBadge(contact.id);
                saveUnread();

                if (window.innerWidth < 768) {
                    sidebar.classList.add('hidden');
                    chatArea.classList.remove('hidden');
                    chatArea.classList.add('flex');
                }

                document.querySelectorAll('#contact-list > div').forEach(el => el.classList.remove('bg-blue-50', 'border-l-4', 'border-blue-500'));
                const activeEl = document.getElementById(`contact-${contact.id}`);
                if (activeEl) activeEl.classList.add('bg-blue-50', 'border-l-4', 'border-blue-500');

                messageArea.innerHTML = '<div id="history-loading" role="status" class="text-center text-gray-400 text-sm mt-4">加载中...</div>';
                renderCurrent(); requestHistory(); updateStatusUI();
                            inputBox.focus();
            }

            function appendMessage(data, scroll = true) {
                if (data.target_id !== currentTargetId) return;
                if (document.getElementById('msg-' + data.message_id)) return;
                const isSelf = isSelfSender(data);
                            const row = document.createElement('div');
                row.className = `w-full flex mb-4 ${isSelf ? 'justify-end' : 'justify-start'}`;
                row.id = `msg-${data.message_id}`;

                if (data.is_recalled) {
                    row.className = 'w-full flex justify-center mb-4';
                    row.innerHTML = `<div class="text-xs text-gray-400 bg-gray-200 px-3 py-1 rounded-full">${escapeHTML(data.content)}</div>`;
                    messageArea.appendChild(row);
                    if(scroll) messageArea.scrollTop = messageArea.scrollHeight;
                    return;
                }

                const col = document.createElement('div');
                col.className = `flex flex-col ${isSelf ? 'items-end' : 'items-start'} max-w-[85%] md:max-w-[75%]`;

                const meta = document.createElement('div');
                meta.className = 'text-[10px] md:text-xs text-gray-400 mb-1 px-1 whitespace-nowrap';
                meta.innerText = `${isSelf ? (myQQ !== null ? String(myQQ) : '我') : data.sender_name} · ${data.time}`;

                const bubble = document.createElement('div');
                const isImage = !!data.media_url && !data.file_name;
                bubble.className = `rounded-2xl text-sm break-words shadow-sm whitespace-pre-wrap cursor-context-menu ${
                    isImage ? 'p-1 bg-transparent border-none shadow-none' :
                    (isSelf ? 'bg-blue-500 text-white rounded-tr-sm px-3 py-1.5 md:px-4 md:py-2' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm px-3 py-1.5 md:px-4 md:py-2')
                }`;
                bubble.style.width = 'fit-content';
                bubble.style.maxWidth = '100%';
                bubble.style.wordBreak = 'break-word';

                if (data.quote) {
                    let quoteDiv = document.createElement('div');
                    quoteDiv.className = 'mb-2 px-2 py-1.5 rounded text-xs border-l-4 border-gray-400/50 bg-black/10 opacity-90 truncate cursor-pointer hover:opacity-100 transition-opacity';
                    quoteDiv.innerHTML = `<span class="font-semibold">${escapeHTML(data.quote.sender_name)}</span>: ${escapeHTML(data.quote.content)}`;
                    quoteDiv.onclick = (e) => {
                        e.stopPropagation();
                        const targetRow = document.getElementById(`msg-${data.quote.id}`);
                        if (targetRow) {
                            targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            targetRow.style.transition = 'background-color 0.3s';
                            targetRow.style.backgroundColor = 'rgba(59, 130, 246, 0.1)';
                            setTimeout(() => { targetRow.style.backgroundColor = 'transparent'; }, 1500);
                        } else {
                            showToast('引用的消息已不在当前会话中');
                        }
                    };
                    bubble.appendChild(quoteDiv);
                }

                ChatCore.renderSegments(bubble, data);

                            bubble.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    contextMenuMsg = data;

                    if (isSelf) {
                        ctxRecall.style.display = 'block';
                    } else {
                        ctxRecall.style.display = 'none';
                    }

                    contextMenu.style.visibility = 'hidden';
                    contextMenu.classList.add('show');

                    const menuWidth = contextMenu.offsetWidth;
                    const menuHeight = contextMenu.offsetHeight;

                    let left = e.clientX;
                    let top = e.clientY;

                    if (isSelf) left = e.clientX - menuWidth;

                    if (left + menuWidth > window.innerWidth) left = left - menuWidth;
                    if (top + menuHeight > window.innerHeight) top = top - menuHeight;

                    contextMenu.style.left = left + 'px';
                    contextMenu.style.top = top + 'px';
                    contextMenu.style.visibility = 'visible';
                });

                col.appendChild(meta);
                col.appendChild(bubble);
                row.appendChild(col);
                messageArea.appendChild(row);
                if(scroll) messageArea.scrollTop = messageArea.scrollHeight;
            }

            function updateRecallUI(messageId, senderName) {
                const row = document.getElementById(`msg-${messageId}`);
                if (row) {
                    const name = senderName || '某人';
                    row.className = 'w-full flex justify-center mb-4';
                    row.innerHTML = `<div class="text-xs text-gray-400 bg-gray-200 px-3 py-1 rounded-full">「${escapeHTML(name)}」撤回了一条消息</div>`;
                }
            }

            document.getElementById('ctx-reply').onclick = () => {
                if (!contextMenuMsg || contextMenuMsg.target_id !== currentTargetId || !currentTargetId || currentTargetId.indexOf('legacy:') === 0) { showToast('只读归档不支持引用或撤回'); return; }
                quoteMsg = contextMenuMsg;
                quoteText.innerText = `引用: ${quoteMsg.content}`;
                quotePreview.classList.remove('hidden');
                inputBox.focus();
                hideContextMenu();
            };

            ctxRecall.onclick = () => {
                if (!contextMenuMsg || contextMenuMsg.target_id !== currentTargetId || !currentTargetId || currentTargetId.indexOf('legacy:') === 0) { showToast('只读归档不支持引用或撤回'); return; }
                if (!connection || !connection.send({
                    type: 'recall_message',
                    target_id: currentTargetId,
                    message_id: contextMenuMsg.message_id
                })) showToast('Web 连接断开，撤回未提交');
                hideContextMenu();
            };

            function cancelQuote() {
                quoteMsg = null;
                quotePreview.classList.add('hidden');
            }
            document.getElementById('cancel-quote').onclick = cancelQuote;

            function escapeHTML(str) {
                return String(str == null ? '' : str).replace(/[&<>'"]/g, tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag));
            }

            function sendMessage() {
                const content = inputBox.value.trim();
                if (!currentTargetId || currentTargetId.indexOf('legacy:') === 0 || !connection || connection.state !== 'open') { showToast('Web 连接已断开，草稿已保留'); return; }
                if (!content && !pendingFile) return;
                if (!connection.napcat || !connection.qq) { showToast('NapCat 或 QQ 离线，草稿已保留'); return; }
                if (pendingFile && (pendingFile.target_id !== currentTargetId || !pendingFile.local_path || currentXHR)) { showToast('文件仍在上传或不属于当前会话'); return; }
                saveDraft();
                const payload = store.begin(currentTargetId, currentMsgType, store.drafts[currentTargetId]);
                if (!connection.send(payload)) { store.result({request_id: payload.request_id, status: 'failed', content: '未写入连接，草稿保留'}); renderCurrent(); return; }
                inputBox.value = ''; inputBox.style.height = '38px'; cancelQuote(); cancelFile();
                store.drafts[currentTargetId] = {text: ''}; renderCurrent();
            }

                        inputBox.addEventListener('compositionstart', () => { composing = true; });
            inputBox.addEventListener('compositionend', () => { composing = false; });
            sendBtn.addEventListener('click', sendMessage);
            inputBox.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !composing && e.keyCode !== 229) {
                    e.preventDefault();
                    sendMessage();
                }
            });

            var uiCfg = { use_image_host: true, theme: 'light', theme_auto: false, dark_start: '20:00', dark_end: '07:00', chat_background: false, sound_enabled: true };
            var settingsData = null, settingsTab = 'whitelist';
            var savedSettings = null, settingsRequest = null, settingsLoading = false;
            var settingsLoadError = '', uiSettingsRequest = null, settingsSaveRequest = null, settingsConflict = false;
            var wallpaperBlob = null, wallpaperUrl = null, wallpaperRequest = null;
            var wallpaperRevealTimer = null;

            function parseHM(v) {
                var p = String(v || '00:00').split(':');
                return (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0);
            }
            function inDarkRange() {
                var now = new Date();
                var cur = now.getHours() * 60 + now.getMinutes();
                var a = parseHM(uiCfg.dark_start), b = parseHM(uiCfg.dark_end);
                if (a === b) return false;
                if (a < b) return cur >= a && cur < b;
                return cur >= a || cur < b;
            }
            function applyTheme() {
                var dark = uiCfg.theme === 'dark';
                if (uiCfg.theme_auto) { dark = inDarkRange(); }
                if (dark) { document.body.classList.add('theme-dark'); } else { document.body.classList.remove('theme-dark'); }
            }
            function applyUiCfg(cfg) {
                if (!cfg) return;
                uiCfg.use_image_host = cfg.use_image_host !== false;
                uiCfg.theme = cfg.theme === 'dark' ? 'dark' : 'light';
                uiCfg.theme_auto = !!cfg.theme_auto;
                uiCfg.dark_start = cfg.dark_start || '20:00';
                uiCfg.dark_end = cfg.dark_end || '07:00';
                uiCfg.chat_background = !!cfg.chat_background;
                uiCfg.sound_enabled = cfg.sound_enabled !== false;
                applyTheme();
                updateWallpaperView();
                ensureWallpaper();
            }
            (function initUiCfg() {
                var x = new XMLHttpRequest();
                uiSettingsRequest = x;
                function finish() {
                    if (uiSettingsRequest !== x) return;
                    uiSettingsRequest = null;
                    if (x.status === 200) {
                        try { applyUiCfg(JSON.parse(x.responseText)); return; } catch (e) {}
                    }
                    applyUiCfg(uiCfg);
                }
                try {
                    x.open('GET', '/api/ui_settings', true);
                    x.timeout = 8000;
                    x.onload = finish;
                    x.onerror = finish;
                    x.ontimeout = finish;
                    x.send();
                } catch (e) { finish(); }
            })();
            if (authToken) { loadSettings(); }
            setInterval(function() { if (uiCfg.theme_auto) { applyTheme(); } }, 30000);

            (function preloadNotifySound() {
                try {
                    notifyAudio = new Audio('/static/qq_notify.mp3');
                    notifyAudio.preload = 'auto';
                    try { notifyAudio.load(); } catch (e) {}
                    try { fetch('/static/qq_notify.mp3', { cache: 'force-cache' }).catch(function() {}); } catch (e) {}
                } catch (e) {}
            })();

            var notifyAudio = null;
            function playNotifySound() {
                if (!uiCfg.sound_enabled) return;
                try {
                    if (!notifyAudio) { notifyAudio = new Audio('/static/qq_notify.mp3'); }
                    notifyAudio.currentTime = 0;
                    var p = notifyAudio.play();
                    if (p && p.catch) { p.catch(function() {}); }
                } catch (e) {}
            }
            function testNotifySound() { playNotifySound(); }
            function openSettings() {
                var m = document.getElementById('settings-modal');
                if (!m) return;
                if (!m.getAttribute('data-bound')) {
                    m.setAttribute('data-bound', '1');
                    var mask = document.getElementById('settings-mask');
                    var cancel = document.getElementById('settings-cancel');
                    var save = document.getElementById('settings-save');
                    if (mask) mask.onclick = closeSettings;
                    if (cancel) cancel.onclick = closeSettings;
                    if (save) save.onclick = saveSettings;
                }
                var alreadyOpen = !m.classList.contains('hidden');
                m.classList.remove('hidden');
                updateSettingsTabs();
                if (!alreadyOpen || !settingsData) {
                    settingsData = savedSettings ? JSON.parse(JSON.stringify(savedSettings)) : null;
                }
                if (!alreadyOpen) { settingsData = null; renderSettingsLoading(); loadSettings(); }
                else if (settingsData) renderSettingsTab(settingsTab);
            }
            function closeSettings() {
                var m = document.getElementById('settings-modal');
                if (m) m.classList.add('hidden');
                settingsData = null;
            }
            function renderSettingsLoading() {
                var el = document.getElementById('settings-body');
                if (!el) return;
                if (settingsLoadError) {
                    el.innerHTML = '<p class="text-sm text-red-500 mb-3">' + escapeAttr(settingsLoadError) + '</p>'
                        + '<button type="button" id="settings-retry" class="px-3 py-1.5 text-sm rounded-md border border-gray-300 text-gray-600">重试</button>';
                    document.getElementById('settings-retry').onclick = function() { loadSettings(); };
                } else {
                    el.innerHTML = '<p class="text-sm text-gray-400" role="status">正在加载设置…</p>';
                }
                document.getElementById('settings-save').disabled = true;
            }
            function resetSettingsCache() {
                var pending = settingsRequest;
                settingsRequest = null;
                settingsLoading = false;
                if (pending) pending.abort();
                var saving = settingsSaveRequest; settingsSaveRequest = null;
                if (saving) saving.abort();
                savedSettings = null; settingsConflict = false;
                settingsLoadError = '';
                closeSettings();
            }
            function updateSettingsTabs() {
                var tabs = document.querySelectorAll('.set-tab');
                for (var i = 0; i < tabs.length; i++) {
                    if (tabs[i].getAttribute('data-tab') === settingsTab) {
                        tabs[i].className = 'set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1 bg-blue-50 text-blue-600 font-semibold';
                    } else {
                        tabs[i].className = 'set-tab w-full text-left px-3 py-2 rounded-lg text-sm mb-1 text-gray-600 hover:bg-gray-100';
                    }
                }
            }
            function switchSettingsTab(tab) {
                collectSettingsTab();
                settingsTab = tab;
                if (settingsData) { renderSettingsTab(tab); } else { renderSettingsLoading(); }
                updateSettingsTabs();
            }
            function loadSettings() {
                if (!authToken || settingsLoading) return;
                var requestToken = authToken;
                var x = new XMLHttpRequest();
                settingsRequest = x;
                settingsLoading = true;
                settingsLoadError = '';
                function isOpen() {
                    var modal = document.getElementById('settings-modal');
                    return modal && !modal.classList.contains('hidden');
                }
                if (isOpen() && !settingsData) renderSettingsLoading();
                function fail(message) {
                    if (settingsRequest !== x) return;
                    settingsRequest = null;
                    settingsLoading = false;
                    if (authToken !== requestToken) return;
                    settingsLoadError = message;
                    if (isOpen()) renderSettingsLoading();
                }
                try {
                    x.open('GET', '/api/settings', true);
                    x.timeout = 8000;
                    x.onload = function() {
                        if (settingsRequest !== x) return;
                        if (authToken !== requestToken) { fail('登录状态已变化，请重新打开设置'); return; }
                        if (x.status !== 200) {
                            fail(x.status === 401 ? '登录已过期，请重新登录' : '设置加载失败，请重试');
                            return;
                        }
                        var cfg;
                        try {
                            cfg = JSON.parse(x.responseText);
                            if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw new Error('Invalid settings');
                        } catch (e) { fail('设置数据无效，请重试'); return; }
                        settingsRequest = null;
                        settingsLoading = false;
                        settingsLoadError = '';
                        savedSettings = cfg.settings || cfg;
                        savedSettings.revision = cfg.revision !== undefined ? cfg.revision : savedSettings.revision;
                        settingsConflict = false;
                        if (isOpen()) {
                            settingsData = JSON.parse(JSON.stringify(savedSettings));
                            renderSettingsTab(settingsTab);
                            updateSettingsTabs();
                        }
                    };
                    x.onerror = function() { fail('设置加载失败，请检查网络后重试'); };
                    x.ontimeout = function() { fail('设置加载超时，请重试'); };
                    x.send();
                } catch (e) { fail('设置加载失败，请重试'); }
            }
            function escapeAttr(v) {
                return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
            }
            function renderSettingsTab(tab) {
                if (!settingsData) return;
                var el = document.getElementById('settings-body');
                if (!el) return;
                var h = '';
                if (tab === 'whitelist') {
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">白名单设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">只有这些 QQ 号 / 群号的消息才会被处理，多个用分号（;）分割。</p>'
                      + '<textarea id="set-whitelist" class="w-full h-40 border border-gray-300 rounded-lg p-3 text-sm outline-none focus:border-blue-500" placeholder="123456;789012">' + escapeAttr((settingsData.whitelist || []).join(';')) + '</textarea>';
                } else if (tab === 'blacklist') {
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">黑名单设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">这些 QQ 号 / 群号的消息会被完全忽略（不回复、不转发）。多个用分号（;）分割。</p>'
                      + '<textarea id="set-blacklist" class="w-full h-40 border border-gray-300 rounded-lg p-3 text-sm outline-none focus:border-blue-500" placeholder="QQ ID">' + escapeAttr((settingsData.blacklist || []).join(';')) + '</textarea>';
                } else if (tab === 'admin') {
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">管理员设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">管理员私聊可使用 = ALL ON/OFF、= CLEAR、= REBOOT；留空则禁用这些管理命令。</p>'
                      + '<input id="set-admin-qq" type="text" inputmode="numeric" maxlength="20" class="w-full border border-gray-300 rounded-lg p-3 text-sm outline-none focus:border-blue-500" placeholder="管理员 QQ 号" value="' + escapeAttr(settingsData.admin_qq_id || '') + '">';
                } else if (tab === 'background') {
                    var yes = settingsData.use_image_host !== false;
                    var chatBg = !!settingsData.chat_background;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">背景设置（所有设备共享）</div>'
                      + '<p class="text-xs text-gray-400 mb-3">登录页背景是否从图床随机加载。</p>'
                      + '<div class="mb-4"><span class="text-sm mr-4">使用图床：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-bg" value="yes" ' + (yes ? 'checked' : '') + '> <span class="ml-2 text-sm">是</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-bg" value="no" ' + (!yes ? 'checked' : '') + '> <span class="ml-2 text-sm">否</span></label></div>'
                      + '<div><span class="text-sm mr-4">聊天背景：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-chat-bg" value="yes" ' + (chatBg ? 'checked' : '') + '> <span class="ml-2 text-sm">是</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-chat-bg" value="no" ' + (!chatBg ? 'checked' : '') + '> <span class="ml-2 text-sm">否</span></label></div>'
                      + '<p class="text-xs text-gray-400 mt-3">聊天背景默认关闭；开启后聊天区域显示图床图片。</p>';
                } else if (tab === 'sound') {
                    var snd = settingsData.sound_enabled !== false;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-2">提示音设置</div>'
                      + '<p class="text-xs text-gray-400 mb-3">收到新消息时播放一次 QQ 提示音。</p>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-sound" value="yes" ' + (snd ? 'checked' : '') + '> <span class="ml-2 text-sm">开启</span></label>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-sound" value="no" ' + (!snd ? 'checked' : '') + '> <span class="ml-2 text-sm">关闭</span></label>'
                      + '<button type="button" class="px-3 py-1.5 text-sm rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50" onclick="testNotifySound()">试听</button>';
                } else if (tab === 'color') {
                    var dark = settingsData.theme === 'dark';
                    var auto = !!settingsData.theme_auto;
                    h = '<div class="text-sm font-semibold text-gray-800 mb-3">颜色设置（所有设备共享）</div>'
                      + '<div class="mb-4"><span class="text-sm mr-4">主题：</span>'
                      + '<label class="inline-flex items-center mr-6"><input type="radio" name="set-theme" value="light" ' + (!dark ? 'checked' : '') + '> <span class="ml-2 text-sm">白</span></label>'
                      + '<label class="inline-flex items-center"><input type="radio" name="set-theme" value="dark" ' + (dark ? 'checked' : '') + '> <span class="ml-2 text-sm">黑</span></label></div>'
                      + '<label class="inline-flex items-center mb-3"><input type="checkbox" id="set-theme-auto" ' + (auto ? 'checked' : '') + '> <span class="ml-2 text-sm">自定义切换时间段</span></label>'
                      + '<div class="flex items-center gap-3 ' + (auto ? '' : 'opacity-50') + '" id="set-theme-range">'
                      + '<span class="text-sm">黑色时间段</span>'
                      + '<input type="time" id="set-dark-start" value="' + escapeAttr(settingsData.dark_start || '20:00') + '" ' + (auto ? '' : 'disabled') + ' class="border border-gray-300 rounded-md px-2 py-1 text-sm">'
                      + '<span class="text-sm">至</span>'
                      + '<input type="time" id="set-dark-end" value="' + escapeAttr(settingsData.dark_end || '07:00') + '" ' + (auto ? '' : 'disabled') + ' class="border border-gray-300 rounded-md px-2 py-1 text-sm">'
                      + '</div>'
                      + '<p class="text-xs text-gray-400 mt-3">启用后，黑色时间段内自动使用黑色主题，其余时间为白色主题；未启用则手动选择。</p>';
                } else {
                    h = '<div class="text-lg font-bold text-gray-800 mb-3">关于 QQ Web Chat</div>'
                      + '<div class="text-sm text-gray-600 space-y-2">'
                      + '<p>Copyright © 2026 LJY2231 · 保留所有权</p>'
                      + '<p>基于 Web + WS 技术架构</p>'
                      + '</div>';
                }
                el.innerHTML = h;
                document.getElementById('settings-save').disabled = !!settingsSaveRequest || settingsConflict;
                if (tab === 'color') {
                    var autoEl = document.getElementById('set-theme-auto');
                    if (autoEl) {
                        autoEl.onchange = function() {
                            var range = document.getElementById('set-theme-range');
                            var ds = document.getElementById('set-dark-start');
                            var de = document.getElementById('set-dark-end');
                            if (this.checked) {
                                range.classList.remove('opacity-50'); ds.disabled = false; de.disabled = false;
                            } else {
                                range.classList.add('opacity-50'); ds.disabled = true; de.disabled = true;
                            }
                        };
                    }
                }
            }
            function collectSettingsTab() {
                if (!settingsData) return;
                if (settingsTab === 'whitelist') {
                    var a = document.getElementById('set-whitelist'); if (a) settingsData.whitelist = a.value.replace(/；/g, ';').split(';').map(function(id) { return id.trim(); }).filter(Boolean);
                } else if (settingsTab === 'blacklist') {
                    var b = document.getElementById('set-blacklist'); if (b) settingsData.blacklist = b.value.replace(/；/g, ';').split(';').map(function(id) { return id.trim(); }).filter(Boolean);
                } else if (settingsTab === 'admin') {
                    var ad = document.getElementById('set-admin-qq'); if (ad) settingsData.admin_qq_id = ad.value.trim();
                } else if (settingsTab === 'background') {
                    var r = document.querySelector('input[name="set-bg"]:checked');
                    if (r) settingsData.use_image_host = (r.value === 'yes');
                    var cb = document.querySelector('input[name="set-chat-bg"]:checked');
                    if (cb) settingsData.chat_background = (cb.value === 'yes');
                } else if (settingsTab === 'sound') {
                    var sr = document.querySelector('input[name="set-sound"]:checked');
                    if (sr) settingsData.sound_enabled = (sr.value === 'yes');
                } else if (settingsTab === 'color') {
                    var t = document.querySelector('input[name="set-theme"]:checked');
                    if (t) settingsData.theme = t.value;
                    var auto = document.getElementById('set-theme-auto'); if (auto) settingsData.theme_auto = auto.checked;
                    var s1 = document.getElementById('set-dark-start'); if (s1) settingsData.dark_start = s1.value;
                    var e1 = document.getElementById('set-dark-end'); if (e1) settingsData.dark_end = e1.value;
                }
            }
            function saveSettings() {
                if (settingsSaveRequest || settingsConflict || !authToken) return;
                collectSettingsTab();
                if (!settingsData) return;
                if (settingsData.revision === undefined) { showToast('设置缺少版本，拒绝覆盖，请重新加载'); return; }
                var requestSession = authToken;
                var payload = {revision: settingsData.revision};
                var allowed = ['whitelist','blacklist','admin_qq_id','use_image_host','theme','theme_auto','dark_start','dark_end','chat_background','sound_enabled'];
                allowed.forEach(function(key) {
                    if (JSON.stringify(settingsData[key]) !== JSON.stringify(savedSettings && savedSettings[key])) payload[key] = settingsData[key];
                });
                var x = new XMLHttpRequest();
                settingsSaveRequest = x; document.getElementById('settings-save').disabled = true;
                function finishSave() {
                    if (settingsSaveRequest !== x) return;
                    settingsSaveRequest = null;
                    if (authToken !== requestSession) return;
                    document.getElementById('settings-save').disabled = false;
                    if (x.status === 200) {
                        var d;
                        try {
                            d = JSON.parse(x.responseText);
                            if (!d.ok || !d.settings) throw new Error('Invalid settings');
                        } catch (e) { showToast('保存失败：服务器返回数据无效'); return; }
                        savedSettings = d.settings; savedSettings.revision = d.revision !== undefined ? d.revision : savedSettings.revision;
                        if (uiSettingsRequest) {
                            var pendingUi = uiSettingsRequest;
                            uiSettingsRequest = null;
                            pendingUi.abort();
                        }
                        applyUiCfg(savedSettings);
                        showToast('设置已保存');
                        closeSettings();
                    } else if (x.status === 409) {
                        showToast('设置已被其他设备修改，未覆盖。请关闭后重新打开并核对最新设置');
                        document.getElementById('settings-save').disabled = true;
                        settingsConflict = true; settingsLoadError = '版本冲突，请重新打开设置';
                    } else {
                        showToast(x.status === 0 ? '保存超时或网络异常，结果未确认；请重新打开核对，草稿保留' : '保存失败，草稿保留');
                    }
                }
                x.onreadystatechange = function() { if (x.readyState === 4) finishSave(); };
                x.onerror = x.ontimeout = finishSave;
                try {
                    x.open('POST', '/api/settings', true);
                    x.setRequestHeader('Content-Type', 'application/json');
                    x.timeout = 8000; x.send(JSON.stringify(payload));
                } catch (e) { finishSave(); }
            }
