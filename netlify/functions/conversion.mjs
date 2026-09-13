import { getStore } from '@netlify/blobs';
import { timingSafeEqual } from 'node:crypto';
const reply = (body,status=200) => Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
const clean = x => typeof x === 'string' && x.length <= 300 && !/[{}]/.test(x) ? x.trim() : '';
export default async function(request) {
  const version = 'whop-bridge-v1';
  if (request.method !== 'GET') return reply({error:'method'},405);
  const q = new URL(request.url).searchParams;
  const secret = process.env.WEBHOOK_SECRET;
  const supplied = request.headers.get('x-webhook-secret') || '';
  if (!secret || !process.env.WHOP_API_KEY) return reply({error:'server_configuration',version},503);
  if (Buffer.byteLength(secret)!==Buffer.byteLength(supplied) || !timingSafeEqual(Buffer.from(secret),Buffer.from(supplied))) return reply({error:'unauthorized'},401);
  const clickId = clean(q.get('click_id'));
  const txid = clean(q.get('txid'));
  const rawPayout = (q.get('payout') || '').trim();
  const payout = Number(rawPayout);
  console.log('conversion_received',{version,click_id:clickId,txid,payout:rawPayout});
  if (!/^\d+(\.\d+)?$/.test(rawPayout) || !Number.isFinite(payout) || payout<=0) {
    console.log('conversion_skipped',{reason:'nonpositive_or_invalid_payout',click_id:clickId});
    return reply({success:true,skipped:true,reason:'nonpositive_or_invalid_payout',version});
  }
  if (!/^[a-f0-9-]{36}$/i.test(clickId)) return reply({error:'invalid_click_id',version},400);
  try {
    const visit = await getStore({name:'whop-visits-v1',consistency:'strong'}).get(clickId,{type:'json'});
    if (!visit || Date.now()-Date.parse(visit.saved_at)>28*86400000) {
      console.log('conversion_blocked',{reason:'missing_or_expired_visit',click_id:clickId});
      return reply({error:'missing_or_expired_visit',version},409);
    }
    const context = {ip_address:visit.ip,user_agent:visit.user_agent};
    for (const [input,output] of [['campaign_id','ad_campaign_id'],['adset_id','ad_set_id'],['ad_id','ad_id'],['fbclid','fbclid']]) {
      const value=clean(q.get(input)); if(value) context[output]=value;
    }
    // Preserve the deployed prefix: changing it would change deduplication keys.
    const eventId='reco_'+(txid || clickId);
    const payload={account_id:'biz_O5LDZ6SDymAiyD',event_name:'complete_registration',action_source:'website',currency:'usd',value:payout,event_id:eventId,url:visit.url,user:{anonymous_id:visit.wuid},context};
    const response=await fetch('https://api.whop.com/api/v1/events',{method:'POST',headers:{Authorization:'Bearer '+process.env.WHOP_API_KEY,'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(12000)});
    const result=await response.text();
    console.log('ClickFlare -> Whop',{version,click_id:clickId,txid,value:payout,event_id:eventId,has_visitor:true,has_url:true,whop_status:response.status});
    return reply({success:response.ok,version,event_id:eventId,whop_status:response.status,whop_response:result},response.ok?200:502);
  } catch(error) {
    console.error('conversion_failed',{version,name:error.name,click_id:clickId});
    return reply({error:'conversion_failed',version},502);
  }
}
