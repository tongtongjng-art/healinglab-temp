from pathlib import Path
import re, json, shutil

ROOT = Path('/root/one_article_picker_v11_server_cron')
EDITOR = Path('/var/www/html/daily/editor.html')
ARCHIVE_DIRS = [Path('/var/www/html/daily/archive'), ROOT / 'archive']
JS_START = '<!-- HL_HISTORY_FILTER_PATCH_START -->'
JS_END = '<!-- HL_HISTORY_FILTER_PATCH_END -->'
CSS_START = '/* HL_HISTORY_FILTER_CSS_START */'
CSS_END = '/* HL_HISTORY_FILTER_CSS_END */'

def clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'<.*?>', '', s or '')).strip()

def parse_history():
    items, seen = [], set()
    for d in ARCHIVE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob('day-*.html'), reverse=True):
            name = p.name
            if name.endswith('-xhs.html') or name.endswith('-editor.html'):
                continue
            date = p.stem.replace('day-', '')
            if not date or date in seen:
                continue
            title, topic, level = '外刊精读', '其他', 'B2'
            try:
                html = p.read_text(encoding='utf-8-sig', errors='ignore')
                m = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
                if m:
                    title = clean(m.group(1)) or title
                for pat in [r'英文标题：</strong>\s*([^<]+)', r'英文标题[:：]\s*([^<\n]+)', r'今日外刊[:：]\s*([^<\n]+)']:
                    mm = re.search(pat, html)
                    if mm:
                        title = clean(mm.group(1)) or title
                        break
                mt = re.search(r'主题[:：]\s*([^<\n｜|]+)', html)
                if mt:
                    topic = clean(mt.group(1)) or topic
                ml = re.search(r'难度[:：]\s*([A-Z0-9\-]+)', html)
                if ml:
                    level = clean(ml.group(1)) or level
            except Exception:
                pass
            items.append({'date': date, 'title': title, 'topic': topic, 'level': level, 'href': '/daily/archive/' + name})
            seen.add(date)
            if len(items) >= 80:
                return items
    return items

def strip_old(html):
    html = re.sub(re.escape(JS_START) + r'.*?' + re.escape(JS_END), '', html, flags=re.S)
    html = re.sub(re.escape(CSS_START) + r'.*?' + re.escape(CSS_END), '', html, flags=re.S)
    return html

