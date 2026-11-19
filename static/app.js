'use strict';
const $=id=>document.getElementById(id);
let state={csrf:'',user:null,task:null,meta:null,recipients:[],subs:[],wealth:null,cards:[],processing:false,pendingMessage:null,activityText:'',activityStart:0,advancePending:{},confirmationUnknown:{},recipientSaveUnknown:false,billRows:[],billReport:null,billCategory:null};
const money=v=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format((v||0)/100);
const labels={active:'进行中',pending:'待执行',waiting_confirmation:'等待确认',awaiting_input:'需要补充',completed:'已完成',paused:'已暂停',cancelled:'已取消',executed:'执行成功',rejected:'已拒绝',succeeded:'成功',failed:'失败'};
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function bankIcon(className){const icon=el('img',undefined,className);icon.src='/static/bank-mark.jpg';icon.alt='智人银行图标';return icon;}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,method='GET',body){const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:body===undefined?undefined:JSON.stringify(body)});let data;try{data=await response.json();}catch{throw new Error('服务返回无法读取的响应');}if(!response.ok){const error=new Error(data.error?.message||data.detail?.[0]?.msg||'请求失败');error.code=data.error?.code;throw error;}return data;}
function showView(name){document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!==`view-${name}`);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.view===name));window.scrollTo(0,0);if(name==='bills')requestAnimationFrame(()=>BankCharts.animate());if(name==='wealth'&&state.wealth){renderWealth(state.wealth);loadBankProducts();}}
function notice(text){$('notice').hidden=!text;$('notice').textContent=text;}
async function enter(session){state.csrf=session.csrf;state.user=session.user;try{state.advancePending=JSON.parse(sessionStorage.getItem('bank-advance-'+state.user.id)||'{}');}catch{state.advancePending={};}if(!state.advancePending||typeof state.advancePending!=='object'||Array.isArray(state.advancePending))state.advancePending={};$('login-screen').hidden=true;$('workspace').hidden=false;$('reset-target').textContent=`${state.user.name}（${state.user.id}）`;$('user-name').textContent=`${session.user.name} · 模拟账户`;$('business-date').textContent=`业务日期 ${state.meta.business_date}`;$('model-status').textContent=state.meta.api_configured?'AI 助手':'助手暂不可用';if(!state.meta.api_configured)notice('AI 助手暂不可用，请联系演示维护者完成模型配置。');await refresh();const tasks=await api('/api/tasks'),saved=localStorage.getItem('bank-task-'+state.user.id);const selected=tasks.find(t=>t.id===saved)||tasks[0];if(selected)renderTask(selected);}
async function refresh(){
 const [account,subs,recipients,tasks,wealth,cards,meta,context,riskQuestions]=await Promise.all([api('/api/account'),api('/api/subscriptions'),api('/api/recipients'),api('/api/tasks'),api('/api/wealth'),api('/api/cards'),api('/api/meta'),state.meta?.scenario_mode?api('/api/customer-context'):Promise.resolve({available:false}),api('/api/wealth/risk-questionnaire')]);
 state.riskQuestions=riskQuestions;state.meta=meta;$('business-date').textContent=`业务日期 ${meta.business_date}`;state.wealth=wealth;state.cards=cards.cards;renderWealth(wealth);renderCards(cards);renderCustomerContext(context);$('balance').textContent=money(account.balance_fen);state.subs=subs.catalog;state.recipients=recipients;renderRecipients();
 if(state.recipientSaveUnknown){state.recipientSaveUnknown=false;$('recipient-form').querySelector('button[type=submit]').disabled=false;$('recipient-check-status').hidden=true;$('recipient-save-status').textContent='列表已更新，请核对收款人是否已保存。';}
 const active=subs.catalog.filter(s=>s.status==='active');$('subscription-summary').textContent=`${active.length} 项 / ${money(active.reduce((v,s)=>v+s.amount_fen,0))}`;renderSubscriptions(subs);
 const select=$('task-select');select.replaceChildren(el('option','选择任务'));select.firstChild.value='';for(const task of tasks){const option=el('option',`${labels[task.status]||task.status} · ${task.goal.slice(0,25)}`);option.value=task.id;select.append(option);}if(state.task)select.value=state.task.id;await loadBills();
}
function renderCustomerContext(context){
 const panel=$('customer-context');panel.hidden=!context.available;if(!context.available)return;
 $('context-title').textContent=`我的财务资料 · ${context.title}`;
 const content=$('context-content');content.replaceChildren(el('p',context.income_pattern),el('p',context.reserve_reason));
 for(const item of context.upcoming_outlays){content.append(el('p',`${item.title} · ${money(item.amount_fen)} · 预计 ${item.due_date}${item.timing_status==='due_or_past_unverified'?'，支付情况待核对':''}`));}
 content.append(el('p',`资料登记于 ${context.recorded_at}；本次办理可明确指定预留要求。`,'fineprint'));
}
function inlineText(node,text){
 const pattern=/(\*\*[^*]+\*\*|`[^`]+`)/g;let start=0;
 for(const match of text.matchAll(pattern)){node.append(document.createTextNode(text.slice(start,match.index)));const code=match[0][0]==='`';node.append(el(code?'code':'strong',match[0].slice(code?1:2,code?-1:-2)));start=match.index+match[0].length;}
 node.append(document.createTextNode(text.slice(start)));
}
function formattedContent(text){
 const box=el('div',undefined,'content formatted');const lines=text.split('\n');let list=null;
 for(let i=0;i<lines.length;i++){
  const line=lines[i];
  if(/^\s*\|.*\|\s*$/.test(line)&&i+1<lines.length&&/^\s*\|?[\s:|\-]+\|\s*$/.test(lines[i+1])){
   const wrap=el('div',undefined,'table-scroll'),table=el('table');let header=true;
   for(;i<lines.length&&/^\s*\|.*\|\s*$/.test(lines[i]);i++){
    if(/^\s*\|?[\s:|\-]+\|\s*$/.test(lines[i]))continue;
    const row=el('tr');for(const cell of lines[i].trim().slice(1,-1).split('|')){const td=el(header?'th':'td');inlineText(td,cell.trim());row.append(td);}table.append(row);header=false;
   }i--;wrap.append(table);box.append(wrap);list=null;continue;
  }
  const item=line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
  if(item){if(!list){list=el('ul');box.append(list);}const li=el('li');inlineText(li,item[1]);list.append(li);continue;}
  list=null;if(!line.trim())continue;
  const heading=line.match(/^#{1,3}\s+(.+)/),node=el(heading?'h4':'p');inlineText(node,heading?heading[1]:line);box.append(node);
 }
 return box;
}
function messageBox(role,text){const box=el('div',undefined,'message '+role);box.append(el('div',role==='user'?'你':'智人银行','role'));box.append(role==='assistant'?formattedContent(text):el('div',text,'content'));return box;}
function renderConversation(task=state.task){
 const conversation=$('conversation');conversation.replaceChildren();
 for(const m of task?.conversation||[])conversation.append(messageBox(m.role,m.text));
 if(state.pendingMessage&&state.pendingMessage.taskId===(task?.id||null)){
  const box=messageBox('user',state.pendingMessage.text);box.dataset.localMessage='true';
  if(state.pendingMessage.status==='unknown')box.append(el('p','尚未取得处理结果。请先刷新核对历史对话，再决定是否重新发送。原需求已保留。','message-warning'));
  conversation.append(box);
 }
 if(state.processing){const progress=el('div',undefined,'activity');progress.id='live-request';progress.setAttribute('role','status');const spinner=el('span',undefined,'spinner');spinner.setAttribute('aria-hidden','true');const body=el('div');body.append(el('strong',state.activityText,'activity-title'));const time=el('p',undefined,'activity-time');time.setAttribute('aria-hidden','true');body.append(time);progress.append(spinner,body);conversation.append(progress);updateActivity();}
 if(!conversation.children.length){const empty=el('div',undefined,'empty-state');empty.append(bankIcon('empty-icon'),el('h3','今天需要帮你办什么？'),el('p','可以查账、转账，或管理订阅、理财和卡片。'));conversation.append(empty);}
 conversation.scrollTop=conversation.scrollHeight;
}
function updateActivity(){
 const elapsed=Math.max(0,Math.floor((Date.now()-state.activityStart)/1000));const title=document.querySelector('#live-request .activity-title'),time=document.querySelector('#live-request .activity-time');
 if(title)title.textContent=state.activityText;
 if(time)time.textContent=`已等待 ${elapsed} 秒${elapsed>=15?' · 仍在等待回复，请勿重复发送':''}`;
 if(state.processing)$('request-status').textContent=`处理中 · ${elapsed} 秒`;
}
function setActivity(text){state.activityText=text;updateActivity();}
function rememberAdvance(tid,aid){if(aid)state.advancePending[tid]=aid;else delete state.advancePending[tid];sessionStorage.setItem('bank-advance-'+state.user.id,JSON.stringify(state.advancePending));}
function renderTask(task){
 const previous=$('actions').querySelector('[data-action-id], [data-repeat-id]');const previousId=previous?.dataset.actionId||previous?.dataset.repeatId;
 state.task=task;localStorage.setItem('bank-task-'+state.user.id,task.id);$('task-select').value=task.id;$('task-status').textContent=state.processing?'处理中':labels[task.status]||task.status;renderConversation(task);
 $('task-steps').replaceChildren();for(const n of task.nodes){const li=el('li',n.title);li.append(el('span',`${labels[n.status]||n.status}${n.depends_on.length?' · 依赖 '+n.depends_on.join('、'):''}`));$('task-steps').append(li);}if(!task.nodes.length)$('task-steps').append(el('li','单步请求，无需多步骤计划'));
 const current=[...(task.repeat_requests||[]).filter(r=>r.status==='pending').map(renderRepeat),...task.actions.filter(a=>a.status==='pending').map(renderAction)];$('actions').replaceChildren(...current);
 $('past-actions').replaceChildren(...(task.repeat_requests||[]).filter(r=>r.status!=='pending').map(renderRepeat),...task.actions.filter(a=>a.status!=='pending').map(renderAction));
 const executed=task.actions.filter(a=>a.status==='executed');$('last-result').replaceChildren();
 if(!current.length&&executed.length){const a=executed.at(-1),receipt=el('div',undefined,'result-receipt');receipt.append(el('strong',actionTitle(a)+' · 已执行'));
  receipt.append(el('p',receiptText(a)));receipt.append(el('small','完整回执可在下方“历史对话与处理详情”中查看。'));$('last-result').append(receipt);}
 const manual=task.goal.startsWith('[手动业务验证]');const recover=task.status==='paused'||!!state.advancePending[task.id]||(task.status==='active'&&!current.length&&executed.length>0);
 $('recovery-bar').hidden=!recover||manual;$('recovery-description').textContent=state.advancePending[task.id]?'操作回执已保留，后续结果尚未取得，可以继续核对。':task.status==='paused'?'处理已暂停，已成功的操作仍然有效。核对后可恢复后续步骤。':'操作结果已经保留，可以继续处理后续步骤。';
 $('continue-task').hidden=task.status==='paused';$('resume-task').hidden=task.status!=='paused';
 $('continue-task').textContent=state.advancePending[task.id]?'查看完整结果':'继续处理';$('continue-task').disabled=state.processing||['paused','cancelled','waiting_confirmation'].includes(task.status)||(task.status==='completed'&&!state.advancePending[task.id])||!state.meta.api_configured||manual;$('resume-task').disabled=state.processing||task.status!=='paused';$('cancel-task').disabled=['cancelled','completed'].includes(task.status);
 const active=$('actions').querySelector('[data-action-id], [data-repeat-id]');const activeId=active?.dataset.actionId||active?.dataset.repeatId;
 if(activeId&&activeId!==previousId)requestAnimationFrame(()=>active.scrollIntoView({block:'nearest',behavior:'smooth'}));
}
function detail(dl,key,value){dl.append(el('dt',key),el('dd',value));}
function button(text,handler,className='secondary'){const b=el('button',text,className);b.type='button';b.addEventListener('click',async()=>{if(state.processing)return;b.disabled=true;try{await handler();}catch(e){toast(e.message);}finally{b.disabled=false;}});return b;}
const actionNames={transfer:'转账',cancel_subscription:'取消订阅',wealth_buy:'申购',wealth_redeem:'赎回',card_apply:'申请信用卡',card_limit:'调整卡片额度',card_freeze:'冻结卡片',card_unlock:'解锁卡片',card_lost:'挂失卡片',card_online:'修改线上交易开关',card_payment:'刷卡消费',card_repay:'信用卡还款'};
function actionTitle(a){return actionNames[a.kind]||'操作';}
function receiptText(a){
 if(a.kind==='transfer')return `${a.recipient.name} · 尾号${a.recipient.phone_tail}，执行后余额 ${money(a.result.balance_fen)}`;
 if(a.kind==='cancel_subscription')return `${a.subscription.merchant}续费已取消`;
 if(a.kind.startsWith('wealth_'))return `${a.product.name} · 本金 ${money(a.params.amount_fen)}，账户余额 ${money(a.result.balance_fen)}。`;
 if(a.kind==='card_apply')return '申请已通过，新卡已开通。';
 if(a.kind==='card_payment')return `消费 ${money(a.params.amount_fen)}，待还款 ${money(a.result.card.used_fen)}。`;
 if(a.kind==='card_repay')return `还款 ${money(a.params.amount_fen)}，待还款 ${money(a.result.card.used_fen)}，现金 ${money(a.result.balance_fen)}。`;
 return `尾号${a.card.tail} · 卡片设置已更新，可在卡片管理中核对。`;
}
function renderAction(a){
 const pending=a.status==='pending',transfer=a.kind==='transfer',wealth=a.kind.startsWith('wealth_');const card=el('div',undefined,'action-card'+(pending?' current-action':''));card.dataset.actionId=a.id;
 card.append(el('p',pending?'请核对后确认':labels[a.status]||a.status,'action-eyebrow'),el('h3',(pending?'确认':'')+actionTitle(a)));
 if(a.params.amount_fen)card.append(el('div',money(a.params.amount_fen),'action-amount'));
 const dl=el('dl');
 if(transfer){detail(dl,'收款人',`${a.recipient.name} · 尾号${a.recipient.phone_tail}`);if(a.recipient.bank_name)detail(dl,'收款账号',`${a.recipient.bank_name} · 尾号${a.recipient.account_tail}`);detail(dl,'至少保留',money(a.params.reserve_fen));}
 else if(a.kind==='cancel_subscription'){detail(dl,'订阅',a.subscription.merchant);detail(dl,'未来月费',money(a.subscription.amount_fen));if(pending)detail(dl,'下次扣费',a.subscription.next_at);else detail(dl,'续费状态',a.status==='executed'?'已取消':'未办理');}
 else if(wealth){detail(dl,'产品',a.product.name);detail(dl,'风险等级',`R${a.product.risk_level}`);detail(dl,'锁定期',`${a.product.lock_days}天`);if(a.kind==='wealth_buy')detail(dl,'至少保留',money(a.params.reserve_fen));card.append(el('p',a.product.description,'fineprint'));}
 else if(a.kind==='card_apply'){detail(dl,'类型','模拟信用卡');detail(dl,'初始额度',money(500000));}
 else{detail(dl,'卡片',`尾号${a.card.tail}`);
  if(a.kind==='card_limit'){detail(dl,'总额度',money(a.params.credit_limit_fen));detail(dl,'单笔限额',money(a.params.single_limit_fen));detail(dl,'当日限额',money(a.params.daily_limit_fen));}
  if(a.kind==='card_online')detail(dl,'线上交易',a.params.enabled?'允许':'限制');
  if(a.kind==='card_payment'){detail(dl,'商户',a.params.merchant);detail(dl,'渠道',a.params.channel==='online'?'线上':'线下');}
  if(a.kind==='card_repay')detail(dl,'现金至少保留',money(a.params.reserve_fen));
  if(a.kind==='card_lost')card.append(el('p','挂失后不可在本系统解挂，仍可归还已用额度。','fineprint'));
 }
 card.append(dl);
 const extra=el('details',undefined,'operation-details');extra.append(el('summary','操作详情'));const metadata=el('dl');detail(metadata,'状态',labels[a.status]||a.status);detail(metadata,'版本',`v${a.version}`);detail(metadata,'确认方式',a.risk==='red'?'需要模拟验证码和确认':'需要本人确认');extra.append(metadata);if(wealth)extra.append(el('p','示例产品均非保本，可能发生本金损失。本演示按本金记账，不模拟市场损益。','fineprint'));
 if(a.result)extra.append(el('p',receiptText(a),'fineprint'));
 card.append(extra);if(!pending)return card;
 if(wealth&&!state.wealth?.trading_enabled){card.append(el('p','模拟理财交易已移除，此项不再办理。','muted'),button('关闭此项',async()=>{await api(`/api/actions/${a.id}/reject`,'POST',{version:a.version});await reloadTask();}));return card;}
 if(state.confirmationUnknown[a.id]){card.append(el('p','暂未取得执行结果，请先核对，勿重复提交。','message-warning'),button('核对执行结果',async()=>{await reloadTask();await refresh();toast('执行状态已核对，请查看结果。');}));return card;}

 let codeInput=null,riskConsent=null;
 if(wealth){const label=el('label',undefined,'reset-consent');riskConsent=el('input');riskConsent.type='checkbox';riskConsent.setAttribute('aria-label','理解模拟产品与本金记账');label.append(riskConsent,document.createTextNode('我已了解产品风险与申赎规则'));card.append(label);}
 if(a.risk==='red'&&!a.verified){const verification=el('div',undefined,'verification-box');verification.append(el('p',transfer?'今日累计转账将超过1000元，需要模拟验证。':'此项资金或卡片敏感变更需要模拟验证。'));const codeLabel=el('label','输入6位模拟验证码');codeInput=el('input');codeInput.inputMode='numeric';codeInput.maxLength=6;codeInput.autocomplete='off';codeInput.setAttribute('aria-label','模拟验证码');codeLabel.append(codeInput);const hint=el('p','获取验证码后填写。','fineprint');verification.append(codeLabel,button('获取模拟验证码',async()=>{const challenge=await api(`/api/actions/${a.id}/challenge`,'POST',{version:a.version});if(hint.expiryTimer)clearInterval(hint.expiryTimer);const deadline=Date.now()+challenge.expires_in*1000;const tick=()=>{if(!hint.isConnected){clearInterval(hint.expiryTimer);return;}const seconds=Math.max(0,Math.ceil((deadline-Date.now())/1000));hint.textContent=seconds?`演示验证码：${challenge.demo_code} · 剩余${seconds}秒`:'验证码已过期，请重新获取。';if(!seconds){codeInput.value='';clearInterval(hint.expiryTimer);}};tick();hint.expiryTimer=setInterval(tick,1000);codeInput.focus();}),hint);card.append(verification);}
 if(a.verified)card.append(el('p','验证已完成','verified'));
 const actions=el('div',undefined,'action-buttons');actions.append(button('确认'+actionTitle(a)+(a.params.amount_fen?' '+money(a.params.amount_fen):''),async()=>{
  try{if(riskConsent&&!riskConsent.checked)throw new Error('请先核对并勾选模拟产品说明');if(codeInput){if(!/^\d{6}$/.test(codeInput.value))throw new Error('请先获取并输入6位模拟验证码');await api(`/api/actions/${a.id}/verify`,'POST',{version:a.version,code:codeInput.value});}await confirmAndAdvance(a,!!riskConsent?.checked);}
  catch(e){if(e.code==='REQUIRES_VERIFICATION'){toast('需要模拟验证，请填写新显示的验证码区域。');await reloadTask();}else throw e;}
 },'primary'),button('暂不办理',async()=>{await api(`/api/actions/${a.id}/reject`,'POST',{version:a.version});await reloadTask();toast('本项未执行。');}));card.append(actions);
 if(transfer){const edit=el('details',undefined,'edit-transfer');edit.append(el('summary','修改金额'));const form=el('form'),label=el('label','新的转账金额（元）'),input=el('input');input.value=String(a.params.amount_fen/100);input.inputMode='decimal';input.setAttribute('aria-label','新的转账金额');label.append(input);const save=el('button','更新金额','secondary');save.type='submit';form.append(label,save);form.addEventListener('submit',async e=>{e.preventDefault();if(state.processing)return;save.disabled=true;try{await api(`/api/actions/${a.id}/revise`,'POST',{version:a.version,...a.params,amount_fen:parseMoney(input.value)});await reloadTask();toast('金额已更新，请重新核对并确认。');}catch(error){toast(error.message);}finally{save.disabled=false;}});edit.append(form);card.append(edit);}
 return card;
}
function renderRepeat(r){
 const card=el('div',undefined,'action-card repeat-card');card.dataset.repeatId=r.id;
 card.append(el('h3',r.source_status==='executed'?'是否再转一笔？':'是否重新发起转账？'));
 const dl=el('dl');detail(dl,'收款人',`${r.recipient.name} · 尾号${r.recipient.phone_tail}`);detail(dl,'转账金额',money(r.params.amount_fen));detail(dl,'保留余额',money(r.params.reserve_fen));detail(dl,'原操作',labels[r.source_status]||r.source_status);card.append(dl);
 card.append(el('p','请选择是否再次办理这笔转账。','fineprint'));
 if(r.status!=='pending'){card.append(el('p',r.status==='approved'?'已选择重新发起，请查看新草稿':r.status==='declined'?'已选择不再转账':'询问已取消'));return card;}
 const choices=el('div',undefined,'action-buttons');
 for(const [label,repeat] of [['是，生成新草稿',true],['不用，不再转账',false]])choices.append(button(label,async()=>{
  await api(`/api/repeats/${r.id}/decide`,'POST',{version:r.version,repeat});await reloadTask();await refresh();
  toast(repeat?'请核对新的转账申请。':'已取消本次转账申请。');
 },repeat?'primary':'secondary'));
 card.append(choices);return card;
}
function parseMoney(value){const text=String(value).trim();if(!/^\d+(\.\d{1,2})?$/.test(text))throw new Error('请输入最多两位小数的非负金额');const [whole,fraction='']=text.split('.');const amount=Number(whole)*100+Number(fraction.padEnd(2,'0'));if(!Number.isSafeInteger(amount))throw new Error('金额过大');return amount;}
async function reloadTask(){if(state.task){const fresh=await api('/api/tasks/'+state.task.id);for(const action of fresh.actions){if(state.confirmationUnknown[action.id]){delete state.confirmationUnknown[action.id];if(action.status==='executed'&&!fresh.goal.startsWith('[手动业务验证]'))rememberAdvance(fresh.id,action.id);}}renderTask(fresh);}}

async function busy(handler,rethrow=false,activity='正在等待助手回复…'){
 if(state.processing)return;state.processing=true;state.activityText=activity;state.activityStart=Date.now();
 for(const id of ['send','continue-task','resume-task','new-task','task-select'])$(id).disabled=true;
 $('send').textContent='处理中…';$('task-status').textContent='处理中';$('actions').inert=true;$('chat-column').setAttribute('aria-busy','true');renderConversation();const timer=setInterval(updateActivity,1000);
 try{await handler();}catch(e){if(rethrow)throw e;toast(e.message);}finally{
  clearInterval(timer);state.processing=false;for(const id of ['send','new-task','task-select'])$(id).disabled=false;$('send').textContent='发送';$('actions').inert=false;$('chat-column').setAttribute('aria-busy','false');
  if(state.task)renderTask(state.task);else{renderConversation();$('task-status').textContent=state.pendingMessage?'结果待核对':'未开始';}$('request-status').textContent='就绪';
 }
}
async function confirmAndAdvance(action,riskAccepted=false){
 return busy(async()=>{
 const taskId=action.task_id;
 const manual=state.task?.goal.startsWith('[手动业务验证]');
 setActivity('正在提交这项操作…');
 let receipt;
 try{receipt=await api(`/api/actions/${action.id}/confirm`,'POST',{version:action.version,risk_acknowledged:riskAccepted});}
 catch(error){
  if(error.code)throw error;
  setActivity('未取得回执，正在核对执行状态…');
  try{receipt=await api(`/api/actions/${action.id}`);}
  catch{state.confirmationUnknown[action.id]=true;toast('执行结果暂时无法核对。恢复连接后请先核对结果。');return;}
  if(receipt.status!=='executed'){await reloadTask();await refresh();toast(receipt.status==='pending'?'当前仍待确认，请核对后再决定是否重试。':'操作状态已更新，请查看结果。');return;}
 }
 delete state.confirmationUnknown[action.id];

 // 先展示真实执行回执；后续模型失败也不会把已成功操作显示成失败。
 if(state.task?.id===taskId)renderTask({...state.task,actions:state.task.actions.map(a=>a.id===receipt.id?receipt:a)});
 if(Number.isSafeInteger(receipt.result.balance_fen))$('balance').textContent=money(receipt.result.balance_fen);
  setActivity('操作已执行，正在处理下一步…');
  try{
   if(manual){toast('手动业务验证已执行，不调用模型。');return;}
   rememberAdvance(taskId,action.id);
   const response=await api(`/api/actions/${action.id}/advance`,'POST',{});
   rememberAdvance(taskId,null);
   if(state.task?.id===taskId)renderTask(response.task);
   toast(response.error?'本次操作已执行；后续推进暂停：'+response.error:response.task.status==='completed'?'任务已完成，请查看执行结果。':'已自动推进，请核对下一步；后续操作仍需确认。');
  }catch(e){toast('本次操作已执行；后续推进未完成：'+e.message+'。请查看任务状态，勿重复转账。');}
  finally{try{await refresh();if(state.task?.id===taskId)await reloadTask();}catch(e){toast('本次操作已执行；状态刷新失败，请稍后刷新核对。');}}
 },true);
}
async function send(text,options={}){
 if(state.processing)return;const taskId=state.task?.id||null;state.pendingMessage={text,taskId,status:'sending'};
 await busy(async()=>{
  let response;
  try{response=await api('/api/chat','POST',{text,task_id:taskId,read_only:!!options.readOnly});}
  catch(error){state.pendingMessage.status='unknown';if(!$('message').value)$('message').value=text;toast('没有取得完整结果，请先刷新核对。你的需求已保留。');return;}
  state.pendingMessage=null;renderTask(response.task);if(response.error)toast(response.error);
  try{await refresh();}catch(error){toast('回复已显示，账户信息刷新未完成，请稍后刷新。');}
 });
}
async function continueTask(resume=false){
 if(!state.task)return;const tid=state.task.id;
 await busy(async()=>{
  if(resume)renderTask(await api(`/api/tasks/${tid}/resume`,'POST',{}));
  const aid=state.advancePending[tid];const path=aid&&!resume?`/api/actions/${aid}/advance`:`/api/tasks/${tid}/continue`;
  const response=await api(path,'POST',{});rememberAdvance(tid,null);renderTask(response.task);if(response.error)toast(response.error);else toast('处理结果已更新。');await refresh();
 });
}
async function loadBills(){const month=$('bill-month').value,compare=$('compare-month').value;const report=await api(`/api/bills?month=${encodeURIComponent(month)}&compare_month=${encodeURIComponent(compare)}`);$('bill-totals').replaceChildren(...[['收入',report.income_fen],['支出',report.expense_fen],['净收支',report.net_fen],['较对比月支出变化',report.expense_change_fen]].map(([label,value])=>{const box=el('div',undefined,'metric');box.append(el('p',label),el('strong',money(value)));return box;}));$('anomalies').replaceChildren(...report.anomalies.map(a=>{const row=el('div',a.type==='large_payment'?'单笔大额支出':'同日同商户同金额的多笔交易','anomaly');row.append(el('span','待核对','status'),el('small',a.entry_ids.join('、')));return row;}));if(!report.anomalies.length)$('anomalies').append(el('p','暂无待核对事项。','muted'));const [year,m]=month.split('-').map(Number);const next=m===12?`${year+1}-01-01`:`${year}-${String(m+1).padStart(2,'0')}-01`;const rows=await api(`/api/transactions?start_date=${month}-01&end_date=${next}`);state.billRows=rows;state.billReport=report;state.billCategory=null;$('bill-transactions').open=false;BankCharts.render(report,renderBillRows);renderBillRows(null);$('bill-scope').textContent=report.capital_flow_note||'收支统计来自本账户账单。';if(!$('view-bills').hidden)BankCharts.animate();}
function renderBillRows(category){
 state.billCategory=category;if(category)$('bill-transactions').open=true;const rows=state.billRows.filter(r=>!category||(r.direction==='out'&&r.category===category));$('transaction-heading').textContent=category?`${category} · ${rows.length}笔支出`:'交易明细与证据';$('clear-bill-filter').hidden=!category;$('bill-entry-count').textContent=` · ${rows.length}笔`;
 $('transactions').replaceChildren(...rows.map(r=>{const tr=el('tr');tr.append(el('td',r.at.slice(0,10)),el('td',r.merchant),el('td',r.category),el('td',(r.direction==='out'?'-':'+')+money(r.amount_fen)));const evidence=el('td',r.note);evidence.append(el('small',r.id));tr.append(evidence);return tr;}));
}
$('clear-bill-filter').addEventListener('click',()=>BankCharts.clear());
function evidenceDetails(merchant,amount){
 const details=el('details',undefined,'payment-evidence');details.append(el('summary','查看历史付款依据'));let loaded=false;
 details.addEventListener('toggle',async()=>{if(!details.open||loaded)return;loaded=true;const content=el('div','正在读取历史记录…','table-scroll');details.append(content);
  try{const data=await api(`/api/subscriptions/evidence?merchant=${encodeURIComponent(merchant)}&amount_fen=${amount}`);const table=el('table'),head=el('tr');for(const title of ['日期','金额','备注'])head.append(el('th',title));table.append(head);for(const payment of data.payments){const row=el('tr');row.append(el('td',payment.at.slice(0,10)),el('td',money(payment.amount_fen)),el('td',payment.note));table.append(row);}content.replaceChildren(table,el('p','历史记录按付款日期排列。','fineprint'));}catch(e){content.textContent=e.message;loaded=false;}
 });return details;
}
function updateSubscriptionSelection(){const count=document.querySelectorAll('[data-subscription]:checked').length;$('send-selection').textContent=count?`取消选中订阅（${count}项）`:'取消选中订阅';$('send-selection').disabled=!count;}
function renderSubscriptions(data){
 $('subscriptions').replaceChildren(...data.catalog.filter(s=>s.status==='active').map(s=>{
  const card=el('div',undefined,'subscription-card');const top=el('div',undefined,'sub-top');top.append(el('h2',s.merchant),el('span',s.status==='active'?'有效':'已取消','status'));card.append(top,el('strong',`${money(s.amount_fen)} / 月`),el('p',`下次扣费：${s.status==='active'?s.next_at:'不再续费'}`));
  const label=el('label'),check=el('input');check.type='checkbox';check.value=s.id;check.disabled=s.status!=='active';check.dataset.subscription='true';check.addEventListener('change',updateSubscriptionSelection);label.append(check,document.createTextNode('选择取消续费'));card.append(label);
  const match=data.candidates.find(c=>c.subscription?.id===s.id);card.append(el('p',match?`${match.observations}笔历史支付与目录匹配`:'目录已确认，历史样本不足'));if(match)card.append(evidenceDetails(s.merchant,s.amount_fen));return card;
 }));if(!$('subscriptions').children.length)$('subscriptions').append(el('p','暂无有效订阅。','muted'));const cancelled=data.catalog.filter(s=>s.status!=='active');$('cancelled-subscriptions').hidden=!cancelled.length;$('cancelled-subscription-count').textContent=`已取消记录（${cancelled.length}项）`;$('cancelled-subscription-list').replaceChildren(...cancelled.map(s=>{const row=el('div',undefined,'cancelled-subscription-row');row.append(el('strong',s.merchant),el('span','续费已取消','muted'));return row;}));updateSubscriptionSelection();
 $('periodic-candidates').replaceChildren(...data.candidates.filter(c=>!c.subscription).map(c=>{
  const row=el('div',undefined,'periodic-card'),header=el('div',undefined,'candidate');const title=el('div',c.merchant);title.append(el('small',`${c.observations}次记录 · 历史付款间隔${Math.min(...c.interval_days)}—${Math.max(...c.interval_days)}天`));header.append(title,el('strong',money(c.amount_fen)));row.append(header,el('p','周期支出','fineprint'),evidenceDetails(c.merchant,c.amount_fen),button('分析这项周期支出',()=>askAssistant(`帮我分析${c.merchant}每次${c.amount_fen/100}元的支出规律。`,{readOnly:true})));return row;
 }));
}
function renderRecipients(){
 const query=$('recipient-search').value.trim().toLowerCase(),items=state.recipients.filter(r=>[r.name,r.phone_tail,r.bank_name,r.account_tail].some(value=>(value||'').toLowerCase().includes(query)));
 $('recipient-count').textContent=query?`当前显示 ${items.length} 位，共 ${state.recipients.length} 位收款人`:`全部 ${state.recipients.length} 位收款人`;$('clear-recipient-search').hidden=!query;
 $('recipient-list').replaceChildren(...items.map(r=>{const card=el('div',undefined,'subscription-card recipient-card');card.append(el('h2',r.name),el('p',`手机尾号 ${r.phone_tail}`));if(r.bank_name)card.append(el('p',`${r.bank_name} · 账号尾号 ${r.account_tail}`));card.append(button('向此人转账',()=>{if(state.processing)return toast('请等当前处理结束。');newConversation();return askAssistant(r.account_tail?`我想给收款账号尾号${r.account_tail}的${r.name}转账，请先问我转账金额。`:`我想给尾号${r.phone_tail}的${r.name}转账，请先问我转账金额。`);}));return card;}));
 if(!items.length)$('recipient-list').append(el('p','没有匹配的已保存收款人。','muted'));
}
$('recipient-search').addEventListener('input',renderRecipients);
$('clear-recipient-search').addEventListener('click',()=>{$('recipient-search').value='';renderRecipients();$('recipient-search').focus();});
BankNumericInputs.bind($('recipient-phone'),$('recipient-phone-hint'),{max:11,hint:'中国大陆手机号：11位，1开头，第二位为3—9。',validate:value=>!value?'请填写手机号':value.length!==11?'手机号需要11位数字。':!/^1[3-9][0-9]{9}$/.test(value)?'格式不正确：应以1开头，第二位为3—9。':''});
BankNumericInputs.bind($('recipient-account'),$('recipient-account-hint'),{max:32,hint:'12至32位数字，可粘贴带分隔空格的账号。',validate:value=>/^[0-9]{12,32}$/.test(value)?'':'收款账号需要12至32位数字。'});
$('recipient-check-status').addEventListener('click',async()=>{try{await refresh();}catch{toast('暂时无法核对，请恢复连接后再试。');}});
$('recipient-form').addEventListener('submit',async e=>{
 e.preventDefault();if(state.recipientSaveUnknown)return;const form=e.currentTarget,submit=form.querySelector('button[type=submit]');submit.disabled=true;$('recipient-save-status').textContent='正在保存…';
 const payload={name:$('recipient-name').value.trim(),bank_name:$('recipient-bank').value.trim(),account_number:$('recipient-account').value.trim(),phone:$('recipient-phone').value.trim(),confirm:$('recipient-confirm').checked},beforeIds=new Set(state.recipients.map(r=>r.id));
 function saved(){form.reset();$('recipient-editor').open=false;$('recipient-search').value='';$('recipient-save-status').textContent='';}
 try{await api('/api/recipients','POST',payload);saved();try{await refresh();}catch{toast('收款人已保存，列表暂未更新，请稍后刷新。');return;}toast('收款人已保存。');}
 catch(error){
  if(error.code){$('recipient-save-status').textContent=error.message;}
  else{
   try{const fresh=await api('/api/recipients');state.recipients=fresh;$('recipient-search').value='';renderRecipients();const matches=fresh.filter(r=>!beforeIds.has(r.id)&&r.name===payload.name&&r.bank_name===payload.bank_name&&r.account_tail===payload.account_number.slice(-4)&&r.phone_tail===payload.phone.slice(-4));if(matches.length===1){saved();toast('已核对，收款人已保存。');}else $('recipient-save-status').textContent='未取得保存回执，列表已更新，请先核对是否已有该收款人。';}
   catch{state.recipientSaveUnknown=true;$('recipient-check-status').hidden=false;$('recipient-save-status').textContent='保存结果暂时无法核对。恢复连接后请先核对结果。';}
  }
 }finally{submit.disabled=state.recipientSaveUnknown;}
});

async function askAssistant(text,options={}){if(state.processing)return toast('请等当前处理结束。');if(state.task&&['completed','cancelled'].includes(state.task.status))newConversation();showView('chat');await send(text,options);}
function moneyField(title,value=''){const label=el('label',title),input=el('input');input.type='number';input.min='0';input.step='0.01';input.value=value;input.required=true;label.append(input);return {label,input};}
function formSubmit(form,title,handler){const submit=el('button',title,'secondary');submit.type='submit';form.append(submit);form.addEventListener('submit',async e=>{e.preventDefault();try{await handler();}catch(error){toast(error.message);}});}
function renderRiskResult(data){
 const target=$('risk-result'),p=data.profile;target.replaceChildren();
 if(!p.completed||!p.assessment_current){target.append(el('h2',p.completed?'请补充完整风险评估':'了解你的投资承受能力'),el('p','完成12项评估，查看风险画像与资金安排建议。','muted'));return;}
 const a=p.assessment;
 target.append(el('h2',`C${a.category} ${a.category_name} · ${p.expired?'评估已过期':`匹配上限 R${p.risk_level}`}`),el('p',`资金期限 ${p.horizon_days}天 · 有效期至 ${p.expires_at}`,'muted'));
 const reasons=el('details',undefined,'context-detail');reasons.append(el('summary','为什么得到这个结果'),el('p',a.reasons.length?a.reasons.join('；'):'依据财务状况、目标、知识、经验和损失承受能力综合评估。'),el('p',`${a.method}。综合分${a.score}，约束条件优先于分数。`,'fineprint'));target.append(reasons);
 const rec=data.recommendation;WealthVisuals.render(target,p,rec);if(!rec?.available){target.append(el('p',rec?.summary||'请重新评估'));return;}
 target.append(el('h3','你的投资安排建议'),el('p',rec.summary));
 const list=el('ul');for(const line of rec.reasons)list.append(el('li',line));target.append(list);
 target.append(el('p',rec.basis,'fineprint'));
 const explain=el('button','和助手讨论这份建议','secondary');explain.type='button';explain.onclick=()=>askAssistant('请读取我最新的风险评估和资金安排建议，解释建议与需要核实的条件。');target.append(explain);
}
function renderRiskQuestions(profile){
 const target=$('risk-extra');if(!target.children.length){
  for(const q of state.riskQuestions||[]){const label=el('label',q.title),select=el('select');select.id='risk-'+q.id;select.required=true;select.append(new Option('请选择',''));for(const option of q.options)select.append(new Option(option.label,option.value));label.append(select);target.append(label);}
 }
 for(const q of state.riskQuestions||[])$('risk-'+q.id).value=profile.assessment?.answers?.[q.id]||'';
 $('risk-monthly').value=profile.assessment?.answers?.monthly_essential_fen?profile.assessment.answers.monthly_essential_fen/100:'';
 updateRiskProgress();
}
function updateRiskProgress(){const fields=[...$('risk-form').querySelectorAll('select,input[type=number]')];$('risk-progress').textContent=`已填写 ${fields.filter(x=>x.value!=='').length} / ${fields.length} 项`;}
$('risk-form').addEventListener('input',updateRiskProgress);
function renderWealth(data){
 const profile=data.profile;renderRiskResult(data);renderRiskQuestions(profile);$('risk-summary').textContent=profile.completed?profile.assessment_current?` · C${profile.assessment.category} ${profile.assessment.category_name}`:' · 请补充评估':' · 尚未完成';
 if(profile.completed){$('risk-loss').value=profile.loss_tolerance;$('risk-experience').value=profile.experience;$('risk-horizon').value=profile.horizon_days;}
 else{$('risk-loss').value='';$('risk-experience').value='';$('risk-horizon').value='';}updateRiskProgress();

}
function renderCards(data){
 $('apply-card').disabled=data.cards.length>=data.max_cards;
 $('bank-cards').replaceChildren(...data.cards.map(c=>{
  const box=el('div',undefined,'surface'),top=el('div',undefined,'card-state');top.append(el('h2',`信用卡 · ${c.tail}`),el('span',({active:'可使用',frozen:'已冻结',lost:'已挂失'})[c.status],'status'));box.append(top);
  const dl=el('dl');for(const [title,value] of [['总额度',money(c.credit_limit_fen)],['待还款',money(c.used_fen)],['可用额度',money(c.available_credit_fen)],['单笔支付限额',money(c.single_limit_fen)],['每日支付限额',money(c.daily_limit_fen)],['今日已刷卡',money(c.today_spent_fen)],['线上交易',c.online_enabled?'允许':'限制']])detail(dl,title,value);box.append(dl);
  const actions=el('div',undefined,'action-buttons');if(c.status!=='lost'){
   actions.append(button(c.status==='active'?'冻结卡片':'解锁卡片',()=>askAssistant(`请${c.status==='active'?'冻结':'解锁'}尾号${c.tail}的信用卡。`)),button('挂失卡片',()=>askAssistant(`请挂失尾号${c.tail}的信用卡。`)),button(c.online_enabled?'限制线上交易':'允许线上交易',()=>askAssistant(`请${c.online_enabled?'限制':'允许'}尾号${c.tail}的信用卡线上支付。`)));
  }box.append(actions);
  if(c.status!=='lost'){
   const limits=el('details',undefined,'card-tools');limits.append(el('summary','额度与支付限额'));const form=el('form'),credit=moneyField('总额度（元）',String(c.credit_limit_fen/100)),single=moneyField('单笔限额（元）',String(c.single_limit_fen/100)),daily=moneyField('日限额（元）',String(c.daily_limit_fen/100));form.append(credit.label,single.label,daily.label);formSubmit(form,'交给助手核对额度',()=>askAssistant(`把尾号${c.tail}的信用卡总额度改成${credit.input.value}元、单笔支付限额${single.input.value}元、每日支付限额${daily.input.value}元。`));limits.append(form);box.append(limits);
  }
  if(c.status==='active'){
   const pay=el('details',undefined,'card-tools');pay.append(el('summary','模拟刷卡 · 验证支付限制'));const form=el('form'),merchantLabel=el('label','模拟商户'),merchant=el('select'),amount=moneyField('消费金额（元）'),channelLabel=el('label','支付渠道'),channel=el('select');channel.setAttribute('aria-label','支付渠道');merchant.required=true;merchant.setAttribute('aria-label','模拟商户');for(const m of data.merchants||[]){const option=el('option',`${m.name} · ${m.category}`);option.value=m.id;option.dataset.name=m.name;merchant.append(option);}merchantLabel.append(merchant);for(const [value,title] of [['online','线上'],['offline','线下']]){const option=el('option',title);option.value=value;channel.append(option);}channelLabel.append(channel);amount.input.min='0.01';form.append(merchantLabel,amount.label,channelLabel);formSubmit(form,'交给助手核对消费',()=>askAssistant(`使用尾号${c.tail}的信用卡在${merchant.selectedOptions[0].dataset.name}${channel.value==='online'?'线上':'线下'}消费${amount.input.value}元。`));pay.append(el('p','请选择付款商户与渠道。','fineprint'),form);box.append(pay);
  }
  if(c.used_fen>0){const repay=el('details',undefined,'card-tools');repay.append(el('summary','归还信用卡本金'));const form=el('form'),amount=moneyField('还款金额（元）'),reserve=moneyField('现金至少保留（元）','0');amount.input.min='0.01';amount.input.max=String(c.used_fen/100);form.append(amount.label,reserve.label);formSubmit(form,'交给助手核对还款',()=>askAssistant(`给尾号${c.tail}的信用卡还款${amount.input.value}元，现金至少保留${reserve.input.value}元。`));repay.append(form);box.append(repay);}
  return box;
 }));
}
$('risk-form').addEventListener('submit',async e=>{
 e.preventDefault();if(state.processing)return toast('请等当前处理结束后再填写问卷。');const submit=e.submitter;submit.disabled=true;
 try{const assessment=Object.fromEntries((state.riskQuestions||[]).map(q=>[q.id,$('risk-'+q.id).value]));const value=$('risk-monthly').value;if(!/^\d+(?:\.\d{1,2})?$/.test(value))throw new Error('每月必要支出最多填写两位小数');assessment.monthly_essential_fen=parseMoney(value);await api('/api/wealth/risk-profile','POST',{assessment,loss_tolerance:$('risk-loss').value,experience:$('risk-experience').value,horizon_days:Number($('risk-horizon').value),confirm:$('risk-consent').checked});$('risk-consent').checked=false;$('risk-questionnaire').open=false;await refresh();toast('评估已完成，投资安排建议已更新。');}catch(error){toast(error.message);}finally{submit.disabled=false;}
});
$('apply-card').addEventListener('click',()=>askAssistant('请为我申请一张新的模拟信用卡。'));
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();if(!state.processing)$('chat-form').requestSubmit();}});

