#!/usr/bin/env python3
import base64,colorsys,json,math,os,random,re,threading,time,urllib.error,urllib.request
from collections import Counter
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageOps
BASE=Path('/root/monochrome');OUT=BASE/'static/generated';STATE=BASE/'sessions/image-generation/usage.json';CREDS=BASE/'.cloudflare_ai.env';TZ=ZoneInfo('Europe/Kirov')
LEGO_REFERENCE=OUT/'ai-mrkt-aaced023-4ffd-45c3-b9d4-e89430fbf59c-0.jpg'
STYLE_REFERENCE=LEGO_REFERENCE
MODEL_ID='@cf/black-forest-labs/flux-1-schnell';DAILY_LIMIT=10000.0;NEURONS_PER_IMAGE=76.8;GEMINI_MODELS=('gemini-3.5-flash-lite','gemini-3.1-flash-lite');GEMINI_DAILY_LIMIT=500;GEMINI_CACHE=BASE/'sessions/image-generation/semantic_cache.json';GEMINI_USAGE=BASE/'sessions/image-generation/gemini_usage.json';GEMINI_RELAY=BASE/'.gemini_relay.env'
_lock=threading.Lock()
def _atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);os.chmod(path.parent,0o700);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));os.chmod(tmp,0o600);tmp.replace(path)
def _read():
 try:return json.loads(STATE.read_text())
 except Exception:return {'records':[]}
def _bootstrap():
 with _lock:
  if STATE.exists():return
  files=['cloudflare_flux_test.jpg','vicecream_iceman_frenchblue_halo.jpg','vicecream_iceman_frenchblue_aurora.jpg','vicecream_iceman_frenchblue_orbit.jpg','vicecream_iceman_diamond_glow.jpg','vicecream_iceman_neon_rings.jpg','vicecream_iceman_frost_lights.jpg']
  records=[]
  for name in files:
   p=OUT/name
   if p.exists():
    ts=p.stat().st_mtime;records.append({'ts':ts,'date':datetime.fromtimestamp(ts,TZ).date().isoformat(),'kind':'setup_test','market':None,'item_id':None,'model':None,'backdrop':None,'filename':name,'neurons':NEURONS_PER_IMAGE,'api_model':MODEL_ID})
  _atomic(STATE,{'records':records})
def usage_status():
 _bootstrap();data=_read();today=datetime.now(TZ).date().isoformat();rows=[x for x in data.get('records',[]) if x.get('date')==today];used=round(sum(float(x.get('neurons') or 0) for x in rows),1);latest={}
 for r in data.get('records',[]):
  if r.get('market') and r.get('item_id'):latest[f"{r['market']}:{r['item_id']}"]=r
 return {'date':today,'count':len(rows),'used_neurons':used,'remaining_neurons':round(max(0,DAILY_LIMIT-used),1),'daily_limit_neurons':DAILY_LIMIT,'percent_used':round(used/DAILY_LIMIT*100,2),'estimated_neurons_per_image':NEURONS_PER_IMAGE,'latest':latest,'api_model':MODEL_ID,'gemini':_gemini_usage_status()}
def _record(row):
 with _lock:
  data=_read();data.setdefault('records',[]).append(row);_atomic(STATE,data)
def _fetch(url):
 if url.startswith('/'):url='http://127.0.0.1:8090'+url
 return urllib.request.urlopen(url,timeout=45).read()
def _quantized(im,n=8,alpha=False):
 im=im.convert('RGBA').resize((256,256));px=[]
 for r,g,b,a in im.getdata():
  if not alpha or a>80:px.append((r,g,b))
 q=Image.new('RGB',(len(px),1));q.putdata(px);q=q.quantize(colors=n,method=Image.Quantize.MEDIANCUT).convert('RGB');return [x for x,_ in Counter(q.getdata()).most_common(n)]
def _luma(c):return .2126*c[0]+.7152*c[1]+.0722*c[2]
def _palette(backdrop,model):
 colors=_quantized(backdrop,12);base=tuple(round(sum(c[i] for c in colors)/len(colors)) for i in range(3));h,s,v=colorsys.rgb_to_hsv(*(x/255 for x in base))
 def hsvcol(ss,vv): return tuple(round(x*255) for x in colorsys.hsv_to_rgb(h,max(0,min(1,ss)),max(0,min(1,vv))))
 # Every generated background color stays on the exact backdrop hue. The collectible itself is composited later unchanged.
 dark=hsvcol(max(.40,s*.92),max(.18,v*.36));deep=hsvcol(max(.36,s*.86),max(.30,v*.64));light=hsvcol(max(.25,s*.62),min(.90,max(v*1.30,v+.16)));ice=hsvcol(max(.14,s*.34),min(.98,max(v*1.48,v+.28)));accent=hsvcol(max(.46,s*.92),min(.94,max(v*1.24,v+.12)))
 mcols=_quantized(model,10,True);model_accent=max(mcols,key=lambda c:colorsys.rgb_to_hsv(*(x/255 for x in c))[1]) if mcols else accent
 return {'base':base,'hue':h,'dark':dark,'deep':deep,'light':light,'ice':ice,'accent':accent,'model_accent':model_accent,'target_hex':'#%02x%02x%02x'%base,'stops':[(0,dark),(.20,deep),(.50,base),(.76,light),(.92,ice),(1,ice)]}
