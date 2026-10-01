(function(){
'use strict';
/* Read-aloud: text-to-speech for article pages using the browser's built-in
   Web Speech API (no backend, works offline where voices are installed).
   Background playback: tab-backgrounded playback keeps going; the Media
   Session API wires lock-screen/notification controls on Android. Whether
   audio continues with the screen fully locked depends on the browser and OS,
   this integration is the supported route to enable it where available. */
var btn=document.getElementById('listen-btn');
if(!btn||!('speechSynthesis' in window)){if(btn)btn.style.display='none';return;}
var synth=window.speechSynthesis;
var article=document.querySelector('.article');
if(!article){btn.style.display='none';return;}
var paras=article.querySelectorAll('p.body,p.lede');
if(!paras.length){btn.style.display='none';return;}

/* Split paragraphs into sentences and wrap them for highlighting. */
var sents=[];
var SENT_RE=/[^.!?\u0964\u0965]+[.!?\u0964\u0965]+["\u201d']?|\S(?:.*\S)?$/g;
paras.forEach(function(p){
  var txt=p.textContent;
  var parts=txt.match(SENT_RE)||[txt];
  p.textContent='';
  parts.forEach(function(part){
    var s=document.createElement('span');
    s.className='ra-sent-src';
    s.textContent=part.trim();
    p.appendChild(s);
    p.appendChild(document.createTextNode(' '));
    if(s.textContent)sents.push(s);
  });
});
if(!sents.length){btn.style.display='none';return;}

var idx=0,playing=false,rate=1,voice=null,voices=[];
var title=(document.querySelector('.article h1')||{}).textContent||'Varta & Samkara';

/* Player UI */
var bar=document.createElement('div');
bar.className='readaloud';
bar.setAttribute('role','region');
bar.setAttribute('aria-label','Audio player');
bar.innerHTML=
 '<div class="ra-inner">'+
 '<div class="ra-title"></div>'+
 '<div class="ra-controls">'+
 '<button class="ra-btn" data-act="prev" aria-label="Previous sentence">&#9664;&#9664;</button>'+
 '<button class="ra-btn primary" data-act="toggle" aria-label="Play or pause">&#9654;</button>'+
 '<button class="ra-btn" data-act="next" aria-label="Next sentence">&#9654;&#9654;</button>'+
 '<select class="ra-select" data-act="rate" aria-label="Playback speed">'+
 '<option value="0.75">0.75x</option><option value="1" selected>1x</option>'+
 '<option value="1.25">1.25x</option><option value="1.5">1.5x</option></select>'+
 '<select class="ra-select" data-act="voice" aria-label="Voice"></select>'+
 '<div class="ra-progress" aria-hidden="true"><i></i></div>'+
 '<span class="ra-count"></span>'+
 '<button class="ra-close" data-act="close" aria-label="Close player">&times;</button>'+
 '</div></div>';
document.body.appendChild(bar);
var titleEl=bar.querySelector('.ra-title');
var toggleBtn=bar.querySelector('[data-act="toggle"]');
var prog=bar.querySelector('.ra-progress i');
var count=bar.querySelector('.ra-count');
var rateSel=bar.querySelector('[data-act="rate"]');
var voiceSel=bar.querySelector('[data-act="voice"]');
titleEl.textContent='Listening: '+title;

function highlight(){
  sents.forEach(function(s){s.classList.remove('ra-sent');});
  if(sents[idx])sents[idx].classList.add('ra-sent');
}
function updateUI(){
  toggleBtn.innerHTML=playing?'&#10074;&#10074;':'&#9654;';
  toggleBtn.setAttribute('aria-label',playing?'Pause':'Play');
  var pct=sents.length?Math.round(idx/sents.length*100):0;
  prog.style.width=pct+'%';
  count.textContent=sents.length?((playing||idx>0)?(Math.min(idx+1,sents.length)+' / '+sents.length):''):'' ;
}
function scrollInto(){
  var el=sents[idx];
  if(!el)return;
  var r=el.getBoundingClientRect();
  if(r.top<70||r.bottom>window.innerHeight-140){
    el.scrollIntoView({block:'center',behavior:'smooth'});
  }
}
function speakCurrent(){
  if(idx>=sents.length){finish();return;}
  var u=new SpeechSynthesisUtterance(sents[idx].textContent);
  u.rate=rate;
  if(voice)u.voice=voice;
  u.onend=function(){
    if(!playing)return;
    idx++;
    highlight();updateUI();scrollInto();
    if(idx<sents.length)speakCurrent();else finish();
  };
  u.onerror=function(){if(playing){idx++;if(idx<sents.length){highlight();updateUI();speakCurrent();}else finish();}};
  synth.speak(u);
}
function play(){
  if(idx>=sents.length)idx=0;
  synth.cancel();
  playing=true;
  bar.classList.add('open');
  highlight();updateUI();scrollInto();
  speakCurrent();
  setMediaSession();
}
function pause(){
  playing=false;
  synth.cancel();
  updateUI();
}
function stop(){
  playing=false;
  synth.cancel();
  idx=0;
  highlight();updateUI();
}
function finish(){
  playing=false;
  idx=sents.length;
  highlight();updateUI();
}
function setMediaSession(){
  if(!('mediaSession' in navigator))return;
  try{
    navigator.mediaSession.metadata=new MediaMetadata({
      title:title,artist:'Varta & Samkara',album:'Varta & Samkara'
    });
    navigator.mediaSession.setActionHandler('play',play);
    navigator.mediaSession.setActionHandler('pause',pause);
    navigator.mediaSession.setActionHandler('stop',stop);
    navigator.mediaSession.setActionHandler('previoustrack',function(){
      idx=Math.max(0,idx-1);if(playing)play();else{highlight();updateUI();}
    });
    navigator.mediaSession.setActionHandler('nexttrack',function(){
      idx=Math.min(sents.length-1,idx+1);if(playing)play();else{highlight();updateUI();}
    });
  }catch(e){}
}
function pickVoice(){
  var saved=null;
  try{saved=localStorage.getItem('vs-voice');}catch(e){}
  if(saved){
    var found=voices.filter(function(v){return v.voiceURI===saved;})[0];
    if(found)return found;
  }
  var pref=voices.filter(function(v){return /^en[-_]IN/i.test(v.lang);})[0]
    ||voices.filter(function(v){return /^en/i.test(v.lang);})[0]
    ||voices[0];
  return pref||null;
}
function loadVoices(){
  voices=synth.getVoices();
  if(!voices.length)return;
  voice=pickVoice();
  voiceSel.innerHTML='';
  voices.forEach(function(v){
    var o=document.createElement('option');
    o.value=v.voiceURI;
    o.textContent=v.name+' ('+v.lang+')';
    if(voice&&v.voiceURI===voice.voiceURI)o.selected=true;
    voiceSel.appendChild(o);
  });
}
if(synth.onvoiceschanged!==undefined)synth.onvoiceschanged=loadVoices;
loadVoices();

bar.addEventListener('click',function(e){
  var act=e.target.closest('[data-act]');
  if(!act)return;
  var a=act.getAttribute('data-act');
  if(a==='toggle'){playing?pause():play();}
  else if(a==='prev'){idx=Math.max(0,idx-1);if(playing)play();else{highlight();updateUI();}}
  else if(a==='next'){idx=Math.min(sents.length-1,idx+1);if(playing)play();else{highlight();updateUI();}}
  else if(a==='close'){stop();bar.classList.remove('open');}
});
rateSel.addEventListener('change',function(){
  rate=parseFloat(rateSel.value)||1;
  if(playing){synth.cancel();speakCurrent();}
});
voiceSel.addEventListener('change',function(){
  var v=voices.filter(function(x){return x.voiceURI===voiceSel.value;})[0];
  if(v){voice=v;try{localStorage.setItem('vs-voice',v.voiceURI);}catch(e){}}
  if(playing){synth.cancel();speakCurrent();}
});
btn.addEventListener('click',function(){
  if(bar.classList.contains('open')&&(playing||idx>0)){playing?pause():play();}
  else{idx=0;play();}
});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&bar.classList.contains('open')){stop();bar.classList.remove('open');}
});
window.addEventListener('beforeunload',function(){synth.cancel();});
updateUI();
})();