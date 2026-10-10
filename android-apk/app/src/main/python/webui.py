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
  · 底部常驻条 = 全选 / 已选张数 / 保存
  · 分享进来会**自动解析**，抓完停在结果页让你确认再入库（不直接写相册）

────────────────────────────────────────────────────────────────────────
返回键：页面自己数楼层，报给 Java
────────────────────────────────────────────────────────────────────────
系统的侧边滑动返回最终落到 Activity 的返回回调，那边只做一件事：
**听网页说现在在第几层**。所以这里的 `history.pushState` 就是返回层级本身 ——
页面 push 了几层，返回手势就能退几层（关大图 → 回首页/历史页），
退到最外层再划一次才退出 App。

⚠ 为什么是"网页报数"而不是让 Java 用 `WebView.canGoBack()`：
那个值取决于 WebView 内部怎么记 pushState 产生的历史条目，实机行为不可预期。
第一版就是因此"改了等于没改"，侧滑依旧直接退回桌面。`depth` 这个数由网页
自己维护，Java 只照它办事，而且**这一层在本机就能测**（`reportDepth` 调了桥）。

**两条规定**，值得写在最显眼的地方：

  · 凡是用户能"进去"的地方，都要 push 一层 —— 否则从那里侧滑就直接退回桌面。
    手机上的第一直觉操作变成"退出应用"，是最容易被骂的那种 bug。
  · 翻页**不算"进去"**，只 `replaceState` —— 那只是同一个层级里换了内容。
    早期版本翻 5 张就压 5 层，退出去得划 5 次，返回手势反而变成了惩罚。

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
/* 设置行右侧的小动作按钮。用 .btn 的常态尺寸会把整行撑得很高，
   也容易把左边的说明挤成两行。 */
.setrow .btn{flex:0 0 auto; min-height:0; padding:9px 14px; font-size:14px}
/* 真查出有新版本时才把标题点亮 —— 平时它就是个安静的入口 */
#updTitle.on{color:var(--accent)}
/* 更新卡片里的"去网页下载"兜底链接（应用内下载走不通时出现）。
   做成一行小字而不是第二个按钮：设置行右侧只放得下一个按钮。 */
.link{display:inline-block; margin-top:3px; font-size:12.5px;
  color:var(--accent); text-decoration:underline}

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
#creditLine{font-size:12.5px; color:var(--sub); margin:10px 0 0;
  line-height:1.5; word-break:break-word}

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
#selInfo{font-size:12.5px; color:var(--sub); flex:1; min-width:0}
#bar .btn{padding:13px 12px; font-size:14.5px}
/* 「复制」挤在保存按钮左边。窄屏（360dp）这一行要塞四个元素，
   所以它比另外两个按钮再收一档宽度，并把"已选 N 张"改成弹性的（flex:1）。 */
#copyBtn{padding:13px 10px; font-size:14px}

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
/* 翻页动画作用在**这一层**，不直接作用在 #vimg 上：
   图片自己还要受"适应 / 原大小"控制，两个 transform 叠在同一个元素上会
   互相覆盖 —— 放大状态下翻页会先跳一下。分开就没有这个问题。 */
#vstage{width:100%; height:100%; display:flex; align-items:center;
        justify-content:center; will-change:transform,opacity}
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

    <div class="card mt">
      <div class="setrow">
        <label>
          <span class="sett" id="updTitle">版本更新</span>
          <span class="setd" id="updDesc">看看 GitHub 上有没有更新的安装包</span>
          <!-- 兜底入口：应用内下载失败（手机网络连不上 GitHub 的下载地址）时
               还有这条路可走。默认藏着，需要时才出现。 -->
          <span class="link" id="updWeb" hidden>去网页下载</span>
        </label>
        <button class="btn" id="updBtn">检查</button>
      </div>
    </div>

    <p class="hint" style="margin:14px 2px 0" id="foot"></p>
  </section>

  <!-- 结果 -->
  <section id="view-result" hidden>
    <div id="status"></div>
    <p id="creditLine" hidden></p>
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
  <button class="btn" id="copyBtn" disabled>复制</button>
  <button class="btn primary" id="saveBtn" disabled>保存</button>
</div>