def _theme(p):
 h=p['hue']
 if h<.04 or h>=.96:return 'ruby plasma aurora, faceted garnet light, glowing ember diamonds and crimson starbursts'
 if h<.11:return 'molten amber aurora, faceted fire opal light, golden sparks and radiant starbursts'
 if h<.19:return 'solar gold aurora, faceted citrine light, luxury gold dust and diamond starbursts'
 if h<.45:return 'emerald bioluminescent aurora, faceted crystal light, floating luminous spores and diamond starbursts'
 if h<.58:return 'frozen aurora, faceted ice light, glittering frost dust and diamond lens flares'
 if h<.75:return 'cosmic sapphire aurora, faceted electric crystal light, orbital glints and diamond starbursts'
 if h<.9:return 'amethyst nebula aurora, faceted violet crystal light, magical glitter dust and diamond starbursts'
 return 'rose quartz aurora, faceted pink crystal light, iridescent glitter and diamond starbursts'
def _json_file(path,default):
 try:return json.loads(path.read_text())
 except Exception:return default
def _gemini_usage_status():
 data=_json_file(GEMINI_USAGE,{'records':[]});today=datetime.now(TZ).date().isoformat();models={}
 for model in GEMINI_MODELS:
  count=sum(1 for x in data.get('records',[]) if x.get('date')==today and x.get('model')==model);models[model]={'count':count,'limit':GEMINI_DAILY_LIMIT,'remaining':max(0,GEMINI_DAILY_LIMIT-count)}
 return {'date':today,'models':models}
def _record_gemini(model,kind='semantic'):
 with _lock:
  data=_json_file(GEMINI_USAGE,{'records':[]});now=time.time();data.setdefault('records',[]).append({'ts':now,'date':datetime.fromtimestamp(now,TZ).date().isoformat(),'model':model,'kind':kind});_atomic(GEMINI_USAGE,data)
def _relay_env():
 out={}
 for line in GEMINI_RELAY.read_text().splitlines():
  if '=' in line:
   k,v=line.split('=',1);out[k]=v
 return out
def _gemini_image_part(im,backdrop=False):
 work=im.convert('RGB' if backdrop else 'RGBA');work.thumbnail((512,512),Image.Resampling.LANCZOS);buf=BytesIO();mime='image/jpeg' if backdrop else 'image/png';work.save(buf,'JPEG' if backdrop else 'PNG',quality=88,optimize=True);return {'inline_data':{'mime_type':mime,'data':base64.b64encode(buf.getvalue()).decode()}}
def _parse_json_text(text):
 text=str(text or '').strip();text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I|re.S)
 try:return json.loads(text)
 except Exception:return {'prompt_fragment':text,'required_visible_details':[],'avoid':[]}
def _gemini_call(model,prompt,model_image,backdrop_image,temperature,kind='semantic'):
 usage=_gemini_usage_status()['models'][model]
 if usage['remaining']<=0:raise RuntimeError(f'Gemini daily limit reached for {model}')
 relay=_relay_env();parts=[{'text':prompt+'\nFirst image: exact collectible model. Second image: exact backdrop color reference.'},_gemini_image_part(model_image),_gemini_image_part(backdrop_image,True)];payload={'contents':[{'role':'user','parts':parts}],'generationConfig':{'temperature':temperature,'maxOutputTokens':1800,'responseMimeType':'application/json'}};request_body={'model':model,'body':payload};req=urllib.request.Request(relay['GEMINI_RELAY_URL'],data=json.dumps(request_body).encode(),headers={'Authorization':'Bearer '+relay['GEMINI_RELAY_SECRET'],'Content-Type':'application/json','User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'},method='POST');response=json.loads(urllib.request.urlopen(req,timeout=120).read());text=response['candidates'][0]['content']['parts'][0]['text'];_record_gemini(model,kind);return _parse_json_text(text)
