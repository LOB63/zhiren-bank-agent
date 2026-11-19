'use strict';
window.WealthVisuals={render(target,profile,rec){
 if(!profile.assessment_current)return;
 const a=profile.assessment.answers,chart=document.createElement('div');chart.className='risk-visuals';
 const panel=document.createElement('div'),title=document.createElement('h3');title.textContent='风险评估画像';panel.append(title);
 const axes=[['财务缓冲',({under1:0,'1to3':33,'3to6':67,over6:100})[a.emergency]],['风险认知',({none:0,basic:33,familiar:67,advanced:100})[a.knowledge]],['投资经验',({none:0,some:50,experienced:100})[profile.experience]],['损失承担',({none:0,small:33,medium:67,large:100})[a.loss_capacity]],['资金期限',profile.horizon_days<7?0:profile.horizon_days<30?33:profile.horizon_days<365?67:100],['波动接受',({sell:0,reduce:33,hold:67,review:100})[a.reaction]]];
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 360 320');svg.setAttribute('role','img');svg.setAttribute('aria-label',axes.map(([k,v])=>`${k}${v}/100`).join('，'));
 const point=(i,r)=>[180+Math.cos(-Math.PI/2+i*Math.PI/3)*r,153+Math.sin(-Math.PI/2+i*Math.PI/3)*r];
 const polygon=(points,cls)=>{const p=document.createElementNS(ns,'polygon');p.setAttribute('points',points.map(x=>x.join(',')).join(' '));p.setAttribute('class',cls);svg.append(p);};
 for(const r of [25,50,75,100])polygon(axes.map((_,i)=>point(i,r)),'risk-grid');
 polygon(axes.map(([,v],i)=>point(i,v)),'risk-shape');
 axes.forEach(([name,v],i)=>{const [x,y]=point(i,128),text=document.createElementNS(ns,'text');text.setAttribute('x',x);text.setAttribute('y',y);text.setAttribute('text-anchor','middle');text.textContent=`${name} ${v}`;svg.append(text);});
 panel.append(svg);const note=document.createElement('p');note.className='fineprint';note.textContent='图中数值是问卷选项的示意刻度，不是收益预测或金融信用分；最终结果还受约束条件限制。';panel.append(note);chart.append(panel);
 if(rec?.available){const budget=document.createElement('div'),h=document.createElement('h3');h.textContent='现金安排';budget.append(h);const bar=document.createElement('div');bar.className='risk-budget';bar.setAttribute('role','img');
 const reserved=Math.min(rec.cash_fen,rec.reserve_fen),trial=rec.suggested_trial_fen,rest=Math.max(0,rec.cash_fen-reserved-trial),money=n=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(n/100);
 for(const [label,value,cls] of [['预留',reserved,'reserved'],['参考投资',trial,'trial'],['其余现金',rest,'remaining']]){const segment=document.createElement('span');segment.className=cls;segment.style.width=(rec.cash_fen?value/rec.cash_fen*100:0)+'%';segment.title=label+money(value);bar.append(segment);const row=document.createElement('p');row.textContent=`${label} ${money(value)}`;budget.append(row);}
 bar.setAttribute('aria-label',`预留${money(reserved)}，参考投资${money(trial)}，其余现金${money(rest)}`);budget.insertBefore(bar,budget.children[1]);if(rec.reserve_fen>rec.cash_fen){const p=document.createElement('p');p.textContent=`距离预留目标还差 ${money(rec.reserve_fen-rec.cash_fen)}`;budget.append(p);}chart.append(budget);}
 target.append(chart);
 if(!matchMedia('(prefers-reduced-motion: reduce)').matches)chart.animate([{opacity:0,transform:'translateY(10px)'},{opacity:1,transform:'translateY(0)'}],{duration:450});
}};
