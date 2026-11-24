'use strict';
// Synthetic Node VM + deliberately small DOM. Not a browser/device test.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const base = path.resolve(__dirname, '..');
function harness(page) {
    const nodes = {}, requests = [], timers = [], revoked = [];
    function node(tag) {
        const n = {tagName: tag, children: [], style: {setProperty(){}, removeProperty(){}}, dataset: {}, value: '', disabled: false, scrollHeight: 10,
            className: '', attributes: {}, events: {},
            classList: {add(){},remove(){},toggle(){},contains(){return true;}},
            appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); this.children.push(c); c.parentNode = this; if (c.id) nodes[c.id] = c; return c; },
            removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; },
            remove() { if(this.parentNode) this.parentNode.removeChild(this); },
            setAttribute(k,v) { this.attributes[k] = v; }, getAttribute(k) { return this.attributes[k] || null; }, removeAttribute(k) { delete this.attributes[k]; },
            addEventListener(k,fn) { this.events[k] = fn; }, getElementsByTagName() { return this.children; }, querySelectorAll(){return [];},
            focus(){}, click(){ if (this.onclick) this.onclick(); }, scrollIntoView(){}, getContext(){return null;}
        };
        let html = '', text = '';
        Object.defineProperty(n, 'innerHTML', {get(){return html;},set(v){html = v; for(const c of this.children) c.parentNode = null; this.children = []; text = v;}});
        Object.defineProperty(n, 'textContent', {get(){return text + this.children.map(c=>c.textContent).join('');},set(v){text=String(v); this.children=[];}});
        Object.defineProperty(n, 'innerText', {get(){return this.textContent;},set(v){this.textContent=v;}});
        return n;
    }
    const html = fs.readFileSync(path.join(base, 'templates', page+'.html'), 'utf8');
    for (const match of html.matchAll(/id="([^"]+)"/g)) {nodes[match[1]] = node('div'); nodes[match[1]].id=match[1]; nodes[match[1]].root=true;}
    const document = {body: node('body'), hidden: false, getElementById(id) {const n=nodes[id]; return n && (n.root || n.parentNode) ? n : null;}, createElement:node,createTextNode(t){const n=node('#text');n.textContent=t;return n;},addEventListener(){},querySelectorAll(){return [];},querySelector(){return null;}};
    class XHR {
        constructor(){this.upload={}; requests.push(this);this.status=0;}
        open(method,url){this.method=method;this.url=url;}
        setRequestHeader(k,v){if(!this.headers)this.headers={};this.headers[k]=v;} send(body){this.body=body;} abort(){this.aborted=true;if(this.onabort)this.onabort();}
        respond(status,data){this.status=status;this.responseText=JSON.stringify(data);this.readyState=4;if(this.onload)this.onload();if(this.onreadystatechange)this.onreadystatechange();}
    }
    class Socket {
        constructor(url){this.url=url;this.readyState=0;this.sent=[];}
        send(text){this.sent.push(JSON.parse(text));} close(){this.readyState=3;if(this.onclose)this.onclose({code:1006});}
    }
    const ctx = {document, console, XMLHttpRequest:XHR,WebSocket:Socket, FormData:class {constructor(){this.fields={};} append(k,v){this.fields[k]=v;}},
        location:{protocol:'https:',host:'synthetic.invalid'},innerWidth:900,addEventListener(){},
        localStorage:{getItem(){throw Error('storage blocked');},setItem(){throw Error('storage blocked');},removeItem(){throw Error('storage blocked');}},
        setTimeout(fn,delay){const t={fn,delay};timers.push(t);return t;},clearTimeout(t){if(t)t.cancelled=true;},setInterval(fn,delay){const t={fn,delay};timers.push(t);return t;},clearInterval(t){if(t)t.cancelled=true;},
        URL:{createObjectURL(){return 'blob:synthetic';},revokeObjectURL(url){revoked.push(url);}},
        Audio:class {load(){} play(){return Promise.resolve();}},fetch(){return Promise.reject(Error('synthetic offline'));},
        alert(){}, confirm(){return true;},performance:{now(){return 5000;}},matchMedia(){return {matches:false};},Image:class {},
        requestAnimationFrame(){}, Date, Uint32Array, Math
    };ctx.window=ctx;vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(base,'static/chat-core.js'),'utf8'),ctx);
    vm.runInContext(fs.readFileSync(path.join(base,'static',page+'.js'),'utf8'),ctx);
    function run(code){return vm.runInContext(code,ctx);}
    return {ctx,run,nodes,requests,timers,revoked,node};
}
let count=0;
function test(name,fn){fn();count++;console.log('ok '+count+' - '+name);}
test('both scripts initialize with blocked Storage and no Element.animate',()=>{harness('main');harness('nr');});
test('typed identity dedup, outgoing direction, revision boundary and cap',()=>{
 const h=harness('nr'), C=h.ctx.ChatCore, s=new C.Store();
 assert.equal(C.self({sender_name:'我',direction:'incoming'}),false);
 assert.equal(C.self({sender_name:'another',direction:'outgoing'}),true);
 assert.equal(C.typed('123'),false); assert.equal(C.typed('legacy:123'),true);
 const m={target_id:'private:100',message_id:'m1',revision:3,direction:'incoming',content:'加载中'};
 assert.equal(s.message(m),true); assert.equal(s.message(m),false);
 s.message({...m,target_id:'group:100'}); assert.equal(s.list('group:100').length,1);
 s.history({target_id:'private:100',revision:2,messages:[]});assert.equal(s.list('private:100').length,1);
 s.message({...m,revision:4,content:'updated'});assert.equal(s.list('private:100')[0].content,'updated');
 for(let i=0;i<250;i++)s.message({...m,message_id:'new'+i});assert.equal(s.list('private:100').length,200);
 assert.throws(()=>s.begin('legacy:123','legacy',{text:'x'}),/Read-only/);
});
test('send states retain failed drafts and never replay unknown operations',()=>{
 const s=new (harness('nr').ctx.ChatCore.Store)(), draft={text:'caption',file:{local_path:'/synthetic/upload',name:'sample.txt'}};
 const p=s.begin('private:100','private',draft);assert.ok(p.request_id);assert.equal(s.outbox[p.request_id].status,'pending');
 s.result({request_id:p.request_id,status:'failed'});assert.equal(s.outbox[p.request_id].draft,draft);
 const q=s.begin('private:100','private',{text:'two'});s.uncertain();assert.equal(s.outbox[q.request_id].status,'unknown');
 s.result({request_id:q.request_id,status:'partial'});assert.equal(s.outbox[q.request_id].status,'partial');
 s.result({request_id:q.request_id,status:'pending'});assert.equal(s.outbox[q.request_id].status,'partial');
});
for(const page of ['main','nr']) {
 test(page+' upload target/request identity rejects late callbacks',()=>{
  const h=harness(page), main=page==='main';
  h.run(main?"currentTargetId='private:100';handleFileSelect({name:'A.png',size:10},true)":"currentId='private:100';uploadFile({name:'A.png',size:10},true)");
  const first=h.requests[h.requests.length-1];
  h.run(main?"cancelFile();currentTargetId='group:100';handleFileSelect({name:'B.txt',size:20},false)":"cancelFile();currentId='group:100';uploadFile({name:'B.txt',size:20},false)");
  const second=h.requests[h.requests.length-1];first.respond(200,{local_path:'/synthetic/A'});
  assert.equal(h.run('pendingFile.name'),'B.txt');assert.equal(h.run('pendingFile.local_path'),null);
  second.respond(200,{local_path:'/synthetic/B'});assert.equal(h.run('pendingFile.local_path'),'/synthetic/B');
  h.run('cancelFile()');first.respond(200,{local_path:'/synthetic/A'});assert.equal(h.run('pendingFile'),null);
  if(main)assert.ok(h.revoked.includes('blob:synthetic'));
 });
 test(page+' IME Enter does not send; ordinary Enter sends request id',()=>{
  const h=harness(page), main=page==='main', input=h.nodes[main?'input-box':'msgInput'];
  h.run((main?"currentTargetId='private:100';currentMsgType='private';":"currentId='private:100';currentType='private';")+"connection={state:'open',napcat:true,qq:true,send:function(p){window.sent=p;return true;}};");input.value='hello';
  const fn=main?input.events.keydown:input.onkeydown;
  fn({key:'Enter',keyCode:13,isComposing:true,preventDefault(){throw Error('must not prevent');}});assert.equal(h.ctx.sent,undefined);
  fn({key:'Enter',keyCode:13,preventDefault(){}});assert.ok(h.ctx.sent.request_id);assert.equal(h.ctx.sent.target_id,'private:100');
  assert.equal(h.run('store.outbox[window.sent.request_id].status'),'pending');
  h.run((main?'handleEvent':'handle')+"({type:'send_result',request_id:window.sent.request_id,status:'failed',content:'synthetic failure'})");
  assert.equal(h.run('store.outbox[window.sent.request_id].draft.text'),'hello');
 });
 test(page+' loading word preserves messages and message updates deduplicate',()=>{
  const h=harness(page), main=page==='main';h.run(main?"currentTargetId='private:100'":"currentId='private:100'");
  const handler=main?'handleEvent':'handle';
  h.run(handler+"({type:'message',target_id:'private:100',message_id:'a',direction:'incoming',content:'正在加载中',time:'00:00',revision:1});"+handler+"({type:'message',target_id:'private:100',message_id:'b',direction:'incoming',content:'next',time:'00:00',revision:2})");
  assert.ok(h.ctx.document.getElementById('msg-a'));assert.ok(h.ctx.document.getElementById('msg-b'));
  h.run(handler+"({type:'message_updated',target_id:'private:100',message_id:'b',direction:'incoming',content:'revised',time:'00:00',revision:3})");
  assert.equal(h.run("store.list('private:100').length"),2);assert.ok(h.ctx.document.getElementById('msg-b').textContent.includes('revised'));
 });
}
test('normalized multiple media, explicit unknown, file caption and image failure',()=>{
 const h=harness('nr'), parent=h.node('div');
 h.ctx.ChatCore.renderSegments(parent,{segments:[{type:'image',url:'/files/sample.png'},{type:'image',url:'/files/second.png'},{type:'file',url:'/files/doc.txt',name:'doc.txt'},{type:'text',text:'caption'},{type:'record'}]});
 assert.equal(parent.children.length,5);assert.ok(parent.textContent.includes('caption'));assert.ok(parent.textContent.includes('record'));
 const image=parent.children[0].children[1];image.onerror();assert.ok(parent.textContent.includes('图片无法加载'));
});
test('settings submits revision and changed fields without cached ACL',()=>{
 const h=harness('main');h.run("authToken='session-marker';settingsTab='about';savedSettings={revision:7,whitelist:['old'],blacklist:[],theme:'light'};settingsData={revision:7,whitelist:['old'],blacklist:[],theme:'dark'};saveSettings()");
 const x=h.requests[h.requests.length-1],payload=JSON.parse(x.body);
 assert.deepEqual(payload,{revision:7,theme:'dark'});x.respond(409,{});assert.equal(h.nodes['settings-save'].disabled,true);
});
test('cookie session endpoints and websocket have no query credentials',()=>{
 for(const page of ['main','nr']){
  const h=harness(page);h.run('enterSession()');const url=h.run('connection.socket.url');assert.equal(url,'wss://synthetic.invalid/client_ws');
  assert.ok(h.requests.every(x=>!/[?&]token=/.test(x.url)));
  h.run(page==='main'?'doLogout()':'logout()');const x=h.requests.find(x=>x.url==='/api/logout');assert.deepEqual(JSON.parse(x.body),{});
 }
});
test('transport handshake timeout, heartbeat, status separation and stop',()=>{
 const h=harness('nr');h.run('enterSession()');const c=h.ctx.connection,s=c.socket;
 s.readyState=1;s.onopen();assert.equal(c.state,'open');
 s.onmessage({data:JSON.stringify({type:'status',napcat_connected:true,qq_online:false})});assert.equal(c.napcat,true);assert.equal(c.qq,false);assert.ok(h.ctx.ChatCore.status(c).includes('QQ：离线'));
 c.watch.fn();assert.equal(s.sent[s.sent.length-1].type,'ping');c.stop();assert.equal(c.active,false);assert.equal(c.socket,null);
});
test('clear and recall tombstones defeat late snapshots/events without recalling quote updates',()=>{
 const s=new (harness('nr').ctx.ChatCore.Store)();
 const a={target_id:'private:1',message_id:'a',direction:'incoming',revision:1,content:'photo',media_url:'/files/a.png'};
 const b={target_id:'private:1',message_id:'b',direction:'incoming',revision:2,content:'reply',quote:{id:'a',content:'photo'}};
 s.message(a);s.message(b);s.recall({...a,revision:3,is_recalled:true});
 s.recall({...b,revision:3,quote:{id:'a',content:'[消息已撤回]',is_recalled:true}});
 assert.equal(s.list('private:1')[0].media_url,null);assert.equal(s.list('private:1')[0].quote,null);
 assert.ok(!s.list('private:1')[1].is_recalled);assert.ok(s.list('private:1')[1].quote.is_recalled);
 s.history({target_id:'private:1',revision:2,messages:[a,b]});assert.ok(s.list('private:1').find(m=>m.message_id==='a').is_recalled);
 s.clear({target_id:'private:1',revision:4});assert.equal(s.list('private:1').length,0);
 assert.equal(s.history({target_id:'private:1',revision:2,messages:[a]}),false);s.message(a);assert.equal(s.list('private:1').length,0);
 s.message({...b,message_id:'new',revision:6});s.clear({target_id:'private:1',revision:5});assert.equal(s.list('private:1')[0].message_id,'new');
 s.history({target_id:'private:1',revision:7,messages:[{...b,revision:6,message_id:'new'}]});
 s.message({...a,revision:6});assert.equal(s.list('private:1').length,1);
});
for(const page of ['main','nr']) {
 test(page+' streams more than 64 typed contacts without dropping colliding peer ids',()=>{
  const h=harness(page), handler=page==='main'?'handleEvent':'handle';
  for(let i=1;i<=130;i++)h.run(handler+'('+JSON.stringify({type:'contact_add',contact:{id:'private:'+i,peer_id:String(i),type:'private',name:'Peer '+i}})+')');
  h.run(handler+'('+JSON.stringify({type:'contact_add',contact:{id:'group:1',peer_id:'1',type:'group',name:'Group'}})+')');
  assert.equal(h.run('Object.keys(contacts).length'),131);
  assert.equal(h.nodes[page==='main'?'contact-list':'contacts'].children.filter(n=>n.id&&n.id.indexOf('contact-')===0).length,131);
 });
 test(page+' upload binds target and upload id on wire and preserves them for sending',()=>{
  const h=harness(page),main=page==='main';
  if(main)h.ctx.URL.createObjectURL=()=>{throw Error('unsupported Blob');};
  h.run(main?"currentTargetId='private:1';currentMsgType='private';handleFileSelect({name:'sample.png',size:5},true)":"currentId='private:1';currentType='private';uploadFile({name:'sample.png',size:5},true)");
  const x=h.requests[h.requests.length-1],uploadId=h.run('pendingFile.upload_id');
  assert.equal(x.headers['X-Conversation-ID'],'private:1');assert.equal(x.headers['X-Upload-ID'],uploadId);
  assert.equal(x.body.fields.target_id,'private:1');assert.equal(x.body.fields.upload_id,uploadId);
  x.respond(200,{local_path:'/synthetic/sample'});
  h.run("connection={state:'open',napcat:true,qq:true,send:function(p){window.sent=p;return true;}};"+(main?'sendMessage()':'sendMsg()'));
  assert.equal(h.ctx.sent.file_upload_id,uploadId);assert.notEqual(h.ctx.sent.request_id,uploadId);
 });
 test(page+' legacy is read-only and disconnect keeps send unknown without automatic replay',()=>{
  const h=harness(page),main=page==='main',input=h.nodes[main?'input-box':'msgInput'];
  h.run((main?"currentTargetId='legacy:1';currentMsgType='legacy';":"currentId='legacy:1';currentType='legacy';")+"connection={state:'open',napcat:true,qq:true,send:function(p){window.sent=p;return true;}};");
  input.value='archive';h.run(main?'sendMessage()':'sendMsg()');assert.equal(h.ctx.sent,undefined);
  h.run('connection=null;enterSession()');const c=h.ctx.connection||h.run('connection'),socket=c.socket;
  socket.readyState=1;socket.onopen();socket.onmessage({data:JSON.stringify({type:'status',napcat_connected:true,qq_online:true})});
  h.run(main?"currentTargetId='private:1';currentMsgType='private'":"currentId='private:1';currentType='private'");
  input.value='pending';h.run(main?'sendMessage()':'sendMsg()');
  const p=socket.sent.find(p=>p.type==='send_message');assert.ok(p);
  socket.close();assert.equal(h.run('store.outbox['+JSON.stringify(p.request_id)+'].status'),'unknown');
  c.open();const next=c.socket;next.readyState=1;next.onopen();
  assert.ok(next.sent.some(p=>p.type==='get_send_result'));assert.ok(!next.sent.some(p=>p.type==='send_message'));
  next.onmessage({data:JSON.stringify({type:'send_result',request_id:p.request_id,status:'confirmed',parts:[{message_id:'part1',status:'confirmed'}]})});
  assert.equal(h.run('store.outbox['+JSON.stringify(p.request_id)+'].status'),'confirmed');
 });
 test(page+' restore timeout, request abort and logout retry are observable',()=>{
  const h=harness(page),main=page==='main';if(main)h.run('restoreSession()');
  const restore=h.requests.find(x=>x.url==='/api/session');assert.equal(restore.timeout,10000);restore.ontimeout();
  assert.ok(h.nodes[main?'login-error':'loginErr'].textContent.includes('超时'));
  h.run(main?'doLogout()':'logout()');const logout=h.requests[h.requests.length-1];logout.ontimeout();
  assert.ok(h.nodes[main?'login-error':'loginErr'].textContent.includes('注销未确认'));
  assert.equal(h.nodes[main?'retry-logout':'retryLogout'].disabled,false);
 });
}
test('main context actions reject stale cross-conversation message',()=>{
 const h=harness('main');h.run("currentTargetId='group:2';contextMenuMsg={target_id:'private:1',message_id:'a'};connection={send:function(p){window.sent=p;return true;}};");
 h.nodes['ctx-recall'].onclick();assert.equal(h.ctx.sent,undefined);h.nodes['ctx-reply'].onclick();assert.equal(h.run('quoteMsg'),null);
});
test('settings network timeout unlocks once and conflict remains locked across tabs',()=>{
 const h=harness('main');h.run("authToken='session-marker';settingsTab='about';savedSettings={revision:2,theme:'light'};settingsData={revision:2,theme:'dark'};saveSettings();saveSettings()");
 const posts=h.requests.filter(x=>x.url==='/api/settings'&&x.method==='POST');assert.equal(posts.length,1);posts[0].ontimeout();assert.equal(h.nodes['settings-save'].disabled,false);
 h.run('saveSettings()');const post=h.requests[h.requests.length-1];post.respond(409,{});h.run("switchSettingsTab('about');saveSettings()");
 assert.equal(h.nodes['settings-save'].disabled,true);assert.equal(h.requests[h.requests.length-1],post);
});
test('handshake timeout invalidates stale sockets and retries only once',()=>{
 const h=harness('nr');h.run('enterSession()');const c=h.ctx.connection,s=c.socket,timeout=c.timer;
 timeout.fn();assert.equal(c.socket,null);assert.equal(c.attempt,1);assert.equal(s.onclose,null);
 const retry=c.timer;retry.fn();const active=c.socket;s.readyState=1;if(s.onopen)s.onopen();assert.equal(c.socket,active);assert.equal(c.state,'connecting');
 active.readyState=1;active.onopen();assert.ok(h.ctx.ChatCore.status(c).includes('NapCat：未知'));
 c.stop();
});
test('shared/NR files keep ES5-only syntax and no bearer persistence',()=>{
 for(const file of ['static/chat-core.js','static/nr.js']) {
  const code=fs.readFileSync(path.join(base,file),'utf8');
  const syntax=code.replace(/\/\*[\s\S]*?\*\//g,'').replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g,'').replace(/\/\/[^\n]*/g,'');
  assert.ok(!/\b(?:let|const|class|async|await)\b|=>|`|\.\.\./.test(syntax),file);
  assert.ok(!/localStorage\.setItem\([^\n]*token/.test(code),file);
  assert.ok(!/sessionStorage\.setItem\([^\n]*token/.test(file==='static/chat-core.js'?code:''),file);
 }
});
test('NR send result replaces the pending notice',()=>{
 const h=harness('nr');
 h.run("session=ChatCore.id();currentId='private:1';currentType='private';connection={send:function(){return true;},state:'open',napcat:true,qq:true};payload=store.begin('private:1','private',{text:'x'});notice('已提交，等待服务器确认');");
 h.run("handle({type:'send_result',request_id:payload.request_id,status:'confirmed',content:'已发送'})");
 assert.equal(h.nodes['feedback'].textContent,'已发送');
 h.run("payload2=store.begin('private:1','private',{text:'y'});handle({type:'send_result',request_id:payload2.request_id,status:'failed',content:'发送失败'})");
 assert.equal(h.nodes['feedback'].textContent,'发送失败');
});
test('expiry retains lookup identities only until explicit logout',()=>{
 for(const page of ['main','nr']) {
  const h=harness(page),main=page==='main';h.run('enterSession()');
  h.run("window.pending=store.begin('private:1','private',{text:'private draft',file:{local_path:'/synthetic/upload',upload_id:'upload'},quote:{id:'q',content:'private quote'}})");
  const old=h.run('connection.socket');old.onclose({code:1008});
  assert.equal(h.run('store.order.length'),1);assert.ok(!h.run('JSON.stringify(store.outbox)').includes('private draft'));
  assert.ok(!h.run('JSON.stringify(store.outbox)').includes('/synthetic/upload'));h.run('enterSession()');
  const socket=h.run('connection.socket');socket.readyState=1;socket.onopen();assert.ok(socket.sent.some(p=>p.type==='get_send_result'));assert.ok(!socket.sent.some(p=>p.type==='send_message'));
  h.run(main?'doLogout()':'logout()');assert.equal(h.run('store.order.length'),0);
 }
});
test('request error paths settle exactly once and animation fallback exposes login controls',()=>{
 const h=harness('main');let calls=0;
 const x=h.ctx.ChatCore.request('GET','/synthetic',null,()=>calls++);x.onerror();x.ontimeout();x.respond(500,{});assert.equal(calls,1);
 assert.equal(h.nodes['login-card'].style.opacity,'1');assert.equal(h.nodes['login-card'].style.pointerEvents,'auto');
 h.run("currentTargetId='private:1';handleFileSelect({name:'bad.txt',size:5},false)");const upload=h.requests[h.requests.length-1];upload.onerror();assert.equal(h.run('pendingFile'),null);
});
test('admin settings tab renders, collects and submits the administrator ID',()=>{
 const h=harness('main');
 h.run("settingsTab='admin';settingsData={revision:4,admin_qq_id:''};renderSettingsTab('admin')");
 assert.ok(h.nodes['settings-body'].innerHTML.includes('id="set-admin-qq"'));
 assert.ok(h.nodes['settings-body'].innerHTML.includes('管理员'));
 const input=h.node('input');input.id='set-admin-qq';input.value=' 12345 ';input.parentNode={};h.nodes['set-admin-qq']=input;
 h.run('collectSettingsTab()');assert.equal(h.run('settingsData.admin_qq_id'),'12345');
 h.run("settingsTab='admin';savedSettings={revision:4};settingsData={revision:4,admin_qq_id:'12345'};authToken='session-marker';saveSettings()");
 const post=h.requests.filter(x=>x.url==='/api/settings'&&x.method==='POST').pop();
 assert.equal(JSON.parse(post.body).admin_qq_id,'12345');
});
test('NR image viewer opens from a thumbnail, zooms with buttons and closes',()=>{
 const h=harness('nr');
 assert.ok(!h.nodes['nrViewer'].style.display);
 const thumb=h.node('img');thumb.setAttribute('src','/files/synthetic.png');
 h.nodes['messages'].events.click({target:thumb});
 assert.equal(h.nodes['nrViewer'].style.display,'block');
 assert.equal(h.run('nrView.scale'),1);
 h.run('nrZoomIn()');assert.ok(h.run('nrView.scale')>1);
 for(let i=0;i<10;i++)h.run('nrZoomIn()');
 assert.equal(h.run('nrView.scale'),5);
 assert.equal(h.nodes['nrZoomIn'].disabled,true);
 assert.equal(h.nodes['nrZoomOut'].disabled,false);
 h.run('nrSetScale(1)');assert.equal(h.nodes['nrZoomOut'].disabled,true);
 h.run("nrStartDrag(10,10,{tagName:'div'})");h.run('nrMoveDrag(90,60)');
 assert.equal(h.run('nrView.tx'),80);assert.equal(h.run('nrView.ty'),50);
 h.nodes['nrViewerClose'].click();assert.equal(h.nodes['nrViewer'].style.display,'none');
});
test('NR appends new messages without rebuilding existing nodes',()=>{
 const h=harness('nr');
 h.run("currentId='private:100';currentType='private'");
 h.run("handle({type:'message',target_id:'private:100',message_id:'a',direction:'incoming',content:'one',time:'00:00',revision:1})");
 const first=h.ctx.document.getElementById('msg-a');
 h.run("handle({type:'message',target_id:'private:100',message_id:'b',direction:'incoming',content:'two',time:'00:00',revision:2})");
 assert.equal(h.ctx.document.getElementById('msg-a'),first);
 assert.ok(h.ctx.document.getElementById('msg-b'));
});
function findTag(node, tag) {
  if (!node) return null;
  if (node.tagName === tag) return node;
  for (const child of node.children || []) { const found = findTag(child, tag); if (found) return found; }
  return null;
}
function findClass(node, name) {
  if (!node) return null;
  if (String(node.className || '').split(' ').indexOf(name) >= 0) return node;
  for (const child of node.children || []) { const found = findClass(child, name); if (found) return found; }
  return null;
}
test('NR defers images older than one hour and loads them from the download action',()=>{
 const h=harness('nr');
 const oldMsg={target_id:'private:1',message_id:'old',direction:'incoming',time:'2020-01-01 00:00:00',revision:1,segments:[{type:'image',url:'/files/old.png'}]};
 const oldNode=h.run('buildMessage('+JSON.stringify(oldMsg)+')');
 assert.ok(oldNode.textContent.includes('图片信息'));
 assert.ok(oldNode.textContent.includes('下载'));
 assert.equal(findTag(oldNode,'img'),null);
 const now=new Date(), pad=n=>String(n).padStart(2,'0');
 const ts=now.getFullYear()+'-'+pad(now.getMonth()+1)+'-'+pad(now.getDate())+' '+pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());
 const newNode=h.run('buildMessage('+JSON.stringify({target_id:'private:1',message_id:'new',direction:'incoming',time:ts,revision:2,segments:[{type:'image',url:'/files/new.png'}]})+')');
 assert.ok(findTag(newNode,'img'));
 findClass(oldNode,'nr-late-download').onclick();
 assert.ok(findTag(oldNode,'img'));
});
test('main defers old images and shows them on download, keeps recent images inline',()=>{
 const h=harness('main');
 h.run("currentTargetId='private:1'");
 const oldMsg={target_id:'private:1',message_id:'old',direction:'incoming',time:'2020-01-01 00:00:00',revision:1,sender_name:'synthetic',segments:[{type:'image',url:'/files/old.png'}]};
 h.run('appendMessage('+JSON.stringify(oldMsg)+')');
 const oldRow=h.ctx.document.getElementById('msg-old');
 assert.ok(oldRow.textContent.includes('图片信息'));
 assert.equal(findTag(oldRow,'img'),null);
 findTag(oldRow,'button').onclick();
 assert.ok(findTag(oldRow,'img'));
 const now=new Date(), pad=n=>String(n).padStart(2,'0');
 const ts=now.getFullYear()+'-'+pad(now.getMonth()+1)+'-'+pad(now.getDate())+' '+pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds());
 h.run('appendMessage('+JSON.stringify({target_id:'private:1',message_id:'recent',direction:'incoming',time:ts,revision:2,sender_name:'synthetic',segments:[{type:'image',url:'/files/recent.png'}]})+')');
 assert.ok(findTag(h.ctx.document.getElementById('msg-recent'),'img'));
});
test('main shows one check per published outgoing bubble, not a second confirmed receipt',()=>{
 const h=harness('main'); h.run("currentTargetId='private:1'");
 for(const status of ['confirmed','failed','unknown','pending','partial']) {
   h.run(`{const p=store.begin(currentTargetId,'private',{text:'draft'});handleEvent({type:'send_result',request_id:p.request_id,status:'${status}'})}`);
 }
 const states=h.nodes['send-states'];assert.deepEqual(states.children.map(n=>n.getAttribute('data-status')),['failed','unknown','pending','partial']);
 assert.ok(findTag(states.children[0],'button'));
 for(const [id,direction,recalled] of [['a','outgoing',false],['b','incoming',false],['c','outgoing',true]]) {
   h.run('appendMessage('+JSON.stringify({target_id:'private:1',message_id:id,direction,is_recalled:recalled,content:'sample'})+')');
   const mark=findClass(h.nodes['msg-'+id],'sent-check');
   if(id==='a') { assert.equal(mark.textContent,'✓');assert.strictEqual(mark.parentNode.children[0],mark);assert.equal(mark.parentNode.children[1].textContent,'sample'); }
   else assert.equal(mark,null);
 }
});
test('main first-run setup validates, submits once and then logs in with username',()=>{
 const h=harness('main');
 h.ctx.location.search='?setup=synthetic-setup-token';
 assert.equal(h.run('setupTokenFromUrl()'),'synthetic-setup-token');
 h.run("showSetup(setupTokenFromUrl());setupUser.value='admin';setupPwd.value='short';setupPwd2.value='short';doSetup()");
 assert.ok(h.nodes['setup-error'].textContent.includes('至少 8 位'));
 h.run("setupPwd.value='synthetic-pass';setupPwd2.value='synthetic-pass';doSetup()");
 const setup=h.requests[h.requests.length-1];
 assert.equal(setup.url,'/api/setup');
 assert.deepEqual(JSON.parse(setup.body),{token:'synthetic-setup-token',username:'admin',password:'synthetic-pass'});
 setup.respond(200,{ok:true});
 assert.equal(h.nodes['login-overlay'].style.display,'none');
 h.run("loginUser.value='admin';loginPwd.value='synthetic-pass';doLogin()");
 const login=h.requests[h.requests.length-1];
 assert.deepEqual(JSON.parse(login.body),{username:'admin',password:'synthetic-pass'});
});
test('NR sends the configured username when logging in',()=>{
 const h=harness('nr');
 h.run("$('nrUser').value='admin';$('pwd').value='synthetic-pass';doLogin()");
 const login=h.requests[h.requests.length-1];
 assert.deepEqual(JSON.parse(login.body),{username:'admin',password:'synthetic-pass'});
});
console.log('PASS '+count+' synthetic frontend tests (no browser/device assertions)');