def _gemini_semantic_bundle(item,model_image,backdrop_image):
 key='universal-v2|'+('|'.join(str(item.get(k) or '').strip().lower() for k in ('collection','model','backdrop')));cache=_json_file(GEMINI_CACHE,{})
 if key in cache:return cache[key]
 fallback=_semantic_details(item);collection=item.get('collection') or '';model=item.get('model') or '';backdrop=item.get('backdrop') or ''
 draft_prompt=f"""You are the lead production designer for a premium collectible advertisement. Study both supplied images carefully: the exact collectible model and exact backdrop reference. Collection: {collection}. Model theme: {model}. Backdrop: {backdrop}.
Your task is NOT to describe the collectible and NOT to fill the scene with generic luxury crystals. Design the empty surrounding physical 3D set so its MODEL THEME is unmistakable at thumbnail size even before the collectible is added.
First infer the single dominant visual language of the model. At least 70 percent of all large visible structures must use that language. The backdrop name controls hue, luminance and materials only; it must never replace the model semantics. For example, if the model is Lego, the large architecture itself must visibly be built from interlocking toy bricks with many clear round studs, seams, stacked courses, corners and arches—not merely accompanied by a few cubes. This is an explanation of the rule, not a special-case template.
Create a coherent Frost Lights physical studio set with an empty central placement zone, low circular glass podium, large thematic structures on left and right, clear foreground objects, middle-ground architecture and background relief. Every required detail must be large, literal, physically plausible and recognizable at 320px. Avoid vague words such as motif, inspired, hints, abstract forms or subtle texture. Avoid generic crystals unless the model semantics calls for crystals. Existing hints: {fallback}.
Return JSON only with fields: dominant_theme, dominance_rule, prompt_fragment (180-280 English words for an image generator, describing background only and explicitly keeping center empty), left_structure (2-3 literal items), right_structure (2-3), foreground_details (3-4), background_details (3-4), required_visible_details (10-14 literal countable objects or construction features), materials (4-7), lighting_details (3-5), avoid (6-10)."""
 try:
  draft=_gemini_call('gemini-3.5-flash-lite',draft_prompt,model_image,backdrop_image,.65)
  review_prompt=f"""You are the final art director and prompt engineer. Inspect the exact model and backdrop images again, then aggressively correct this scene plan: {json.dumps(draft,ensure_ascii=False)}.
Collection: {collection}. Model: {model}. Backdrop: {backdrop}.
Reject the plan if the model identity is represented only by small props, textures, particles or one wall panel. The model identity must form the LARGE side architecture and foreground construction. Enforce at least 70 percent thematic dominance, 10 clearly countable semantic features, strong silhouette variety, foreground/middle/background depth, and an empty narrow center for later compositing. Remove generic luxury filler that competes with the theme. Do not describe or generate the central collectible. Do not introduce a second accent hue: the exact backdrop controls the entire background palette and brightness. Make all objects concrete, visible and image-generator-friendly. Return the same JSON fields only, with a complete 220-320 word prompt_fragment."""
  final=_gemini_call('gemini-3.1-flash-lite',review_prompt,model_image,backdrop_image,.35);source='gemini-3.5+3.1-v2'
 except Exception as exc:
  final={'dominant_theme':model,'dominance_rule':'model theme forms most large structures','prompt_fragment':fallback,'required_visible_details':[],'materials':[],'lighting_details':[],'avoid':['generic unrelated scenery']};source='local-fallback';final['error']=str(exc)[:180]
 def joined(name,limit):return '; '.join(str(x) for x in final.get(name,[])[:limit])
 zones=' | '.join(f"{name.replace('_',' ')}: {joined(name,4)}" for name in ('left_structure','right_structure','foreground_details','background_details') if final.get(name))
 bundle={'source':source,'dominant_theme':str(final.get('dominant_theme') or model),'dominance_rule':str(final.get('dominance_rule') or ''),'prompt_fragment':str(final.get('prompt_fragment') or fallback),'zones':zones,'details':joined('required_visible_details',14),'materials':joined('materials',7),'lighting':joined('lighting_details',5),'avoid':joined('avoid',10),'raw':final,'created_at':time.time()};cache[key]=bundle;_atomic(GEMINI_CACHE,cache);return bundle

def _semantic_details(item):
 collection=str(item.get('collection') or '').lower();model=str(item.get('model') or '').lower();backdrop=str(item.get('backdrop') or '').lower()
 collection_map={
  'vice cream':'frozen dessert architecture, creamy spiral ridges, waffle-cone diamond textures and tiny crystalline sugar highlights',
  'ice cream':'frosted cream swirls, crystalline sugar, waffle geometry and playful frozen droplets',
  'chill flame':'intertwined hot-and-cold energy ribbons, sculptural torch arcs, glowing flame contours and rising sparks',
  'fine pen':'elegant calligraphic light trails, fountain-pen nib geometry, flowing ink ribbons and precise engraved lines',
  'liberty figure':'monumental neoclassical rays, crown-spike geometry, polished stone arches and ceremonial star motifs',
  'snoop dogg':'luxury street-art energy, polished chain-like curves, rhythmic light waves and bold stage lighting',
 }
 model_map={
  'iceman':'snowflake rosettes, fractured ice crystals, frozen scarf-like ribbons, delicate frost branches and drifting snow dust',
  'lego':'modular toy-block geometry, raised circular studs, translucent stacked cubes and playful construction patterns',
  'balinese':'ornate Balinese temple filigree, ceremonial fan shapes, carved floral geometry and layered tropical ornament',
  'royal goblet':'royal chalice silhouettes, crown filigree, jewel-cut glass facets and elegant liquid reflections',
  'elastic':'stretched luminous ribbons, kinetic loops, tension curves, spring-like arcs and flowing elastic motion',
  'choco cone':'chocolate spiral ribbons, waffle-cone diamond grids, glossy cocoa droplets and confectionery facets',
  'mermaid':'pearl bubbles, seashell curves, aquatic caustic light, flowing wave ribbons and iridescent scale geometry',
  'plushie':'soft velvet clouds, stitched seam patterns, rounded padded forms and subtle textile fibers',
  'pitcher':'dynamic throwing arcs, curved ball-stitch patterns, speed trails and focused stadium-like beams',
  'quarterback':'bold stadium beams, football-stitch curves, strategic field geometry and energetic motion trails',
  'pigeon':'layered feather motifs, wing-like arcs, airy city light and soft floating down particles',
 }
 backdrop_map={
  'french blue':'French-blue glass, icy satin reflections and cool cobalt atmospheric depth',
  'emerald':'deep emerald gemstone facets, jewel reflections and saturated green light shafts',
  'mint green':'fresh mint crystal, translucent pale-green glass and luminous botanical freshness',
  'turquoise':'turquoise mineral facets, lagoon-like caustics and bright cyan-green light',
  'deep cyan':'deep-cyan glass, underwater depth and intense cyan edge lighting',
  'orange':'fire-opal facets, luminous orange glass and warm radiant highlights',
  'copper':'polished copper reflections, metallic arcs and glowing burnished surfaces',
  'sky blue':'open airy light, soft cloudlike layers and crystalline sky-blue rays',
  'tactical pine':'dark pine geometry, forest-depth light shafts and precise tactical contour lines',
  'neon blue':'electric neon tubes, intense blue edge light and luminous futuristic grids',
  'cyberpunk':'holographic grids, neon rain, electric circuitry and futuristic city-light reflections',
  'caramel':'amber sugar glass, warm caramel ribbons and glossy golden-brown reflections',
 }
 c=next((v for k,v in collection_map.items() if k in collection),f'visual motifs inspired by the {collection or "collectible"} theme')
 m=next((v for k,v in model_map.items() if k in model),f'distinctive decorative motifs inspired by the model concept {model or "collectible"}')
 b=next((v for k,v in backdrop_map.items() if k in backdrop),f'atmospheric materials and lighting expressing the backdrop concept {backdrop or "monochrome"}')
 return f'{c}; {m}; {b}'

