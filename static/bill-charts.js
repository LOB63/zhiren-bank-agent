'use strict';
window.BankCharts=(()=>{
 const ns='http://www.w3.org/2000/svg',colors=['#47685d','#7f9b8d','#7892a5','#b39a69','#a294ac','#bb8b75','#93a17b','#89969b'];
 const money=n=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(n/100);
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
 let circles=[],choose=null;
 function render(report,onSelect){
  const target=document.getElementById('expense-chart'),legend=document.getElementById('categories');target.replaceChildren();legend.replaceChildren();circles=[];
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 240 240');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${report.month}支出分类，总额${money(report.expense_fen)}`);
  const ring=(color)=>{const c=document.createElementNS(ns,'circle');for(const [key,value] of Object.entries({cx:120,cy:120,r:91,fill:'none',stroke:color,'stroke-width':27,pathLength:100}))c.setAttribute(key,value);svg.append(c);return c;};
  ring('#e5e8e2');let offset=0;
  const center=el('div',undefined,'donut-center'),label=el('span','本月支出'),value=el('strong',money(report.expense_fen)),hint=el('small',report.month);center.append(label,value,hint);
  const wrap=el('div',undefined,'donut-wrap');wrap.append(svg,center);target.append(wrap);
  const buttons=[];let selectedName=null;const highlight=name=>circles.forEach(c=>{c.classList.toggle('selected',c.dataset.category===name);c.classList.toggle('muted-segment',!!name&&c.dataset.category!==name);});
  choose=name=>{selectedName=name;const selected=report.categories.find(c=>c.name===name);label.textContent=selected?selected.name:'本月支出';value.textContent=money(selected?selected.amount_fen:report.expense_fen);hint.textContent=selected?`占比 ${(selected.amount_fen/report.expense_fen*100).toFixed(1)}%`:report.month;buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.category===name)));highlight(name);onSelect(name||null);};
  report.categories.forEach((cat,i)=>{
   const percent=report.expense_fen?cat.amount_fen/report.expense_fen*100:0;
   const arc=ring(colors[i%colors.length]);arc.setAttribute('stroke-dasharray',`${percent} ${100-percent}`);arc.setAttribute('transform',`rotate(${offset*3.6-90} 120 120)`);arc.dataset.percent=percent;arc.dataset.category=cat.name;arc.setAttribute('aria-label',`${cat.name}，${money(cat.amount_fen)}`);offset+=percent;circles.push(arc);
   const button=el('button',undefined,'chart-legend-item');button.type='button';button.setAttribute('aria-controls','expense-chart');button.addEventListener('pointerenter',()=>highlight(cat.name));button.addEventListener('pointerleave',()=>highlight(selectedName));button.addEventListener('focus',()=>highlight(cat.name));button.addEventListener('blur',()=>highlight(selectedName));button.dataset.category=cat.name;button.setAttribute('aria-pressed','false');button.setAttribute('aria-label',`${cat.name} ${money(cat.amount_fen)}，占比${percent.toFixed(1)}%，查看交易`);
   const name=el('span',undefined,'legend-name'),dot=el('i');dot.style.backgroundColor=colors[i%colors.length];dot.setAttribute('aria-hidden','true');name.append(dot,document.createTextNode(cat.name));button.append(name,el('strong',money(cat.amount_fen)),el('span',percent.toFixed(1)+'%','legend-percent'));button.addEventListener('click',()=>choose(button.getAttribute('aria-pressed')==='true'?null:cat.name));buttons.push(button);legend.append(button);
  });
  if(!report.expense_fen)hint.textContent='本月无已记录支出';
  const comparison=document.getElementById('bill-comparison');comparison.replaceChildren();
  const previous=report.comparison;const max=Math.max(report.expense_fen,previous?.expense_fen||0,1);
  for(const [data,current] of [[previous,false],[report,true]]){
   if(!data)continue;
   const row=el('div',undefined,'compare-row'),title=el('div',undefined,'compare-label');title.append(el('span',data.month),el('strong',money(data.expense_fen)));const track=el('div',undefined,'compare-track'),fill=el('div',undefined,'compare-fill'+(current?' current':''));fill.style.width=(data.expense_fen/max*100)+'%';track.append(fill);row.append(title,track);comparison.append(row);
  }
  if(previous){const change=report.expense_fen-previous.expense_fen;comparison.append(el('p',`较${previous.month}${change>=0?'增加':'减少'} ${money(Math.abs(change))}`,'comparison-note'));}
 }
 function animate(){
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  document.querySelectorAll('#view-bills .metric, #view-bills .chart-panel').forEach((node,i)=>{node.getAnimations().forEach(a=>a.cancel());node.animate([{opacity:0,transform:'translateY(16px)'},{opacity:1,transform:'translateY(0)'}],{duration:460,delay:i*45,easing:'cubic-bezier(.2,.8,.2,1)',fill:'backwards'});});
  circles.forEach((c,i)=>{c.getAnimations().forEach(a=>a.cancel());const p=Number(c.dataset.percent);c.animate([{strokeDasharray:'0 100'},{strokeDasharray:`${p} ${100-p}`}],{duration:850,delay:120+i*45,easing:'cubic-bezier(.2,.8,.2,1)',fill:'backwards'});});
  document.querySelectorAll('#bill-comparison .compare-fill').forEach((node,i)=>{node.getAnimations().forEach(a=>a.cancel());node.animate([{transform:'scaleX(0)'},{transform:'scaleX(1)'}],{duration:750,delay:180+i*100,easing:'ease-out',fill:'backwards'});});
 }
 return {render,animate,clear:()=>choose?.(null)};
})();
