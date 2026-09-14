(function(){
  "use strict";
  var atlas=window.CAMPUSFLOW_ATLAS,visual=window.CampusFlowRouteVisual;
  var query=new URLSearchParams(location.search),selected=query.get("campus"),gallery=document.getElementById("route-gallery");
  if(query.get("capture")==="1")document.body.classList.add("capture");
  var records=[];
  ["beiyangyuan","weijinlu"].forEach(function(key){
    var campus=atlas[key],route=campus.routes[0];
    var article=document.getElementById("route-template").content.firstElementChild.cloneNode(true);
    article.dataset.campus=key;article.dataset.routeId=route.id;
    article.setAttribute("aria-label",campus.label+"："+route.originLabel+"到"+route.destinationLabel);
    var fields={campus:campus.label+"校区",code:campus.code+" / CAMPUS ROUTE",origin:route.originLabel,destination:route.destinationLabel,mode:route.modeLabel+"约",minutes:route.estimatedMinutes,distance:route.distance,pack:route.pack,depart:route.depart,arrive:route.arrive,"map-campus":campus.label+"校园路线","map-mode":route.modeLabel};
    article.querySelectorAll("[data-field]").forEach(function(node){node.textContent=fields[node.dataset.field];});
    var host=article.querySelector(".map-host");host.id="showcase-"+key+"-map";
    if(atlas[selected]&&selected!==key)article.hidden=true;
    gallery.appendChild(article);
    records.push({host:host,campus:campus,route:route});
  });
  function draw(){records.forEach(function(record){visual.drawMap(record.host.id,record.campus,record.route,0,true);});}
  var pending,observer=new ResizeObserver(function(){cancelAnimationFrame(pending);pending=requestAnimationFrame(draw);});
  records.forEach(function(record){observer.observe(record.host);});
  draw();
})();