def _map_palette(im,p):
 g=ImageOps.autocontrast(ImageOps.grayscale(ImageOps.fit(im,(1024,768),method=Image.Resampling.LANCZOS,centering=(.5,.5))),cutoff=1);g=g.point(lambda x:round(255*(.13+.87*((x/255)**.78))));stops=p['stops'];luts=[[],[],[]]
 for v in range(256):
  t=v/255;lo,hi=stops[0],stops[-1]
  for a,b in zip(stops,stops[1:]):
   if a[0]<=t<=b[0]:lo,hi=a,b;break
  f=(t-lo[0])/max(.0001,hi[0]-lo[0]);rgb=tuple(round(lo[1][j]+f*(hi[1][j]-lo[1][j])) for j in range(3))
  for j in range(3):luts[j].append(rgb[j])
 return Image.merge('RGB',tuple(g.point(luts[j]) for j in range(3))).convert('RGBA')
def _effects(p,seed):
 rng=random.Random(seed^0x51A7); W,H=1024,768
 bloom=Image.new('RGBA',(W,H)); crisp=Image.new('RGBA',(W,H)); db=ImageDraw.Draw(bloom); dc=ImageDraw.Draw(crisp)
 mono=[p['ice'],p['light'],p['base']]; warm=p.get('model_accent',p['accent'])
 for i in range(42):
  for _try in range(50):
   x=rng.randint(35,989); y=rng.randint(45,635)
   if not (300<x<724 and 80<y<610): break
  r=rng.choices([2,3,4,5,7,9,12],weights=[7,8,8,7,5,3,1])[0]; c=warm if i<4 else rng.choice(mono)
  db.ellipse((x-r*2,y-r*2,x+r*2,y+r*2),fill=(*c,42))
  dc.line((x-r*3,y,x+r*3,y),fill=(*c,175),width=1); dc.line((x,y-r*3,x,y+r*3),fill=(*c,175),width=1)
  if r>=7:
   dc.line((x-r*2,y-r*2,x+r*2,y+r*2),fill=(*c,92),width=1); dc.line((x-r*2,y+r*2,x+r*2,y-r*2),fill=(*c,92),width=1)
  dc.ellipse((x-2,y-2,x+2,y+2),fill=(255,255,255,220))
 for _ in range(26):
  x=rng.choice([rng.randint(30,300),rng.randint(724,994)]); y=rng.randint(80,650); r=rng.choice([1,1,2,2,3]); dc.ellipse((x-r,y-r,x+r,y+r),fill=(*rng.choice(mono),rng.randint(75,150)))
 return Image.alpha_composite(bloom.filter(ImageFilter.GaussianBlur(8)),crisp)
def _clean_center(im,p):
 # Hard post-generation exclusion zone: AI scenery is physically removed here before the exact model is composited.
 clean=Image.new('RGBA',(1024,768));d=ImageDraw.Draw(clean)
 for y in range(768):
  t=min(1.0,abs(y-330)/520);a=p['light'] if y<590 else p['deep'];b=p['base'];col=tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3));d.line((0,y,1024,y),fill=(*col,255))
 halo=Image.new('RGBA',clean.size);hd=ImageDraw.Draw(halo);hd.ellipse((315,20,709,650),fill=(*p['ice'],125));hd.ellipse((385,120,639,610),fill=(*p['light'],110));clean=Image.alpha_composite(clean,halo.filter(ImageFilter.GaussianBlur(95)))
 mask=Image.new('L',clean.size);md=ImageDraw.Draw(mask);md.rounded_rectangle((350,42,674,640),radius=105,fill=255);mask=mask.filter(ImageFilter.GaussianBlur(38))
 return Image.composite(clean,im,mask)

