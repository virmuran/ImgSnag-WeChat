# -*- coding: utf-8 -*-
"""ImgSnag 安卓版的页面（HTML + CSS + JS 单文件）

页面用字符串常量而不是独立文件，是有意的：
Chaquopy 把 Python 源码打进 APK 后，"源码同目录的文件运行时还能不能读到"
没有明确文档保证 —— 一旦读不到，表现是**白屏**，而且只能在真机上才发现。
放进字符串就完全没有这一层不确定：页面从哪来、怎么发出去，本机就能跑到。

代价是改页面要动这个文件，但好处是：整页（结构/样式/交互）在一处看全，
而且 `python imgsnag_web.py` 起服务后浏览器打开就是**同一个页面**，
改完刷新即可 —— 界面这一层因此变成了可验证的。

交互约定：
  · 点缩略图 = 看大图（手机上的直觉）；点右上角圆圈 = 勾选/取消勾选
  · 大图左右滑动 = 翻上/下一张（放大状态下改成平移，不翻页）
  · 底部常驻条 = 全选 / 已选张数 / 保存到相册
  · 分享进来会**自动解析**，抓完停在结果页让你确认再入库（不直接写相册）

────────────────────────────────────────────────────────────────────────
返回键：页面自己管好"层级"
────────────────────────────────────────────────────────────────────────
系统的侧边滑动返回最终落到 Activity 的返回键，那边只做一件事：
**WebView 能回退就回退**。所以这里的 `history.pushState` 就是返回层级本身 ——
页面 push 了几层，返回手势就能退几层（关大图 → 回首页/历史页），
退到最外层再划一次才退出 App。

这条规定值得写在最显眼的地方：**凡是用户能"进去"的地方，都要 push 一层**，
否则从那里侧滑就直接退回桌面 —— 手机上的第一直觉操作变成"退出应用"，
是最容易被骂的那种 bug。

────────────────────────────────────────────────────────────────────────
剪贴板
────────────────────────────────────────────────────────────────────────
「从剪贴板」走 Java 侧注入的只读 JS 桥（`window.imgsnag.read`），
拿回 `{text, html}` 一起交给后端，由后端决定用哪份（见 imgsnag_web.pick_source）。
桥不存在（电脑上直接用浏览器打开页面）时按钮给一句人话，而不是静默失效。
"""

PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ImgSnag</title>
<style>
:root{
  --bg:#f4f4f6; --fg:#1b1b1f; --sub:#76767f; --card:#fff; --line:#e4e4e9;
  --accent:#07c160; --accent-fg:#fff; --danger:#d9483f; --warn:#c98a00;
  --shadow:0 1px 3px rgba(0,0,0,.07);
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#101013; --fg:#f0f0f4; --sub:#98989f; --card:#1a1a1e; --line:#2a2a30;
         --shadow:none; }
}
*{box-sizing:border-box; -webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Noto Sans SC","Segoe UI",sans-serif;
  -webkit-text-size-adjust:100%;
  overflow-wrap:break-word;
}
button{font:inherit;color:inherit;cursor:pointer}
[hidden]{display:none !important}

/* ── 顶栏 ── */
#topbar{
  position:sticky; top:0; z-index:6; display:flex; align-items:center; gap:4px;
  padding:calc(env(safe-area-inset-top) + 6px) 6px 6px;
  background:var(--card); border-bottom:1px solid var(--line);
}
#topbar h1{
  flex:1; margin:0; font-size:16px; font-weight:600; text-align:center;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.tb{
  min-width:44px; height:42px; padding:0 8px; border:0; background:none;
  font-size:15px; border-radius:10px;
}
.tb:active{background:var(--line)}

main{padding:12px 12px 104px}

/* ── 通用 ── */
.card{background:var(--card); border-radius:14px; padding:14px; box-shadow:var(--shadow);
      border:1px solid var(--line)}
