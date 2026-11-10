import { e0509Model } from "./robot-e0509-model.js";
import { identity, multiply, transformPoint, frameAxis, armFrames, poseFrame, mountedGripperParts, gripperMount, parcelBoxGeometry } from "./robot-arm.js";

const dot=(a,b)=>a.reduce((sum,v,i)=>sum+v*b[i],0);
const sub=(a,b)=>a.map((v,i)=>v-b[i]),add=(a,b)=>a.map((v,i)=>v+b[i]);
const cross=([a,b,c],[x,y,z])=>[b*z-c*y,c*x-a*z,a*y-b*x];
const norm=a=>{const n=Math.hypot(...a);return a.map(v=>v/(n||1));};
function lookAt(eye,target) {
  const z=norm(sub(eye,target)),x=norm(cross([0,0,1],z)),y=cross(z,x),m=identity();
  [x,y,z].forEach((a,i)=>{a.forEach((v,j)=>m[j*4+i]=v);m[12+i]=-dot(a,eye);});return m;
}
function ortho(l,r,b,t) {
  const m=identity();m[0]=2/(r-l);m[5]=2/(t-b);m[10]=-2/5999;m[12]=-(r+l)/(r-l);m[13]=-(t+b)/(t-b);m[14]=-6001/5999;return m;
}
const decode=(value,Type)=>{const text=atob(value);return new Type(Uint8Array.from(text,c=>c.charCodeAt(0)).buffer);};
const faces=[[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]];
function svgNode(name,attrs,value) {
  const n=document.createElementNS("http://www.w3.org/2000/svg",name);
  Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(value!==undefined)n.textContent=value;return n;
}