def _clean_reference_subject(im,p):
 clean=Image.new('RGBA',(1024,768));d=ImageDraw.Draw(clean)
 for y in range(768):
  t=min(1.0,abs(y-320)/520);a=p['light'] if y<590 else p['deep'];b=p['base'];col=tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3));d.line((0,y,1024,y),fill=(*col,255))
 halo=Image.new('RGBA',clean.size);ImageDraw.Draw(halo).ellipse((300,40,724,630),fill=(*p['ice'],105));clean=Image.alpha_composite(clean,halo.filter(ImageFilter.GaussianBlur(90)))
 mask=Image.new('L',clean.size);ImageDraw.Draw(mask).rounded_rectangle((330,55,705,615),radius=105,fill=255);mask=mask.filter(ImageFilter.GaussianBlur(34))
 return Image.composite(clean,im,mask)

def _lego_3d_overlay(p,seed):
 # Volumetric side architecture: shaded top/front/side faces and raised cylindrical studs.
 rng=random.Random(seed^0x31E60);W,H=1024,768
 shadow=Image.new('RGBA',(W,H));sd=ImageDraw.Draw(shadow)
 crisp=Image.new('RGBA',(W,H));d=ImageDraw.Draw(crisp)
 def mix(c,a): return tuple(max(0,min(255,int(c[i]*a))) for i in range(3))
 def brick(x,y,w,h,dep,cols,base=None,alpha=245):
  c=base or rng.choice([p['base'],p['light'],p['ice']]);front=mix(c,.78);side=mix(c,.50);top=mix(c,1.16);edge=mix(p['ice'],1.15)
  # y is top-front edge; depth projects up-right.
  sd.rounded_rectangle((x-16,y-8,x+w+dep+20,y+h+25),radius=22,fill=(0,20,18,145))
  d.polygon([(x,y),(x+dep,y-dep),(x+w+dep,y-dep),(x+w,y)],fill=(*top,alpha),outline=(*edge,220))
  d.rounded_rectangle((x,y,x+w,y+h),radius=max(7,int(h*.14)),fill=(*front,alpha),outline=(*edge,225),width=3)
  d.polygon([(x+w,y),(x+w+dep,y-dep),(x+w+dep,y+h-dep),(x+w,y+h)],fill=(*side,alpha),outline=(*edge,210))
  d.line((x+7,y+7,x+w-7,y+7),fill=(255,255,255,145),width=3)
  # Raised studs: lower barrel plus glossy top ellipse.
  gap=w/(cols+1);ry=max(5,min(10,int(dep*.28)));rx=max(8,min(14,int(gap*.22)))
  for i in range(cols):
   cx=x+gap*(i+1)+dep*.48;cy=y-dep*.53
   d.rectangle((cx-rx,cy-ry,cx+rx,cy+ry),fill=(*mix(c,.72),alpha))
   d.ellipse((cx-rx,cy-ry*2,cx+rx,cy),fill=(*top,alpha),outline=(*edge,230),width=2)
   d.arc((cx-rx+3,cy-ry*2+3,cx+rx-3,cy-3),190,345,fill=(255,255,255,185),width=2)
 # stepped towers; big tangible pieces, outer 28%% only.
 left=[(18,610,215,70,30,4),(42,535,180,66,28,3),(18,460,205,65,28,4),(62,390,150,60,26,3),(30,326,174,58,24,3)]
 right=[(790,610,205,70,30,4),(812,535,172,66,28,3),(792,460,202,65,28,4),(824,390,154,60,26,3),(808,326,176,58,24,3)]
 for args in left+right: brick(*args)
 # lower modular foreground plates and a few translucent gem-bricks.
 for args in [(70,690,170,48,23,4),(248,706,108,42,20,2),(668,706,108,42,20,2),(784,690,170,48,23,4)]: brick(*args,base=p['light'],alpha=235)
 # small side pieces add construction rhythm but never enter the center exclusion zone.
 for x,y in [(238,570),(745,575),(255,430),(730,438),(112,255),(846,255)]:
  brick(x,y,78,42,20,2,base=p['ice'],alpha=218)
 out=Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)),crisp)
 # restrained specular bloom over the physical geometry.
 bloom=out.copy().filter(ImageFilter.GaussianBlur(20));bloom.putalpha(bloom.getchannel('A').point(lambda a:int(a*.16)))
 return Image.alpha_composite(bloom,out)

def _call_cloudflare(prompt,seed):
 creds={k:v for k,v in (line.split('=',1) for line in CREDS.read_text().splitlines() if '=' in line)};url=f"https://api.cloudflare.com/client/v4/accounts/{creds['CF_ACCOUNT_ID']}/ai/run/{MODEL_ID}";last=''
 prompts=[prompt]
 for i,text in enumerate(prompts):
  body=json.dumps({'prompt':text}).encode();req=urllib.request.Request(url,data=body,headers={'Authorization':'Bearer '+creds['CF_API_TOKEN'],'Content-Type':'application/json'},method='POST')
  try:
   d=json.loads(urllib.request.urlopen(req,timeout=180).read());return Image.open(BytesIO(base64.b64decode(d['result']['image']))).convert('RGB')
  except urllib.error.HTTPError as e:last=f'HTTP {e.code}: '+e.read().decode('utf-8','ignore')[:300]
 raise RuntimeError(last or 'Cloudflare generation failed')