def main():
    if not EDITOR.exists():
        raise SystemExit('editor.html not found: ' + str(EDITOR))
    shutil.copy2(EDITOR, EDITOR.with_suffix('.html.bak_history_mobile'))
    history = parse_history()
    hjson = json.dumps(history, ensure_ascii=False)
    html = strip_old(EDITOR.read_text(encoding='utf-8', errors='ignore'))
    css = f'''
{CSS_START}
.hl-history-section{{margin-top:18px;padding:18px;border:1px solid #dce8e3;border-radius:22px;background:#fbfaf6}}
.hl-history-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;margin-bottom:14px}}
.hl-history-head h2{{margin:0;font-size:22px;color:#1d252c}}
.hl-history-head span{{font-size:12px;color:#66737d}}
.hl-history-filters{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}}
.hl-history-filters select{{width:100%;border:1px solid #dce8e3;border-radius:999px;background:white;padding:10px 12px;font-size:14px;color:#426e60;font-weight:700}}
.hl-history-list{{display:grid;gap:10px}}
.hl-history-item{{display:block;text-decoration:none;color:#1d252c;padding:13px 14px;border:1px solid #dce8e3;border-radius:16px;background:white}}
.hl-history-meta{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:7px}}
.hl-history-date{{color:#426e60;font-weight:900;font-size:13px}}
.hl-history-tag{{font-size:12px;color:#426e60;background:#eef5f1;border-radius:999px;padding:3px 8px;font-weight:800}}
.hl-history-title{{font-size:14px;line-height:1.5;color:#2f3b43}}
.hl-history-empty{{font-size:14px;color:#66737d;padding:14px;border-radius:16px;background:white}}
@media(max-width:520px){{.hl-history-section{{padding:15px;border-radius:20px}}.hl-history-head h2{{font-size:20px}}.hl-history-filters{{grid-template-columns:1fr}}}}
{CSS_END}
'''
    js = f'''
{JS_START}
<script>
(function(){{
  const SERVER_HISTORY = {hjson};
  const STORAGE_KEY = 'hl_manual_daily_history_v1';
  const TOPICS = ['全部','社会议题','生态环境','游戏文化','教育','科技','文化历史','生活','自然科学','其他'];
  const LEVELS = ['全部','B1','B1-B2','B2','C1'];
  function esc(s){{return String(s||'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
  function val(id,fb){{const e=document.getElementById(id);return e&&e.value?e.value:fb;}}
  function loadLocal(){{try{{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'[]')}}catch(e){{return []}}}}
  function saveLocal(x){{try{{localStorage.setItem(STORAGE_KEY,JSON.stringify(x.slice(0,120)))}}catch(e){{}}}}
  function saveCurrent(){{
    let title='今日外刊精读', date=new Date().toISOString().slice(0,10);
    try{{if(window.data){{title=data.title_cn||data.title_raw||title;date=data.today||date;}}}}catch(e){{}}
    const item={{date,title,topic:val('topic','其他'),level:val('level','B2'),href:location.pathname+location.search}};
    let arr=loadLocal().filter(x=>!(x.date===item.date&&x.title===item.title));
    arr.unshift(item); saveLocal(arr);
  }}
  function allHistory(){{
    const m=new Map();
    [...loadLocal(),...SERVER_HISTORY].forEach(x=>{{
      const k=(x.date||'')+'|'+(x.title||'');
      if(!m.has(k))m.set(k,{{date:x.date||'',title:x.title||'外刊精读',topic:x.topic||'其他',level:x.level||'B2',href:x.href||'#'}});
    }});
    return Array.from(m.values()).sort((a,b)=>String(b.date).localeCompare(String(a.date)));
  }}
  function sectionHtml(){{
    return `<section class="hl-history-section" id="hlHistorySection"><div class="hl-history-head"><h2>历史记录</h2><span>按主题 / 难度筛选</span></div><div class="hl-history-filters"><select id="hlTopicFilter">${{TOPICS.map(t=>`<option value="${{esc(t)}}">${{esc(t)}}</option>`).join('')}}</select><select id="hlLevelFilter">${{LEVELS.map(l=>`<option value="${{esc(l)}}">${{esc(l)}}</option>`).join('')}}</select></div><div class="hl-history-list" id="hlHistoryList"></div></section>`;
  }}
  function renderList(root){{
    const topic=root.querySelector('#hlTopicFilter')?.value||'全部';
    const level=root.querySelector('#hlLevelFilter')?.value||'全部';
    const list=root.querySelector('#hlHistoryList'); if(!list)return;
    let items=allHistory();
    if(topic!=='全部')items=items.filter(x=>(x.topic||'其他')===topic);
    if(level!=='全部')items=items.filter(x=>(x.level||'B2')===level);
    if(!items.length){{list.innerHTML='<div class="hl-history-empty">暂无符合条件的历史文章。</div>';return;}}
    list.innerHTML=items.slice(0,60).map(x=>`<a class="hl-history-item" href="${{esc(x.href)}}" target="_blank" rel="noopener"><div class="hl-history-meta"><span class="hl-history-date">${{esc(x.date)}}</span><span class="hl-history-tag">${{esc(x.topic||'其他')}}</span><span class="hl-history-tag">${{esc(x.level||'B2')}}</span></div><div class="hl-history-title">${{esc(x.title||'外刊精读')}}</div></a>`).join('');
  }}
  function addHistory(){{
    const final=document.getElementById('finalPreview'); if(!final)return;
    const phone=final.querySelector('.phone')||final.firstElementChild||final; if(!phone)return;
    phone.querySelectorAll('#hlHistorySection').forEach(x=>x.remove());
    phone.insertAdjacentHTML('beforeend',sectionHtml());
    const t=phone.querySelector('#hlTopicFilter'), l=phone.querySelector('#hlLevelFilter');
    if(t)t.onchange=()=>renderList(phone); if(l)l.onchange=()=>renderList(phone); renderList(phone);
  }}
  const oldRender=window.renderFinal;
  if(typeof oldRender==='function'&&!window.__HL_HISTORY_PATCHED__){{window.renderFinal=function(){{const r=oldRender.apply(this,arguments);saveCurrent();setTimeout(addHistory,30);return r;}};}}
  const oldOpen=window.openFinalPage;
  if(typeof oldOpen==='function'&&!window.__HL_HISTORY_OPEN_PATCHED__){{window.openFinalPage=function(){{if(typeof window.renderFinal==='function')window.renderFinal();setTimeout(()=>{{addHistory();oldOpen.apply(window,arguments);}},60);}};}}
  window.__HL_HISTORY_PATCHED__=true; window.__HL_HISTORY_OPEN_PATCHED__=true;
  setTimeout(()=>{{const f=document.getElementById('finalPreview');if(f&&!f.textContent.includes('点“生成最终页”后显示'))addHistory();}},300);
}})();
</script>
{JS_END}
'''
    if '</style>' in html:
        html = html.replace('</style>', css + '\n</style>', 1)
    else:
        html = html.replace('</head>', '<style>' + css + '</style></head>', 1)
    if '</body>' in html:
        html = html.replace('</body>', js + '\n</body>', 1)
    else:
        html += js
    EDITOR.write_text(html, encoding='utf-8')
    print('OK patched editor.html')
    print('history items:', len(history))

if __name__ == '__main__':
    main()
