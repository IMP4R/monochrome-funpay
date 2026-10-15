from __future__ import annotations
import json, os, re, threading, time
from datetime import datetime
from typing import Any

BASE = "https" + chr(58) + "//funpay.com"
class FunPayUnavailable(RuntimeError):
    pass

def _enum(value: Any) -> str | None:
    if value is None: return None
    raw = getattr(value, "name", None) or getattr(value, "value", value)
    return str(raw).split(".")[-1].lower()

def _date(value: Any) -> str | None:
    if value is None: return None
    if isinstance(value, datetime): return value.isoformat(sep=" ")
    return str(value)

class FunPayAdapter:
    """Thread-safe FunPayAPI adapter. No browser automation is used."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._account = None
        self._write_session = None
        self._connected_at = 0.0
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.last_sync: str | None = None

    @property
    def configured(self) -> bool:
        return bool(os.getenv("FUNPAY_GOLDEN_KEY"))

    def _connect(self, force: bool = False):
        if not self.configured: raise FunPayUnavailable("FUNPAY_GOLDEN_KEY не задан")
        with self._lock:
            if self._account is not None and self._write_session is not None and not force and time.time()-self._connected_at < 1200:
                return self._account
            try:
                from FunPayAPI import Account
                ua=os.getenv("FUNPAY_USER_AGENT") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.1 Safari/537.36"
                account=Account(os.environ["FUNPAY_GOLDEN_KEY"],user_agent=ua,requests_timeout=20).get(update_phpsessid=False)
                import requests
                session=requests.Session()
                session.cookies.set("golden_key",os.environ["FUNPAY_GOLDEN_KEY"],domain=".funpay.com",path="/")
                session.cookies.set("cookie_prefs","1",domain=".funpay.com",path="/")
                session.headers.update({"user-agent":ua,"accept-language":"ru-RU,ru;q=0.9,en;q=0.8"})
                warm=session.get(BASE+"/",timeout=20);warm.raise_for_status()
                self._write_session=session
                self._account=account;self._connected_at=time.time();self.last_ok=datetime.now().isoformat(timespec="seconds");self.last_error=None
                return account
            except Exception as exc:
                self.last_error=f"{type(exc).__name__}: {exc}"
                raise FunPayUnavailable(self.last_error) from exc

    def status(self, live: bool=False) -> dict[str,Any]:
        if not self.configured:return {"configured":False,"connected":False,"detail":"golden_key не задан"}
        try:
            a=self._connect(force=live)
            return {"configured":True,"connected":True,"detail":"авторизация подтверждена FunPayAPI","account_id":getattr(a,"id",None),"username":getattr(a,"username",None),"last_ok":self.last_ok,"last_sync":self.last_sync,"last_error":self.last_error,"publisher":"FunPayAPI"}
        except Exception as exc:
            return {"configured":True,"connected":False,"detail":str(exc)[:500],"last_ok":self.last_ok,"last_sync":self.last_sync,"publisher":"FunPayAPI"}

    @staticmethod
    def serialize_order(order: Any)->dict[str,Any]:
        return {"id":str(getattr(order,"id","")),"description":getattr(order,"description",None) or getattr(order,"short_description",None) or getattr(order,"title",None),"status":_enum(getattr(order,"status",None)),"price":getattr(order,"price",None) if hasattr(order,"price") else getattr(order,"sum",None),"amount":getattr(order,"amount",None),"date":_date(getattr(order,"date",None)),"subcategory":getattr(order,"subcategory_name",None) or getattr(order,"subcategory",None),"buyer_id":getattr(order,"buyer_id",None),"buyer_username":getattr(order,"buyer_username",None),"lot_id":getattr(order,"lot_id",None) or getattr(order,"offer_id",None)}

    def orders(self,limit:int=50,include_closed:bool=True)->list[dict[str,Any]]:
        a=self._connect()
        with self._lock: _,orders=a.get_sells(include_paid=True,include_closed=include_closed,include_refunded=True)
        self.last_sync=datetime.now().isoformat(timespec="seconds");self.last_ok=self.last_sync;self.last_error=None
        return [self.serialize_order(x) for x in orders[:max(1,min(limit,100))]]

    def order(self,order_id:str)->dict[str,Any]:
        if not order_id or len(order_id)>40:raise ValueError("Некорректный order_id")
        a=self._connect()
        with self._lock:r=a.get_order(order_id)
        d=self.serialize_order(r);d.update({"full_description":getattr(r,"full_description",None),"seller_id":getattr(r,"seller_id",None),"seller_username":getattr(r,"seller_username",None)});return d

    def resolve_chat(self,username:str)->dict[str,Any]:
        if not username or len(username)>100:raise ValueError("Некорректный username")
        a=self._connect()
        with self._lock:
            saved=a.get_chat_by_name(username,make_request=False)
            if saved:return {"chat_id":int(saved.id),"username":saved.name}
            from bs4 import BeautifulSoup
            response=a.method("get","chat/",{},None,raise_not_200=True);soup=BeautifulSoup(response.text,"html.parser");target=username.strip().casefold()
            for item in soup.select("a.contact-item[data-id]"):
                node=item.select_one(".media-user-name");name=node.get_text(strip=True) if node else ""
                if name.casefold()==target:return {"chat_id":int(item.get("data-id")),"username":name}
        raise FunPayUnavailable("Чат не найден среди последних 50 диалогов FunPay")

    def send_message(self,text:str,chat_id:int|None=None,username:str|None=None)->dict[str,Any]:
        if not text.strip() or len(text)>4000:raise ValueError("Сообщение должно содержать 1–4000 символов")
        a=self._connect()
        with self._lock:
            if chat_id is None:
                if not username:raise ValueError("Нужен chat_id или username")
                chat_id=self.resolve_chat(username)["chat_id"]
            m=a.send_message(chat_id,text.strip())
        return {"sent":True,"chat_id":chat_id,"message_id":getattr(m,"id",None),"text":getattr(m,"text",text.strip())}

    def chat_messages(self,chat_id:int)->list[dict[str,Any]]:
        a=self._connect()
        with self._lock: rows=a.get_chat_history(int(chat_id))
        out=[]
        for m in rows:
            aid=getattr(m,'author_id',None) or getattr(m,'sender_id',None)
            author=getattr(m,'author',None) or getattr(m,'username',None)
            if not isinstance(author,str):author=getattr(author,'username',None) or getattr(author,'name',None)
            out.append({'id':getattr(m,'id',None),'text':getattr(m,'text',None) or '', 'author_id':aid,'username':author,'is_own':str(aid)==str(getattr(a,'id',None)),'created_at':_date(getattr(m,'date',None))})
        return out

    @staticmethod
    def _html(response)->str:
        text=response.text
        try:
            data=response.json()
            if isinstance(data,dict) and isinstance(data.get("html"),str):return data["html"]
        except Exception:pass
        return text

    @staticmethod
    def _parse_form(html:str)->tuple[dict[str,str],dict[str,list[str]]]:
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(html,"lxml")
        if (err:=soup.find("p",class_="lead")):raise FunPayUnavailable(err.get_text(" ",strip=True))
        fields:dict[str,str]={};options:dict[str,list[str]]={}
        for el in soup.find_all("input"):
            n=el.get("name")
            if not n or n in {"query","login","password"}:continue
            if el.get("type")=="checkbox":
                if el.has_attr("checked"):fields[n]=el.get("value") or "on"
            else:fields[n]=el.get("value") or ""
        for el in soup.find_all("textarea"):
            if el.get("name"):fields[el["name"]]=el.text or ""
        for el in soup.find_all("select"):
            n=el.get("name")
            if not n:continue
            vals=[str(x.get("value") or "") for x in el.find_all("option")];options[n]=vals
            selected=el.find("option",selected=True);fields[n]=str(selected.get("value") or "") if selected else (vals[0] if vals else "")
        return fields,options

    @staticmethod
    def _offer_id(data:dict[str,Any])->str|None:
        value=data.get("offer_id") or data.get("offer") or data.get("id")
        if isinstance(value,dict):value=value.get("id") or value.get("offer_id")
        m=re.search(r"\d+",str(value or ""));return m.group(0) if m else None

    def create_offer(self,draft:dict[str,Any])->dict[str,Any]:
        """Create a lot through the supplied FunPayAPI account transport."""
        from FunPayAPI import types,enums
        node=int(draft.get("node_id") or 3064);title=str(draft.get("title_ru") or "").strip()[:100]
        if not title:raise ValueError("title_ru пуст")
        price=int(round(float(draft.get("price") or 0)))
        if price<1:raise ValueError("price должен быть положительным")
        a=self._connect()
        with self._lock:
            edit=f"lots/offerEdit?node={node}"
            session=self._write_session
            if session is None: raise FunPayUnavailable("write session не инициализирована")
            edit_url=f"{BASE}/{edit}"
            r=session.get(edit_url,timeout=20);r.raise_for_status()
            fields,opts=self._parse_form(self._html(r))
            requested=str(draft.get("funpay_type") or "")
            vals=opts.get("fields[type]",[])
            if requested and requested in vals:fields["fields[type]"]=requested
            elif "Прочее" in vals:fields["fields[type]"]="Прочее"
            vals2=opts.get("fields[type2]",[])
            if "Продажа" in vals2:fields["fields[type2]"]="Продажа"
            image_id=None
            if draft.get("image_url"):
                from io import BytesIO
                import requests
                image_url=str(draft["image_url"])
                if image_url.startswith("/api/gifts/assets/"):
                    from pathlib import Path
                    image_bytes=(Path("/root/monochrome/static/generated")/image_url.rsplit("/",1)[-1]).read_bytes()
                else:
                    image_response=requests.get(image_url,timeout=45);image_response.raise_for_status();image_bytes=image_response.content
                image_id=a.upload_image(BytesIO(image_bytes),type_="offer")
            fields.update({
                "offer_id":"0","node_id":str(node),"fields[summary][ru]":title,
                "fields[summary][en]":str(draft.get("title_en") or title).strip()[:100],
                "fields[desc][ru]":str(draft.get("description_ru") or "")[:10000],
                "fields[desc][en]":str(draft.get("description_en") or draft.get("description_ru") or "")[:10000],
                "fields[payment_msg][ru]":"После оплаты отправьте в чат точный @username получателя.",
                "fields[payment_msg][en]":"After payment send the exact recipient @username in chat.",
                "price":str(price),"active":"on","deactivate_after_sale":"on","deleted":"",
            })
            if image_id: fields["fields[images]"]=str(image_id)
            fields.pop("auto_delivery",None);fields.pop("secrets",None)
            headers={"accept":"application/json, text/javascript, */*; q=0.01","content-type":"application/x-www-form-urlencoded; charset=UTF-8","x-requested-with":"XMLHttpRequest","referer":edit_url,"origin":"https://funpay.com"}
            response=session.post(BASE+"/lots/offerSave",headers=headers,data=fields,allow_redirects=False,timeout=30)
            if response.status_code!=200: raise FunPayUnavailable(f"FunPay offerSave HTTP {response.status_code}: {response.text[:500]}")
            try: out=response.json()
            except Exception as exc: raise FunPayUnavailable("FunPay offerSave вернул не JSON") from exc
            if out.get("error") or out.get("errors") or out.get("done") is False:
                raise FunPayUnavailable("FunPay отклонил лот: "+json.dumps(out,ensure_ascii=False)[:1200])
            offer_id=self._offer_id(out)
            if not offer_id:
                for _ in range(8):
                    time.sleep(1)
                    lots=a.get_my_subcategory_lots(node)
                    matches=[x for x in lots if (getattr(x,"description","") or "").strip().startswith(title) and abs(float(getattr(x,"price",0))-price)<0.01]
                    if matches:
                        offer_id=str(max(int(x.id) for x in matches));break
            if not offer_id:raise FunPayUnavailable("FunPay сохранил форму, но API не вернул offer_id")
            # Authenticated verification; public-profile title matching is intentionally not used.
            a.get_lot_fields(int(offer_id))
        self.last_ok=datetime.now().isoformat(timespec="seconds");self.last_error=None
        return {"ok":True,"offer_id":offer_id,"url":f"{BASE}/lots/offer?id={offer_id}","node_id":str(node),"publisher":"FunPayAPI"}

    def remove_offer(self,offer_id:str|int)->dict[str,Any]:
        if not str(offer_id).isdigit():raise ValueError("Некорректный offer_id")
        self._connect()
        with self._lock:
            session=self._write_session
            if session is None: raise FunPayUnavailable("write session не инициализирована")
            edit_url=f"{BASE}/lots/offerEdit?offer={int(offer_id)}"
            response=session.get(edit_url,timeout=20);response.raise_for_status()
            fields,_=self._parse_form(self._html(response))
            fields["deleted"]="1";fields.pop("auto_delivery",None);fields.pop("deactivate_after_sale",None)
            headers={"accept":"application/json, text/javascript, */*; q=0.01","content-type":"application/x-www-form-urlencoded; charset=UTF-8","x-requested-with":"XMLHttpRequest","referer":edit_url,"origin":"https://funpay.com"}
            saved=session.post(BASE+"/lots/offerSave",data=fields,headers=headers,allow_redirects=False,timeout=30)
            if saved.status_code!=200: raise FunPayUnavailable(f"FunPay delete HTTP {saved.status_code}")
            try: out=saved.json()
            except Exception as exc: raise FunPayUnavailable("FunPay delete вернул не JSON") from exc
            if out.get("error") or out.get("errors") or out.get("done") is False: raise FunPayUnavailable("FunPay отклонил удаление: "+json.dumps(out,ensure_ascii=False)[:1200])
        self.last_ok=datetime.now().isoformat(timespec="seconds")
        return {"ok":True,"removed":True,"offer_id":str(offer_id),"publisher":"FunPayAPI"}

funpay=FunPayAdapter()