.hint{color:var(--sub); font-size:13.5px; line-height:1.7; margin:0 0 12px}
.hint b{color:var(--fg)}
.row{display:flex; gap:8px; align-items:center}
.btn{
  border:1px solid var(--line); background:var(--card); border-radius:12px;
  padding:13px 16px; font-size:15px; min-height:48px;
}
.btn.primary{background:var(--accent); color:var(--accent-fg); border-color:transparent;
  font-weight:600; flex:1}
.btn.block{width:100%}
.btn:disabled{opacity:.45}
input[type=url],input[type=text]{
  width:100%; min-height:48px; padding:12px 14px; font-size:16px;
  background:var(--card); color:var(--fg);
  border:1px solid var(--line); border-radius:12px;
}
input::placeholder{color:var(--sub)}
.mt{margin-top:10px}

/* ── 开关 ── */
.setrow{display:flex; align-items:center; gap:12px; padding:9px 0}
.setrow + .setrow{border-top:1px solid var(--line)}
.setrow label{flex:1; min-width:0}
.sett{display:block; font-size:15px}
.setd{display:block; font-size:12.5px; color:var(--sub); margin-top:2px}
.sw{
  appearance:none; -webkit-appearance:none; flex:0 0 auto;
  width:48px; height:29px; margin:0; border-radius:15px;
  background:var(--line); position:relative; transition:background .18s;
}
.sw::after{
  content:''; position:absolute; top:3px; left:3px; width:23px; height:23px;
  border-radius:50%; background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.25);
  transition:transform .18s;
}
.sw:checked{background:var(--accent)}
.sw:checked::after{transform:translateX(19px)}

/* ── 状态条 ── */
#status{display:none; margin:0 0 10px; padding:11px 13px; border-radius:12px;
  background:var(--card); border:1px solid var(--line); font-size:13.5px;
  white-space:pre-wrap}
#status.show{display:block}
#status.ok{border-color:var(--accent); color:var(--fg)}
#status.err{border-color:var(--danger); color:var(--danger)}
#status.busy{color:var(--sub)}
#bar2{height:3px; border-radius:2px; background:var(--line); overflow:hidden; margin-top:9px}
#bar2 i{display:block; height:100%; width:30%; background:var(--accent);
  animation:slide 1.1s linear infinite}
@keyframes slide{from{transform:translateX(-100%)}to{transform:translateX(340%)}}
#bar2.det i{width:0; animation:none; transition:width .25s}
#checkLine{font-size:12.5px; color:var(--sub); margin:8px 0 0}

/* ── 缩略图网格 ── */
#grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:7px;
      margin-top:10px}
.tile{
  position:relative; aspect-ratio:1/1; border-radius:11px; overflow:hidden;
  background:var(--line); border:2.5px solid transparent;
  display:flex; align-items:center; justify-content:center;
}
.tile.on{border-color:var(--accent)}
.tile img{width:100%; height:100%; object-fit:cover; display:block; background:var(--line)}
.tile .bad{font-size:11.5px; color:var(--sub); text-align:center; padding:6px; line-height:1.35}
.tile .no{
  position:absolute; left:5px; bottom:5px; font-size:11px; padding:0 5px;
  border-radius:6px; background:rgba(0,0,0,.55); color:#fff;
}
/* 体检出来"偏小"的图：默认不勾选，但一眼能看出为什么 */
.tile.mini::after{
  content:'小'; position:absolute; left:5px; top:5px;
  font-size:10.5px; line-height:1.5; padding:0 5px; border-radius:6px;
  background:rgba(201,138,0,.9); color:#fff;
}
.tile .ck{
  position:absolute; right:4px; top:4px; width:30px; height:30px;
  display:flex; align-items:center; justify-content:center;
}
.tile .ck i{
  width:21px; height:21px; border-radius:50%; border:2px solid #fff;
  background:rgba(0,0,0,.38); color:transparent; font-size:12px; font-style:normal;
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 0 0 1px rgba(0,0,0,.25);
}
.tile.on .ck i{background:var(--accent); color:#fff}

/* ── 底部操作条 ── */
#bar{
  position:fixed; left:0; right:0; bottom:0; z-index:6; display:flex; gap:8px;
  align-items:center; padding:9px 12px calc(9px + env(safe-area-inset-bottom));
  background:var(--card); border-top:1px solid var(--line);
}
#selInfo{font-size:13px; color:var(--sub); min-width:64px}
#bar .btn{padding:12px 14px}

