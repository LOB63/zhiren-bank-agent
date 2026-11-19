'use strict';
// 输入限制改善体验，服务端仍独立校验最终保存的值。
window.BankNumericInputs=(()=>{
 const digits=value=>value.replace(/[０-９]/g,c=>String(c.charCodeAt(0)-0xff10)).replace(/\s/g,'');
 function bind(input,feedback,{max,validate,hint}){
  const show=(message,error=false)=>{feedback.textContent=message;feedback.classList.toggle('field-error',error);};
  function check(){
   const error=validate(input.value);input.setCustomValidity(error);input.setAttribute('aria-invalid',String(!!error&&!!input.value));
   show(error&&input.value?error:hint,!!error&&!!input.value);return !error;
  }
  function insert(text){
   const cleaned=digits(text);
   if(!/^[0-9]*$/.test(cleaned)){show('只能输入数字，请检查后重试。',true);return;}
   const start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
   if(input.value.length-(end-start)+cleaned.length>max){show(`最多${max}位数字，请检查完整号码。`,true);return;}
   input.setRangeText(cleaned,start,end,'end');input.dispatchEvent(new Event('input',{bubbles:true}));
  }
  input.addEventListener('beforeinput',event=>{
   if(event.isComposing||event.inputType!=='insertText'||!event.data)return;
   if(!/^[0-9]+$/.test(event.data)){event.preventDefault();if(/^[０-９]+$/.test(event.data))insert(event.data);else show('只能输入数字。',true);}
  });
  input.addEventListener('paste',event=>{event.preventDefault();insert(event.clipboardData.getData('text'));});
  input.addEventListener('input',()=>{
   // IME、自动填充等路径也要检查；不把含字母的整段内容悄悄改成另一号码。
   const raw=input.value,cleaned=digits(raw);
   if(!/^[0-9]*$/.test(cleaned)||cleaned.length>max){input.value='';input.setCustomValidity('请输入正确的数字号码');show('号码含无效字符或过长，请重新输入。',true);input.setAttribute('aria-invalid','true');return;}
   input.value=cleaned;check();
  });
  input.addEventListener('blur',check);input.addEventListener('invalid',()=>{check();show(input.validationMessage,true);});
  input.form?.addEventListener('reset',()=>{input.setCustomValidity('');input.removeAttribute('aria-invalid');show(hint);});
  show(hint);return {check};
 }
 return {bind};
})();
