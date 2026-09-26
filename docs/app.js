(() => {
  const $ = id => document.getElementById(id);
  const money = x => (x < 0 ? '-' : '+') + '$' + Math.abs(Number(x)||0).toFixed(2);
  const balance = x => '$' + Number(x||0).toFixed(2);
  const signed = (x,d=2) => (x > 0 ? '+' : '') + Number(x||0).toFixed(d);
  const cssSign = x => x < -1e-8 ? 'neg' : x > 1e-8 ? 'pos' : '';
  const dateLabel = x => {const d=new Date(String(x).slice(0,10)+'T12:00:00');return Number.isNaN(d.valueOf())? x : d.toLocaleDateString(undefined,{month:'short',day:'numeric'});};
  const dateTime = x => {const d=new Date(x);return Number.isNaN(d.valueOf()) ? 'Unknown' : d.toLocaleString();};
  const safeLink = x => {try {const u = new URL(x);return u.protocol==='https:'?u.href:null;}catch{return null;}};
  function txt(id, value, className) {$(id).textContent=String(value); if(className!==undefined) $(id).className=className;}
  function element(name,cls,content) {const n=document.createElement(name);if(cls)n.className=cls;if(content!==undefined)n.textContent=content;return n;}
  function drawChart(daily) {
    const area=$('chartArea'), tip=$('chartTooltip');area.replaceChildren();
    if(!daily.length){area.append(element('p','muted','No completed days yet.'));return;}
    const w=940,h=270,pad={l:48,r:23,t:19,b:39};
    const values=daily.map(r=>Number(r.banked_points)||0);
    const low=Math.min(0,...values),high=Math.max(0,...values);
    const range=Math.max(2,high-low),bottom=low-range*.12,top=high+range*.12;
    const x=i=>pad.l + i*((w-pad.l-pad.r)/Math.max(1,daily.length-1));
    const y=v=>pad.t+(top-v)*(h-pad.t-pad.b)/(top-bottom);
    const ns='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('role','img');svg.setAttribute('aria-label','History of cumulative challenge points by date. Select each dot to see exact points and fake dollars.');
    function add(tag,attrs,text) {const o=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))o.setAttribute(k,String(v));if(text!==undefined)o.textContent=text;svg.append(o);return o;}
    for(let i=0;i<=4;i++){const v=bottom+(top-bottom)*i/4; add('line',{x1:pad.l,y1:y(v),x2:w-pad.r,y2:y(v),class:'gridline'});add('text',{x:pad.l-9,y:y(v)+4,'text-anchor':'end',class:'axisLabel'},v.toFixed(1));}
    const last=daily.length-1;
    const tickIds=[...new Set([0,Math.round(last/2),last])];
    for(const i of tickIds)add('text',{x:x(i),y:h-12,'text-anchor':i===0?'start':i===last?'end':'middle',class:'axisLabel'},dateLabel(daily[i].market_date));
    add('path',{d:values.map((v,i)=>(i?'L':'M')+x(i)+','+y(v)).join(' '),class:'line'});
    function show(r){tip.replaceChildren();const heading=element('strong','',r.market_date+' · '+signed(r.banked_points,3)+' total pts');tip.append(heading,element('div','',signed(r.point_change,3)+' pts today · '+money(r.cash_change)+' fake today · ending '+balance(r.cash_after)));}
    daily.forEach((r,i)=>{
      const cx=x(i),cy=y(values[i]);add('circle',{cx,cy,r:5,fill:'#59dbbd',stroke:'#112334','stroke-width':2});
      const hit=add('circle',{cx,cy,r:16,fill:'transparent',class:'dot',tabindex:0,'aria-label':`${r.market_date}, ${signed(r.banked_points,3)} points`});
      hit.addEventListener('pointerenter',()=>show(r));hit.addEventListener('pointerdown',()=>show(r));hit.addEventListener('focus',()=>show(r));
    });
    area.append(svg);txt('chartCount',`${daily.length} market days`);show(daily[daily.length-1]);
  }
  function render(data){
    const updated=new Date(data.updated_at);const mins=(Date.now()-updated.valueOf())/60000;
    txt('connection',`Cloud data: ${dateTime(data.updated_at)}${mins>60?' · may be stale':''}`);
    txt('totalPoints',signed(data.display_points,3),cssSign(data.display_points));
    txt('balance',balance(data.display_balance),cssSign(data.display_balance-20));
    txt('pointDelta',data.live?`${signed(data.live.points,3)} unbanked points this round`:`${signed(data.banked_points,3)} banked points`);
    txt('cashDelta',data.live?`${money(data.live.money_change)} so far this round`:`${money(data.display_balance-20)} since $20 start`);
    txt('rounds',data.completed_rounds);
    txt('remaining',`${Math.max(0,100-data.display_points).toFixed(2)} points to go`);
    const pct=Math.max(0,Math.min(100,data.display_points/Math.max(1,data.goal_points)*100));$('progress').style.width=pct+'%';txt('pct',pct.toFixed(1)+'% complete');
    txt('marketStatus',data.error ? `${data.status} ${data.error}` : data.status);
    txt('rerollStatus',data.auto_reroll ? ('AUTO-REROLL ON · New companies once each trading day after 9:35 AM Eastern · Last pick: '+(data.last_reroll_market_date || 'waiting for first cloud round')) : 'Daily auto-reroll status unavailable.');
    txt('strategy',data.current_strategy ? data.current_strategy.toUpperCase():'WAITING FOR NEXT SESSION');
    const holdings=$('holdings');holdings.replaceChildren();
    if(!data.live || !data.live.holdings.length) holdings.append(element('p','muted','No current fake holdings. The bot automatically picks new companies during the next market session.'));
    else data.live.holdings.forEach(s=>{
      const row=element('div','holding'),lhs=element('div'),rhs=element('div','right');
      lhs.append(element('div','symbol',s.ticker),element('small','',`${(s.weight*100).toFixed(1)}% of portfolio · entry ${balance(s.entry)} · now ${balance(s.last_price)}`));
      if(s.headline && !s.headline.startsWith('No company-specific')) {const note=element('div','news');const url=safeLink(s.link);if(url){const a=element('a','',s.headline);a.href=url;a.target='_blank';a.rel='noopener noreferrer';note.append(a);}else note.textContent=s.headline; lhs.append(note);}
      rhs.append(element('strong',cssSign(s.money_change),money(s.money_change)),element('small',cssSign(s.points),signed(s.points,3)+' pts'));
      row.append(lhs,rhs);holdings.append(row);
    });
    txt('todayMoney',data.live?money(data.live.money_change):'$0.00',data.live?('dayValue '+cssSign(data.live.money_change)):'dayValue');
    txt('todayPoints',data.live?signed(data.live.points,3)+' pts':'No active round');
    txt('todayDate',data.live?dateLabel(data.live.market_date):'—');
    txt('quoteTime',data.live?'Latest saved quote: '+dateTime(data.live.quote_time):'Waiting for next live paper round.');
    const daily=data.daily||[];drawChart(daily);
    const history=$('historyRows');history.replaceChildren();
    for(const r of [...daily].reverse()) {const tr=element('tr');for(const [value,cls] of [[r.market_date,''],[signed(r.point_change,3),cssSign(r.point_change)],[money(r.cash_change),cssSign(r.cash_change)],[balance(r.cash_after),''],[(r.companies||[]).join(', ')||'—','']]) tr.append(element('td',cls,value));history.append(tr);}
    if(!daily.length){const tr=element('tr');const cell=element('td','', 'No completed rounds yet.');cell.colSpan=5;tr.append(cell);history.append(tr);}
    const scores=$('strategyScores');scores.replaceChildren();for(const [k,v] of Object.entries(data.strategy_scores||{})){const box=element('div','score',k.toUpperCase());box.append(element('strong','',signed(v,3)));scores.append(box);}
    txt('importNote',data.import_note||'');
  }
  async function refresh(){try {const r=await fetch('./data/public.json?v='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error('No published data yet');render(await r.json());}catch(e){txt('connection','Could not load saved cloud data');txt('marketStatus','Site is available, but the saved bot data could not be loaded: '+e.message);}}
  refresh();setInterval(refresh,60000);
})();