/* ── 历史 ── */
.hrow{
  display:flex; align-items:stretch; gap:6px; background:var(--card);
  border:1px solid var(--line); border-radius:12px; margin-bottom:8px; overflow:hidden;
}
.hmain{flex:1; padding:12px 4px 12px 13px; min-width:0}
.hmain:active{background:var(--line)}
.ht{font-size:14.5px; font-weight:600; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap}
.hm{font-size:12.5px; color:var(--sub); margin-top:3px}
.hx{border:0; background:none; color:var(--sub); padding:0 13px; font-size:15px}
.empty{color:var(--sub); text-align:center; padding:28px 0; font-size:14px}

/* ── 看大图 ── */
#viewer{position:fixed; inset:0; z-index:20; background:#000; display:flex;
        flex-direction:column}
#vbar{display:flex; align-items:center; gap:6px; padding:calc(env(safe-area-inset-top) + 6px) 8px 6px;
      color:#fff}
#vbar .tb{color:#fff}
#vbar span{flex:1; text-align:center; font-size:13px; color:#b9b9c0}
#vbody{flex:1; overflow:auto; -webkit-overflow-scrolling:touch; display:flex;
      align-items:center; justify-content:center}
#vimg.fit{max-width:100%; max-height:100%; object-fit:contain}
#vimg.full{max-width:none; max-height:none}
/* 左右翻页按钮：手机上滑动为主，这是给"够不到/习惯点"的人留的 */
.vnav{
  position:absolute; top:50%; transform:translateY(-50%); z-index:2;
  width:40px; height:78px; padding:0; border:0; border-radius:12px;
  background:rgba(255,255,255,.14); color:#fff; font-size:26px; line-height:1;
  opacity:.6;
}
.vnav:active{opacity:1; background:rgba(255,255,255,.3)}
#vPrev{left:6px}
#vNext{right:6px}

/* ── 日志 ── */
details{margin-top:14px; border-top:1px solid var(--line); padding-top:10px}
summary{font-size:13px; color:var(--sub); cursor:pointer}
pre{font-size:11.5px; line-height:1.5; color:var(--sub); white-space:pre-wrap;
    max-height:230px; overflow:auto; margin:8px 0 0}
#toast{
  position:fixed; left:50%; bottom:96px; transform:translateX(-50%); z-index:30;
  background:rgba(0,0,0,.83); color:#fff; font-size:13.5px; padding:10px 16px;
  border-radius:22px; max-width:86%; text-align:center; white-space:pre-wrap;
}
</style>
</head>
<body>

<header id="topbar">
  <button class="tb" id="back" hidden>返回</button>
  <h1 id="topTitle">ImgSnag</h1>
  <button class="tb" id="goHistory" title="下载历史">历史</button>
</header>

