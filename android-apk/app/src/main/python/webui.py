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
  · 底部常驻条 = 全选 / 已选张数 / 保存到相册
  · 分享进来会**自动解析**，抓完停在结果页让你确认再入库（不直接写相册）
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

/* ── 缩略图网格 ── */
#grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:7px}
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
        也可以把文章链接粘贴到下面。
      </p>
      <input id="url" type="url" inputmode="url" autocomplete="off" autocapitalize="off"
             spellcheck="false" placeholder="https://mp.weixin.qq.com/s/...">
      <div class="row mt">
        <button class="btn primary" id="doParse">解析图片</button>
        <button class="btn" id="clear">清空</button>
      </div>
    </div>
    <p class="hint" style="margin:14px 2px 0" id="foot"></p>
  </section>

  <!-- 结果 -->
  <section id="view-result" hidden>
    <div id="status"></div>
    <div id="bar2" hidden><i></i></div>
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
</div>

<div id="toast" hidden></div>

<script>
'use strict';
var $ = function(s){ return document.querySelector(s); };
var st = {phase:'idle'};
var view = 'home', sid = '', sel = new Set(), lastShare = '', lastPhase = '';
var vIndex = 0, vFit = true;

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

/* ── 顶栏 ── */
function setView(v){
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

$('#back').onclick = function(){ setView('home'); };
$('#goHistory').onclick = function(){ setView('history'); loadHistory(); };

/* ── 首页 ── */
function startParse(text){
  var src = text || $('#url').value.trim();
  if (!src){ toast('先粘贴文章链接'); return Promise.resolve({ok:false}); }
  return api('/api/parse', {source:src}).then(function(r){
    if (r.state) applyState(r.state);
    return r;
  });
}
$('#doParse').onclick = function(){ startParse(); };
$('#clear').onclick = function(){ $('#url').value = ''; $('#url').focus(); };
$('#url').onkeydown = function(e){
  if (e.key === 'Enter'){ e.preventDefault(); $('#url').blur(); startParse(); }
};

/* ── 结果页 ── */
$('#allBtn').onclick = function(){
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

/* ── 看大图 ── */
function openViewer(i){
  vIndex = i;
  $('#vimg').src = '/img/' + st.sid + '/f/' + i;
  $('#vimg').className = 'fit';
  vFit = true;
  $('#vpos').textContent = i + ' / ' + st.count;
  $('#vPick').textContent = sel.has(i) ? '取消勾选' : '勾选';
  $('#viewer').hidden = false;
}
$('#vClose').onclick = function(){ $('#viewer').hidden = true; $('#vimg').src = ''; };
$('#vimg').onclick = function(){
  vFit = !vFit;
  this.className = vFit ? 'fit' : 'full';
};
$('#vPick').onclick = function(){
  if (sel.has(vIndex)) sel.delete(vIndex); else sel.add(vIndex);
  paintTiles(); paintBar();
  this.textContent = sel.has(vIndex) ? '取消勾选' : '勾选';
};
$('#vSave').onclick = function(){
  api('/api/save', {idxs:[vIndex]}).then(function(r){
    if (r.state) applyState(r.state);
    $('#viewer').hidden = true;
    if (!r.ok) toast('现在不能保存');
    else toast('正在保存第 ' + vIndex + ' 张…');
  });
};

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

  if (changed && p !== 'idle') setView('result');

  if (changed){
    if (p === 'parsing' || (p === 'ready' && s.sid !== sid)){
      // 新会话：默认全选
      if (s.sid !== sid) sel.clear();
      sid = s.sid || sid;
    }
    if (p === 'saved') toast(s.message || '已保存');
  }

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
      buildGrid();
    } else if (s.count !== Number($('#grid').dataset.count || -1)){
      $('#grid').dataset.count = s.count;
      buildGrid();
    }
    // 新会话时把默认选中补上
    if (s.phase === 'ready' || s.phase === 'saving' || s.phase === 'saved'){
      if (!sel.size && s.count && !$('#grid').dataset.touched){
        for (var i = 1; i <= s.count; i++) sel.add(i);
        paintTiles();
      }
      paintBar();
    }
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

    var running = (s.phase === 'parsing' || s.phase === 'saving');
    clearTimeout(busyTimer);
    busyTimer = setTimeout(tick, running ? 450 : 1200);
  });
}

setView('home');
refresh();
tick();
</script>
</body>
</html>
'''
