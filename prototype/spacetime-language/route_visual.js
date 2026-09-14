/* Shared source-coordinate route rendering for the workbench and README visuals. */
(function(){
  "use strict";
  var ns="http://www.w3.org/2000/svg";
  function el(id){return document.getElementById(id);}
  function make(tag,attrs,text){
    var node=document.createElementNS(ns,tag);
    Object.keys(attrs||{}).forEach(function(k){node.setAttribute(k,attrs[k]);});
    if(text !== undefined)node.textContent=text;
    return node;
  }
  function bounds(points){
    var xs=points.map(function(p){return p.x;}),ys=points.map(function(p){return p.y;});
    return {minX:Math.min.apply(null,xs),maxX:Math.max.apply(null,xs),minY:Math.min.apply(null,ys),maxY:Math.max.apply(null,ys)};
  }
  function makeProjection(points,w,h,margin){
    var b=bounds(points),dx=Math.max(b.maxX-b.minX,100),dy=Math.max(b.maxY-b.minY,100);
    var scale=Math.min((w-margin*2)/dx,(h-margin*2)/dy);
    var cx=(b.minX+b.maxX)/2,cy=(b.minY+b.maxY)/2;
    return function(p){return {x:(p.x-cx)*scale+w/2,y:(p.y-cy)*scale+h/2};};
  }
  function pathData(ids,nodes,project){
    return ids.map(function(id,i){var p=project(nodes[id]);return (i?"L":"M")+p.x.toFixed(2)+","+p.y.toFixed(2);}).join("");
  }
  function textLabel(svg,p,label,w,h,occupied,preferLeft,feature){
    var fontSize=feature?16:12;
    var rowHeight=fontSize+17;
    var textWidth=Array.from(label).reduce(function(n,ch){return n+(/[\u3000-\u9fff]/.test(ch)?fontSize:fontSize*.6);},0)+20;
    var options=[
      {x:p.x+(preferLeft?-textWidth-14:14),y:p.y-15},
      {x:p.x+(preferLeft?14:-textWidth-14),y:p.y-15},
      {x:p.x-textWidth/2,y:p.y-43},
      {x:p.x-textWidth/2,y:p.y+19}
    ];
    var r;
    function hits(a,b){return a.x<b.x+b.w+4&&a.x+a.w+4>b.x&&a.y<b.y+b.h+4&&a.y+a.h+4>b.y;}
    for(var i=0;i<options.length;i++){
      var o=options[i];
      r={x:Math.max(9,Math.min(w-textWidth-9,o.x)),y:Math.max(10,Math.min(h-40,o.y)),w:textWidth,h:rowHeight};
      var marker={x:p.x-10,y:p.y-10,w:20,h:20};
      if(!occupied.some(function(q){return hits(r,q);})&&!hits(r,marker))break;
    }
    occupied.push(r);
    var g=make("g",{"class":"endpoint-label"});
    g.appendChild(make("rect",{x:r.x,y:r.y,width:r.w,height:r.h,rx:3}));
    g.appendChild(make("text",{x:r.x+10,y:r.y+fontSize+7},label));
    svg.appendChild(g);
  }
  function drawMap(hostId,c,r,phase,feature){
    var host=el(hostId);
    if(!host.getClientRects().length)return;
    var w=Math.round(host.getBoundingClientRect().width),h=Math.round(host.getBoundingClientRect().height);
    if(w<1)return;
    var nodes={};
    c.nodes.forEach(function(n){nodes[n.id]=n;});
    var points=r.path.map(function(id){return nodes[id];});
    var project=makeProjection(points,w,h,Math.min(feature?90:60,w*.17));
    var svg=make("svg",{viewBox:"0 0 "+w+" "+h,role:"img","aria-labelledby":hostId+"-title "+hostId+"-desc","data-source":c.source,"data-route-id":r.id,"data-distance-m":r.distance,"data-minutes":r.estimatedMinutes,"data-mode":r.mode});
    svg.appendChild(make("title",{id:hostId+"-title"},r.originLabel+"到"+r.destinationLabel+"，本地步行路网距离"+r.distance+"米"));
    svg.appendChild(make("desc",{id:hostId+"-desc"},r.modeLabel+"约"+r.estimatedMinutes+"分钟，"+r.depart+"出发，"+r.arrive+"预计抵达。"));
    var grid=make("g",{"class":"map-grid","aria-hidden":"true"});
    for(var gx=20;gx<w;gx+=40)grid.appendChild(make("line",{x1:gx,y1:0,x2:gx,y2:h}));
    for(var gy=20;gy<h;gy+=40)grid.appendChild(make("line",{x1:0,y1:gy,x2:w,y2:gy}));
    svg.appendChild(grid);
    var network=c.edges.map(function(edge){return pathData(edge,nodes,project);}).join("");
    svg.appendChild(make("path",{d:network,"class":"map-network","aria-hidden":"true"}));
    var d=pathData(r.path,nodes,project);
    svg.appendChild(make("path",{d:d,"class":"selected-underlay","aria-hidden":"true"}));
    svg.appendChild(make("path",{d:d,"class":"selected-route","data-path-nodes":r.path.join(","),"aria-hidden":"true"}));
    points.slice(1,-1).forEach(function(n){
      var p=project(n),shape=make("circle",{cx:p.x,cy:p.y,r:3,"class":"route-node"});
      shape.appendChild(make("title",{},n.name));
      svg.appendChild(shape);
    });
    var start=project(points[0]),end=project(points[points.length-1]);
    if(phase===0||phase===1||phase===2)svg.appendChild(make("circle",{cx:start.x,cy:start.y,r:13,"class":"start-halo"}));
    svg.appendChild(make("circle",{cx:start.x,cy:start.y,r:6,"class":phase===-1||phase===2||phase===3?"last-node":"start-node","data-position-state":phase===-1?"planned-origin":(phase===2||phase===3?"last-confirmed":"confirmed")}));
    svg.appendChild(make("rect",{x:end.x-6,y:end.y-6,width:12,height:12,rx:1,"class":"destination-node"}));
    if(phase===3){
      svg.appendChild(make("circle",{cx:end.x,cy:end.y,r:14,"class":"start-halo"}));
      svg.appendChild(make("circle",{cx:end.x,cy:end.y,r:6,"class":"start-node"}));
    }
    var occupied=[];
    textLabel(svg,start,r.originLabel,w,h,occupied,points[1].x>points[0].x,feature);
    textLabel(svg,end,r.destinationLabel,w,h,occupied,points[points.length-2].x>points[points.length-1].x,feature);
    svg.appendChild(make("text",{x:w-15,y:21,"text-anchor":"end","class":"direction-label"},"N ↑"));
    svg.appendChild(make("text",{x:12,y:h-12,"class":"direction-label"},c.code+" / CAMPUS ROUTE"));
    host.replaceChildren(svg);
  }
  function drawSignature(hostId,c,r){
    var host=el(hostId),byId={};
    c.nodes.forEach(function(n){byId[n.id]=n;});
    var points=r.path.map(function(id){return byId[id];});
    var project=makeProjection(points,160,70,9);
    var svg=make("svg",{viewBox:"0 0 160 70",role:"img","aria-label":r.originLabel+"至"+r.destinationLabel+"的路线签名"});
    svg.appendChild(make("path",{d:pathData(r.path,byId,project),fill:"none",stroke:"#2157a0","stroke-width":2,"stroke-linejoin":"round"}));
    var first=project(points[0]),last=project(points[points.length-1]);
    svg.appendChild(make("circle",{cx:first.x,cy:first.y,r:4,fill:"#fffefa",stroke:"#2157a0","stroke-width":2}));
    svg.appendChild(make("rect",{x:last.x-3.5,y:last.y-3.5,width:7,height:7,fill:"#2157a0"}));
    host.replaceChildren(svg);
  }
  window.CampusFlowRouteVisual={drawMap:drawMap,drawSignature:drawSignature};
})();