<main>
  <!-- 首页 -->
  <section id="view-home">
    <div class="card">
      <p class="hint">
        <b>最简单的用法：</b>在微信里打开文章 → 右上角 <b>…</b> → 分享 → 选 <b>ImgSnag</b>，
        会自动开始抓图。<br>
        也可以把文章链接粘贴到下面，或在浏览器里复制正文后点「从剪贴板」。
      </p>
      <input id="url" type="url" inputmode="url" autocomplete="off" autocapitalize="off"
             spellcheck="false" placeholder="https://mp.weixin.qq.com/s/...">
      <div class="row mt">
        <button class="btn" id="doPaste">从剪贴板</button>
        <button class="btn primary" id="doParse">解析图片</button>
      </div>
      <div class="row mt">
        <button class="btn block" id="clear">清空输入框</button>
      </div>
    </div>

    <div class="card mt">
      <div class="setrow">
        <label for="setOriginal">
          <span class="sett">保存原图</span>
          <span class="setd">关掉则存压缩档，省流量</span>
        </label>
        <input type="checkbox" class="sw" id="setOriginal">
      </div>
      <div class="setrow">
        <label for="setSkipTiny">
          <span class="sett">自动跳过小图</span>
          <span class="setd">表情、二维码这类小图默认不勾选</span>
        </label>
        <input type="checkbox" class="sw" id="setSkipTiny">
      </div>
    </div>

    <p class="hint" style="margin:14px 2px 0" id="foot"></p>
  </section>

  <!-- 结果 -->
  <section id="view-result" hidden>
    <div id="status"></div>
    <div id="bar2" hidden><i></i></div>
    <p id="checkLine" hidden></p>
    <div id="grid"></div>
    <div id="gridEmpty" class="empty" hidden>还没有可用的图</div>
    <details>
      <summary>诊断日志（出问题时截这里）</summary>
      <pre id="log"></pre>
    </details>
  </section>

  <!-- 历史 -->
  <section id="view-history" hidden>
    <div id="hlist"></div>
    <details>
      <summary>诊断日志（出问题时截这里）</summary>
      <pre id="log2"></pre>
    </details>
  </section>
</main>

<div id="bar" hidden>
  <button class="btn" id="allBtn">全选</button>
  <span id="selInfo">已选 0 张</span>
  <button class="btn primary" id="saveBtn" disabled>保存到相册</button>
</div>

<div id="viewer" hidden>
  <div id="vbar">
    <button class="tb" id="vClose">关闭</button>
    <span id="vpos"></span>
    <button class="tb" id="vPick">勾选</button>
    <button class="tb" id="vSave">存这张</button>
  </div>
  <div id="vbody"><img id="vimg" class="fit" alt=""></div>
  <button class="vnav" id="vPrev" title="上一张">‹</button>
  <button class="vnav" id="vNext" title="下一张">›</button>
</div>

<div id="toast" hidden></div>

<script>
'use strict';
var $ = function(s){ return document.querySelector(s); };
var st = {phase:'idle'};
var view = 'home', sid = '', sel = new Set(), tiny = new Set();
var lastShare = '', lastPhase = '';
var vIndex = 0, vFit = true;
/* 用户有没有自己动过勾选。动过之后就不再"自动全选" ——
   否则体检每报出一张小图就把用户的选择重算一遍，
   表现是"我刚取消的勾选又自己回来了"。 */
var autoSel = true;
/* 用户动过开关之后，不再用轮询回来的设置覆盖界面上的开关状态：
   轮询可能比刚才那次提交晚一拍，会把开关拨回去，看着像"点了没用"。 */
var settingsTouched = false;

/* ── 与后端说话 ── */
function api(path, body){
  var opt;
  if (body !== undefined){
    opt = {method:'POST', headers:{'Content-Type':'application/json'},
           body:JSON.stringify(body)};
  }
  return fetch(path, opt).then(function(r){ return r.json(); })
    .catch(function(e){ return {error:String(e)}; });
}

function esc(s){
  return String(s === null || s === undefined ? '' : s)
    .replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
    });
}

var toastTimer = 0;
function toast(msg){
  var t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function(){ t.hidden = true; }, 2600);
}

/* ── 视图与返回栈 ── */
/* paintView = 只画；setView = 画 + 压一层返回栈。
   系统返回手势能不能用，全看这里有没有老老实实 push。 */
function paintView(v){
  view = v;
  $('#view-home').hidden = v !== 'home';
  $('#view-result').hidden = v !== 'result';
  $('#view-history').hidden = v !== 'history';
  $('#back').hidden = v === 'home';
  $('#goHistory').hidden = v !== 'home';
  $('#bar').hidden = v !== 'result';
  $('#topTitle').textContent = v === 'history' ? '下载历史'
    : (v === 'result' ? (st.title || '抓取结果') : 'ImgSnag');
}
function setView(v, opts){
  if (!(opts && opts.fromPop) && v !== view){
    history.pushState({view:v}, '', '#' + v);
  }
  paintView(v);
}
window.addEventListener('popstate', function(e){
  // 返回：先把最上层的弹层关掉，再退视图。
  // e.state 是"弹出之后"那一条 —— 也就是打开弹层之前的状态。
  closeViewer(true);
  var v = (e.state && e.state.view) || 'home';
  paintView(v);
  if (v === 'history') loadHistory();
});
// 栈底那一条换成我们自己的，免得后退退到 WebView 的初始条目上
history.replaceState({view:'home'}, '', '#home');