def _premium_podium(p):
 W,H=1024,768; sh=Image.new('RGBA',(W,H)); sd=ImageDraw.Draw(sh); sd.ellipse((220,585,804,735),fill=(0,15,12,150))
 out=Image.new('RGBA',(W,H)); d=ImageDraw.Draw(out); dark=p['dark']; deep=p['deep']; light=p['light']; ice=p['ice']
 d.rectangle((255,618,769,690),fill=(*dark,225)); d.ellipse((255,645,769,720),fill=(*dark,242),outline=(*deep,230),width=4); d.ellipse((255,578,769,660),fill=(*deep,218),outline=(*light,210),width=4)
 d.arc((270,590,754,651),188,350,fill=(*ice,165),width=3)
 d.rectangle((330,558,694,621),fill=(*dark,210)); d.ellipse((330,580,694,646),fill=(*dark,232),outline=(*deep,225),width=3); d.ellipse((330,525,694,595),fill=(*light,170),outline=(*ice,220),width=4)
 d.arc((346,536,678,585),190,348,fill=(255,255,255,130),width=3)
 return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(24)),out)

def _compose_candidate(raw,p,model,item,seed):
 bg=_map_palette(raw,p)
 glow=Image.new('RGBA',bg.size);gd=ImageDraw.Draw(glow)
 gd.ellipse((220,55,804,655),fill=(*p['light'],105));gd.ellipse((350,145,674,590),fill=(*p['ice'],65))
 bg=Image.alpha_composite(bg,glow.filter(ImageFilter.GaussianBlur(52)));bg=_clean_center(bg,p);bg=Image.alpha_composite(bg,_premium_podium(p));bg=Image.alpha_composite(bg,_effects(p,seed))
 shade=Image.new('RGBA',bg.size);ImageDraw.Draw(shade).rectangle((0,590,1024,768),fill=(*p['dark'],128));bg=Image.alpha_composite(bg,shade.filter(ImageFilter.GaussianBlur(28)))
 target_h=535;target_w=max(1,int(model.width*target_h/model.height));m=model.resize((target_w,target_h),Image.Resampling.LANCZOS);x=(1024-target_w)//2;y=56
 alpha=m.getchannel('A');shadow=Image.new('RGBA',bg.size);sh=Image.new('RGBA',m.size,(*p['deep'],220));sh.putalpha(alpha.filter(ImageFilter.GaussianBlur(24)));shadow.alpha_composite(sh,(x+10,y+22));bg=Image.alpha_composite(bg,shadow);bg.alpha_composite(m,(x,y))
 text=f"{item.get('model') or 'MODEL'}  ×  {item.get('backdrop') or 'BACKDROP'}".upper();font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',36);draw=ImageDraw.Draw(bg);box=draw.textbbox((0,0),text,font=font);draw.text(((1024-(box[2]-box[0]))/2,680),text,font=font,fill=(*p['ice'],255))
 return bg

def _generated_qa(image,dominant,required):
 reference=Image.open(STYLE_REFERENCE).convert('RGB') if STYLE_REFERENCE.exists() else Image.new('RGB',(64,64),'white')
 prompt=f"""Compare the FIRST finished candidate with the SECOND golden style reference. IMPORTANT: judge thematic details ONLY in the surrounding side and foreground environment. The exact smooth central collectible is intentionally composited unchanged; NEVER require it to be built from the theme and NEVER count it as thematic evidence. Ignore the second image's LEGO objects, torch, green hue and caption; copy only its reusable visual quality and composition grammar. Expected candidate environment theme: {dominant}. Suggested literal features: {required}. The target formula is: a razor-sharp central collectible; a dark multi-tier circular reflective glass podium; asymmetric bright upper-left glow against deep right and lower shadow zones; distinct foreground, midground and softly blurred background; tasteful faceted debris near the floor; medium-density diamond star glints and micro-particles; only 2 to 5 recognizable theme props at outer sides; cinematic ambient occlusion, rim light, reflections and depth of field. Reject flat bright empty backgrounds, flat geometric shards, full theme wallpaper, weak podiums, symmetric lighting, or decorative clutter over the collectible. Return JSON only: {{"passed":boolean,"dominant_theme_score":0-10,"reference_style_match_score":0-10,"premium_depth_score":0-10,"sparkle_quality_score":0-10,"theme_intrusiveness_score":0-10,"negative_space_score":0-10,"visible_required_details":[strings],"missing_details":[strings],"misleading_shapes":[strings],"center_duplicate":boolean,"brightness_match":0-10,"palette_match":0-10,"blunt_verdict":string,"corrective_prompt":string}}. Pass only if theme>=7, reference style>=7, depth>=7, sparkle quality>=6, intrusiveness<=5, negative space>=5, at least two literal thematic feature classes, and no center duplicate."""
 try:
  qa=_gemini_call('gemini-3.5-flash-lite',prompt,image.convert('RGB'),reference,0,kind='pixel_qa')
 except Exception as exc:
  return {'passed':False,'dominant_theme_score':0,'reference_style_match_score':0,'premium_depth_score':0,'sparkle_quality_score':0,'visible_required_details':[],'missing_details':['pixel QA unavailable'],'blunt_verdict':str(exc)[:180],'corrective_prompt':''}
 qa['passed']=bool(qa.get('passed')) and float(qa.get('dominant_theme_score') or 0)>=7 and float(qa.get('reference_style_match_score') or 0)>=7 and float(qa.get('premium_depth_score') or 0)>=7 and float(qa.get('sparkle_quality_score') or 0)>=6 and len(qa.get('visible_required_details') or [])>=2 and float(qa.get('theme_intrusiveness_score') or 10)<=5 and float(qa.get('negative_space_score') or 0)>=5 and not bool(qa.get('center_duplicate'))
 return qa

