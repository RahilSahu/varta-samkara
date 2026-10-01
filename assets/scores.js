(function(){
'use strict';
var ESPN='https://site.api.espn.com/apis/site/v2/sports';
var IST=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',hour12:true});

/* WWE Premium Live Events, verified against multiple reports (sportsbrackets.net,
   khelnow.com, sacnilk.com, Sep-Oct 2026). Dates can shift; check wwe.com. */
var WWE_EVENTS=[
  {name:'Money in the Bank',date:'2026-10-10T22:00:00Z',venue:'Smoothie King Center, New Orleans, Louisiana'},
  {name:'Crown Jewel',date:'2026-11-07T18:00:00Z',venue:'Riyadh Season Stadium at KAFD, Riyadh, Saudi Arabia'},
  {name:'Survivor Series: WarGames',date:'2026-11-28T23:00:00Z',venue:'Daikin Park, Houston, Texas'}
];
/* Boxing has no free live-score feed. Next major bouts, verified from
   Reuters, DAZN, Bad Left Hook and British Boxing News (Oct 2026). */
var BOXING=[
  {bout:'Daniel Dubois vs Fabio Wardley 2',title:'WBO heavyweight title rematch',date:'17 Oct 2026',venue:'O2 Arena, London',note:'DAZN PPV'},
  {bout:'Canelo Alvarez vs Christian Mbilli',title:'WBC super middleweight title',date:'31 Oct 2026',venue:'Venue TBA',note:'Halloween night'},
  {bout:'Agit Kabayel vs Nelson Hysa',title:'WBC heavyweight title',date:'28 Nov 2026',venue:'Merkur Spiel-Arena, Dusseldorf',note:'DAZN'}
];

var LEAGUES={
  cricket:[{id:'8048',name:'IPL'},{id:'8044',name:'Big Bash League'},{id:'8046',name:'State League T20'},{id:'8043',name:'Sheffield Shield'}],
  football:[{id:'ind.1',name:'Indian Super League'},{id:'eng.1',name:'Premier League'},{id:'esp.1',name:'La Liga'},{id:'uefa.champions',name:'Champions League'}]
};
var TAB_IDS=['cricket','football','f1','ufc','motogp','wwe'];
var panel,bar,leagueSel,updatedEl;
var current='cricket',leaguePick={},timer=null,lastFetch=0,lastAnyLive=false,agoTimer=null;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function fmtIST(iso){try{return IST.format(new Date(iso));}catch(e){return '';}}
function stateOf(e){return (e.status&&e.status.type)||{};}

function badgeFor(st){
  if(st.state==='in')return '<span class="badge live"><span class="live-pulse"></span>Live</span>';
  if(st.state==='post')return '<span class="badge done">'+esc(st.description||'Final')+'</span>';
  return '<span class="badge upcoming">'+esc(st.description||'Scheduled')+'</span>';
}
function teamRow(c){
  var t=c.team||{},name=t.displayName||'TBD',abbr=t.abbreviation||'',
      score=(c.score==null||c.score==='')?'':c.score,logo=t.logo||'',
      init=esc((abbr||name).slice(0,2).toUpperCase());
  return '<div class="team-row"><span class="team-logo'+(logo?'':' noimg')+'">'+
    (logo?'<img src="'+esc(logo)+'" alt="" loading="lazy" onerror="this.parentNode.classList.add(\'noimg\');this.remove()">':'')+
    '<span class="team-init">'+init+'</span></span>'+
    '<span class="team-name">'+esc(name)+'</span>'+
    '<span class="team-score">'+esc(String(score))+'</span></div>';
}
function emptyNote(){return '<div class="empty-note"><strong>No matches right now.</strong>Check back soon, the board refreshes automatically.</div>';}
function errorNote(){return '<div class="error-note"><strong>Could not load scores.</strong>Check your connection and try again.<br><button class="retry-btn" id="scores-retry" type="button">Retry</button></div>';}

function eventCard(e,compName){
  var st=stateOf(e),comp=(e.competitions&&e.competitions[0])||{};
  var rows=((comp.competitors)||[]).map(teamRow).join('');
  var when=st.state==='pre'?'<div class="score-meta">'+esc(fmtIST(e.date))+' IST</div>':'';
  var venue=(comp.venue&&comp.venue.fullName)?'<div class="score-meta">'+esc(comp.venue.fullName)+'</div>':'';
  var result='';
  if(st.state==='post'){
    var w=(comp.competitors||[]).filter(function(c){return c.winner;})[0];
    if(w&&w.team)result='<div class="score-result">'+esc(w.team.displayName)+' won</div>';
  }
  return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
    esc(compName||e.shortName||e.name||'')+'</span>'+badgeFor(st)+'</div>'+rows+result+when+venue+'</article>';
}

function renderGeneric(d){
  var evs=d.events||[];
  if(!evs.length)return emptyNote();
  return evs.map(function(e){return eventCard(e,e.name);}).join('');
}
function renderF1(d){
  var evs=d.events||[];
  if(!evs.length)return emptyNote();
  return evs.map(function(e){
    var st=stateOf(e),cir=e.circuit||{},addr=cir.address||{};
    var place=esc(cir.fullName||'')+((addr.city||addr.country)?', '+esc([addr.city,addr.country].filter(Boolean).join(', ')):'');
    var sess=(e.competitions||[]).map(function(c){
      var t=c.type||{},cst=(c.status&&c.status.type)||{};
      return '<div class="session-row"><span class="sess">'+esc(t.abbreviation||'Session')+'</span><span>'+
        esc(fmtIST(c.date))+' IST</span>'+badgeFor(cst)+'</div>';
    }).join('');
    return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
      esc(e.name||'Grand Prix')+'</span>'+badgeFor(st)+'</div><div class="score-meta">'+place+'</div>'+sess+'</article>';
  }).join('');
}
function renderUFC(d){
  var evs=d.events||[];
  var out=evs.length?evs.map(function(e){
    var st=stateOf(e),comp=(e.competitions&&e.competitions[0])||{};
    var rows=(comp.competitors||[]).map(function(c){
      var a=c.athlete||{},rec=(c.records&&c.records[0]&&c.records[0].summary)||'',
          flag=(a.flag&&a.flag.href)||'',
          init=esc((a.displayName||'?').slice(0,2).toUpperCase());
      return '<div class="team-row"><span class="team-logo'+(flag?'':' noimg')+'">'+
        (flag?'<img src="'+esc(flag)+'" alt="" loading="lazy" onerror="this.parentNode.classList.add(\'noimg\');this.remove()">':'')+
        '<span class="team-init">'+init+'</span></span>'+
        '<span class="team-name">'+esc(a.fullName||'TBD')+(c.winner?' <span class="badge done">Winner</span>':'')+'</span>'+
        '<span class="team-score">'+esc(rec)+'</span></div>';
    }).join('');
    var when=st.state==='pre'?'<div class="score-meta">'+esc(fmtIST(e.date))+' IST</div>':'';
    var venue=(comp.venue&&comp.venue.fullName)?'<div class="score-meta">'+esc(comp.venue.fullName)+'</div>':'';
    return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
      esc(e.name||'UFC')+'</span>'+badgeFor(st)+'</div>'+rows+when+venue+'</article>';
  }).join(''):emptyNote();
  out+='<h3 class="scores-subhead">Boxing: next major bouts</h3>'+
    '<div class="honest-note">Boxing has no free live-score feed, so these are the next big fights as reported, not live results.</div>'+
    BOXING.map(function(b){
      return '<article class="score-card"><div class="score-head"><span class="comp-name">'+
        esc(b.title)+'</span><span class="badge upcoming">'+esc(b.date)+'</span></div>'+
        '<div class="score-result">'+esc(b.bout)+'</div>'+
        '<div class="score-meta">'+esc(b.venue)+' &middot; '+esc(b.note)+'</div></article>';
    }).join('');
  return out;
}
function renderMotoGP(d){
  var riders=d.riders||[];
  var head='<div class="honest-note">'+esc(d.note||'2026 MotoGP rider lineup.')+
    ' <span class="countdown">Updated '+(d.updated?esc(d.updated):'daily')+'.</span></div>';
  if(!riders.length)return head+emptyNote();
  return head+'<div class="rider-grid">'+riders.map(function(r){
    return '<div class="rider-card"><div class="rider-num">'+esc(String(r.number==null?'':r.number))+'</div><div>'+
      '<div class="rider-name">'+esc(r.name)+'</div><div class="rider-team">'+esc(r.team)+'</div>'+
      '<div class="rider-country">'+(r.flag?'<img src="'+esc(r.flag)+'" alt="" loading="lazy">':'')+esc(r.country)+'</div></div></div>';
  }).join('')+'</div>';
}
function daysUntil(iso){return Math.max(0,Math.ceil((new Date(iso).getTime()-Date.now())/864e5));}
function renderWWE(){
  var cards=WWE_EVENTS.map(function(w){
    var n=daysUntil(w.date);
    var when=n===0?'Today':(n===1?'Tomorrow':'In '+n+' days');
    return '<article class="score-card wwe-card"><div class="score-head"><span class="comp-name">Premium Live Event</span>'+
      '<span class="badge upcoming">'+esc(when)+'</span></div>'+
      '<div class="score-result">'+esc(w.name)+'</div>'+
      '<div class="score-meta">'+esc(fmtIST(w.date))+' IST &middot; '+esc(w.venue)+'</div></article>';
  }).join('');
  return '<div class="honest-note">WWE is scripted entertainment, so there are no live scores anywhere. '+
    'These are the next confirmed Premium Live Events; dates can shift, check <a href="https://www.wwe.com" target="_blank" rel="noopener">wwe.com</a>.</div>'+cards;
}

function endpointFor(tab){
  if(tab==='cricket'){var l=leaguePick.cricket||LEAGUES.cricket[0];return {url:ESPN+'/cricket/'+l.id+'/scoreboard',label:l.name};}
  if(tab==='football'){var f=leaguePick.football||LEAGUES.football[0];return {url:ESPN+'/soccer/'+f.id+'/scoreboard',label:f.name};}
  if(tab==='f1')return {url:ESPN+'/racing/f1/scoreboard',label:'Formula 1'};
  if(tab==='ufc')return {url:ESPN+'/mma/ufc/scoreboard',label:'UFC'};
  return null;
}

function paintUpdated(){
  if(!lastFetch){updatedEl.textContent='';return;}
  var s=Math.max(0,Math.round((Date.now()-lastFetch)/1000));
  updatedEl.textContent=s<10?'Updated just now':'Updated '+(s<60?s+' sec':Math.round(s/60)+' min')+' ago';
}
function scheduleNext(){
  clearTimeout(timer);
  timer=setTimeout(function(){
    if(document.hidden){scheduleNext();return;}
    load(current,true);
  },lastAnyLive?30000:60000);
}

function load(tab,silent){
  var ep=endpointFor(tab);
  if(!ep){ /* static tabs */
    panel.innerHTML=tab==='motogp'?'<div class="scores-loading">Loading MotoGP lineup...</div>':'';
    if(tab==='motogp'){
      fetch('assets/motogp.json').then(function(r){return r.json();}).then(function(d){
        panel.innerHTML=renderMotoGP(d);lastFetch=Date.now();paintUpdated();
      }).catch(function(){panel.innerHTML=errorNote();});
    }else if(tab==='wwe'){
      panel.innerHTML=renderWWE();lastFetch=Date.now();paintUpdated();
    }
    clearTimeout(timer);
    return;
  }
  if(!silent)panel.innerHTML='<div class="scores-loading">Loading '+esc(ep.label)+'...</div>';
  fetch(ep.url).then(function(r){
    if(!r.ok)throw new Error('http '+r.status);
    return r.json();
  }).then(function(d){
    var evs=d.events||[];
    lastAnyLive=evs.some(function(e){return stateOf(e).state==='in';});
    var html=tab==='f1'?renderF1(d):(tab==='ufc'?renderUFC(d):renderGeneric(d));
    panel.innerHTML=html;
    lastFetch=Date.now();paintUpdated();scheduleNext();
  }).catch(function(){
    panel.innerHTML=errorNote();clearTimeout(timer);
  });
}

function showLeagueBar(tab){
  var leagues=LEAGUES[tab];
  if(!leagues){bar.style.display='none';return;}
  bar.style.display='flex';
  leagueSel.innerHTML=leagues.map(function(l,i){
    var cur=leaguePick[tab]||leagues[0];
    return '<option value="'+esc(l.id)+'"'+(l.id===cur.id?' selected':'')+'>'+esc(l.name)+'</option>';
  }).join('');
}
function activate(tab,push){
  if(TAB_IDS.indexOf(tab)<0)tab='cricket';
  current=tab;lastAnyLive=false;clearTimeout(timer);
  document.querySelectorAll('.scores-tab').forEach(function(b){
    var on=b.getAttribute('data-tab')===tab;
    b.classList.toggle('active',on);b.setAttribute('aria-selected',on?'true':'false');
  });
  showLeagueBar(tab);load(tab,false);
  if(push!==false&&location.hash!=='#'+tab)location.hash=tab;
}

function init(){
  panel=document.getElementById('scores-panel');
  bar=document.getElementById('scores-bar');
  leagueSel=document.getElementById('league-select');
  updatedEl=document.getElementById('scores-updated');
  if(!panel)return;
  document.querySelectorAll('.scores-tab').forEach(function(b){
    b.addEventListener('click',function(){activate(b.getAttribute('data-tab'));});
  });
  leagueSel.addEventListener('change',function(){
    var leagues=LEAGUES[current]||[];
    var pick=leagues.filter(function(l){return l.id===leagueSel.value;})[0];
    if(pick){leaguePick[current]=pick;load(current,false);}
  });
  panel.addEventListener('click',function(e){
    if(e.target&&e.target.id==='scores-retry')load(current,false);
  });
  document.addEventListener('visibilitychange',function(){
    if(!document.hidden)load(current,true);
  });
  agoTimer=setInterval(paintUpdated,20000);
  window.addEventListener('hashchange',function(){
    var h=location.hash.replace('#','');
    if(h&&h!==current)activate(h,false);
  });
  var start=location.hash.replace('#','');
  activate(TAB_IDS.indexOf(start)>=0?start:'cricket',false);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
else init();
})();