$('#back').onclick = function(){ history.back(); };
$('#goHistory').onclick = function(){ setView('history'); loadHistory(); };

/* ── 首页 ── */
function startParse(text, html){
  var src = (text === undefined || text === null) ? $('#url').value.trim() : text;
  if (!src && !html){ toast('先粘贴文章链接'); return Promise.resolve({ok:false}); }
  return api('/api/parse', {source:src, html:html || ''}).then(function(r){
    if (r && r.state) applyState(r.state);
    return r;
  });
}
$('#doParse').onclick = function(){ startParse(); };
$('#clear').onclick = function(){ $('#url').value = ''; $('#url').focus(); };
$('#url').onkeydown = function(e){
  if (e.key === 'Enter'){ e.preventDefault(); $('#url').blur(); startParse(); }
};

/* 剪贴板：Java 侧注入的只读桥。浏览器里直接开这个页面时桥不存在，
   这时给一句人话（而不是点了没反应）。 */
function readClipboard(){
  try{
    if (window.imgsnag && window.imgsnag.read){
      return JSON.parse(window.imgsnag.read());
    }
  }catch(e){ /* 桥坏了就当没有桥 */ }
  return null;
}
$('#doPaste').onclick = function(){
  var c = readClipboard();
  if (!c){ toast('这个按钮要在 App 里用；浏览器里请直接粘到上面'); return; }
  var text = (c.text || '').trim(), html = c.html || '';
  if (!text && !html){ toast('剪贴板里没有内容'); return; }
  // 短的才回填输入框；整页源码写进去只会让人一脸问号
  if (text.length && text.length <= 200) $('#url').value = text;
  startParse(text, html).then(function(r){
    if (r && r.ok === false) toast('现在不能解析：' + (r.state ? r.state.message : ''));
  });
};

/* ── 设置 ── */
function applySettings(s){
  st.settings = s || {};
  if (!settingsTouched){
    $('#setOriginal').checked = !!st.settings.original;
    $('#setSkipTiny').checked = !!st.settings.skip_tiny;
  }
}
function bindSwitch(id, key){
  $('#' + id).onchange = function(){
    var patch = {};
    patch[key] = this.checked;
    settingsTouched = true;
    var box = this;
    api('/api/settings', patch).then(function(r){
      if (r && r.settings) applySettings(r.settings);
      // 自动全选状态下的勾选要跟着新设置重算
      if (autoSel) paintBar();
    });
  };
}
bindSwitch('setOriginal', 'original');
bindSwitch('setSkipTiny', 'skip_tiny');

/* ── 结果页 ── */
$('#allBtn').onclick = function(){
  autoSel = false;
  if (sel.size >= st.count){ sel.clear(); }
  else { sel.clear(); for (var i = 1; i <= st.count; i++) sel.add(i); }
  paintTiles(); paintBar();
};
$('#saveBtn').onclick = function(){
  if (!sel.size) return;
  var idxs = Array.from(sel).sort(function(a, b){ return a - b; });
  api('/api/save', {idxs:idxs}).then(function(r){
    if (r.state) applyState(r.state);
    if (!r.ok) toast('现在不能保存：' + (r.state ? r.state.message : ''));
  });
};

