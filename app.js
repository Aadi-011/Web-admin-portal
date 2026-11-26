(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('en-IN');
  const fmtPct = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : `${Math.round(Number(value))}%`;
  const qs = () => `site_id=${encodeURIComponent(state.siteId)}`;
  const roleLabels = {super_admin:'Super Admin',site_admin:'Site Admin',emergency_operator:'Emergency Operator',analytics_viewer:'Analytics Viewer'};
  const viewInfo = {
    dashboard:['Operational overview','Site occupancy, incidents and database status'],
    'live-map':['Site operations','Illustrative zone layout and available crowd observations'],
    warnings:['Incident management','Review incidents, maintain a status history and record response actions'],
    visitors:['Visitor services','Search the site visitor directory and review check-in records'],
    analytics:['Performance & footfall','Historical visitor series and registered model forecasts'],
    broadcast:['Communications','Record a targeted message; delivery depends on an authorized provider'],
    admins:['Access & team','Provision administrator accounts and manage role access'],
    audit:['Access & accountability','Persistent audit events for the selected site']
  };
  const state = {
    user:null, health:null, sites:[], siteId:1, siteName:'', currentView:'dashboard', chartDays:14,
    aggregation:'daily', visitorPage:1, visitorPageSize:25, visitorFilter:'all', warningFilter:'all',
    selectedZone:null, mapZoom:1, heatVisible:true, mapFilter:'all', loading:false, booted:false, searchTimer:null,
    apiErrorShown:false
  };

  function toast(title, message, kind='success') {
    const region=$('#toastRegion'); if (!region) return;
    const node=document.createElement('div'); node.className='toast';
    node.innerHTML=`<span class="toast-icon" style="${kind==='error'?'color:#b72f2a;background:#fff0ef':''}"><svg class="icon"><use href="#${kind==='error'?'i-alert':'i-check'}"/></svg></span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
    region.appendChild(node); setTimeout(()=>{node.style.opacity='0';node.style.transform='translateY(8px)';setTimeout(()=>node.remove(),230);},3800);
  }

  function postOptions(body) { return {method:'POST',body:JSON.stringify(body)}; }
  async function api(path, options={}) {
    let response;
    try {
      response=await fetch(path,{cache:'no-store',credentials:'same-origin',...options,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
    } catch (error) { throw new Error('Could not reach the API. Check that the dashboard server is running.'); }
    let payload={}; try {payload=await response.json();} catch (_) { /* non-JSON error body */ }
    if (!response.ok) {
      const error=new Error(payload.error||payload.detail||`Request failed (${response.status})`); error.status=response.status;
      if (response.status===401 && !path.startsWith('/api/login') && !path.startsWith('/api/session')) expireSession();
      throw error;
    }
    return payload;
  }

  function setHealth(health) {
    state.health=health||null;
    const demoHint=$('#demoCredentials');if(demoHint)demoHint.hidden=!(health?.sample_data||health?.mode==='demo');
    const status=$('#connectionStatus'), source=$('#dataSourceLabel');
    if (status) status.textContent=health?.connected?'Database connected':'Database unavailable';
    if (source) source.textContent=health?.connected?`${health.engine==='sqlserver'?'SQL Server':'SQLite'} · ${health.database||'configured'}`:'Check the API and database configuration';
    const banner=$('#modeBanner');
    if (banner && health) {
      banner.hidden=false;
      if (health.sample_data || health.mode==='demo') {
        $('#modeBannerTitle').textContent='Development sample data';
        $('#modeBannerText').textContent='Displayed records are illustrative and are not connected to the YatraFlow mobile app. No GPS feed, push delivery or emergency-service dispatch provider is connected; local broadcasts and dispatch requests are stored only.';
      } else if (!health.connected) {
        $('#modeBannerTitle').textContent='Database unavailable';
        $('#modeBannerText').textContent='Operational values are not verified. The API is not returning a healthy database connection; no sample values are substituted.';
      } else {
        $('#modeBannerTitle').textContent='Configured database · integrations not verified';
        const mobile=health.mobile_api_key_configured?'Mobile ingestion key is configured; this does not confirm an active feed.':'Mobile app/GPS ingestion is not configured.';
        $('#modeBannerText').textContent=`${mobile} Push delivery and emergency-service dispatch are not connected. Broadcasts and dispatch requests are persisted as pending integration; delivery is not claimed.`;
      }
    }
    const badge=$('#liveBadge'); if (badge) { badge.hidden=state.user?.role_key==='analytics_viewer'; badge.innerHTML=`<span class="pulse-dot"></span>${health?.sample_data?'SAMPLE DATA':health?.connected?'DATABASE':'UNAVAILABLE'}`; }
    const sourceTag=health?.sample_data?'Development sample':health?.connected?'Database source':'Unavailable';
    ['#mapSourceLabel','#liveMapSource'].forEach(sel=>{const el=$(sel);if(el)el.textContent=sourceTag;});
    if ($('#analyticsDataMode')) $('#analyticsDataMode').textContent=health?.sample_data?'Development sample series (not observed live data)':health?.connected?'Configured footfall database':'Footfall data unavailable';
    if ($('#analyticsIntegrationStatus')) $('#analyticsIntegrationStatus').textContent=health?.mobile_api_key_configured?'Ingestion key configured · feed not verified':'Mobile/GPS integration not connected';
    if ($('#mapIntegrationNote')) $('#mapIntegrationNote').textContent=health?.mobile_api_key_configured?'A keyed ingestion endpoint is configured, but no live map tiles or responder GPS positions are provided.':'No mobile/GPS location feed is connected. This illustrative map does not show live responder positions.';
  }
  async function refreshHealth(quiet=true) {
    try { setHealth(await api('/api/health')); }
    catch (error) { setHealth({connected:false,engine:'sqlite',sample_data:false}); if(!quiet)toast('Database health check failed',error.message,'error'); }
  }

  function showLogin(message='') {
    $('#appShell').hidden=true; $('#authGate').hidden=false;
    $('#loginError').textContent=message; $('#loginPassword').value=''; state.user=null; state.booted=false;
    setTimeout(()=>$('#loginEmail')?.focus(),0);
  }
  function expireSession() {
    if (!state.user) return;
    state.user=null; state.booted=false; showLogin('Your session expired. Please sign in again.');
  }
  function roleAllowed(element) {
    const allowed=(element.dataset.roleAllow||'').split(',').map(s=>s.trim()).filter(Boolean);
    return !allowed.length || allowed.includes(state.user?.role_key);
  }
  function applyRoleUI() {
    $$('[data-role-allow]').forEach(el=>{el.hidden=!roleAllowed(el);});
    const role=state.user?.role_key||'';
    if (role==='analytics_viewer') {
      $$('.nav-item[data-view]').forEach(item=>{item.hidden=item.dataset.view!=='analytics';});
      state.currentView='analytics';
    } else {
      $$('.nav-item[data-view]').forEach(item=>{if(!item.hasAttribute('data-role-allow'))item.hidden=false;});
      if(role==='emergency_operator') $$('.nav-item[data-view="admins"],.nav-item[data-view="audit"]').forEach(item=>item.hidden=true);
    }
    const profileName=$('#profileName'); const profileRole=$('#profileRole');
    if(profileName)profileName.textContent=state.user?.full_name||'Administrator';
    if(profileRole)profileRole.textContent=roleLabels[role]||'Administrator';
    const avatar=state.user?.full_name?.split(/\s+/).slice(0,2).map(x=>x[0]||'').join('').toUpperCase()||'AD';
    $('#profileButton').textContent=avatar; $('#profileButton').setAttribute('aria-label',`Open administrator menu for ${state.user?.full_name||'administrator'}`);
    $('.admin-avatar').textContent=avatar;
    $('.admin-copy strong').textContent=state.user?.full_name||'Administrator';
    $('.admin-copy small').textContent=roleLabels[role]||'Administrator';
    const assignedSite=Number(state.user?.site_id||1); state.siteId=assignedSite;
  }
  function activateApp(user) {
    state.user=user; $('#authGate').hidden=true; $('#appShell').hidden=false; state.booted=true;
    applyRoleUI(); state.currentView=user.role_key==='analytics_viewer'?'analytics':'dashboard';
    if(state.currentView==='analytics'&&user.role_key==='analytics_viewer') $('#liveBadge').hidden=true;
    loadSites().then(()=>switchView(state.currentView,true)).catch(()=>switchView(state.currentView,true));
  }
  async function boot() {
    await refreshHealth(true);
    try { const session=await api('/api/session'); if(session.authenticated&&session.user){activateApp(session.user);return;} }
    catch (_) { /* sign-in remains visible and can show database feedback */ }
    showLogin();
  }
  $('#loginForm').addEventListener('submit',async event=>{
    event.preventDefault(); const button=$('#loginSubmit'); const email=$('#loginEmail').value.trim(); const password=$('#loginPassword').value;
    $('#loginError').textContent='';
    if(!email||!password){$('#loginError').textContent='Enter your email and password.';return;}
    button.disabled=true; button.textContent='Checking credentials…';
    try { const result=await api('/api/login',postOptions({email,password})); if(!result.authenticated)throw new Error('Sign in could not be completed'); activateApp(result.user); }
    catch(error){$('#loginError').textContent=error.message;}
    finally{button.disabled=false;button.textContent='Sign in securely';}
  });

  function siteMonogram(site) { return String(site?.name||'YS').split(/\s+/).slice(0,2).map(x=>x[0]||'').join('').toUpperCase(); }
  function renderSites(items) {
    state.sites=Array.isArray(items)?items:[]; const menu=$('#siteMenu');
    if(!menu)return;
    if(!state.sites.length){menu.innerHTML='<div class="popover-option">No sites are assigned to this administrator.</div>';return;}
    menu.innerHTML=state.sites.map(site=>`<button class="popover-option" type="button" data-site-id="${Number(site.id)}"><svg class="icon"><use href="#${Number(site.id)===state.siteId?'i-check':'i-map'}"/></svg><span>${escapeHtml(site.name)}<small>${escapeHtml(site.city)}</small></span></button>`).join('');
    const current=state.sites.find(s=>Number(s.id)===Number(state.siteId))||state.sites[0]; setSite(current,false);
  }
  async function loadSites() {
    const result=await api('/api/sites'); renderSites(result.items||[]);
  }
  function setSite(site,notify=true) {
    if(!site)return;
    state.siteId=Number(site.id||1); state.siteName=site.full_name||`${site.name||''}, ${site.city||''}`.replace(/^, |, $/g,'');
    $('#sidebarSiteName').textContent=state.siteName.replace(', ',' · ');
    $('.site-monogram').textContent=siteMonogram(site);
    if(state.currentView==='dashboard')$('#pageTitleText').textContent=state.siteName;
    $$('#siteMenu [data-site-id]').forEach(button=>button.querySelector('use')?.setAttribute('href',Number(button.dataset.siteId)===state.siteId?'#i-check':'#i-map'));
    if(notify){$('#siteMenu').hidden=true;$('#siteButton').setAttribute('aria-expanded','false');state.visitorPage=1;toast('Site changed',`Showing ${state.siteName}.`);loadViewData(state.currentView);}
  }
  $('#siteButton').addEventListener('click',()=>{if(state.user?.role_key==='analytics_viewer')return;$('#siteMenu').hidden=!$('#siteMenu').hidden;$('#siteButton').setAttribute('aria-expanded',String(!$('#siteMenu').hidden));});
  $('#siteMenu').addEventListener('click',event=>{const button=event.target.closest('[data-site-id]');if(!button)return;const site=state.sites.find(s=>Number(s.id)===Number(button.dataset.siteId));setSite(site,true);});
  document.addEventListener('click',event=>{if(!$('#siteButton').contains(event.target)&&!$('#siteMenu').contains(event.target)){$('#siteMenu').hidden=true;$('#siteButton').setAttribute('aria-expanded','false');}});

  function switchView(view,load=true) {
    if(!viewInfo[view])return;
    if(state.user?.role_key==='analytics_viewer'&&view!=='analytics'){toast('Read-only analytics access','Your role is limited to analytics.','error');return;}
    if(state.user?.role_key==='emergency_operator'&&['admins','audit'].includes(view)){toast('Access restricted','Administrator management is not enabled for your role.','error');return;}
    state.currentView=view;
    $$('[data-view-panel]').forEach(panel=>panel.hidden=panel.dataset.viewPanel!==view);
    $$('.nav-item[data-view]').forEach(item=>{const active=item.dataset.view===view;item.classList.toggle('is-active',active);if(active)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');});
    const [eyebrow,subtitle]=viewInfo[view];$('#pageEyebrow').textContent=eyebrow.toUpperCase();$('#pageSubtitle').textContent=subtitle;
    $('#pageTitleText').textContent=view==='dashboard'?state.siteName:view==='live-map'?`${state.siteName} · zone map`:({warnings:'Warning center',visitors:'Visitor directory',analytics:'Footfall analytics',broadcast:'Broadcast alerts',admins:'Manage admins',audit:'Audit log'}[view]||view);
    $('#liveBadge').hidden=['broadcast','admins','audit'].includes(view)||state.user?.role_key==='analytics_viewer';
    if(window.innerWidth<=760)closeSidebar(); window.scrollTo({top:0,behavior:'smooth'});
    if(load)loadViewData(view);
  }
  $$('.nav-item[data-view]').forEach(item=>item.addEventListener('click',()=>switchView(item.dataset.view)));
  $$('[data-view-link]').forEach(item=>item.addEventListener('click',()=>switchView(item.dataset.viewLink)));

  const modalBackdrop=$('#modalBackdrop'); const modalContent=$('#modalContent');
  function openModal(markup) { modalContent.innerHTML=markup; modalBackdrop.hidden=false; setTimeout(()=>modalContent.querySelector('input:not([type=hidden]),textarea,select,button:not([data-modal-close])')?.focus(),0); }
  function closeModal(){modalBackdrop.hidden=true;modalContent.innerHTML='';}
  $('#modalClose').addEventListener('click',closeModal); modalBackdrop.addEventListener('click',event=>{if(event.target===modalBackdrop)closeModal();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeModal();$('#notificationsPanel').hidden=true;$('#profileMenu').hidden=true;$('#siteMenu').hidden=true;closeSidebar();}if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){event.preventDefault();$('#globalSearch').focus();}});

  function setTableState(id,message,kind='info',columns=6) {const el=$(`#${id}`);if(!el)return;el.innerHTML=`<tr><td colspan="${columns}" style="padding:28px;text-align:center;color:${kind==='error'?'#b72f2a':'#8b8999'}">${escapeHtml(message)}${kind==='error'?' <button class="btn btn-sm" data-action="refresh">Try again</button>':''}</td></tr>`;}
  function timeAgo(value,minutes) {
    if(minutes!==undefined&&minutes!==null&&Number.isFinite(Number(minutes)))return Number(minutes)<=0?'Just now':`${Math.round(Number(minutes))} min ago`;
    const d=new Date(value||'');if(Number.isNaN(d.getTime()))return '—';
    const diff=Math.max(0,Math.round((Date.now()-d.getTime())/60000));return diff<1?'Just now':diff<60?`${diff} min ago`:`${Math.floor(diff/60)} hr ago`;
  }
  function dateTime(value) {const d=new Date(value||'');return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat('en-IN',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'}).format(d);}
  function searchText(...parts){return parts.filter(Boolean).join(' ').toLowerCase();}
  function applySearch(){const query=$('#globalSearch').value.trim().toLowerCase();$$('[data-searchable]').forEach(row=>{row.classList.toggle('search-hidden',Boolean(query)&&!`${row.dataset.search||''} ${row.textContent}`.toLowerCase().includes(query));});}
  $('#globalSearch').addEventListener('input',()=>{applySearch();clearTimeout(state.searchTimer);if(state.currentView==='visitors'){state.visitorPage=1;state.searchTimer=setTimeout(()=>loadVisitors(),260);}});

  function statusClass(status) {const s=String(status||'').toLowerCase();if(['assigned','acknowledged','awaiting acknowledgement','assistance dispatched'].some(x=>s.includes(x)))return 'assigned';if(['resolved','checked in','on site','on shift'].some(x=>s.includes(x)))return 'resolved';if(['review','pending','submitted','open','monitoring'].some(x=>s.includes(x)))return 'review';if(['inactive','cancelled','failed'].some(x=>s.includes(x)))return 'inactive-status';return '';}
  function renderMetrics(data) {
    const m=data.metrics||{};const values=$$('#view-dashboard .metric-value');
    if(values[0])values[0].textContent=fmt(m.people_inside);
    if(values[1])values[1].textContent=fmtPct(m.capacity_used);
    if(values[2])values[2].textContent=fmt(m.active_rescue_requests);
    if(values[3])values[3].textContent=m.predicted_tomorrow==null?'Unavailable':fmt(m.predicted_tomorrow);
    const notes=$$('#view-dashboard .metric-note');
    if(notes[0]){notes[0].textContent=m.people_change_percent==null?'Change not available':`${Number(m.people_change_percent)>=0?'+':''}${Number(m.people_change_percent).toFixed(1)}% vs prior hour · ${m.occupancy_source||'database'}`;notes[0].className='metric-note';}
    if(notes[1])notes[1].textContent=`Capacity ${fmt(m.capacity)} · source: site record`;
    if(notes[2])notes[2].textContent=`${fmt(m.unassigned_rescue_requests)} unassigned · counts from incident status records`;
    if(notes[3]){notes[3].textContent=m.forecast_available?`Model: ${m.forecast_model||'recorded forecast'} · ${m.forecast_updated_at?dateTime(m.forecast_updated_at):'timestamp unavailable'}`:'No model forecast record. Forecast unavailable.';notes[3].className='metric-note';}
    const bar=$('#view-dashboard .capacity-progress span');if(bar)bar.style.width=`${Math.max(0,Math.min(100,Number(m.capacity_used)||0))}%`;
    const date=$('#currentDate');if(date&&data.as_of){const d=new Date(`${data.as_of}T12:00:00+05:30`);date.textContent=new Intl.DateTimeFormat('en-IN',{day:'2-digit',month:'short',year:'numeric',timeZone:'Asia/Kolkata'}).format(d);}
    const src=$('#occupancySource');if(src)src.textContent=`Source: ${m.occupancy_source||'unknown'}${m.source_updated_at?' · updated '+dateTime(m.source_updated_at):''}`;
    const count=$('#dashboardWarningCount');if(count)count.textContent=`${fmt(m.open_incidents)} open`;
    const navCount=$('#warningsNavCount');if(navCount)navCount.textContent=fmt(m.open_incidents);
    const siteHeader=$('#zoneSiteHeader');if(siteHeader)siteHeader.textContent=`${fmt(m.people_inside)} of ${fmt(m.capacity)} site capacity`;
    const siteDensity=$('#zoneSiteDensity');if(siteDensity)siteDensity.textContent=`${fmtPct(m.capacity_used)} site`;
  }

  function renderAlertFeed(items) {
    const feed=$('.alert-feed');if(!feed)return;
    if(!items.length){feed.innerHTML='<div class="feed-row"><div class="feed-body"><strong>No alert records</strong><span>No alerts are stored for this site.</span></div></div>';return;}
    feed.innerHTML=items.slice(0,5).map(item=>`<div class="feed-row" data-searchable data-search="${escapeHtml(searchText(item.category,item.title,item.description))}"><span class="feed-tag ${String(item.category).toLowerCase()==='weather'?'weather':String(item.category).toLowerCase()==='security'?'security':''}">${escapeHtml(String(item.category||'info').toUpperCase())}</span><div class="feed-body"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.description||dateTime(item.created_at))}</span></div></div>`).join('');
  }
  function renderNotificationFeed(items) {
    const root=$('#notificationItems');const count=$('#notificationCount');if(!root)return;
    if(count)count.textContent=String(items.length);
    root.innerHTML=items.length?items.slice(0,6).map((item,index)=>`<div class="notification-item"><span class="mini-dot" style="background:${index===0?'#d84640':index===1?'#e29a32':'#6b55b5'}"></span><p><strong>${escapeHtml(item.title)}</strong> ${escapeHtml(item.description||'')}<small>${escapeHtml(dateTime(item.created_at))} · database record</small></p></div>`).join(''):'<div class="notification-item"><p>No alert records are stored for this site.</p></div>';
  }

  function renderIncidentCards(items) {
    const list=$('#dashboardIncidents');if(!list)return;
    if(!items.length){list.innerHTML='<div class="incident-card report-card"><p class="incident-copy" style="margin:0">No active incidents are recorded for this site.</p></div>';return;}
    list.innerHTML=items.map(item=>{const sos=String(item.type).toUpperCase()==='SOS';const report=String(item.type).toLowerCase()==='report';
      return `<div class="incident-card ${report?'report-card':''}" data-searchable data-search="${escapeHtml(searchText(item.id,item.type,item.title,item.visitor_name,item.zone_name,item.status))}"><div class="incident-meta"><span class="incident-type ${report?'report':''}">${escapeHtml(sos?'SOS':String(item.type||'INCIDENT').toUpperCase())}</span><strong class="incident-id">${escapeHtml(item.id)}</strong><time class="incident-time">${escapeHtml(timeAgo(item.created_at,item.reported_minutes))}</time></div><p class="incident-copy"><strong>${escapeHtml(item.visitor_name||item.title)}</strong> · ${escapeHtml(item.zone_name||'Zone unavailable')} · ${escapeHtml(item.status)}</p><div class="incident-actions"><button class="btn btn-sm" data-action="incident-detail" data-incident-id="${escapeHtml(item.id)}">Open record</button>${sos?`<button class="btn btn-danger btn-sm" data-action="incident-detail" data-incident-id="${escapeHtml(item.id)}">Response</button>`:''}</div></div>`;
    }).join('');
  }
  function renderIncidentTable(items) {
    const body=$('#warningTable');if(!body)return;
    if(!items.length){setTableState('warningTable','No incident records match the selected site/filter.','info',7);return;}
    body.innerHTML=items.map(item=>`<tr data-searchable data-kind="${escapeHtml(String(item.type||'').toLowerCase())}" data-status="${escapeHtml(String(item.status||'').toLowerCase())}" data-search="${escapeHtml(searchText(item.id,item.type,item.title,item.zone_name,item.visitor_name,item.assigned_to,item.status))}"><td><span class="priority ${String(item.priority).toLowerCase()==='critical'?'':String(item.priority).toLowerCase()==='medium'?'medium':'low'}">● ${escapeHtml(item.priority||'Info')}</span></td><td><span class="table-primary">${escapeHtml(item.id)}</span><span class="table-secondary">${escapeHtml(item.title)} · ${escapeHtml(item.description||'')}</span></td><td>${escapeHtml(item.zone_name||'—')}</td><td>${escapeHtml(timeAgo(item.created_at,item.reported_minutes))}</td><td>${escapeHtml(item.assigned_to||'Unassigned')}</td><td><span class="status-pill ${statusClass(item.status)}">${escapeHtml(item.status)}</span></td><td><button class="btn btn-sm" data-action="incident-detail" data-incident-id="${escapeHtml(item.id)}">View</button></td></tr>`).join('');
    applyWarningFilter();applySearch();
  }
  function updateWarningSummary(items) {
    const cards=$$('#view-warnings .summary-card');if(cards.length<3)return;
    const terminal=new Set(['resolved','closed','complete','completed','cancelled','failed']);const open=items.filter(i=>!terminal.has(String(i.status).toLowerCase()));const resolved=items.filter(i=>String(i.status).toLowerCase()==='resolved');
    const ages=open.map(i=>Number(i.reported_minutes)).filter(Number.isFinite);cards[0].querySelector('strong').textContent=fmt(open.length);cards[0].querySelector('span').textContent=`${open.filter(i=>!i.assigned_to).length} unassigned`;cards[1].querySelector('strong').textContent=ages.length?`${Math.round(ages.reduce((a,b)=>a+b,0)/ages.length)} min`:'—';cards[1].querySelector('span').textContent='Open incident age from stored records';cards[2].querySelector('strong').textContent=fmt(resolved.length);cards[2].querySelector('span').textContent='Resolved records in returned queue';
    const sos=items.filter(i=>String(i.type).toLowerCase()==='sos').length;const chips={all:`All incidents · ${items.length}`,open:`Open · ${open.length}`,sos:`SOS · ${sos}`};Object.entries(chips).forEach(([key,label])=>{const button=$(`[data-warning-filter="${key}"]`);if(button)button.textContent=label;});
  }
  function renderIncidents(items, metrics=null) {
    renderIncidentCards((items||[]).filter(item=>!['resolved','closed','complete','completed','cancelled','failed'].includes(String(item.status).toLowerCase())).slice(0,3));
    renderIncidentTable(items||[]);updateWarningSummary(items||[]);const openCount=(items||[]).filter(i=>!['resolved','closed','complete','completed','cancelled','failed'].includes(String(i.status).toLowerCase())).length;$('#warningsNavCount').textContent=fmt(metrics?.open_incidents??openCount);if(metrics)$('#dashboardWarningCount').textContent=`${fmt(metrics.open_incidents)} open`;
  }

  function zoneFill(density){
    if(density===null||density===undefined||density===''||!Number.isFinite(Number(density)))return 'rgba(223,220,230,.88)';
    if(!state.heatVisible)return 'rgba(255,255,255,.88)';
    const value=Number(density);return value>=90?'rgba(203,52,48,.30)':value>=75?'rgba(233,140,42,.28)':value>=50?'rgba(224,193,70,.24)':'rgba(52,151,92,.22)';
  }
  function renderZones(items) {
    if(!Array.isArray(items))return;
    const hasObservations=items.some(z=>z.density_percent!==null&&z.density_percent!==undefined&&Number.isFinite(Number(z.density_percent)));
    $$('[data-map-stage]').forEach(stage=>{
      let overlay=$('.map-no-feed',stage);
      if(!hasObservations){
        if(!overlay){overlay=document.createElement('div');overlay.className='map-no-feed';stage.appendChild(overlay);}
        overlay.textContent=items.length?'No crowd observations are available from this database source. The zone layout is illustrative; no GPS or live map feed is connected.':'No zones are configured for this site.';
      }else if(overlay)overlay.remove();
    });
    $$('[data-map-stage] .map-svg').forEach(svg=>{
      let layer=$('.zone-layer',svg);if(!layer){layer=document.createElementNS('http://www.w3.org/2000/svg','g');layer.setAttribute('class','zone-layer');svg.appendChild(layer);}
      layer.replaceChildren();
      const count=items.length;if(!count)return;
      const columns=Math.min(4,Math.max(1,Math.ceil(Math.sqrt(count))));const rows=Math.ceil(count/columns);
      const margin=22,gap=12,cellWidth=(760-2*margin-(columns-1)*gap)/columns,cellHeight=(360-2*margin-(rows-1)*gap)/rows;
      items.forEach((zone,index)=>{
        const col=index%columns,row=Math.floor(index/columns),x=margin+col*(cellWidth+gap),y=margin+row*(cellHeight+gap),density=zone.density_percent==null?null:Number(zone.density_percent);
        const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.setAttribute('class','zone');group.setAttribute('role','button');group.setAttribute('tabindex','0');group.setAttribute('aria-label',`${zone.name}, ${density==null?'no crowd observation':`${fmtPct(density)} density`}`);group.dataset.zone=zone.name;group.dataset.zoneId=String(zone.id);group.dataset.density=density==null?'':String(density);
        const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');rect.setAttribute('class','zone-fill');rect.setAttribute('x',String(x));rect.setAttribute('y',String(y));rect.setAttribute('width',String(cellWidth));rect.setAttribute('height',String(cellHeight));rect.setAttribute('rx','13');rect.style.fill=zoneFill(density);
        const label=document.createElementNS('http://www.w3.org/2000/svg','text');label.setAttribute('class','zone-name');label.setAttribute('x',String(x+14));label.setAttribute('y',String(y+27));label.textContent=zone.name.length>24?`${zone.name.slice(0,22)}…`:zone.name;
        const sub=document.createElementNS('http://www.w3.org/2000/svg','text');sub.setAttribute('class','zone-sub');sub.setAttribute('x',String(x+14));sub.setAttribute('y',String(y+47));sub.textContent=density==null?'No observation':`${fmtPct(density)} density`;
        group.append(rect,label,sub);layer.appendChild(group);
      });
    });
    const list=$('.zone-list');if(list){list.innerHTML=items.length?items.map(z=>{const density=z.density_percent==null?null:Number(z.density_percent);return `<button type="button" class="zone-row ${state.selectedZone===z.name?'is-selected':''}" data-zone-select="${escapeHtml(z.name)}" data-density="${density??''}" data-zone-id="${Number(z.id)}"><span class="zone-row-top"><span>${escapeHtml(z.name)}</span><span>${fmtPct(density)}</span></span><span class="zone-bar"><span style="width:${Math.max(0,Math.min(100,density||0))}%"></span></span><span class="zone-meta"><span>${fmt(z.current_visitors)} people · cap ${fmt(z.capacity)}</span><span>${escapeHtml(z.warning_status||z.level||'No observation')}</span></span></button>`;}).join(''):'<p class="data-note">No zones are configured for this site.</p>';}
    if(items.length){const selected=items.find(z=>z.name===state.selectedZone)||items.find(z=>z.density_percent!=null)||items[0];selectZone(selected.name,selected.density_percent);}
    applyMapFilter();
  }
  function selectZone(name,density){state.selectedZone=name;$$('.map-svg .zone').forEach(zone=>zone.classList.toggle('selected',zone.dataset.zone===name));$$('[data-zone-select]').forEach(row=>row.classList.toggle('is-selected',row.dataset.zoneSelect===name));
    const value=density==null?'density unavailable':`${fmtPct(density)} density`;const dash=$('#mapReading'),live=$('#liveMapReading');if(dash)dash.textContent=`${name} zone · ${value}`;if(live)live.textContent=`Selected zone · ${name} · ${value}`;
  }

  function renderVisitors(result) {
    const body=$('#visitorTable');if(!body)return;const items=result.items||[];
    if(!items.length)setTableState('visitorTable','No visitor records match the selected filters.','info',7);
    else body.innerHTML=items.map(v=>{const presence=v.needs_assistance?`Assistance flagged · ${v.status||'presence unknown'}`:(v.status||'Unknown');return `<tr data-searchable data-search="${escapeHtml(searchText(v.id,v.full_name,v.pass_id,v.zone_name,v.pass_type,v.status,v.needs_assistance?'assistance':''))}"><td><span class="table-primary">${escapeHtml(v.full_name)}</span><span class="table-secondary">Visitor ID · ${escapeHtml(v.id)}</span></td><td>${escapeHtml(v.pass_id)}</td><td>${escapeHtml(v.zone_name||'—')}</td><td>${escapeHtml(v.entry_time||'—')}</td><td>${escapeHtml(v.pass_type||'—')}</td><td><span class="status-pill ${v.needs_assistance?'assigned':statusClass(v.status)}">${escapeHtml(presence)}</span></td><td><button class="btn btn-sm" data-action="visitor-detail" data-visitor-id="${escapeHtml(v.id)}">View</button></td></tr>`;}).join('');
    const cards=$$('#view-visitors .summary-card');if(cards.length>=3){cards[0].querySelector('strong').textContent=fmt(state.lastDashboard?.metrics?.people_inside);cards[0].querySelector('span').textContent=state.lastDashboard?.metrics?.occupancy_source||'Source unavailable';cards[1].querySelector('strong').textContent=fmt(state.lastDashboard?.metrics?.checked_in_today);cards[1].querySelector('span').textContent='Site/database check-in total';cards[2].querySelector('strong').textContent=fmt(result.summary?.needs_assistance);cards[2].querySelector('span').textContent=`${fmt(result.total)} matching records · page ${result.page}/${result.pages}`;}
    const summary=result.summary||{};const labels={all:`All visitors · ${fmt(summary.visitor_records)}`,assistance:`Needs assistance · ${fmt(summary.needs_assistance)}`,vip:`VIP passes · ${fmt(summary.vip_passes)}`};Object.entries(labels).forEach(([key,label])=>{const button=$(`[data-visitor-filter="${key}"]`);if(button)button.textContent=label;});
    const paging=$('#visitorPaging');if(paging)paging.innerHTML=`<span>Page ${result.page} of ${result.pages} · ${fmt(result.total)} records</span><span><button class="btn btn-sm" data-action="visitor-page" data-page="${Math.max(1,result.page-1)}" ${result.page<=1?'disabled':''}>Previous</button> <button class="btn btn-sm" data-action="visitor-page" data-page="${Math.min(result.pages,result.page+1)}" ${result.page>=result.pages?'disabled':''}>Next</button></span>`;
    applySearch();
  }
  function renderAdmins(items) {
    const body=$('#adminTable');if(!body)return;
    const caption=$('#adminTableCaption');if(caption)caption.textContent=`Administrator accounts for ${state.siteName||'the selected site'}.`;
    if(!items.length)setTableState('adminTable','No administrator accounts are recorded for this site.','info',6);
    else {
      const colors=['','green','orange','blue'];body.innerHTML=items.map((a,index)=>{const initials=String(a.full_name||'NA').split(/\s+/).slice(0,2).map(p=>p[0]||'').join('').toUpperCase();const accountClass=!a.active?'inactive-status':a.account_status==='Active'?'resolved':'pending-status';return `<tr data-searchable data-search="${escapeHtml(searchText(a.full_name,a.email,a.role,a.access_scope,a.shift_status,a.account_status))}"><td><div class="person-cell"><span class="person-avatar ${colors[index%colors.length]}">${escapeHtml(initials)}</span><span><span class="table-primary">${escapeHtml(a.full_name)}</span><span class="table-secondary">${escapeHtml(a.email)}</span></span></div></td><td>${escapeHtml(a.role)}</td><td>${escapeHtml(a.access_scope)}</td><td><span class="status-pill ${accountClass}">${escapeHtml(a.active?a.account_status:'Inactive')}</span></td><td>${escapeHtml(a.last_login?dateTime(a.last_login):a.last_active||'Never')}</td><td><button class="btn btn-sm" data-action="admin-options" data-admin-id="${escapeHtml(a.id)}" data-admin-name="${escapeHtml(a.full_name)}">${state.user?.role_key==='super_admin'?'Manage':'View'}</button></td></tr>`;}).join('');
    }
    const cards=$$('#view-admins .summary-card');if(cards.length>=3){cards[0].querySelector('strong').textContent=fmt(items.filter(a=>a.active).length);cards[0].querySelector('span').textContent='Active administrator accounts';cards[1].querySelector('strong').textContent=fmt(items.filter(a=>String(a.shift_status).toLowerCase().includes('on shift')).length);cards[1].querySelector('span').textContent='Shift labels from the admin record';cards[2].querySelector('strong').textContent=fmt(items.filter(a=>a.active&&!String(a.account_status).toLowerCase().includes('active')).length);cards[2].querySelector('span').textContent='Need local credential setup';}
    applySearch();
  }
  function renderBroadcasts(items) {
    const list=$('#broadcastHistoryList');if(!list)return;if(!items.length){list.innerHTML='<div class="broadcast-record"><p>No broadcast records are stored for this site.</p></div>';return;}
    list.innerHTML=items.map(item=>`<div class="broadcast-record"><div class="record-head"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(dateTime(item.created_at))}</span></div><p>${escapeHtml(item.message)}</p><div class="record-meta"><span>${escapeHtml(item.category||item.priority||'Information')} · ${escapeHtml(String(item.priority||'info').toUpperCase())}</span><span>${escapeHtml(item.audience||'Selected audience')}</span><span>${escapeHtml(item.delivery_status||'unknown')} · not delivered</span></div></div>`).join('');
  }
  function renderAudit(items) {
    const body=$('#auditTable');if(!body)return;if(!items.length){setTableState('auditTable','No audit events are recorded yet.','info',5);return;}
    body.innerHTML=items.map(row=>`<tr data-searchable data-search="${escapeHtml(searchText(row.actor,row.entity_type,row.entity_id,row.action,row.detail))}"><td>${escapeHtml(dateTime(row.created_at))}</td><td>${escapeHtml(row.actor)}</td><td><span class="status-pill review">${escapeHtml(row.action)}</span></td><td>${escapeHtml(row.entity_type)} · ${escapeHtml(row.entity_id)}</td><td>${escapeHtml(row.detail||'')}</td></tr>`).join('');applySearch();
  }

  function renderAnalytics(data) {
    if(!data||!Array.isArray(data.labels))return;const max=16000;const x0=58,x1=742,y0=28,y1=216;const n=Math.max(data.labels.length,2);const x=i=>x0+(x1-x0)*i/(n-1);const y=v=>y1-(Math.max(0,Math.min(max,Number(v)||0))/max)*(y1-y0);
    const points=values=>(values||[]).map((v,i)=>v==null?null:`${x(i).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean).join(' ');
    $$('.footfall-chart').forEach(svg=>{const s=data.series||{};$('.series-2024',svg)?.setAttribute('points',points(s.avg_2024));$('.series-2025',svg)?.setAttribute('points',points(s.avg_2025));const actual=$$('.series-actual',svg);if(actual[0])actual[0].setAttribute('points',points(s.actual_2026));if(actual[1])actual[1].setAttribute('points',points(s.forecast_2026));$('.series-prediction',svg)?.setAttribute('points',points(s.forecast_2026));const index=Math.max(0,Math.min(n-1,Number(data.current_index)||0));const guide=$('.guide',svg);if(guide){guide.setAttribute('x1',x(index));guide.setAttribute('x2',x(index));guide.style.display=data.labels.length?'':'none';}const labels=$$('.date-label',svg);labels.forEach((el,i)=>{const pos=Math.round((n-1)*i/Math.max(labels.length-1,1));el.textContent=data.labels[pos]||'';el.setAttribute('x',Math.max(x0,Math.min(x1-42,x(pos)-(i===0?0:20))));});});
    const rangeLabel=$('.chart-range-label');if(rangeLabel)rangeLabel.textContent=`${data.days} days · ${data.aggregation||'daily'} records`;
    const empty=$('#view-analytics .trend-empty');if(empty)empty.hidden=Boolean(data.labels.length);
    const summary=data.summary||{};const kpis=$$('#view-analytics .analytics-kpi');if(kpis.length>=3){kpis[0].querySelector('strong').textContent=fmt(summary.visitors_today);kpis[0].querySelector('small').textContent='Database check-in total';const dwell=Number(summary.average_dwell_minutes||0);kpis[1].querySelector('strong').textContent=dwell?`${Math.floor(dwell/60)}h ${String(dwell%60).padStart(2,'0')}m`:'—';kpis[1].querySelector('small').textContent='Average dwell from site record';kpis[2].querySelector('strong').textContent=summary.forecast_available?fmt(summary.predicted_tomorrow):'Unavailable';kpis[2].querySelector('small').textContent=summary.forecast_available?`Recorded model · ${summary.peak_window||'time unavailable'}`:'No model forecast record';}
    $('#analyticsForecastStatus').textContent=data.forecast?.available?`${data.forecast.model_name||'Recorded model'} · generated ${data.forecast.generated_at?dateTime(data.forecast.generated_at):'time unavailable'}`:'No model forecast record is available';
    if($('#analyticsDataMode'))$('#analyticsDataMode').textContent=data.data_mode==='demo'?'Development sample series (generated illustrative history)': 'Configured footfall table · source timestamps stored by date';
    const caption=$('#analyticsCaption');if(caption)caption.textContent=data.data_mode==='demo'?'Development sample data · not live':'Database footfall series';
  }

  async function loadDashboard() {
    const data=await api(`/api/dashboard?${qs()}`);state.lastDashboard=data;
    if(data.site){state.siteName=data.site.full_name||`${data.site.name}, ${data.site.city}`;$('#sidebarSiteName').textContent=state.siteName.replace(', ',' · ');if(state.currentView==='dashboard')$('#pageTitleText').textContent=state.siteName;}
    renderMetrics(data);renderZones(data.zones||[]);renderIncidents(data.incidents||[],data.metrics);renderAlertFeed(data.alerts||[]);renderNotificationFeed(data.alerts||[]);
    const analytics=await api(`/api/analytics?${qs()}&days=${state.chartDays}&aggregation=${state.aggregation}`);renderAnalytics(analytics);
  }
  async function loadVisitors() {
    const query=$('#globalSearch').value.trim();const status=$('#visitorStatusFilter')?.value||'';const from=$('#visitorFrom')?.value||'';const to=$('#visitorTo')?.value||'';
    const params=new URLSearchParams({site_id:String(state.siteId),page:String(state.visitorPage),page_size:String(state.visitorPageSize)});if(query)params.set('q',query);if(status)params.set('status',status);if(from)params.set('start_date',from);if(to)params.set('end_date',to);if(state.visitorFilter!=='all')params.set('filter',state.visitorFilter);
    const [result,dashboard]=await Promise.all([api(`/api/visitors?${params}`),api(`/api/dashboard?${qs()}`)]);state.lastDashboard=dashboard;renderVisitors(result);
  }
  function renderBroadcastZones(zones) {
    const select=$('#audience');if(!select)return;const previous=select.value;
    const options=[['all_visitors','All visitors at this site'],...(zones||[]).map(z=>[`zone:${z.name}`,`Visitors in ${z.name}`]),['staff','On-site staff · pending integration']];
    select.innerHTML=options.map(([value,label])=>`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
    if(options.some(([value])=>value===previous))select.value=previous;
  }
  async function loadViewData(view=state.currentView) {
    if(!state.user||!state.booted)return;
    const panel=$(`[data-view-panel="${view}"]`);if(panel){panel.setAttribute('aria-busy','true');panel.classList.add('is-loading');}
    const tableMap={warnings:['warningTable',7],visitors:['visitorTable',7],admins:['adminTable',6],audit:['auditTable',5]};
    if(tableMap[view])setTableState(tableMap[view][0],'Loading records…','info',tableMap[view][1]);
    try {
      if(view==='dashboard')await loadDashboard();
      else if(view==='live-map'){const [r,d]=await Promise.all([api(`/api/zones?${qs()}`),api(`/api/dashboard?${qs()}`)]);state.lastDashboard=d;renderMetrics(d);renderZones(r.items||[]);}
      else if(view==='warnings'){const r=await api(`/api/incidents?${qs()}`);renderIncidents(r.items||[]);}
      else if(view==='visitors')await loadVisitors();
      else if(view==='analytics'){const r=await api(`/api/analytics?${qs()}&days=${state.chartDays}&aggregation=${state.aggregation}`);renderAnalytics(r);}
      else if(view==='broadcast'){const [r,z]=await Promise.all([api(`/api/broadcasts?${qs()}`),api(`/api/zones?${qs()}`)]);renderBroadcasts(r.items||[]);renderBroadcastZones(z.items||[]);}
      else if(view==='admins'){const r=await api(`/api/admins?${qs()}`);renderAdmins(r.items||[]);}
      else if(view==='audit'){const r=await api(`/api/audit?${qs()}`);renderAudit(r.items||[]);}
      state.apiErrorShown=false;
    } catch(error) {
      if(error.status===401)return;
      const tableMap={warnings:['warningTable',7],visitors:['visitorTable',7],admins:['adminTable',6],audit:['auditTable',5]};
      if(tableMap[view])setTableState(tableMap[view][0],`Unable to load data: ${error.message}`, 'error',tableMap[view][1]);
      if(!state.apiErrorShown){state.apiErrorShown=true;toast('Could not load site data',error.message,'error');}
    } finally {
      if(panel){panel.removeAttribute('aria-busy');panel.classList.remove('is-loading');}
    }
  }

  function csvCell(value){let text=String(value??'');if(/^\s*[=+\-@]/.test(text))text="'"+text;return `"${text.replace(/"/g,'""')}"`;}
  async function exportReport() {
    try {
      const [dashboard,incidents,analytics]=await Promise.all([api(`/api/dashboard?${qs()}`),api(`/api/incidents?${qs()}`),api(`/api/analytics?${qs()}&days=${state.chartDays}&aggregation=${state.aggregation}`)]);
      const m=dashboard.metrics||{};const rows=[['YatraFlow site operations report',state.siteName],['Generated (IST)',dateTime(new Date())],['Data mode',dashboard.data_mode||'configured'],['Occupancy source',m.occupancy_source||'unavailable'],[],['Metric','Value'],['People inside',m.people_inside??'Unavailable'],['Checked in today',m.checked_in_today??'Unavailable'],['Capacity used',m.capacity_used==null?'Unavailable':`${m.capacity_used}%`],['Active SOS records',m.active_rescue_requests],['Model forecast tomorrow',m.predicted_tomorrow??'Unavailable'],[],['Incident ID','Type','Zone','Status','Assigned to'],...(incidents.items||[]).map(i=>[i.id,i.type,i.zone_name,i.status,i.assigned_to||'Unassigned']),[],['Footfall aggregation',analytics.aggregation],['Footfall days',analytics.days],['Forecast available',analytics.forecast?.available?'Yes':'No']];
      const csv=rows.map(row=>row.map(csvCell).join(',')).join('\n');const link=document.createElement('a');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));link.href=url;link.download='yatraflow-site-report.csv';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Report exported','CSV includes API values, data mode and unavailable metrics.');
    }catch(error){toast('Export unavailable',error.message,'error');}
  }

  function transitionOptions(incident) {
    const status=String(incident.status||'');const sos=String(incident.type).toUpperCase()==='SOS';
    if(sos){const map={'Submitted':['Awaiting Acknowledgement','Cancelled','Failed'],'Awaiting Acknowledgement':['Acknowledged','Cancelled','Failed'],'Acknowledged':['Cancelled','Failed'],'Assigned':['Resolved','Cancelled','Failed']};return map[status]||[];}
    const map={'Open':['Resolved','Cancelled'],'In Review':['Assigned','Resolved','Cancelled'],'Monitoring':['Assigned','Resolved','Cancelled'],'Assigned':['Resolved','Cancelled']};return map[status]||[];
  }
  function renderIncidentDetail(item) {
    const detail=(label,value)=>`<div class="detail-stat"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value??'—')}</strong></div>`;
    const actions=transitionOptions(item);const sos=String(item.type).toUpperCase()==='SOS';const canAssign=sos?item.status==='Acknowledged':['Open','In Review','Monitoring'].includes(item.status);
    const dispatchPending=(item.assignments||[]).some(a=>a.request_status==='pending_external_integration');
    const location=item.latitude!=null&&item.longitude!=null?`${item.latitude}, ${item.longitude} (received coordinates; no map tiles)`:'No location coordinates received';
    const statusForm=actions.length?`<form id="incidentTransitionForm" data-incident-id="${escapeHtml(item.id)}"><div class="form-group"><label for="nextIncidentStatus">Change status</label><select id="nextIncidentStatus" name="status" required>${actions.map(a=>`<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join('')}</select></div><div class="form-group"><label for="transitionNote">Operator note (required for resolve/cancel/fail)</label><input id="transitionNote" name="note" maxlength="1000" placeholder="Add context for the audit history" /></div><button class="btn btn-primary" type="submit">Save status transition</button></form>`:'';
    const assignmentForm=canAssign?`<form id="incidentAssignForm" data-incident-id="${escapeHtml(item.id)}"><div class="form-group"><label for="responderLabel">Responder/team label</label><input id="responderLabel" name="responder" maxlength="160" required placeholder="Enter the on-site responder name" /></div><button class="btn btn-soft" type="submit">Record assignment</button><small class="data-note">Saved to this database only; no responder is contacted.</small></form>`:'';
    const dispatch=sos&&item.status==='Assigned'?`<div class="incident-actions" style="margin:10px 0"><button class="btn btn-danger" data-modal-action="dispatch" data-incident-id="${escapeHtml(item.id)}" ${dispatchPending?'disabled':''}>${dispatchPending?'Dispatch request pending integration':'Record dispatch request'}</button><button class="btn" data-modal-action="contact" data-incident-id="${escapeHtml(item.id)}">Contact visitor</button></div><small class="data-note">No ambulance, police or external provider call is made by this action.</small>`:'';
    const history=(item.status_history||[]).map(h=>`<div class="notification-item"><span class="mini-dot" style="background:#6b55b5"></span><p><strong>${escapeHtml(h.old_status||'Received')} → ${escapeHtml(h.new_status)}</strong><small>${escapeHtml(dateTime(h.created_at))} · ${escapeHtml(h.actor_admin_id||'')}</small>${h.note?`<small>${escapeHtml(h.note)}</small>`:''}</p></div>`).join('')||'<p class="data-note">No status history events recorded.</p>';
    const notes=(item.notes||[]).map(n=>`<div class="broadcast-record"><div class="record-head"><strong>${escapeHtml(n.author_admin_id)}</strong><span>${escapeHtml(dateTime(n.created_at))}</span></div><p>${escapeHtml(n.note)}</p></div>`).join('')||'<p class="data-note">No operator notes.</p>';
    const assignments=(item.assignments||[]).map(a=>`<div class="broadcast-record"><strong>${escapeHtml(a.responder)}</strong><p>${escapeHtml(a.request_status)} · ${escapeHtml(a.provider)} · ${escapeHtml(dateTime(a.created_at))}</p><span class="data-note">External action: not sent</span></div>`).join('')||'';
    openModal(`<div class="modal-kicker">${escapeHtml(item.type)} · ${escapeHtml(item.id)}</div><h2 class="modal-title" id="modalTitle">${escapeHtml(item.title)}</h2><p class="modal-desc">${escapeHtml(item.description||'No description was provided.')}</p><div class="detail-grid">${detail('Priority',item.priority)}${detail('Status',item.status)}${detail('Visitor',item.visitor_name||'Unidentified')}${detail('Zone',item.zone_name||'Unavailable')}${detail('Assigned to',item.assigned_to||'Unassigned')}${detail('Reported',dateTime(item.created_at))}${detail('Location',location)}${detail('Visitor profile',item.visitor?.pass_id||'No linked profile')}</div>${item.visitor?.phone?`<p class="data-note">Contact number is visible to your role only: ${escapeHtml(item.visitor.phone)}</p>`:''}<h3 style="font-size:12px;margin:18px 0 8px">Workflow actions</h3>${statusForm}${assignmentForm}${dispatch}<form id="incidentNoteForm" data-incident-id="${escapeHtml(item.id)}"><div class="form-group"><label for="incidentNote">Add operator note</label><textarea id="incidentNote" name="note" maxlength="1000" placeholder="Add a concise, factual note…"></textarea></div><button class="btn btn-sm" type="submit">Save note</button></form><h3 style="font-size:12px;margin:18px 0 8px">Assignment records</h3>${assignments||'<p class="data-note">No assignment/dispatch requests are stored.</p>'}<h3 style="font-size:12px;margin:18px 0 8px">Status history</h3><div>${history}</div><h3 style="font-size:12px;margin:18px 0 8px">Operator notes</h3><div>${notes}</div><div class="modal-actions"><button class="btn" data-modal-close>Close</button></div>`);
  }
  async function openIncident(incidentId) { try{const detail=await api(`/api/incidents/${encodeURIComponent(incidentId)}?${qs()}`);renderIncidentDetail(detail);}catch(error){toast('Incident unavailable',error.message,'error');} }
  async function mutateIncident(incidentId,action,payload) {const result=await api(`/api/incidents/${encodeURIComponent(incidentId)}/${action}`,postOptions({site_id:state.siteId,...payload}));return result.item||result;}

  function openVisitorDetailMarkup(visitor) {
    const sessions=(visitor.sessions||[]).map(s=>`<div class="broadcast-record"><div class="record-head"><strong>${escapeHtml(s.zone_name||'Zone not recorded')}</strong><span>${escapeHtml(dateTime(s.checked_in_at))}</span></div><p>${s.checked_out_at?`Checked out ${escapeHtml(dateTime(s.checked_out_at))}`:'Active session'} · source ${escapeHtml(s.source||'unknown')}</p></div>`).join('')||'<p class="data-note">No mobile check-in sessions are recorded for this visitor.</p>';
    const active=(visitor.sessions||[]).some(s=>!s.checked_out_at);
    openModal(`<div class="modal-kicker">VISITOR PROFILE · ${escapeHtml(visitor.id)}</div><h2 class="modal-title" id="modalTitle">${escapeHtml(visitor.full_name)}</h2><p class="modal-desc">Personal data is shown only to authorized operational roles. Source: visitor directory database.</p><div class="detail-grid"><div class="detail-stat"><small>Pass ID</small><strong>${escapeHtml(visitor.pass_id)}</strong></div><div class="detail-stat"><small>Current zone</small><strong>${escapeHtml(visitor.zone_name||'—')}</strong></div><div class="detail-stat"><small>Pass type</small><strong>${escapeHtml(visitor.pass_type||'—')}</strong></div><div class="detail-stat"><small>Presence</small><strong>${escapeHtml(visitor.status||'—')}</strong></div>${visitor.phone?`<div class="detail-stat"><small>Contact phone</small><strong>${escapeHtml(visitor.phone)}</strong></div>`:''}</div><h3 style="font-size:12px;margin:18px 0 8px">Check-in history</h3>${sessions}${active?`<div class="modal-actions"><button class="btn btn-danger" data-modal-action="checkout" data-visitor-id="${escapeHtml(visitor.id)}">Check out</button></div>`:''}<div class="modal-actions"><button class="btn" data-modal-close>Close</button></div>`);
  }
  async function openVisitor(visitorId) {try{const visitor=await api(`/api/visitors/${encodeURIComponent(visitorId)}?${qs()}`);openVisitorDetailMarkup(visitor);}catch(error){toast('Visitor profile unavailable',error.message,'error');}}

  function openZoneDetail(zoneId,name) {
    if(!zoneId){toast('Zone details unavailable','This zone does not have a configured database record.','error');return;}
    api(`/api/zones/${encodeURIComponent(zoneId)}?${qs()}`).then(zone=>{const trend=(zone.recent_trend||[]).map(t=>`<div class="broadcast-record"><div class="record-head"><strong>${fmt(t.visitor_count)} visitors · ${fmtPct(t.density_percent)}</strong><span>${escapeHtml(dateTime(t.observed_at))}</span></div><p>Source: ${escapeHtml(t.source||'unknown')}</p></div>`).join('')||'<p class="data-note">No time-series crowd observations stored for this zone.</p>';openModal(`<div class="modal-kicker">SITE ZONE</div><h2 class="modal-title" id="modalTitle">${escapeHtml(zone.name)}</h2><p class="modal-desc">${escapeHtml(zone.source||'Database source status unavailable')}</p><div class="detail-grid"><div class="detail-stat"><small>Visitors</small><strong>${fmt(zone.current_visitors)}</strong></div><div class="detail-stat"><small>Capacity</small><strong>${fmt(zone.capacity)}</strong></div><div class="detail-stat"><small>Density</small><strong>${fmtPct(zone.density_percent)}</strong></div><div class="detail-stat"><small>Status</small><strong>${escapeHtml(zone.warning_status||zone.level)}</strong></div></div><p class="modal-desc" style="margin-top:12px">${escapeHtml(zone.recommended_action||'')}</p><h3 style="font-size:12px">Recent observations</h3>${trend}<h3 style="font-size:12px">Linked warnings</h3>${(zone.active_warnings||[]).map(i=>`<button class="btn btn-sm" data-action="incident-detail" data-incident-id="${escapeHtml(i.id)}">${escapeHtml(i.id)} · ${escapeHtml(i.status)}</button>`).join('')||'<p class="data-note">No active incidents linked to this zone.</p>'}<div class="modal-actions"><button class="btn" data-modal-close>Close</button></div>`);}).catch(error=>toast('Zone unavailable',error.message,'error'));
  }

  function openAssignmentModal(incidentId) {
    openModal(`<div class="modal-kicker">RESPONSE RECORD</div><h2 class="modal-title" id="modalTitle">Assign a responder</h2><p class="modal-desc">This records an operator-selected responder label in the incident database. It does not contact staff or emergency services.</p><form id="quickAssignForm" data-incident-id="${escapeHtml(incidentId)}"><div class="form-group"><label for="quickResponder">Responder/team name</label><input id="quickResponder" name="responder" required maxlength="160" placeholder="Enter a responder label" /></div><div class="modal-actions"><button type="button" class="btn" data-modal-close>Cancel</button><button class="btn btn-primary" type="submit">Save assignment</button></div></form>`);
  }
  function openBroadcastModal() {switchView('broadcast');$('#broadcastMessage').focus();}
  function openInviteModal() {
    const superAdmin=state.user?.role_key==='super_admin';
    const roleOptions=superAdmin?Object.entries(roleLabels).map(([key,label])=>`<option value="${key}">${label}</option>`).join(''):'<option value="emergency_operator">Emergency Operator</option><option value="analytics_viewer">Analytics Viewer</option>';
    openModal(`<div class="modal-kicker">LOCAL ADMIN ACCOUNT</div><h2 class="modal-title" id="modalTitle">Create administrator account</h2><p class="modal-desc">Account and password hash are stored in the database. No invitation email is sent; deliver the initial password through your approved secure channel.</p><form id="inviteForm"><div class="form-group"><label for="inviteName">Full name</label><input id="inviteName" name="full_name" required maxlength="160" autocomplete="name" /></div><div class="form-group"><label for="inviteEmail">Work email</label><input id="inviteEmail" name="email" type="email" required maxlength="200" autocomplete="off" /></div><div class="form-group"><label for="inviteRole">Role</label><select id="inviteRole" name="role_key">${roleOptions}</select></div><div class="form-group"><label for="invitePassword">Initial password · minimum 12 characters</label><input id="invitePassword" name="password" type="password" required minlength="12" autocomplete="new-password" /></div><div class="modal-actions"><button type="button" class="btn" data-modal-close>Cancel</button><button class="btn btn-primary" type="submit">Create account</button></div></form>`);
  }
  function openAdminOptions(adminId,adminName) {
    const canManage=state.user?.role_key==='super_admin';
    api(`/api/admins?${qs()}`).then(result=>{const admin=(result.items||[]).find(a=>a.id===adminId);if(!admin)throw new Error('Administrator record was not found');
      const roles=Object.entries(roleLabels).map(([key,label])=>`<option value="${key}" ${admin.role_key===key?'selected':''}>${label}</option>`).join('');
      openModal(`<div class="modal-kicker">ADMINISTRATION · ${escapeHtml(admin.id)}</div><h2 class="modal-title" id="modalTitle">${escapeHtml(admin.full_name||adminName)}</h2><p class="modal-desc">Account status: ${escapeHtml(admin.account_status)}. Changes are audited. Deactivation revokes active sessions.</p><div class="detail-grid"><div class="detail-stat"><small>Email</small><strong>${escapeHtml(admin.email)}</strong></div><div class="detail-stat"><small>Current role</small><strong>${escapeHtml(admin.role)}</strong></div><div class="detail-stat"><small>Scope</small><strong>${escapeHtml(admin.access_scope)}</strong></div><div class="detail-stat"><small>Last login</small><strong>${escapeHtml(admin.last_login?dateTime(admin.last_login):'Never')}</strong></div></div>${canManage?`<form id="adminUpdateForm" data-admin-id="${escapeHtml(admin.id)}"><div class="form-group"><label for="adminRole">Role</label><select id="adminRole" name="role_key">${roles}</select></div><div class="form-group"><label><input type="checkbox" name="active" ${admin.active?'checked':''}/> Account active</label></div><div class="modal-actions"><button type="button" class="btn" data-modal-close>Close</button><button class="btn btn-primary" type="submit">Save access change</button></div></form>`:'<div class="modal-actions"><button class="btn" data-modal-close>Close</button></div>'}`);
    }).catch(error=>toast('Admin details unavailable',error.message,'error'));
  }
  function openNotificationModal(category,incidentId='') {
    const title=`${category} notification${incidentId?` · ${incidentId}`:''}`;
    openModal(`<div class="modal-kicker">DATABASE NOTIFICATION RECORD</div><h2 class="modal-title" id="modalTitle">${escapeHtml(title)}</h2><p class="modal-desc">The record will be saved and audited. No push notification, phone call, SMS or emergency-service contact is made by this action.</p><form id="notificationForm" data-incident-id="${escapeHtml(incidentId)}"><input type="hidden" name="category" value="${escapeHtml(category)}"/><div class="form-group"><label for="notificationTitle">Title</label><input id="notificationTitle" name="title" required maxlength="160" value="${escapeHtml(category)}${incidentId?` for ${escapeHtml(incidentId)}`:''}" /></div><div class="form-group"><label for="notificationMessage">Message</label><textarea id="notificationMessage" name="message" maxlength="500" required>${escapeHtml(incidentId?`${category} response requested for incident ${incidentId}.`:`${category} notification requested for ${state.siteName}.`)}</textarea></div><div class="modal-actions"><button type="button" class="btn" data-modal-close>Cancel</button><button class="btn btn-primary" type="submit">Save pending record</button></div></form>`);
  }

  async function performContact(incidentId) {
    try {const result=await api(`/api/incidents/${encodeURIComponent(incidentId)}/call`,postOptions({site_id:state.siteId}));const phone=String(result.phone||'');const dial=phone.replace(/[^0-9+*#]/g,'');openModal(`<div class="modal-kicker">CONTACT WORKFLOW · ${escapeHtml(incidentId)}</div><h2 class="modal-title" id="modalTitle">${phone?'Contact details':'Contact request recorded'}</h2><p class="modal-desc">The dashboard only recorded your contact request. No call was placed.</p>${phone?`<div class="detail-stat"><small>Visitor phone (authorized role)</small><strong>${escapeHtml(phone)}</strong></div><div class="modal-actions"><button class="btn" data-modal-close>Close</button><a class="btn btn-primary" href="tel:${escapeHtml(dial)}">Open device dialer</a></div>`:`<p class="data-note">${escapeHtml(result.message||'No phone number is recorded.')}</p><div class="modal-actions"><button class="btn" data-modal-close>Close</button></div>`}`);await loadViewData(state.currentView);}
    catch(error){toast('Contact workflow failed',error.message,'error');}
  }

  async function submitBroadcast(form) {
    const fd=new FormData(form);const audience=String(fd.get('audience')||'all_visitors');const zoneName=audience.startsWith('zone:')?audience.slice(5):'';
    const payload={site_id:state.siteId,title:String(fd.get('title')||'').trim(),message:String(fd.get('message')||'').trim(),category:String(fd.get('category')||'Information'),severity:String(fd.get('severity')||'info'),audience,zone_name:zoneName};
    if(!payload.title||!payload.message){toast('Required fields missing','Enter a title and message.','error');return;}
    if(!window.confirm('Save this site alert? It will be stored as pending integration; no message will be delivered by this dashboard.'))return;
    const button=form.querySelector('[type=submit]');button.disabled=true;
    try {const result=await api('/api/broadcasts',postOptions(payload));form.reset();toast('Alert record saved',`${result.item?.delivery_status||'pending integration'} · delivery not confirmed.`);await loadViewData('broadcast');}
    catch(error){toast('Broadcast could not be saved',error.message,'error');}
    finally{button.disabled=false;}
  }

  async function doLogout() {
    const button=$('#logoutButton');if(button)button.disabled=true;
    try {await api('/api/logout',postOptions({}));$('#profileMenu').hidden=true;showLogin('You have signed out.');}
    catch(error){toast('Sign out could not be confirmed','The server did not revoke this session. Check the connection and try again.','error');}
    finally{if(button)button.disabled=false;}
  }
  $('#profileButton').addEventListener('click',()=>{const menu=$('#profileMenu');menu.hidden=!menu.hidden;$('#profileButton').setAttribute('aria-expanded',String(!menu.hidden));});
  $('#logoutButton').addEventListener('click',doLogout);
  document.addEventListener('click',event=>{if(!$('#profileButton').contains(event.target)&&!$('#profileMenu').contains(event.target)){$('#profileMenu').hidden=true;$('#profileButton').setAttribute('aria-expanded','false');}});
  $('#notificationButton').addEventListener('click',()=>{const panel=$('#notificationsPanel');panel.hidden=!panel.hidden;$('#notificationButton').setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden&&state.user)api(`/api/alerts?${qs()}`).then(result=>renderNotificationFeed(result.items||[])).catch(error=>toast('Site feed unavailable',error.message,'error'));});
  document.addEventListener('click',event=>{if(!$('#notificationButton').contains(event.target)&&!$('#notificationsPanel').contains(event.target)){$('#notificationsPanel').hidden=true;$('#notificationButton').setAttribute('aria-expanded','false');}});
  function openSidebar(){$('#sidebar').classList.add('open');$('#sidebarBackdrop').classList.add('show');}function closeSidebar(){$('#sidebar').classList.remove('open');$('#sidebarBackdrop').classList.remove('show');}
  $('#mobileMenu').addEventListener('click',openSidebar);$('#sidebarBackdrop').addEventListener('click',closeSidebar);

  document.addEventListener('click',async event=>{
    const actionEl=event.target.closest('[data-action]');if(!actionEl)return;let action=actionEl.dataset.action;
    const row=actionEl.closest('tr,.incident-card');
    const incidentId=actionEl.dataset.incidentId||row?.querySelector('.incident-id,.table-primary')?.textContent?.trim()||'';
    if(action==='refresh'){await refreshHealth(true);await loadViewData(state.currentView);toast('Data refreshed','The current view was reloaded from the API.');}
    else if(action==='export')await exportReport();
    else if(action==='broadcast')openBroadcastModal();
    else if(action==='invite')openInviteModal();
    else if(action==='incident-detail'||action==='review'||action==='details'||action==='dispatch')await openIncident(incidentId||actionEl.dataset.incidentId||'');
    else if(action==='visitor-detail'){const id=actionEl.dataset.visitorId||row?.querySelector('.table-secondary')?.textContent?.split('·').pop()?.trim()||'';if(id)await openVisitor(id);else toast('Visitor record unavailable','Reload the visitor directory and choose a record.','error');}
    else if(action==='zone-detail')openZoneDetail(actionEl.dataset.zoneId,actionEl.dataset.zoneName);
    else if(action==='assign')openAssignmentModal(incidentId||actionEl.dataset.incidentId||'');
    else if(action==='call'){if(incidentId)await performContact(incidentId);else toast('Select an incident','Open an SOS record to view contact options.','error');}
    else if(action==='notify'||action==='police'||action==='medical')openNotificationModal(actionEl.dataset.category||(action==='medical'?'Medical team':'Police'),incidentId);
    else if(action==='admin-options')openAdminOptions(actionEl.dataset.adminId,actionEl.dataset.adminName||row?.querySelector('.table-primary')?.textContent||'Administrator');
    else if(action==='visitor-page'){state.visitorPage=Number(actionEl.dataset.page)||1;await loadVisitors();}
    else if(action==='broadcast-history'){switchView('broadcast');}
  });

  modalContent.addEventListener('click',async event=>{
    if(event.target.closest('[data-modal-close]')){closeModal();return;}
    const action=event.target.closest('[data-modal-action]');if(!action)return;
    const kind=action.dataset.modalAction;
    if(kind==='dispatch'){
      if(!window.confirm('Record a dispatch request? This dashboard cannot contact emergency services; the request will remain pending integration.'))return;
      try{const result=await api(`/api/incidents/${encodeURIComponent(action.dataset.incidentId)}/dispatch`,postOptions({site_id:state.siteId,responder:'Assigned on-site responder',service:'medical'}));toast('Request saved',result.item?.message||'External dispatch was not sent.');await openIncident(action.dataset.incidentId);await loadViewData(state.currentView);}catch(error){toast('Dispatch request not saved',error.message,'error');}
    } else if(kind==='contact'){await performContact(action.dataset.incidentId);}
    else if(kind==='checkout'){
      if(!window.confirm('Check this visitor out of the active session? This updates the local database record.'))return;
      try{const result=await api(`/api/visitors/${encodeURIComponent(action.dataset.visitorId)}/checkout`,postOptions({site_id:state.siteId}));toast('Visitor checked out',dateTime(result.checked_out_at));closeModal();await loadVisitors();}catch(error){toast('Checkout failed',error.message,'error');}
    }
  });

  modalContent.addEventListener('submit',async event=>{
    const form=event.target;event.preventDefault();
    if(form.id==='incidentTransitionForm'){
      const status=String(new FormData(form).get('status')||'');const note=String(new FormData(form).get('note')||'').trim();
      if(['Resolved','Cancelled','Failed'].includes(status)&&!note){toast('Add a note','A factual note is required for this terminal transition.','error');$('#transitionNote').focus();return;}
      if(!window.confirm(`Change incident status to “${status}”? This will be written to the incident history and audit log.`))return;
      try{await mutateIncident(form.dataset.incidentId,'transition',{status,note});toast('Incident status saved',`${form.dataset.incidentId} → ${status}`);await openIncident(form.dataset.incidentId);await loadViewData(state.currentView);}catch(error){toast('Status change rejected',error.message,'error');}
    } else if(form.id==='incidentAssignForm'||form.id==='quickAssignForm'){
      const responder=String(new FormData(form).get('responder')||'').trim();if(!responder){toast('Responder required','Enter an on-site responder/team label.','error');return;}
      if(!window.confirm(`Record assignment to “${responder}”? No staff member will be contacted.`))return;
      try{await mutateIncident(form.dataset.incidentId,'assign',{responder});toast('Assignment saved','Assignment record updated; no external contact was made.');await openIncident(form.dataset.incidentId);await loadViewData(state.currentView);}catch(error){toast('Assignment rejected',error.message,'error');}
    } else if(form.id==='incidentNoteForm'){
      const note=String(new FormData(form).get('note')||'').trim();if(!note){toast('Note required','Enter a short factual note.','error');return;}
      try{await api(`/api/incidents/${encodeURIComponent(form.dataset.incidentId)}/notes`,postOptions({site_id:state.siteId,note}));toast('Note saved','Incident note and audit event were recorded.');await openIncident(form.dataset.incidentId);}catch(error){toast('Note not saved',error.message,'error');}
    } else if(form.id==='inviteForm'){
      const fd=new FormData(form);const payload={site_id:state.siteId,full_name:String(fd.get('full_name')).trim(),email:String(fd.get('email')).trim(),role_key:String(fd.get('role_key')),password:String(fd.get('password'))};
      if(payload.password.length<12){toast('Password too short','Use at least 12 characters.','error');return;}
      if(!window.confirm(`Create a local administrator account for ${payload.email}? No email invitation will be sent.`))return;
      const button=form.querySelector('[type=submit]');button.disabled=true;
      try{await api('/api/admins',postOptions(payload));closeModal();toast('Administrator account created','Password hash saved. Share the initial password only via an approved secure channel.');if(state.currentView==='admins')await loadViewData('admins');}catch(error){toast('Account creation failed',error.message,'error');}finally{button.disabled=false;}
    } else if(form.id==='adminUpdateForm'){
      const fd=new FormData(form);const payload={role_key:String(fd.get('role_key')),active:fd.get('active')==='on'};
      if(!window.confirm('Save this role/account change? Active sessions are revoked when an account is deactivated.'))return;
      try{await api(`/api/admins/${encodeURIComponent(form.dataset.adminId)}/update`,postOptions(payload));closeModal();toast('Administrator access updated','Change recorded in the audit log.');await loadViewData('admins');}catch(error){toast('Access change rejected',error.message,'error');}
    } else if(form.id==='notificationForm'){
      const fd=new FormData(form);const payload={site_id:state.siteId,category:String(fd.get('category')),title:String(fd.get('title')).trim(),message:String(fd.get('message')).trim(),severity:'urgent'};
      if(!payload.message||!payload.title){toast('Required fields missing','Add a title and message.','error');return;}
      if(!window.confirm('Save this notification request? It will remain pending integration; no call, SMS or push message will be sent.'))return;
      const button=form.querySelector('[type=submit]');if(button){button.disabled=true;button.textContent='Saving record…';}
      try{const result=await api('/api/alerts/notify',postOptions(payload));closeModal();toast('Notification record saved',`${result.item?.delivery_status||'pending'} · push not delivered.`);await loadDashboard();}catch(error){toast('Notification not saved',error.message,'error');}finally{if(button){button.disabled=false;button.textContent='Save pending record';}}
    }
  });

  $('#broadcastComposer').addEventListener('submit',event=>{event.preventDefault();submitBroadcast(event.target);});
  $('#visitorStatusFilter')?.addEventListener('change',()=>{state.visitorPage=1;loadVisitors();});
  $('#visitorFrom')?.addEventListener('change',()=>{state.visitorPage=1;loadVisitors();});$('#visitorTo')?.addEventListener('change',()=>{state.visitorPage=1;loadVisitors();});
  $$('[data-visitor-filter]').forEach(button=>button.addEventListener('click',()=>{state.visitorFilter=button.dataset.visitorFilter;$$('[data-visitor-filter]').forEach(b=>b.classList.toggle('is-selected',b===button));state.visitorPage=1;loadVisitors();}));
  $$('[data-warning-filter]').forEach(button=>button.addEventListener('click',()=>{state.warningFilter=button.dataset.warningFilter;$$('[data-warning-filter]').forEach(b=>b.classList.toggle('is-selected',b===button));applyWarningFilter();}));
  function applyWarningFilter(){const filter=state.warningFilter;$$('#warningTable tr').forEach(row=>{const kind=row.dataset.kind||'';const status=row.dataset.status||'';const closed=['resolved','closed','complete','completed','cancelled','failed'].includes(status);row.hidden=(filter==='sos'&&!kind.includes('sos'))||(filter==='open'&&closed);});}
  $$('[data-range]').forEach(button=>button.addEventListener('click',async()=>{state.chartDays=Number(button.dataset.range.replace('d',''))||14;$$('[data-range]').forEach(b=>b.classList.toggle('is-selected',Number(b.dataset.range.replace('d',''))===state.chartDays));try{const result=await api(`/api/analytics?${qs()}&days=${state.chartDays}&aggregation=${state.aggregation}`);renderAnalytics(result);}catch(error){toast('Analytics unavailable',error.message,'error');}}));
  $('#analyticsAggregation')?.addEventListener('change',async event=>{state.aggregation=event.target.value;try{renderAnalytics(await api(`/api/analytics?${qs()}&days=${state.chartDays}&aggregation=${state.aggregation}`));}catch(error){toast('Analytics unavailable',error.message,'error');}});
  $$('[data-map-zoom]').forEach(button=>button.addEventListener('click',()=>{state.mapZoom=Math.max(.75,Math.min(1.45,state.mapZoom+(button.dataset.mapZoom==='in'?.1:-.1)));$$('[data-map-stage] .map-svg').forEach(svg=>svg.style.transform=`scale(${state.mapZoom})`);$$('[data-zoom-label]').forEach(label=>label.textContent=`${Math.round(state.mapZoom*100)}%`);}));
  $$('[data-map-layer]').forEach(button=>button.addEventListener('click',()=>{state.heatVisible=!state.heatVisible;$$('[data-map-stage] .map-svg .zone').forEach(zone=>{const rect=$('.zone-fill',zone);if(rect)rect.style.fill=zoneFill(zone.dataset.density===''?null:zone.dataset.density);});$$('[data-map-layer]').forEach(b=>b.setAttribute('aria-pressed',String(state.heatVisible)));}));
  document.addEventListener('click',event=>{const zone=event.target.closest('.map-svg .zone');if(zone){selectZone(zone.dataset.zone,zone.dataset.density===''?null:Number(zone.dataset.density));if(zone.dataset.zoneId)openZoneDetail(zone.dataset.zoneId,zone.dataset.zone);}const row=event.target.closest('[data-zone-select]');if(row){selectZone(row.dataset.zoneSelect,row.dataset.density===''?null:Number(row.dataset.density));if(row.dataset.zoneId)openZoneDetail(row.dataset.zoneId,row.dataset.zoneSelect);}});
  function applyMapFilter(){
    const filter=state.mapFilter;
    $$('.zone-list .zone-row,.map-svg .zone').forEach(el=>{const raw=el.dataset.density;const density=Number(raw);const hide=filter==='all'?false:raw===''?true:filter==='high'?density<75:density>=50;if(el.classList.contains('zone'))el.style.display=hide?'none':'';else el.hidden=hide;});
  }
  $$('[data-map-filter]').forEach(button=>button.addEventListener('click',()=>{state.mapFilter=button.dataset.mapFilter;$$('[data-map-filter]').forEach(b=>b.classList.toggle('is-selected',b===button));applyMapFilter();}));

  // Data refreshes are honest database polling, not a claim of GPS/live integration.
  boot();
  setInterval(async()=>{if(!state.user||document.hidden)return;await refreshHealth(true);await loadViewData(state.currentView);},30000);
})();
