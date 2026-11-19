'use strict';
const $=id=>document.getElementById(id);
let csrf='',processing=false;
async function request(path,body){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(data.error?.message||'请求失败，请核对登录状态与参数');return data;
}
function fen(value){const text=String(value);if(!/^\d+(\.\d{1,2})?$/.test(text))throw new Error('金额需为非负数字，最多两位小数');const [whole,fraction='']=text.split('.');const result=Number(whole)*100+Number(fraction.padEnd(2,'0'));if(!Number.isSafeInteger(result))throw new Error('金额超出范围');return result;}
async function createDraft(kind,params){
 if(processing)return;processing=true;$('dev-controls').inert=true;
 const output=$('dev-result-text');$('dev-result').hidden=false;output.textContent='正在创建待确认草稿…';
 try{
  const task=await request('/api/manual/tasks',{text:kind==='transfer'?'检验转账确认与记账':'检验订阅取消'});
  const result=await request('/api/manual/actions',{task_id:task.id,kind,params});
  output.textContent=result.repeat_confirmation_required?'发现相同转账，请返回助手核对是否重新发起。当前未扣款。':'待确认草稿已创建，请返回助手核对操作卡。当前账户尚未执行这项操作。';
 }catch(e){output.textContent='未取得完整草稿结果：'+e.message+'。请返回助手核对历史记录后再试。';}
 finally{processing=false;$('dev-controls').inert=false;}
}
$('manual-transfer').addEventListener('submit',async e=>{e.preventDefault();try{await createDraft('transfer',{recipient_id:$('manual-recipient').value,amount_fen:fen($('manual-amount').value),reserve_fen:fen($('manual-reserve').value)});}catch(error){$('dev-result').hidden=false;$('dev-result-text').textContent=error.message;}});
$('manual-subscription').addEventListener('submit',async e=>{e.preventDefault();if($('manual-subscription-id').value)await createDraft('cancel_subscription',{subscription_id:$('manual-subscription-id').value});});
(async()=>{
 try{
  const meta=await request('/api/meta');$('dev-model').textContent=`服务版本 ${meta.version} · ${meta.api_configured?'模型已配置':'模型未配置'} · 业务日期 ${meta.business_date}`;
  const session=await request('/api/session');csrf=session.csrf;
  const [recipients,subs]=await Promise.all([request('/api/recipients'),request('/api/subscriptions')]);
  for(const r of recipients){const option=document.createElement('option');option.value=r.id;option.textContent=`${r.name} · 尾号${r.phone_tail}`;$('manual-recipient').append(option);}
  for(const s of subs.catalog.filter(s=>s.status==='active')){const option=document.createElement('option');option.value=s.id;option.textContent=s.merchant;$('manual-subscription-id').append(option);}
  $('manual-cancel').disabled=!$('manual-subscription-id').options.length;
  $('dev-session').textContent=`当前账户：${session.user.name}（${session.user.id}）`;$('dev-controls').hidden=false;
 }catch(e){$('dev-session').textContent='请先返回智人银行登录，再打开本页。'+e.message;}
})();