<div id="viewer" hidden>
  <div id="vbar">
    <button class="tb" id="vClose">关闭</button>
    <span id="vpos"></span>
    <button class="tb" id="vPick">勾选</button>
    <button class="tb" id="vSave">存这张</button>
  </div>
  <div id="vbody"><div id="vstage"><img id="vimg" class="fit" alt=""></div></div>
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
/* 网页在第几层。0 = 最外层（再退一次就该退出 App）。
   Java 的返回回调只认这个数 —— 为什么不让它自己用 WebView.canGoBack()：
   那个值取决于 WebView 内部怎么记 pushState 产生的历史条目，实机行为
   不可预期（第一版就是因此"改了等于没改"）。这里由网页自己数楼层，
   数完同步过去，语义就是"还能退几层"。 */
var depth = 0;
/* 系统开了"减少动效"就别硬播动画 */
var reduced = false;
try{ reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; }catch(e){}
/* 最近一次更新检查的结果（后端返回什么就是什么） */
var update = {};
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
    depth++; reportDepth();
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
  // 退了一层就少一层。用 max 兜底：有的浏览器首次加载也会派发一次 popstate，
  // 那一下不该把层级记成负数（负数会让 Java 那边永远判定"还有层可退"）。
  depth = Math.max(0, depth - 1);
  reportDepth();
});
// 栈底那一条换成我们自己的，免得后退退到 WebView 的初始条目上
history.replaceState({view:'home'}, '', '#home');
reportDepth();

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

/* ── 与 Java 的桥 ──
   七个方法（read / copy / depth / open / installedAt / canInstall / install）
   一律写成"桥不在就当没有"：在电脑浏览器里直接开这个页面调试时 window.imgsnag
   不存在，这里若抛异常，后面的脚本整段都不会执行 —— 表现是"页面半死"，最难查。 */

/* 把"现在在第几层"同步给 Java。它的返回回调照这个数决定：
   还有层就退网页，退到底了才退出 App。 */
function reportDepth(){
  try{
    if (window.imgsnag && window.imgsnag.depth) window.imgsnag.depth(depth);
  }catch(e){ /* 没桥就算了 */ }
}

/* 打开外部链接（更新说明、下载页）。交给系统浏览器 ——
   别在这个"壳"里迷路：壳里没有地址栏，退不出去就只能杀进程。 */
function openExternal(url){
  if (!url) return false;
  try{
    if (window.imgsnag && window.imgsnag.open){ window.imgsnag.open(url); return true; }
  }catch(e){ /* 落到下面的兜底 */ }
  try{ window.open(url, '_blank'); return true; }catch(e){}
  return false;
}

/* 本机这个 App 是什么时候装的（毫秒时间戳）。0 = 问不到。
   更新检查拿它做**兜底**判据（主判据是版本号）：两边版本号相同但重新构建过时，
   "构建时间比安装时间新"就是唯一能发现更新的线索。 */
function installedAt(){
  try{
    if (window.imgsnag && window.imgsnag.installedAt){
      // ⚠ 不能写 `| 0`：毫秒时间戳远超 32 位整数范围，`| 0` 会把它截成负数，
      // 于是"远端比本机新"永远判不出来，而且一句报错都没有。
      return Number(window.imgsnag.installedAt()) || 0;
    }
  }catch(e){ /* 没桥 */ }
  return 0;
}

/* ── 应用内更新：装更新包 ──
   下载在本机服务里做（能进度、能校验），**最后一步必须靠 Java** ——
   把 APK 交给系统安装器是安卓平台的活，网页做不到。
   桥不在（电脑浏览器里调试）时返回空串，界面据此只给"去网页下载"。 */

/* 现在能不能直接装包（用户给过「允许安装未知应用」没有）。
   '' = 问不到（没桥），'1' = 可以，'0' = 还得先去系统里授权。 */
function canInstallNative(){
  try{
    if (window.imgsnag && window.imgsnag.canInstall){
      return String(window.imgsnag.canInstall());
    }
  }catch(e){ /* 没桥 */ }
  return '';
}

/* 把下载好的安装包交给系统安装器。返回 Java 侧的原话：
   ok / settings（已把用户送去授权页）/ bad / fail。 */
function installApk(path){
  if (!path) return 'bad';
  try{
    if (window.imgsnag && window.imgsnag.install){
      return String(window.imgsnag.install(path));
    }
  }catch(e){ /* 桥坏了按失败处理 */ }
  return '';
}

/* 把一段文字写进系统剪贴板（「复制」用）。返回 true = 确实写进去了。
   为什么不试 `navigator.clipboard`：它要求安全上下文 + 用户授权，而这个页面是
   `http://127.0.0.1`，实机行为不可预期 —— 走原生桥最稳，也顺手避开权限弹窗。 */