def generate_listing_text(item,visual,sale_price,random_bg=False):
 model_url=visual.get('model_img') or item.get('image_url');bg_url=visual.get('bg_url')
 if not model_url or not bg_url:raise RuntimeError('Для этого подарка пока нет слоёв конструктора')
 model_image=Image.open(BytesIO(_fetch(model_url))).convert('RGBA');backdrop_image=Image.open(BytesIO(_fetch(bg_url))).convert('RGBA')
 collection=str(item.get('collection') or 'Telegram Gift');model=str(item.get('model') or 'NFT-подарок');backdrop=str(item.get('backdrop') or 'Monochrome');symbol=str(item.get('symbol') or '')
 random_rule=('Скажи, что фон при покупке случайный, но отдельно опиши показанный пример цвета '+backdrop+'.' if random_bg else 'Это конкретный цвет подарка: '+backdrop+'. Не называй фон случайным.')
 prompt=f"""Ты сильный русскоязычный копирайтер товарных карточек коллекционных NFT-подарков. Изучи первое изображение модели и второе изображение точного фона. Напиши готовое объявление на русском языке для площадки.
Данные: коллекция — {collection}; модель — {model}; точный цвет фона — {backdrop}; символ — {symbol}; цена продажи — {int(sale_price)}₽. {random_rule}
Главный акцент: выразительный монохром. Обязательно естественно переведи/объясни английское название цвета {backdrop} по-русски и опиши оттенок, яркость, настроение, сочетание модели с фоном и заметность в профиле. Не выдумывай детали, которых нет на изображениях.
Структура: яркий заголовок с ценой; отдельный блок про модель и цвет; 2-3 предложения о монохромном образе; затем на отдельной строке вставь точный маркер [[DEAL_PROCESS]] и НЕ пиши свои инструкции об оплате или сделке; после маркера добавь четыре коротких преимущества. Используй уместные эмодзи и переносы строк. Объём остальных частей 650-1050 знаков.
СТРОГИЕ ЗАПРЕТЫ: никаких TON, криптовалюты, курса закупки, себестоимости, маркетплейсов, названий торговых сервисов, ссылок, адресов магазина, «эквивалента внутри Telegram», оценочной стоимости, обещаний роста цены, ложной редкости или гарантированной инвестиционной ценности. Не называй Telegram продавцом или оценщиком. Не добавляй номер подарка.
Процесс оплаты происходит только на площадке: никогда не предлагай перевод, карту, криптовалюту, личную оплату или иной способ расчёта. Верни JSON только с полем text."""
 result=_gemini_call('gemini-3.5-flash-lite',prompt,model_image,backdrop_image,.72,kind='listing_text');text=str(result.get('text') or '').strip()
 blocked=(' ton','тон ','http://','https://','эквивалент внутри telegram','оценивается telegram','mrkt','portals','tonnel')
 if not text or '[[DEAL_PROCESS]]' not in text or any(x in (' '+text.lower()) for x in blocked):
  correction=f"""Перепиши следующий рекламный текст полностью, сохранив цену {int(sale_price)}₽, модель {model}, коллекцию {collection} и точный цвет {backdrop}: {text}
Удали любые ссылки, названия маркетов, TON, криптовалюту, закупочный курс, оценочную стоимость, эквиваленты и инвестиционные обещания. Обязательно понятно опиши по-русски, что за оттенок скрывается за названием {backdrop}, и как он сочетается с моделью. Вместо любых инструкций по сделке вставь точный маркер [[DEAL_PROCESS]]. Не предлагай способы оплаты. Верни JSON только с полем text."""
  result=_gemini_call('gemini-3.1-flash-lite',correction,model_image,backdrop_image,.35,kind='listing_text_review');text=str(result.get('text') or '').strip()
 if not text:raise RuntimeError('Gemini не вернул текст объявления')
 deal_process='''📦 Процесс сделки:
1️⃣ Оплатите заказ на площадке.
2️⃣ Отправьте в чат свой Telegram-никнейм — @username.
3️⃣ Ожидайте отправку NFT-подарка на указанный аккаунт.
4️⃣ После получения подтвердите выполнение заказа.
5️⃣ Если всё понравилось — оставьте отзыв.

⚠️ Перед отправкой внимательно проверьте @username: перевод NFT на ошибочный аккаунт отменить невозможно.'''
 if '[[DEAL_PROCESS]]' in text:text=text.replace('[[DEAL_PROCESS]]',deal_process)
 else:text=text.rstrip()+'\n\n'+deal_process
 return {'ok':True,'text':text,'source':'gemini','model':'gemini-3.5-flash-lite','color':backdrop,'sale_price':int(sale_price),'usage':_gemini_usage_status()}