// Read-only rendering. No robot API, joint command, or teaching writes here.
export function connectArmRenderer(canvas,overlay,graphics,signal,onOrbit) {
  let gl,program,u,a,parts=[],dynamic,ready=false,queued=false,last,vp,width,height,disposed=false;
  let view={name:"oblique",yaw:-50,pitch:24},drag;
  try {
    const saved=JSON.parse(localStorage.getItem("vision-e0509-view-v1"));
    if (Number.isFinite(saved?.yaw)&&Number.isFinite(saved?.pitch)&&["oblique","front","side","free"].includes(saved.name))
      view={name:saved.name,yaw:saved.yaw%360,pitch:Math.max(5,Math.min(78,saved.pitch))};
  } catch { /* View preferences are optional. */ }
  function remember() {try{localStorage.setItem("vision-e0509-view-v1",JSON.stringify(view));}catch{/* Optional. */}}
  function buffer(target,data) {const b=gl.createBuffer();gl.bindBuffer(target,b);gl.bufferData(target,data,gl.STATIC_DRAW);return b;}
  function mesh(vertices,normals,indices) {
    return {v:buffer(gl.ARRAY_BUFFER,vertices),n:buffer(gl.ARRAY_BUFFER,normals),f:indices?buffer(gl.ELEMENT_ARRAY_BUFFER,indices):null,count:indices?.length??vertices.length/3};
  }
  function shader(type,source) {
    const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){gl.deleteShader(s);throw Error("E0509 shader compilation failed");}return s;
  }
  function initialize() {
    try {
      gl=canvas.getContext("webgl",{antialias:true,alpha:true,preserveDrawingBuffer:true});
      if(!gl)throw Error("WebGL unavailable");
      program=gl.createProgram();
      for(const s of [shader(gl.VERTEX_SHADER,"attribute vec3 p;attribute vec3 n;uniform mat4 model;uniform mat4 vp;varying vec3 normal;void main(){normal=mat3(model)*n;gl_Position=vp*model*vec4(p,1.);}"),
        shader(gl.FRAGMENT_SHADER,"precision mediump float;varying vec3 normal;uniform vec4 color;uniform float unlit;void main(){vec3 N=normalize(normal);float key=max(0.,dot(N,normalize(vec3(.35,-.65,1.))));float fill=max(0.,dot(N,normalize(vec3(-.7,.4,.25))));vec3 lit=color.rgb*(.44+.5*key+.12*fill)+vec3(.11)*pow(key,20.);gl_FragColor=vec4(mix(lit,color.rgb,unlit),color.a);}")]){gl.attachShader(program,s);gl.deleteShader(s);}
      gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error("E0509 shader linking failed");gl.useProgram(program);
      u=Object.fromEntries(["model","vp","color","unlit"].map(n=>[n,gl.getUniformLocation(program,n)]));a={p:gl.getAttribLocation(program,"p"),n:gl.getAttribLocation(program,"n")};
      parts=e0509Model.parts.map(p=>{
        const v=Float32Array.from(decode(p.v,Int16Array),n=>n/10),n=Float32Array.from(decode(p.n,Int8Array),n=>n/127),f=decode(p.f,Uint16Array);
        const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];v.forEach((x,i)=>{lo[i%3]=Math.min(lo[i%3],x);hi[i%3]=Math.max(hi[i%3],x);});
        const bounds=[];for(const x of [lo[0],hi[0]])for(const y of [lo[1],hi[1]])for(const z of [lo[2],hi[2]])bounds.push([x,y,z]);
        return {...mesh(v,n,f),link:p.link,name:p.name,bounds};
      });
      dynamic=mesh(new Float32Array(6),new Float32Array(6),null);ready=true;graphics.hidden=true;
    } catch {ready=false;graphics.hidden=false;graphics.textContent="3D 가속을 사용할 수 없어 관절 선도로 표시합니다.";}
  }
  function draw(part,model,color,unlit=0,mode=gl.TRIANGLES) {
    gl.uniformMatrix4fv(u.model,false,Float32Array.from(model));gl.uniform4fv(u.color,color);gl.uniform1f(u.unlit,unlit);
    for(const [key,b]of [["p",part.v],["n",part.n]]){gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.enableVertexAttribArray(a[key]);gl.vertexAttribPointer(a[key],3,gl.FLOAT,false,0,0);}
    if(part.f){gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,part.f);gl.drawElements(mode,part.count,gl.UNSIGNED_SHORT,0);}else gl.drawArrays(mode,0,part.count);
  }
  function lines(points,color) {
    if(!points.length)return;
    gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.v);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(points.flat()),gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.n);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(points.length*3).fill(.001),gl.DYNAMIC_DRAW);
    dynamic.count=points.length;draw(dynamic,identity(),color,1,gl.LINES);
  }
  function plate(points,color) {
    const vertices=[points[0],points[1],points[2],points[0],points[2],points[3]];
    gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.v);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices.flat()),gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.n);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(18).fill(.001),gl.DYNAMIC_DRAW);
    dynamic.count=6;draw(dynamic,identity(),color,1);
  }
  function handPart(part,mount){
    const points=part.points.map(p=>transformPoint(mount,p));
    if(part.wire){
      const edges=part.faces.flatMap(face=>face.flatMap((index,i)=>[points[index],points[face[(i+1)%face.length]]]));
      lines(edges,[.65,.75,.76,.65]);return;
    }
    for(const face of part.faces){
      const ps=face.map(i=>points[i]),vertices=[];
      for(let i=1;i<ps.length-1;i++)vertices.push(ps[0],ps[i],ps[i+1]);
      const n=norm(cross(sub(ps[1],ps[0]),sub(ps[2],ps[0])));
      gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.v);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices.flat()),gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER,dynamic.n);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices.flatMap(()=>n)),gl.DYNAMIC_DRAW);
      dynamic.count=vertices.length;draw(dynamic,identity(),part.color);
    }
  }
  function cube(center,size,color,axes=null) {
    const p=[];for(const z of [-1,1])for(const y of [-1,1])for(const x of [-1,1]){
      const local=[x,y,z].map((v,i)=>v*size[i]/2);
      p.push(center.map((v,i)=>v+(axes?local.reduce((sum,n,j)=>sum+n*axes[j][i],0):local[i])));
    }
    faces.forEach((ids,i)=>plate(ids.map(n=>p[n]),color.map((v,j)=>j===3?v:v*(.78+i*.035))));
  }
  function screen(p) {const s=transformPoint(vp,p);return [(s[0]+1)*width/2,(1-s[1])*height/2];}
  function labels(frames,data) {
    overlay.setAttribute("viewBox","0 0 "+width+" "+height);const nodes=[],placed=[];
    function label(p,text,offset=[0,0],selected=false) {
      const [x,y]=screen(p),tw=Math.max(16,[...text].reduce((w,c)=>w+(c.charCodeAt(0)>255?11:7),0));let lx,ly,rect;
      for(const [dx,dy]of [offset,[offset[0],offset[1]-23],[offset[0],offset[1]+23],[-tw-18,offset[1]],[-tw-18,offset[1]-23],[offset[0]+28,offset[1]+23],[offset[0],offset[1]-46]]){
        lx=Math.max(8,Math.min(width-tw-8,x+dx));ly=Math.max(17,Math.min(height-32,y+dy));rect=[lx-3,ly-12,tw+6,18];
        if(!placed.some(([px,py,pw,ph])=>rect[0]<px+pw&&rect[0]+rect[2]>px&&rect[1]<py+ph&&rect[1]+rect[3]>py))break;
      }
      placed.push(rect);nodes.push(svgNode("line",{x1:x,y1:y,x2:lx-4,y2:ly-3,class:"arm-leader"}),svgNode("circle",{cx:x,cy:y,r:selected?3.4:2.4,"data-selected":selected}),svgNode("text",{x:lx,y:ly},text));
    }
    if(!ready){
      const path=frames.map(m=>screen(transformPoint(m,[0,0,0])));
      for(let i=1;i<path.length;i++)nodes.push(svgNode("line",{x1:path[i-1][0],y1:path[i-1][1],x2:path[i][0],y2:path[i][1],stroke:"#9cd2c5","stroke-width":5}));
      if(data.gripper){const mount=gripperMount(frames[6]);for(const p of mountedGripperParts(data.gripper.opening_mm))for(const face of p.faces)
        nodes.push(svgNode("polygon",{points:face.map(i=>screen(transformPoint(mount,p.points[i])).join(",")).join(" "),fill:"none",stroke:"#b9cbcc","stroke-width":1}));}
      const box=parcelBoxGeometry(data.parcelBox);
      if(box)for(const face of faces.filter((_,i)=>i!==1))nodes.push(svgNode("polygon",{points:face.map(i=>screen(box.points[i]).join(",")).join(" "),fill:"none",stroke:"#e5b96f","stroke-width":1.5}));
    }
    const offsets=[[-36,27],[18,-27],[-34,-21],[32,-28],[35,19],[-40,30]];
    for(let i=1;i<=6;i++)if(!data.simulation?.recipe||data.selected===i-1)label(transformPoint(frames[i],[0,0,0]),"J"+i,offsets[i-1],data.selected===i-1);
    label([0,0,0],"BASE 원점",[-20,30]);
    const box=parcelBoxGeometry(data.parcelBox);
    if(box){label(box.points[7],box.draft?"택배 박스 · 편집 중":"택배 박스",[10,-18]);label(box.bottom,"박스 바닥면 중심",[12,22]);}
    if(data.simulation?.route)for(const [i,point]of data.simulation.route.entries())if(!data.simulation.recipe||(point.marker&&point.number===data.simulation.segment+1))label(point.pose.slice(0,3),data.simulation.recipe?point.number+" · "+point.title:"지점 "+(i+1),[25,-45]);
    if(data.simulation?.route)for(const point of data.simulation.route)if(point.via_pose&&point.number===data.simulation.segment+1)label(point.via_pose.slice(0,3),'movec · 원호 경유 TCP',[25,-25]);
    if(data.product){label(transformPoint(data.product.frame,[0,0,0]),data.product.holding?"가상 상품 · 파지":data.product.released?"가상 상품 · 놓임":"가상 상품",[12,24]);label(data.product.approach.slice(0,3),data.product.region==='floor'?'수직 접근':'수평 접근',[12,-18]);}
    if(!ready&&data.simulation?.path)nodes.push(svgNode("polyline",{points:data.simulation.path.map(p=>screen(p).join(",")).join(" "),fill:"none",stroke:"#85e8cf","stroke-width":2}));
    if(data.guides){
      const g=data.guides;label(g.lens,"카메라 · 가이드",[-25,-22]);if(data.product?.region==='shelf')label(data.product.initial,'가상 선반',[20,-28]);else label(g.wall.points[2],"벽",[0,-10]);
      if(g.target&&!data.product)label(g.target,"교시 TCP",[18,22]);
      if(g.objectPoints.length&&!data.product)label(g.objectPoints[3],g.exampleObject?"예시 객체":"탐지 물체",[8,-17]);
      if(g.floor)nodes.push(svgNode("text",{x:width-10,y:height-22,"text-anchor":"end"},"바닥 0 mm · BASE Z "+g.floor.baseZ+" mm"));
    }
    const tool=data.tcp?poseFrame(data.tcp):frames[6],origin=transformPoint(tool,[0,0,0]);
    label(origin,data.simulation?"시뮬레이션 TCP":data.tcp?"실측 TCP":"모델 플랜지",[35,-16]);
    if(data.gripper)label(transformPoint(gripperMount(frames[6]),[0,0,143]),!Number.isFinite(data.gripper.opening_mm)?"그리퍼 · 벌림 미확인":"그리퍼 · "+data.gripper.opening_mm.toFixed(1)+" mm",[20,25]);
    if(height>190)for(let i=0;i<3;i++)label(add(origin,frameAxis(tool,i).map(v=>v*80)),"XYZ"[i]+"t",[[8,-12],[12,10],[8,16]][i]);
    overlay.replaceChildren(...nodes);
  }
  function render() {
    if(disposed||!last||!canvas.clientWidth||!canvas.clientHeight||canvas.closest("[hidden]"))return;
    const data=last;const frames=armFrames(data.angles),box=parcelBoxGeometry(data.parcelBox);width=canvas.clientWidth;height=canvas.clientHeight;
    const hand=data.gripper?{parts:mountedGripperParts(data.gripper.opening_mm),mount:gripperMount(frames[6])}:null;
    if(!gl&&!parts.length)initialize();
    const center=[230,0,330],rad=Math.PI/180,eye=add(center,[Math.cos(view.yaw*rad)*Math.cos(view.pitch*rad)*3000,Math.sin(view.yaw*rad)*Math.cos(view.pitch*rad)*3000,Math.sin(view.pitch*rad)*3000]);
    const camera=lookAt(eye,center),bounds=parts.length?parts.flatMap(p=>p.bounds.map(b=>transformPoint(camera,transformPoint(frames[p.link],b)))):frames.map(m=>transformPoint(camera,transformPoint(m,[0,0,0])));
    if(hand)bounds.push(...hand.parts.flatMap(p=>p.points.map(v=>transformPoint(camera,transformPoint(hand.mount,v)))));
    if(box)bounds.push(...box.points.map(p=>transformPoint(camera,p)));
    if(data.product)bounds.push(transformPoint(camera,transformPoint(data.product.frame,[0,0,0])));
    if(data.simulation?.bounds)bounds.push(...data.simulation.bounds.map(p=>transformPoint(camera,p)));
    if(data.guides){
      const g=data.guides,p=g.wall.points;
      // Match the mockup framing: fit the arm, camera and wall context. The
      // guide planes may extend past the viewport without shrinking the arm.
      const wallContext=p[0].map((v,i)=>(v+p[1][i])/2+(p[3][i]-v)*650/760+(p[1][i]-v)*50/760);
      bounds.push(...[g.lens,wallContext,...g.objectPoints].map(p=>transformPoint(camera,p)));
      if(g.target)bounds.push(transformPoint(camera,g.target));
    }
    if(data.tcp)bounds.push(transformPoint(camera,data.tcp.slice(0,3)));
    const min=[0,1].map(i=>Math.min(...bounds.map(p=>p[i]))),max=[0,1].map(i=>Math.max(...bounds.map(p=>p[i])));
    const scale=Math.min(Math.max(1,width-105)/Math.max(1,max[0]-min[0]),Math.max(1,height-95)/Math.max(1,max[1]-min[1])),cx=(min[0]+max[0])/2,cy=(min[1]+max[1])/2;
    vp=multiply(ortho(cx-width/scale/2,cx+width/scale/2,cy-height/scale/2,cy+height/scale/2),camera);
    if(ready){
      const ratio=Math.min(2,devicePixelRatio||1);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);gl.viewport(0,0,canvas.width,canvas.height);
      gl.clearColor(0,0,0,0);gl.depthMask(true);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.enable(gl.BLEND);gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.uniformMatrix4fv(u.vp,false,Float32Array.from(vp));
      const g=data.guides;
      if(g){
        gl.depthMask(false);if(g.floor){plate(g.floor.points,[.15,.32,.29,.8]);lines(g.floor.lines.flat(),[.43,.62,.59,.5]);}
        if(!data.product||data.product.region!=="shelf"){plate(g.wall.points,[.19,.32,.35,.65]);lines(g.wall.lines.flat(),[.43,.62,.59,.5]);}gl.depthMask(true);
        const wallCenter=[0,1,2].map(i=>g.wall.points.reduce((sum,p)=>sum+p[i],0)/4);
        lines([g.lens,wallCenter],[.6,.8,.94,.3]);
      }
      for(const part of parts){const gray=part.link===0||/_1_[14]$|_[2345]_2$/.test(part.name),base=gray?[.52,.58,.58,1]:[.91,.95,.94,1];
        draw(part,frames[part.link],part.link===data.selected+1?base.map((v,i)=>i<3?v*.9+[.15,.9,.73][i]*.1:v):base);
      }
      if(hand)hand.parts.forEach(part=>handPart(part,hand.mount));
      if(data.product){
        const p=data.product,center=transformPoint(p.frame,[0,0,0]);cube(center,p.size,[.92,.65,.29,1],[0,1,2].map(i=>frameAxis(p.frame,i)));
        if(p.region==="shelf"){
          const [x,y,z]=p.initial,w=p.size[1]/2+70,front=x-p.size[0]/2;
          plate([[front,y-w,z-100],[front,y+w,z-100],[front,y+w,z+120],[front,y-w,z+120]],[.3,.43,.5,.7]);
          cube([x,y,z-p.size[2]/2-4],[p.size[0]+100,p.size[1]+140,8],[.43,.55,.6,1]);
        }
      }
      if(box){
        // An open carton: bottom and four sides, with an uncovered rim.
        faces.forEach((ids,i)=>{if(i!==1)plate(ids.map(j=>box.points[j]),[.58+i*.025,.38+i*.025,.19+i*.018,1]);});
        const edges=faces.flatMap(ids=>ids.flatMap((j,i)=>[box.points[j],box.points[ids[(i+1)%4]]]));
        lines(edges,[.95,.76,.45,1]);
      }
      if(g){
        if(g.objectPoints.length&&!data.product)faces.forEach((ids,i)=>plate(ids.map(j=>g.objectPoints[j]),[.65+i*.012,.43+i*.013,.26+i*.015,1]));
        // The mockup uses a fixed-size position marker, not camera dimensions.
        cube(sub(g.lens,g.camera[0].map(v=>v*26)),[55,36,31],[.37,.6,.68,1],g.camera);
        cube(g.lens,[14,20,22],[.63,.77,.8,1],g.camera);
        const marks=[];if(g.target)for(let i=0;i<3;i++){const a=[...g.target],b=[...g.target];a[i]-=14;b[i]+=14;marks.push(a,b);}lines(marks,[.5,.93,.77,1]);
      }
      const tool=data.tcp?poseFrame(data.tcp):frames[6],origin=transformPoint(tool,[0,0,0]);gl.disable(gl.DEPTH_TEST);
      if(data.product){const a=data.product.approach.slice(0,3),b=data.product.pick.slice(0,3),dir=norm(sub(b,a)),side=norm(cross(dir,Math.abs(dir[2])<.9?[0,0,1]:[0,1,0])),arrow=[a,b];for(const sign of [-1,1])arrow.push(b,b.map((v,i)=>v-dir[i]*15+side[i]*7*sign));lines(arrow,[.95,.76,.4,.95]);}
      if(data.simulation?.path){
        const path=data.simulation.path;lines(path.slice(1).flatMap((p,i)=>[path[i],p]),[.5,.93,.78,.85]);
        const marks=[];for(const point of data.simulation.route)for(let i=0;i<3;i++){
          const a=point.pose.slice(0,3),b=[...a];a[i]-=9;b[i]+=9;marks.push(a,b);
        }lines(marks,[1,.77,.4,1]);
      }
      for(let i=0;i<3;i++){const dir=frameAxis(tool,i),to=add(origin,dir.map(v=>v*80)),side=norm(cross(dir,Math.abs(dir[2])<.9?[0,0,1]:[0,1,0])),arrow=[origin,to];
        for(const sign of [-1,1])arrow.push(to,to.map((v,j)=>v-dir[j]*10+side[j]*4*sign));lines(arrow,[[.9,.42,.3,1],[.27,.72,.43,1],[.31,.61,.94,1]][i]);}
      gl.enable(gl.DEPTH_TEST);
    }
    labels(frames,data);canvas.dataset.rendered=ready?"mesh":"skeleton";canvas.dataset.joints=data.angles.join(",");
    canvas.dataset.gripperAttached=Boolean(hand);
    canvas.dataset.simulation=data.simulation?.state||"";
    canvas.dataset.tcp=data.tcp?JSON.stringify(data.tcp):"";
    canvas.dataset.virtualProduct=data.product?JSON.stringify({center:transformPoint(data.product.frame,[0,0,0]),holding:data.product.holding,released:data.product.released}):"";
    canvas.dataset.parcelBox=box?JSON.stringify({size_mm:data.parcelBox.size_mm,center_base_mm:box.bottom,draft:box.draft}):"";
    if(hand){canvas.dataset.gripperPosition=data.gripper.position??"";canvas.dataset.gripperOpening=data.gripper.opening_mm??"";}
    else{delete canvas.dataset.gripperPosition;delete canvas.dataset.gripperOpening;}
  }
  function schedule() {if(!queued&&!disposed){queued=true;requestAnimationFrame(()=>{queued=false;render();});}}
  const on=(node,event,fn)=>node.addEventListener(event,fn,{signal});
  on(canvas,"pointerdown",e=>{drag={x:e.clientX,y:e.clientY,yaw:view.yaw,pitch:view.pitch};canvas.setPointerCapture(e.pointerId);});
  on(canvas,"pointermove",e=>{if(!drag)return;view={name:"free",yaw:drag.yaw+(drag.x-e.clientX)*.45,pitch:Math.max(5,Math.min(78,drag.pitch+(e.clientY-drag.y)*.32))};onOrbit(view.name);schedule();});
  on(canvas,"pointerup",()=>{if(drag){drag=null;remember();}});on(canvas,"pointercancel",()=>{drag=null;});
  on(canvas,"webglcontextlost",event=>{event.preventDefault();ready=false;graphics.hidden=false;graphics.textContent="3D 가속 연결 끊김 · 관절 선도로 표시합니다.";schedule();});
  on(canvas,"webglcontextrestored",()=>{gl=null;parts=[];initialize();schedule();});
  const observer=new ResizeObserver(schedule);observer.observe(canvas);
  signal.addEventListener("abort",()=>{disposed=true;observer.disconnect();if(gl){for(const p of [...parts,dynamic].filter(Boolean)){gl.deleteBuffer(p.v);gl.deleteBuffer(p.n);if(p.f)gl.deleteBuffer(p.f);}if(program)gl.deleteProgram(program);gl.getExtension("WEBGL_lose_context")?.loseContext();}},{once:true});
  return {update(data){last=data;schedule();},getView:()=>view.name,setView(name){const selected={oblique:[-50,24],front:[-90,6],side:[0,6]}[name];if(selected){view={name,yaw:selected[0],pitch:selected[1]};remember();schedule();}}};
}