function buildGrid(){
  var g = $('#grid');
  g.innerHTML = '';
  for (var i = 1; i <= st.count; i++){
    var t = document.createElement('div');
    t.className = 'tile';
    t.dataset.i = i;
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = '';
    img.src = '/img/' + st.sid + '/t/' + i;
    img.onerror = function(){
      var n = this.parentNode.dataset.i;
      this.parentNode.innerHTML =
        '<span class="bad">第 ' + n + ' 张<br>没下下来</span>';
    };
    var ck = document.createElement('div');
    ck.className = 'ck';
    ck.innerHTML = '<i>✓</i>';
    var no = document.createElement('span');
    no.className = 'no';
    no.textContent = i;
    t.appendChild(img); t.appendChild(ck); t.appendChild(no);
    t.onclick = function(){ openViewer(parseInt(this.dataset.i, 10)); };
    ck.onclick = function(e){
      e.stopPropagation();
      var n = parseInt(this.parentNode.dataset.i, 10);
      autoSel = false;
      if (sel.has(n)) sel.delete(n); else sel.add(n);
      paintTiles(); paintBar();
    };
    g.appendChild(t);
  }
  $('#gridEmpty').hidden = st.count > 0;
  paintTiles();
  paintBar();
}

function paintTiles(){
  var tiles = document.querySelectorAll('#grid .tile');
  for (var k = 0; k < tiles.length; k++){
    var n = parseInt(tiles[k].dataset.i, 10);
    tiles[k].classList.toggle('on', sel.has(n));
    tiles[k].classList.toggle('mini', tiny.has(n));
  }
}
function paintBar(){
  $('#selInfo').textContent = '已选 ' + sel.size + ' 张';
  $('#saveBtn').disabled = sel.size === 0 || st.phase === 'parsing'
    || st.phase === 'saving';
  $('#saveBtn').textContent = st.phase === 'saving' ? '保存中…'
    : ('保存到相册' + (st.phase === 'saved' ? '（再存）' : ''));
  $('#allBtn').textContent = (st.count && sel.size >= st.count) ? '全不选' : '全选';
}
function paintCheck(){
  var c = st.check || {};
  var el = $('#checkLine');
  if (c.running && c.total){
    el.hidden = false;
    el.textContent = '正在认小图 ' + c.done + '/' + c.total + '…';
  } else if (tiny.size){
    el.hidden = false;
    el.textContent = '已自动跳过 ' + tiny.size + ' 张小图，想存就把它们勾上';
  } else {
    el.hidden = true;
  }
}

/* ── 看大图 ── */
function openViewer(i){
  vIndex = i;
  $('#vimg').src = '/img/' + st.sid + '/f/' + i;
  $('#vimg').className = 'fit';
  vFit = true;
  $('#vpos').textContent = i + ' / ' + st.count;
  $('#vPick').textContent = sel.has(i) ? '取消勾选' : '勾选';
  // 只有一张时不给翻页按钮，免得点了没反应
  $('#vPrev').hidden = st.count <= 1;
  $('#vNext').hidden = st.count <= 1;
  $('#viewer').hidden = false;
  // 进大图 = 压一层。这样侧滑返回是"关掉大图"而不是"退回桌面"
  history.pushState({view:view, viewer:i}, '', '');
}
/* byPop=true 表示"已经在返回的路上了"，只负责收拾界面。
   不传则是用户主动关：交给 history.back() 走同一条返回路径 ——
   两条路合一，就不会出现"点关闭能关、侧滑返回关不掉"这种不一致。 */
function closeViewer(byPop){
  if ($('#viewer').hidden) return;
  if (!byPop){ history.back(); return; }
  $('#viewer').hidden = true;
  $('#vimg').src = '';
  $('#vimg').className = 'fit';
  vFit = true;
}
function step(d){
  var n = vIndex + d;
  if (n < 1){ toast('已经是第一张了'); return; }
  if (n > st.count){ toast('已经是最后一张了'); return; }
  openViewer(n);
}
$('#vClose').onclick = function(){ closeViewer(); };
$('#vPrev').onclick = function(){ step(-1); };
$('#vNext').onclick = function(){ step(1); };
$('#vimg').onclick = function(){
  if (swallowClick){ swallowClick = false; return; }
  vFit = !vFit;
  this.className = vFit ? 'fit' : 'full';
};
$('#vPick').onclick = function(){
  autoSel = false;
  if (sel.has(vIndex)) sel.delete(vIndex); else sel.add(vIndex);
  paintTiles(); paintBar();
  this.textContent = sel.has(vIndex) ? '取消勾选' : '勾选';
};
$('#vSave').onclick = function(){
  api('/api/save', {idxs:[vIndex]}).then(function(r){
    if (r.state) applyState(r.state);
    closeViewer();
    if (!r.ok) toast('现在不能保存');
    else toast('正在保存第 ' + vIndex + ' 张…');
  });
};

