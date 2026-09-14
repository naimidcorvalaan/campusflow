/* Presentation state reads one generated route record for every view. */
(function () {
  "use strict";
  var atlas=window.CAMPUSFLOW_ATLAS,visual=window.CampusFlowRouteVisual;
  var query=new URLSearchParams(location.search);
  var state={
    campus:atlas[query.get("campus")]?query.get("campus"):"beiyangyuan",
    view:["today","journey","textures"].includes(query.get("view"))?query.get("view"):"today",
    leg:query.get("leg")==="1"?1:0,
    phase:Math.max(0,Math.min(3,Number(query.get("phase"))||0))
  };
  var root=document.querySelector(".workspace");
  if(query.get("capture")==="1")document.body.classList.add("capture");
  function el(id){return document.getElementById(id);}
  function txt(id,value){el(id).textContent=value;}
  function campus(){return atlas[state.campus];}
  function route(index){return campus().routes[index===undefined?state.leg:index];}
  function drawMaps(){
    visual.drawMap("today-map",campus(),route(),state.leg===1?-1:0);
    visual.drawMap("journey-map",campus(),route(0),state.phase);
  }
  function renderPhases(){
    var r=route(0),labels=["仍在原地","准备出发","移动中","确认抵达"],times=[campus().schedule.reference,r.pack,r.depart,r.arrive];
    var controls=el("phase-controls");
    controls.replaceChildren();
    labels.forEach(function(label,i){
      var button=document.createElement("button");
      button.type="button";button.dataset.phase=i;button.setAttribute("aria-pressed",String(state.phase===i));
      var span=document.createElement("span");span.textContent=times[i];
      button.append(span,document.createTextNode(label));
      button.addEventListener("click",function(){state.phase=i;renderJourney();announce();});
      controls.appendChild(button);
    });
  }
  function renderJourney(){
    var r=route(0),p=state.phase,s=campus().schedule;
    var times=[s.reference,r.pack,r.depart,r.arrive];
    var titles=["在"+r.originLabel+"，继续手边的事","收拾东西，准备出发","正在前往"+r.destinationLabel,"抵达"+r.destinationLabel];
    var copy=[
      "写作业到 "+s.focusUntil+"，接着背单词。",
      "留出 "+r.transitionMinutes+" 分钟收拾，"+r.depart+" 出发。",
      r.modeLabel+"约 "+r.estimatedMinutes+" 分钟，"+r.arrive+" 预计抵达。",
      "接下来，吃饭。"
    ];
    txt("journey-route-label",r.originLabel+" → "+r.destinationLabel);
    txt("journey-mode",r.modeLabel+" · "+r.distance+" m");
    txt("journey-map-caption",campus().label+"校园路线");
    txt("phase-time",times[p]);txt("phase-title",titles[p]);txt("phase-description",copy[p]);
    txt("phase-kicker",p===2?"校园移动":"当前安排");
    txt("position-label",p===2?"上次确认":"当前位置");
    txt("position-evidence",p===3?r.destinationLabel:r.originLabel);
    txt("journey-destination",r.destinationLabel);
    txt("journey-arrival",r.arrive+(p===3?" 抵达":" 预计抵达")+" · "+r.modeLabel+"约 "+r.estimatedMinutes+" 分钟");
    root.querySelectorAll("[data-phase]").forEach(function(button){button.setAttribute("aria-pressed",String(Number(button.dataset.phase)===p));});
    visual.drawMap("journey-map",campus(),r,p);
  }
  function renderToday(){
    var c=campus(),s=c.schedule,first=route(0),second=route(1),r=route();
    txt("reference-time",s.reference);txt("now-reference","NOW / "+s.reference);
    txt("focus-minutes",s.focusMinutes);txt("focus-until",s.focusUntil);txt("next-interval",s.focusUntil+" — "+s.nextUntil);
    txt("current-place",first.originLabel);txt("strip-pack",first.pack);txt("strip-depart",first.depart);
    txt("strip-arrive",first.arrive);txt("strip-origin",first.originLabel);txt("strip-destination",first.destinationLabel);
    txt("class-place",second.destinationLabel);txt("class-time",s.classStarts);
    txt("class-travel",second.depart+" 出发 → "+second.arrive+" 到楼 · 留出 "+s.classPreparationMinutes+" 分钟课前准备");
    txt("route-origin",r.originLabel);txt("route-destination",r.destinationLabel);
    el("route-distance").replaceChildren(document.createTextNode(String(r.distance)+" "));
    var unit=document.createElement("small");unit.textContent="m";el("route-distance").appendChild(unit);
    txt("route-time",r.modeLabel+"约 "+r.estimatedMinutes+" 分钟");
    txt("route-depart",r.depart);txt("route-arrive",r.arrive);
    txt("today-map-caption",c.label+"校园路线");
    document.querySelector(".map-edition").textContent="ROUTE / 0"+(state.leg+1);
    document.querySelector(".route-heading h2").textContent=state.leg?"随后去上课":"下一段校园移动";
    root.querySelectorAll("[data-leg]").forEach(function(button){button.setAttribute("aria-pressed",String(Number(button.dataset.leg)===state.leg));});
    visual.drawMap("today-map",c,r,state.leg===1?-1:0);
    visual.drawSignature("route-signature",c,first);
  }
  function render(){
    var c=campus();
    root.dataset.campus=state.campus;
    ["today","journey","textures"].forEach(function(view){el(view+"-panel").hidden=view!==state.view;});
    document.querySelectorAll("[data-view]").forEach(function(button){button.setAttribute("aria-pressed",String(button.dataset.view===state.view));});
    document.querySelectorAll("[data-campus-label]").forEach(function(node){node.textContent=c.label;});
    txt("breadcrumb","CampusFlow / "+({today:"今天",journey:"时间线",textures:"路网纹理"}[state.view]));
    txt("workspace-context",{today:"今日工作台",journey:"校园移动",textures:"路网纹理"}[state.view]);
    el("campus").value=state.campus;
    el("rail-texture").src="assets/"+state.campus+"-topology.svg";
    document.querySelector(".imprint-code").textContent=c.code+" / CAMPUS NETWORK";
    el("ribbon-texture").src="assets/"+state.campus+"-ribbon.svg";
    txt("specimen-pack",route(0).pack);txt("specimen-depart",route(0).depart);txt("specimen-arrival",route(0).arrive);
    renderToday();renderPhases();renderJourney();
    visual.drawSignature("signature-sample",c,route(0));
  }
  function announce(){
    txt("announcer",campus().label+"校区，"+({today:"今日应用",journey:"移动过程",textures:"路网纹理"}[state.view])+
      (state.view==="journey"?"，"+["仍在原地","准备出发","移动中","确认抵达"][state.phase]:""));
  }
  document.querySelectorAll("[data-view]").forEach(function(button){
    button.addEventListener("click",function(){state.view=button.dataset.view;render();announce();});
  });
  document.querySelectorAll("[data-leg]").forEach(function(button){
    button.addEventListener("click",function(){
      state.leg=Number(button.dataset.leg);renderToday();
      txt("announcer",route().originLabel+"到"+route().destinationLabel+"，"+route().distance+"米，"+route().modeLabel+"约"+route().estimatedMinutes+"分钟");
    });
  });
  el("campus").addEventListener("change",function(){state.campus=this.value;state.phase=0;render();announce();});
  var pending;
  var observer=new ResizeObserver(function(){cancelAnimationFrame(pending);pending=requestAnimationFrame(drawMaps);});
  observer.observe(el("today-map"));observer.observe(el("journey-map"));
  render();
})();
