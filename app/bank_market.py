"""Bounded read-only adapter for a fixed official bank source, never a trade API."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import threading
import time
import uuid
import httpx
from pydantic import BaseModel, ConfigDict, Field

HOST='https://www.nanhaibank.com'
LIST_URL=HOST+'/java/www/searchFinancial.jspx?channelId=1914'
CST=timezone(timedelta(hours=8))

class Product(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^bank:[0-9]{1,12}$')
    name: str = Field(min_length=1,max_length=160)
    code: str | None = Field(default=None,max_length=40)
    risk_description: str | None = Field(default=None,max_length=80)
    term: str | None = Field(default=None,max_length=80)
    offering_period: str | None = Field(default=None,max_length=80)
    benchmark: str | None = Field(default=None,max_length=80)
    maturity_date: str | None = Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    nav_date: str | None = Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    nav: str | None = Field(default=None,pattern=r'^\d{1,7}(?:\.\d{1,10})?$')
    source_url: str = Field(max_length=220)
    provider: str = '南海农商银行官网'
    tradable: bool = False

    def checked(self):
        # Source URLs always come from a canonical ID, never upstream hrefs.
        if self.source_url!=product_url(self.id.split(':')[1]) or self.tradable:
            raise ValueError('Invalid source or trading flag')
        for v in (self.nav_date,self.maturity_date):
            if v:date.fromisoformat(v)
        return self.model_dump()

def product_url(cid):
    if not re.fullmatch(r'[0-9]{1,12}',cid):raise ValueError('Invalid bank product identifier')
    return HOST+'/java/www/viewFinancial.jspx?channelId=1914&contentId='+cid

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.row=[];self.cell=None;self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
        if tag=='tr':self.row=[]
        if tag in ('td','th'):self.cell=[]
    def handle_data(self,text):
        if self.cell is not None and not self.skip:self.cell.append(text)
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
        if tag in ('td','th') and self.cell is not None:
            self.row.append(re.sub(r'\s+',' ',''.join(self.cell)).strip());self.cell=None
        if tag=='tr' and self.row:self.rows.append(self.row)

def parse_product(html,cid):
    parser=Tables();parser.feed(html);fields={};navs=[]
    for row in parser.rows:
        for i in range(0,len(row)-1,2):
            key=row[i].strip('：: ')
            if key not in fields or len(row[i+1])<len(fields[key]):fields[key]=row[i+1]
        if len(row)>=2 and re.fullmatch(r'\d{8}',row[0]):
            try:
                day=date.fromisoformat(row[0][:4]+'-'+row[0][4:6]+'-'+row[0][6:])
                value=Decimal(row[1])
                if day<=datetime.now(CST).date() and value.is_finite() and 0<value<1000000 and re.fullmatch(r'\d{1,7}(?:\.\d{1,10})?',row[1]):navs.append((day.isoformat(),row[1]))
            except (ValueError,InvalidOperation):pass
    name=fields.get('产品名称')
    if not name:raise ValueError('Bank source schema changed')
    code=re.match(r'[A-Za-z0-9-]{1,40}',fields.get('产品代码',''))
    benchmark=re.match(r'[0-9.%％~～至\-—+ ]{1,70}',fields.get('业绩比较基准',''))
    maturity=fields.get('到期日','').replace('.','-')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',maturity):maturity=None
    latest=max(navs) if navs else (None,None)
    return Product(id='bank:'+cid,name=name,code=code[0] if code else None,
        risk_description=fields.get('产品风险等级') or None,term=fields.get('产品期限') or None,
        offering_period=fields.get('募集期') or None,benchmark=benchmark[0].strip() if benchmark else None,
        maturity_date=maturity,nav_date=latest[0],nav=latest[1],source_url=product_url(cid)).checked()

def fetch_html(client,url):
    # No user URLs, redirects, credentials or account data are accepted.
    if url!=LIST_URL and not re.fullmatch(re.escape(HOST)+r'/java/www/viewFinancial\.jspx\?channelId=1914&contentId=[0-9]{1,12}',url):raise ValueError('Source not allowed')
    started=time.monotonic()
    with client.stream('GET',url,follow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code!=200:raise ValueError('Unexpected source response')
        chunks=[];size=0
        for chunk in response.iter_bytes():
            if time.monotonic()-started>8:raise ValueError('Bank response exceeded time budget')
            size+=len(chunk)
            if size>512*1024:raise ValueError('Bank response too large')
            chunks.append(chunk)
        return b''.join(chunks).decode('utf-8',errors='replace')

def fetch_catalog():
    started=time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(5.0),trust_env=False,headers={'User-Agent':'ZhirenBank-Research/1.0'}) as client:
        html=fetch_html(client,LIST_URL)
        ids=list(dict.fromkeys(re.findall(r'viewFinancial\.jspx\?channelId=1914(?:&amp;|&)contentId=([0-9]{1,12})',html)))[:6]
        if not ids:raise ValueError('Bank listing schema changed')
        products=[];failures=0
        for cid in ids:
            if time.monotonic()-started>35:failures+=1;break
            try:products.append(parse_product(fetch_html(client,product_url(cid)),cid))
            except (ValueError,httpx.HTTPError):failures+=1
        if not products:raise ValueError('No verified bank products')
        return {'schema_version':1,'fetched_at':datetime.now(CST).isoformat(timespec='seconds'),
                'source':'银行官网直连','source_url':LIST_URL,'products':products,'partial':bool(failures),
                'notice':'公开披露数据，不代表当前在售或可以申购；风险描述保留官网原文，未自行映射R等级。业绩比较基准不等于收益承诺。'}

class BankCatalog:
    def __init__(self,path,fetcher=fetch_catalog):
        self.path=Path(path);self.fetcher=fetcher;self.lock=threading.Lock();self.last_attempt=None;self.last_error=False
    def read(self,query=''):
        try:
            if self.path.stat().st_size>1024*1024:raise ValueError()
            d=json.loads(self.path.read_text(encoding='utf-8'))
            if d['schema_version']!=1 or not 0<len(d['products'])<=6:raise ValueError()
            products=[Product.model_validate(p).checked() for p in d['products']]
            stamp=datetime.fromisoformat(d['fetched_at']);age=(datetime.now(CST)-stamp).total_seconds()
            if age<0:raise ValueError()
            # Only explicitly allowed fields leave the cache boundary.
            return {'available':True,'source':'银行官网直连','source_url':LIST_URL,'fetched_at':stamp.isoformat(),
                    'stale':age>86400,'refresh_failed':self.last_error,'partial':bool(d.get('partial')),
                    'products':[p for p in products if query in p['name'] or query in (p['code'] or '') or query==p['id']],
                    'total':len(products),'notice':'公开披露，不代表当前在售；风险原文未映射R等级，基准不代表收益承诺。'}
        except (OSError,ValueError,KeyError,TypeError):
            return {'available':False,'products':[],'total':0,'source':'银行官网直连','refresh_failed':self.last_error,'message':'尚无可验证的银行产品缓存，请更新数据。'}
    def refresh(self):
        if not self.lock.acquire(blocking=False):return {**self.read(),'refresh_status':'busy'}
        try:
            if self.last_attempt is not None and time.monotonic()-self.last_attempt<60:return {**self.read(),'refresh_status':'cooldown'}
            self.last_attempt=time.monotonic()
            try:
                data=self.fetcher()
                # Validate before replacing a previous successful snapshot.
                if not 0<len(data['products'])<=6:raise ValueError()
                stamp=datetime.fromisoformat(data['fetched_at'])
                if stamp.tzinfo is None or stamp>datetime.now(CST):raise ValueError('Invalid fetched timestamp')
                data['products']=[Product.model_validate(p).checked() for p in data['products']]
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                try:
                    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(self.path)
                finally:
                    if temp.exists():temp.unlink()
                self.last_error=False
            except (ValueError,KeyError,TypeError,OSError,httpx.HTTPError):self.last_error=True
            return {**self.read(),'refresh_status':'failed' if self.last_error else 'updated'}
        finally:self.lock.release()