/* ── 左右滑动翻页 ──
   只在"没放大"时翻页：放大之后手指是在平移图片，翻页会让人抓狂。
   判定用"走得够远 + 以横向为主 + 不太慢"，短促的点按仍然算点击（切适应/原大小）。 */
var tStart = null, swallowClick = false;
function onDown(x, y){
  tStart = {x:x, y:y, t:Date.now()};
  swallowClick = false;
}
function onUp(x, y){
  var s = tStart;
  tStart = null;
  if (!s) return;
  var dx = x - s.x, dy = y - s.y, dt = Date.now() - s.t;
  if (dt > 800 || Math.abs(dx) < 45) return;
  if (Math.abs(dx) < Math.abs(dy) * 1.2) return;
  if (!vFit){ swallowClick = true; return; }
  swallowClick = true;
  step(dx < 0 ? 1 : -1);
}
$('#vbody').addEventListener('touchstart', function(e){
  var t = e.touches[0];
  if (t) onDown(t.clientX, t.clientY);
}, {passive:true});
$('#vbody').addEventListener('touchend', function(e){
  var t = e.changedTouches[0];
  if (t) onUp(t.clientX, t.clientY);
}, {passive:true});
$('#vbody').addEventListener('touchcancel', function(){ tStart = null; }, {passive:true});
$('#vbody').addEventListener('mousedown', function(e){ onDown(e.clientX, e.clientY); });
$('#vbody').addEventListener('mouseup', function(e){ onUp(e.clientX, e.clientY); });
document.addEventListener('keydown', function(e){
  if ($('#viewer').hidden) return;
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'Escape') closeViewer();
});

/* ── 历史 ── */
function loadHistory(){
  api('/api/history').then(function(r){
    var box = $('#hlist');
    box.innerHTML = '';
    if (!r.items || !r.items.length){
      box.innerHTML = '<p class="empty">还没有下载记录</p>';
      return;
    }
    r.items.forEach(function(it){
      var row = document.createElement('div');
      row.className = 'hrow';
      var main = document.createElement('div');
      main.className = 'hmain';
      var extra = it.status === 'done'
        ? (' · 已存 ' + it.success + ' 张') : (' · ' + esc(it.status_text));
      main.innerHTML = '<div class="ht">' + esc(it.title) + '</div>'
        + '<div class="hm">' + esc(it.time) + ' · 共 ' + it.total + ' 张'
        + extra + (it.has_cache ? ' · 可重开' : '') + '</div>';
      main.onclick = function(){ pick(it); };
      var del = document.createElement('button');
      del.className = 'hx';
      del.textContent = '✕';
      del.onclick = function(e){
        e.stopPropagation();
        if (!confirm('删除这条记录？（相册里的图片不受影响）')) return;
        api('/api/history/delete', {id:it.id}).then(loadHistory);
      };
      row.appendChild(main); row.appendChild(del);
      box.appendChild(row);
    });
  });
}

function pick(it){
  if (it.has_cache){
    api('/api/history/open', {id:it.id}).then(function(r){
      if (r.ok){
        setView('result');
        refresh();
      } else {
        toast(r.message || '打不开这次记录');
        if (r.reason === 'nocache' && r.source) reparse(r.source);
      }
    });
  } else if (it.url){
    reparse(it.url);
  } else {
    toast('这次的缓存已经清掉了');
  }
}
function reparse(url){
  if (!confirm('重新解析这篇文章？（会重新抓一遍图）')) return;
  startParse(url).then(function(r){
    if (r && r.ok === false) toast('解析没能开始：' + (r.state ? r.state.message : ''));
  });
}