function copyText(text){
  try{
    if (window.imgsnag && window.imgsnag.copy){
      return window.imgsnag.copy(text) === 'ok';
    }
  }catch(e){ /* 桥坏了按失败处理 */ }
  return false;
}

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
/* 复制：复制的是这篇文章的**名字**，不含日期编号（后端给的 title 本来就是原文，
   从历史打开时那边也已剥掉前缀）。没桥时给一句人话，而不是点了没反应。 */
$('#copyBtn').onclick = function(){
  var t = (st.title || '').trim();
  if (!t){ toast('还没有标题可复制'); return; }
  // 长标题塞进 toast 会把提示条撑爆，回显时截一下（复制进剪贴板的仍是全文）
  var shown = t.length > 14 ? t.slice(0, 14) + '…' : t;
  toast(copyText(t) ? ('已复制：' + shown) : '复制失败 —— 这个按钮要在 App 里用');
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
    : (st.phase === 'saved' ? '再存' : '保存');
  // 复制的是**这次的文章标题**（后端给的 st.title 本来就是原文，不含日期编号；
  // 从历史打开时那边同样已剥掉前缀）。没标题、或还在解析中就没什么可复制的。
  $('#copyBtn').disabled = !st.title || st.phase === 'parsing';
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

/* 把"当前这张"画出来。与"怎么进场"分开写：
   进场有"从网格点开"和"翻页"两条路，画的内容却是同一套 ——
   混在一起写，两条路就会各漏一点（比如翻页时忘了更新右上角那个勾选状态）。 */
function paintViewer(){
  $('#vimg').src = '/img/' + st.sid + '/f/' + vIndex;
  $('#vimg').className = 'fit';
  vFit = true;
  $('#vpos').textContent = vIndex + ' / ' + st.count;
  $('#vPick').textContent = sel.has(vIndex) ? '取消勾选' : '勾选';
  // 只有一张时不给翻页按钮，免得点了没反应
  $('#vPrev').hidden = st.count <= 1;
  $('#vNext').hidden = st.count <= 1;
}

/* 翻页的过渡：内容先顺着方向滑出去，换图，再从另一侧滑进来。
   dir=1 往左走（看下一张），-1 往右走。
   动的是 #vstage 而不是 #vimg —— 图片自己还要受"适应/原大小"控制，
   两个 transform 放在同一个元素上会互相覆盖。 */
function slideStage(dir, swap){
  var stage = $('#vstage');
  if (!dir || reduced){ swap(); return; }
  stage.style.transition = 'transform .15s ease-out, opacity .15s ease-out';
  stage.style.transform = 'translateX(' + (-26 * dir) + '%)';
  stage.style.opacity = '.35';
  setTimeout(function(){
    swap();
    stage.style.transition = 'none';
    stage.style.transform = 'translateX(' + (26 * dir) + '%)';
    stage.style.opacity = '.35';
    requestAnimationFrame(function(){
      stage.style.transition = 'transform .18s ease-out, opacity .18s ease-out';
      stage.style.transform = 'translateX(0)';
      stage.style.opacity = '1';
      // 收干净内联样式，免得之后跟"放大后拖动"打架
      setTimeout(function(){ stage.style.transition = ''; }, 220);
    });
  }, 155);
}

/* opts.dir：翻页方向（0/不传 = 不播动画） */
function openViewer(i, opts){
  var dir = (opts && opts.dir) || 0;
  if ($('#viewer').hidden){
    // 从网格点开：先露面再淡入，比"啪一下出现"顺眼
    vIndex = i;
    paintViewer();
    $('#viewer').hidden = false;
    if (!reduced){
      var stage = $('#vstage');
      stage.style.transition = 'none';
      stage.style.transform = 'translateX(0)';
      stage.style.opacity = '0';
      requestAnimationFrame(function(){
        stage.style.transition = 'opacity .18s ease-out';
        stage.style.opacity = '1';
        setTimeout(function(){ stage.style.transition = ''; }, 220);
      });
    }
    // 进大图 = 压一层。这样侧滑返回是"关掉大图"而不是"退回桌面"
    history.pushState({view:view, viewer:i}, '', '');
    depth++; reportDepth();
    return;
  }
  // 翻页：**只替换当前这一层，不压新的**。
  // 早先这里跟"进场"共用同一个 pushState，于是翻过 5 张就得划 5 次才退得出去 ——
  // 返回手势一下变成惩罚。翻页是"在同一层里换内容"，不是"又进了一层"。
  slideStage(dir, function(){ vIndex = i; paintViewer(); });
  history.replaceState({view:view, viewer:i}, '', '');
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
  // 动画留下的内联样式要清掉，否则下次打开是"歪着"进来的
  var stage = $('#vstage');
  stage.style.transition = 'none';
  stage.style.transform = '';
  stage.style.opacity = '';
}
function step(d){
  var n = vIndex + d;
  if (n < 1){ toast('已经是第一张了'); return; }
  if (n > st.count){ toast('已经是最后一张了'); return; }
  openViewer(n, {dir:d});
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

  // 来源与版权提示：解析出来之后一直显示，换一篇文章就跟着换。
  // 取不到站点信息时后端给空串，这里就把整行藏起来。
  var credit = $('#creditLine');
  if (p !== 'idle' && s.credit){
    credit.textContent = s.credit;
    credit.hidden = false;
  } else {
    credit.hidden = true;
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

  // 应用内更新的进度跟着这条轮询回来。下载中每轮都重画（进度要动），
  // 其余只在状态切换时画一次 —— 否则每 1.2 秒把按钮文案擦一遍，
  // 用户正在点的时候按钮会闪。
  if (s.dl){
    updDl = s.dl;
    if (s.dl.phase !== lastDlPhase || s.dl.phase === 'downloading'){
      lastDlPhase = s.dl.phase;
      paintUpdate();
    }
  }
}

function refresh(){
  return api('/api/state').then(applyState);
}

/* ── 版本更新 ── */
/* 判定全在后端（它有网络、也能读盘做节流），这里只把结论说成人话。
   信息态四种：查不到 / 有新版本 / 查到了但不知道本机装的是哪个 / 已是最新。
   动作态三种（应用内更新）：下载中 / 已下载可安装 / 下载失败。
   动作态优先 —— 用户已经在动手了，别让信息态把它盖掉。 */
var updUrl = '';
var updAction = 'check';
/* 最近一次下载状态（跟着轮询更新，见 applyState）。单独放一个变量：
   `update` 每次检查都被整个替换掉，挂在它上面会被抹掉。 */
var updDl = {};
var lastDlPhase = '';
var updChecking = false;

function fmtBytes(n){
  n = Number(n) || 0;
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

function paintUpdate(){
  if (updChecking) return;        // 正在检查：别把"检查中"的临时状态擦掉
  var title = $('#updTitle'), desc = $('#updDesc'), btn = $('#updBtn'), web = $('#updWeb');
  updUrl = update.page_url || '';
  updAction = 'check';
  title.className = '';
  btn.disabled = false;
  web.hidden = true;

  var dl = updDl || {};

  /* ① 下载中 */
  if (dl.phase === 'downloading'){
    var pct = dl.total ? Math.round(dl.done * 100 / dl.total) : 0;
    title.textContent = '正在下载更新';
    title.className = 'on';
    desc.textContent = fmtBytes(dl.done)
      + (dl.total ? ' / ' + fmtBytes(dl.total) + '（' + pct + '%）' : '');
    btn.textContent = '取消';
    updAction = 'cancel';
    return;
  }

  /* ② 已下载：可以装了 */
  if (dl.phase === 'done'){
    if (!canInstallNative()){
      // 桥不在（电脑浏览器里调试）：装不了，只能去网页
      title.textContent = '更新已下载';
      desc.textContent = '要在 App 里才能装，这里只能去网页下载';
      btn.disabled = true;
      web.hidden = false;
      return;
    }
    title.textContent = '更新已下载';
    title.className = 'on';
    desc.textContent = 'v' + (update.remote_version || dl.version || '')
      + ' 已就绪，点「安装」交给系统（首次会先要一次安装权限）';
    btn.textContent = '安装';
    updAction = 'install';
    return;
  }

  /* ③ 下载失败：这条路走不通了，**必须**把网页那条路摆出来 */
  if (dl.phase === 'error'){
    title.textContent = '下载失败';
    desc.textContent = dl.error || '没能下载下来';
    btn.textContent = '重试';
    updAction = 'update';
    web.hidden = false;
    return;
  }

  /* ④ 还没开始下载：原来的信息态 */
  if (!update || update.current === undefined){
    title.textContent = '版本更新';
    desc.textContent = (update && update.error) ? update.error : '还没检查过';
    btn.textContent = '检查';
    return;
  }
  if (!update.ok){
    title.textContent = '版本更新';
    desc.textContent = update.error || '查不到 GitHub 上的发布';
    btn.textContent = '重试';
    return;
  }
  if (update.has_update){
    var hasBridge = (canInstallNative() !== '');
    title.textContent = '有新版本';
    title.className = 'on';
    desc.textContent = 'v' + (update.remote_version || '?')
      + (hasBridge ? '，可在这儿直接下好并安装' : '（' + (update.remote_time || '') + '构建）');
    if (hasBridge){
      btn.textContent = '更新';      // 应用内：下载 → 安装，一条龙
      updAction = 'update';
    } else {
      btn.textContent = '去下载';    // 电脑浏览器里只能跳网页
      updAction = 'web';
    }
    return;
  }
  if (update.unknown){
    // 拿不到本机安装信息（在电脑浏览器里调试时就是这样）：
    // 只说远端有什么，**不下"有没有新版"的结论** —— 猜一个比不给更糟
    title.textContent = '版本更新';
    desc.textContent = 'GitHub 上最新：v' + (update.remote_version || '?')
      + '（' + (update.remote_time || '') + '）';
    btn.textContent = '去看看';
    updAction = 'web';
    return;
  }
  title.textContent = '已是最新';
  desc.textContent = 'v' + (update.current || '')
    + (update.remote_time ? ' · 与 GitHub 上那个（' + update.remote_time + '）一致' : '');
  btn.textContent = '再检查';
}

function checkUpdate(force){
  var btn = $('#updBtn');
  updChecking = true;
  btn.disabled = true;
  if (force) $('#updDesc').textContent = '正在检查…';
  // ⚠ 判"接口通了没有"不能用 `r.error`：正常响应里也有个 error 字段
  //（那是"检查更新失败"的原因），跟 api() 自己出错时的形状撞上了。
  // 用 current —— 后端每次都会给这个字段。
  var q = '/api/update?at=' + installedAt() + (force ? '&force=1' : '');
  return api(q).then(function(r){
    update = (r && r.current !== undefined) ? r : {error: '连不上本机服务'};
    updChecking = false;
    paintUpdate();
    if (force){
      toast(update.ok
        ? (update.has_update ? '有新版本 v' + update.remote_version : '已经是最新版')
        : ('检查失败：' + (update.error || '网络不通')));
    }
  });
}

/* 应用内更新：开始下载。后端下、这里只看进度（进度跟着轮询回来）。 */
function startDownload(){
  var btn = $('#updBtn');
  btn.disabled = true;
  $('#updDesc').textContent = '正在准备下载…';
  return api('/api/update/download', {at: installedAt()}).then(function(r){
    updDl = (r && r.phase) ? r : {phase: 'error', error: (r && r.error) || '起不来下载'};
    lastDlPhase = updDl.phase;
    paintUpdate();
    if (updDl.phase === 'error') toast(updDl.error || '下载失败');
  });
}

/* 把下载好的包装进系统。Java 说"settings"＝它已把用户送去授权页，
   授权回来会自动接着装（见 MainActivity.onResume）。 */
function doInstall(){
  var path = (updDl && updDl.path) || '';
  var r = installApk(path);
  if (r === 'ok') return;
  if (r === 'settings'){
    toast('请在系统里允许「安装未知应用」，回来会自动继续');
    return;
  }
  // 桥不在、或路径被拒：别让用户对着"点了没反应"发呆
  toast(r === 'bad' ? '安装包不见了，请重新下载' : '没能拉起安装器，可以试试去网页下载');
  $('#updWeb').hidden = false;
}

$('#updBtn').onclick = function(){
  if (updAction === 'cancel'){
    api('/api/update/cancel', {}).then(function(r){
      if (r && r.phase) updDl = r;
      paintUpdate();
      toast('已取消下载');
    });
    return;
  }
  if (updAction === 'install') return void doInstall();
  if (updAction === 'update') return void startDownload();
  if (updAction === 'web') return void openExternal(updUrl);
  checkUpdate(true);
};

/* 兜底入口：应用内下载走不通时去 Release 页自己下 */
$('#updWeb').onclick = function(){
  if (!openExternal(updUrl || (update && update.page_url))) toast('这个要在 App 里用');
};

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
paintUpdate();
refresh();
tick();
// 静默查一次更新。后端有 6 小时节流，所以并不是每次开 App 都会真的联网；
// 手动点「检查」会带 force，绕过节流。
checkUpdate(false);
</script>
</body>
</html>
'''