def generate(item,visual):
 status=usage_status()
 if status['remaining_neurons']<NEURONS_PER_IMAGE:raise RuntimeError('Дневной бесплатный лимит генераций исчерпан')
 model_url=visual.get('model_img') or item.get('image_url');bg_url=visual.get('bg_url')
 if not model_url or not bg_url:raise RuntimeError('Для этого подарка пока нет слоёв конструктора')
 model=Image.open(BytesIO(_fetch(model_url))).convert('RGBA');backdrop=Image.open(BytesIO(_fetch(bg_url))).convert('RGBA');p=_palette(backdrop,model);theme=_theme(p)
 semantic_bundle=_gemini_semantic_bundle(item,model,backdrop);semantics=semantic_bundle['prompt_fragment'];required=semantic_bundle.get('details') or '';dominant=semantic_bundle.get('dominant_theme') or item.get('model') or ''
 required_short='; '.join([x.strip() for x in required.replace('|',';').split(';') if x.strip()][:6])[:520]
 base_prompt=f"PREMIUM CINEMATIC LUXURY PRODUCT SHOWCASE, photorealistic macro 3D, theme: {dominant}. Reproduce this composition DNA: central hero zone above a dark multi-tier circular reflective glass podium; asymmetric radiant upper-left glow fading into deep moody right and lower shadows; strong specular highlights, ambient occlusion, rim light and volumetric atmosphere; distinct foreground, midground and softly blurred background; tasteful faceted crystal debris near the floor; medium-density diamond star glints and floating micro-particles. Place only 2 to 5 large readable physical theme accents at the far outer sides and foreground: {required_short}. Theme accents are secondary, asymmetric, tangible, bevelled and reflective; never wallpaper and never full walls or floor. Keep a wide clean center for later product compositing. Monochrome {item.get('backdrop') or 'backdrop'} hue with brilliant highlights AND deep same-hue shadows, not a flat bright wash. Premium Frost Lights marketplace thumbnail. NO FLAT EMPTY BACKGROUND, NO FULL THEME ROOM, NO REPETITIVE GRID, NO TEXT, NO LOGO, NO CHARACTER."
 safe=re.sub(r'[^a-z0-9]+','-',f"{item.get('market')}-{item.get('id')}",flags=re.I).strip('-')[:90];ts=int(time.time());OUT.mkdir(parents=True,exist_ok=True)
 best=None;best_score=-1;attempts=0;prompt=base_prompt;started=time.time();qa={}
 for attempt in range(1,3):
  if status['remaining_neurons']<attempt*NEURONS_PER_IMAGE:break
  attempts=attempt;seed=random.SystemRandom().randint(1,2**31-1);raw=_call_cloudflare(prompt,seed);raw.save(OUT/f'diag-{safe}-{ts}-a{attempt}-raw.jpg','JPEG',quality=92)
  candidate=_compose_candidate(raw,p,model,item,seed);candidate.convert('RGB').save(OUT/f'diag-{safe}-{ts}-a{attempt}-final.jpg','JPEG',quality=92)
  current=_generated_qa(candidate,dominant,required_short);score=.40*float(current.get('dominant_theme_score') or 0)+.35*float(current.get('reference_style_match_score') or 0)+.25*float(current.get('premium_depth_score') or 0)-.15*float(current.get('theme_intrusiveness_score') or 0)
  if score>best_score:best,best_score,qa=candidate,score,current
  if current.get('passed'):break
  correction=str(current.get('corrective_prompt') or '')[:650];missing='; '.join(str(x) for x in (current.get('missing_details') or [])[:6])
  prompt=(f"CORRECT TOWARD THE GOLDEN CINEMATIC REFERENCE WITHOUT ADDING CLUTTER: {correction}. MISSING LITERAL FEATURES: {missing}. Preserve at least 55 percent clean negative space and use only a few supporting theme accents. "+base_prompt)[:1750]
 if best is None:raise RuntimeError('Генератор не вернул ни одного кандидата')
 filename=f'ai-{safe}-{ts}.jpg';final=OUT/filename;best.convert('RGB').save(final,'JPEG',quality=95,optimize=True)
 qa.update({'attempts':attempts,'center_hard_mask':True,'strict_backdrop_palette':True,'backdrop_driven_brightness':True,'decorative_effects':'golden_reference_frost_lights','style_reference':STYLE_REFERENCE.name,'target_backdrop_hex':p.get('target_hex'),'qa_status':'passed' if qa.get('passed') else 'rejected_best_candidate'})
 now=time.time();neurons=round(attempts*NEURONS_PER_IMAGE,1);row={'ts':now,'date':datetime.fromtimestamp(now,TZ).date().isoformat(),'kind':'inventory','market':item.get('market'),'item_id':str(item.get('id')),'model':item.get('model'),'backdrop':item.get('backdrop'),'filename':filename,'url':'/generated/'+filename,'neurons':neurons,'api_model':MODEL_ID,'seconds':round(now-started,2),'theme':theme,'semantics':semantics,'semantic_source':semantic_bundle.get('source'),'semantic_dominant_theme':dominant,'semantic_zones':semantic_bundle.get('zones'),'steps':6,'qa':qa};_record(row)
 return {'ok':True,'url':row['url'],'filename':filename,'neurons':neurons,'seconds':row['seconds'],'theme':theme,'semantics':semantics,'semantic_source':semantic_bundle.get('source'),'semantic_dominant_theme':dominant,'semantic_zones':semantic_bundle.get('zones'),'steps':6,'qa':qa,'usage':usage_status()}