/* ── 状态渲染 ── */
function applyState(s){
  st = s;
  var p = s.phase || 'idle';
  var changed = (p !== lastPhase);
  lastPhase = p;

  tiny = new Set(s.tiny || []);
  applySettings(s.settings || {});

  // 换了文章（含"从历史打开另一次"）：重开一次自动选择
  if (s.sid !== sid){
    sid = s.sid || sid;
    autoSel = true;
    sel = new Set();
  }

  if (changed && p !== 'idle') setView('result');
  if (changed && p === 'saved') toast(s.message || '已保存');

  var status = $('#status');
  status.className = 'show ' + (p === 'error' ? 'err'
    : (p === 'saved' ? 'ok' : (p === 'ready' ? '' : 'busy')));
  if (p !== 'idle'){
    status.textContent = s.message || '';
  }

  var bar = $('#bar2');
  if (p === 'parsing'){
    bar.hidden = false; bar.className = '';
  } else if (p === 'saving' && s.save && s.save.total){
    var pct = Math.round(s.save.done * 100 / s.save.total);
    bar.hidden = false; bar.className = 'det';
    bar.firstElementChild.style.width = pct + '%';
  } else {
    bar.hidden = true;
  }

  if (view === 'result'){
    if (s.sid && s.sid !== $('#grid').dataset.sid){
      $('#grid').dataset.sid = s.sid;
      $('#grid').dataset.count = s.count;
      buildGrid();
    } else if (s.count !== Number($('#grid').dataset.count || -1)){
      $('#grid').dataset.count = s.count;
      buildGrid();
    }
    // 自动选择：跟着"小图清单"走 —— 体检每认出一张，它就自己从勾选里掉出去。
    // 后端在过滤关掉时会给空表，所以这里不重复判一次开关
    // （同一件事两处判，迟早会不一致，而且不一致时两边都不报错）。
    if (autoSel && s.count
        && (p === 'ready' || p === 'saving' || p === 'saved')){
      var next = new Set();
      for (var i = 1; i <= s.count; i++){
        if (tiny.has(i)) continue;
        next.add(i);
      }
      var same = (next.size === sel.size);
      if (same){ next.forEach(function(v){ if (!sel.has(v)) same = false; }); }
      if (!same){ sel = next; paintTiles(); }
    }
    paintBar();
    paintCheck();
    $('#topTitle').textContent = s.title || '抓取结果';
  }

  var logs = (s.log || []).slice(-40).reverse().join('\n');
  $('#log').textContent = logs;
  $('#log2').textContent = logs;

  var foot = (s.label || '') + ' ' + (s.version ? 'v' + s.version : '')
    + '  ·  支持：' + (s.sites || '') + '  ·  相册：Pictures/' + (s.album || '');
  $('#foot').textContent = foot.trim();
}

function refresh(){
  return api('/api/state').then(applyState);
}

/* ── 轮询：兼顾"分享进来的内容" ── */
var busyTimer = 0;
function tick(){
  api('/api/poll').then(function(s){
    if (!s || s.error) return;
    applyState(s);

    // 别人把这个组件当成"轮询到就干一件事"的入口，这里只管分享
    if (s.share && s.share !== lastShare){
      var busy = (s.phase === 'parsing' || s.phase === 'saving');
      if (!busy){
        startParse(s.share).then(function(r){
          // 只有真的开跑了才记住，否则下一轮还能再来一次
          if (r && r.ok) lastShare = s.share;
        });
      }
    }

    var running = (s.phase === 'parsing' || s.phase === 'saving'
                   || (s.check && s.check.running));
    clearTimeout(busyTimer);
    busyTimer = setTimeout(tick, running ? 450 : 1200);
  });
}

paintView('home');
refresh();
tick();
</script>
</body>
</html>
'''