document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
document.querySelectorAll('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{$('message').value=b.dataset.prompt;$('message').focus();}));
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{await enter(await api('/api/login','POST',{username:$('username').value,password:$('password').value}));}catch(e){$('login-error').textContent=e.message;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout','POST',{});location.reload();}catch(e){toast(e.message);}});
$('chat-form').addEventListener('submit',async e=>{e.preventDefault();const text=$('message').value.trim();if(!text)return;$('message').value='';await send(text);});
function newConversation(){if(state.user)localStorage.removeItem('bank-task-'+state.user.id);state.pendingMessage=null;state.task=null;$('task-details').open=false;$('last-result').replaceChildren();$('past-actions').replaceChildren();$('recovery-bar').hidden=true;$('conversation').replaceChildren(el('p','新对话。请输入你的需求。','muted'));$('actions').replaceChildren();$('task-steps').replaceChildren();$('task-status').textContent='未开始';$('continue-task').disabled=true;$('resume-task').disabled=true;$('cancel-task').disabled=true;$('task-select').value='';}
$('new-task').addEventListener('click',newConversation);
$('task-select').addEventListener('change',async()=>{if(!$('task-select').value)return;try{state.pendingMessage=null;renderTask(await api('/api/tasks/'+$('task-select').value));}catch(e){toast(e.message);}});
$('refresh').addEventListener('click',async()=>{try{await refresh();await reloadTask();toast('数据已更新。');}catch(e){toast(e.message);}});
$('bill-form').addEventListener('submit',async e=>{e.preventDefault();try{await loadBills();}catch(e){toast(e.message);}});
$('send-selection').addEventListener('click',async()=>{const selected=[...document.querySelectorAll('[data-subscription]:checked')].map(x=>state.subs.find(s=>s.id===x.value));if(!selected.length)return toast('请先选择订阅');showView('chat');await send('取消'+selected.map(s=>s.merchant).join('、')+'的续费。');});
$('continue-task').addEventListener('click',()=>continueTask());
$('resume-task').addEventListener('click',()=>continueTask(true));
$('cancel-task').addEventListener('click',async()=>{try{renderTask(await api('/api/tasks/'+state.task.id+'/cancel','POST',{}));toast('未执行步骤已停止；已执行结果保留');}catch(e){toast(e.message);}});
$('load-audit').addEventListener('click',async()=>{try{$('audit').textContent=JSON.stringify(await api('/api/audit'+(state.task?'?task_id='+state.task.id:'')),null,2);}catch(e){toast(e.message);}});
(async()=>{try{state.meta=await api('/api/meta');if(state.meta.scenario_mode){const select=$('username');select.replaceChildren(...state.meta.demo_accounts.map(a=>{const option=el('option',a.name);option.value=a.username;return option;}));document.querySelector('#login-form .muted').textContent='选择一种财务情境，资料、账本和操作分别保存。';$('reset-panel').querySelector('p:nth-of-type(2)').textContent='恢复当前情境的初始账本、收款人、订阅、卡片、持仓与财务资料；清除本账户的对话和操作。其他情境不变，重置前保存本机备份。';}try{await enter(await api('/api/session'));}catch{/* 未登录 */}}catch(e){$('login-error').textContent=e.message;}})();

$('reset-confirm').addEventListener('change',()=>{$('reset-demo').disabled=!$('reset-confirm').checked||state.processing;});
$('demo-reset-form').addEventListener('submit',async e=>{
 e.preventDefault();if(state.processing)return toast('请等当前处理结束后再重置。');if(!$('reset-confirm').checked)return;
 $('reset-demo').disabled=true;
 try{await busy(async()=>{
  let result;try{result=await api('/api/demo/reset','POST',{confirm:true});}catch(error){toast('未取得重置结果：'+error.message+'。请先刷新核对。');return;}
  state.advancePending={};sessionStorage.removeItem('bank-advance-'+state.user.id);localStorage.removeItem('bank-task-'+state.user.id);newConversation();$('reset-confirm').checked=false;$('reset-panel').open=false;
  try{await refresh();showView('chat');toast('当前模拟账户已重置，初始余额 '+money(result.account.balance_fen)+'。其他用户未改变，备份保存在本机。');}catch(error){toast('重置已完成，请刷新页面加载初始数据。');}
 },false,'正在重置当前模拟账户…');}finally{$('reset-demo').disabled=!$('reset-confirm').checked;}
});

setInterval(()=>{if(state.user&&!state.processing){api('/api/meta').then(meta=>{if(meta.business_date!==state.meta.business_date)refresh().catch(e=>toast(e.message));}).catch(()=>{});}},60000);

function renderBankProducts(data){
 state.bankProducts=data;const status=$('bank-products-status');
 status.textContent=data.available?`来源：${data.source} · 抓取时间 ${data.fetched_at}${data.stale?' · 缓存已超过24小时':''}${data.refresh_failed?' · 更新失败，显示上次成功记录':''}${data.partial?' · 部分产品未能读取':''}`:(data.message||'暂时没有可核实的产品数据');
 const query=$('bank-products-search').value.trim(),rows=(data.products||[]).filter(p=>p.name.includes(query)||(p.code||'').includes(query));
 $('bank-products').replaceChildren(...rows.map(p=>{const card=el('div',undefined,'subscription-card');card.dataset.bankProductId=p.id;
 card.append(el('h3',p.name),el('p',`产品代码 ${p.code||'未披露'} · ${p.provider}`),el('p',`官网风险描述：${p.risk_description||'待核实'} · 期限 ${p.term||'待核实'}`),el('p',`业绩比较基准 ${p.benchmark||'未披露'}`),el('p',`募集期 ${p.offering_period||'待核实'} · 到期日 ${p.maturity_date||'待核实'}`),el('p',p.nav?`披露净值 ${p.nav} · 净值日期 ${p.nav_date}`:'净值：官网暂未披露'));
 const link=el('a','查看银行原文');try{const url=new URL(p.source_url);if(url.origin==='https://www.nanhaibank.com'&&url.pathname==='/java/www/viewFinancial.jspx'){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}}catch{}
 const ask=el('button','请助手解释产品要点','secondary');ask.type='button';ask.onclick=()=>askAssistant(`请查询银行官网缓存中代码${p.code||p.id}的真实理财产品，解释期限、风险原文、基准与净值日期，指出尚未核实的条件；只查询，不办理任何交易。`,{read_only:true});card.append(ask);return card;}));
 if(data.available&&!rows.length)$('bank-products').append(el('p','没有匹配的产品。'));
}
async function loadBankProducts(){try{renderBankProducts(await api('/api/wealth/bank-products'));}catch{$('bank-products-status').textContent='产品数据暂时不可用，其他账户业务不受影响。';}}
$('bank-products-search').addEventListener('input',()=>{if(state.bankProducts)renderBankProducts(state.bankProducts);});
$('bank-products-refresh').addEventListener('click',async()=>{const b=$('bank-products-refresh');b.disabled=true;$('bank-products-status').textContent='正在核对银行官网，请稍候…';try{const d=await api('/api/wealth/bank-products/refresh','POST',{});renderBankProducts(d);if(d.refresh_status==='cooldown')toast('刚刚更新过，请稍后再试。');}catch{$('bank-products-status').textContent='更新未完成，可稍后重试。';}finally{b.disabled=false;}});
