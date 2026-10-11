'use strict';
// CDP response URLs identify the actual response hop. Playwright Response.url()
// is taken from its tracked Request and can misattribute native media redirects.
// Raw URLs stay in memory only for exact hop comparison, never in snapshots.
function safeURL(value) {
  try {const u=new URL(value);return {origin:u.origin,path:u.pathname};}
  catch(_){return {origin:'invalid',path:''};}
}
function createNetworkLedger({appOrigin,workerOrigin,phase=()=>'',onRequest=()=>{},onData=()=>{},onFailure=()=>{},maxRequests=10000,maxPendingExtraInfo=1000}) {
  const records=new Map(),requests=[],failures=[],issues=[];let bodyBytes=0,pendingExtraInfo=0;
  const issue=code=>{if(!issues.includes(code))issues.push(code);};
  const notify=(fn,row)=>{try{fn({...row});}catch(_){issue('observer-callback');}};
  const stage=()=>{try{const s=phase();return typeof s==='string'&&/^[a-z0-9-]{0,80}$/i.test(s)?s:'unknown';}catch(_){return 'unknown';}};
  const header=(headers,name)=>{const row=Object.entries(headers||{}).find(([key])=>key.toLowerCase()===name);return row?String(row[1]):'';};
  const validStatus=value=>Number.isInteger(value)&&value>=100&&value<=599;
  function recordFor(id) {
    let record=records.get(id);
    if(!record){if(records.size>=maxRequests){issue('request-limit');return null;}record={hops:[],expected:[],extra:[]};records.set(id,record);}
    return record;
  }
  // These two CDP channels can arrive in either order. Within a request ID,
  // pair only the chronological response hops declaring that ExtraInfo exists.
  // Keep numeric status alone: ExtraInfo headers/cookies are never retained.
  function pairExtra(record) {
    while(record.expected.length&&record.extra.length) {
      const hop=record.expected.shift(),status=record.extra.shift();pendingExtraInfo--;
      Object.assign(hop.response,{wireStatus:status,wireStatusSource:'extra-info',wireStatusMatched:status===hop.response.status});
    }
  }
  function metadata(response) {
    const type=header(response.headers,'content-type').split(';')[0].trim().toLowerCase(),length=header(response.headers,'content-length'),range=header(response.headers,'content-range'),cors=header(response.headers,'access-control-allow-origin');
    return {status:response.status,contentType:/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(type)?type:'',
      contentLength:/^(0|[1-9][0-9]{0,19})$/.test(length)?length:'',
      contentRange:/^bytes (?:[0-9]+-[0-9]+\/[0-9]+|\*\/[0-9]+)$/.test(range)?range:'',
      corsOrigin:[appOrigin,workerOrigin,'null'].includes(cors)?cors: cors?'other':'',
      noStore:/(?:^|,)\s*no-store\s*(?:,|$)/i.test(header(response.headers,'cache-control')),
      privateNoStore:header(response.headers,'cache-control').trim().toLowerCase()==='private, no-store',
      fromServiceWorker:!!response.fromServiceWorker,fromDiskCache:!!response.fromDiskCache,
      fromPrefetchCache:!!response.fromPrefetchCache,fromEarlyHints:!!response.fromEarlyHints};
  }
  function respond(record,response,kind,hasExtraInfo) {
    if(!record?.current){issue('response-without-request');return;}
    const hop=record.current;
    if(hop.response){issue('duplicate-hop-response');return;}
    hop.response={...safeURL(response.url),...metadata(response),responseKind:kind,protocolURLMatched:response.url===hop.rawURL,
      extraInfoExpected:typeof hasExtraInfo==='boolean'?hasExtraInfo:null,wireStatus:null,wireStatusSource:'unverified',wireStatusMatched:false};
    if(hasExtraInfo===true){hop.response.wireStatusSource='pending';record.expected.push(hop);pairExtra(record);}
    else if(hasExtraInfo===false&&validStatus(response.status))Object.assign(hop.response,{wireStatus:response.status,wireStatusSource:'response-no-extra',wireStatusMatched:true});
    if(!hop.response.protocolURLMatched)issue('response-url-mismatch');
  }
  const handlers={
    requestWillBeSent(event) {
      if(requests.length>=maxRequests){issue('request-limit');return;}
      const record=recordFor(event.requestId);if(!record)return;
      if(event.redirectResponse)respond(record,event.redirectResponse,'redirect',event.redirectHasExtraInfo);
      else if(record?.current){issue('request-id-reused-without-redirect');return;}
      const row={...safeURL(event.request.url),method:event.request.method,type:event.type||'Other',phase:stage()};
      const hop={...row,rawURL:event.request.url,bodyBytes:0};record.hops.push(hop);record.current=hop;requests.push(row);notify(onRequest,row);
    },
    responseReceived(event){respond(records.get(event.requestId),event.response,'final',event.hasExtraInfo);},
    responseReceivedExtraInfo(event) {
      if(!validStatus(event.statusCode)){issue('invalid-wire-status');return;}
      if(pendingExtraInfo>=maxPendingExtraInfo){issue('extra-info-limit');return;}
      const record=recordFor(event.requestId);if(!record)return;
      record.extra.push(event.statusCode);pendingExtraInfo++;pairExtra(record);
    },
    requestServedFromCache(event) {
      const hop=records.get(event.requestId)?.current;
      if(hop)hop.servedFromCache=true;else issue('cache-without-request');
    },
    dataReceived(event) {
      const delta=event.dataLength;
      if(!Number.isSafeInteger(delta)||delta<0){issue('invalid-body-count');return;}
      bodyBytes+=delta;
      const hop=records.get(event.requestId)?.current;
      if(hop){hop.bodyBytes+=delta;notify(onData,{...safeURL(hop.rawURL),method:hop.method,type:hop.type,phase:stage(),bytes:delta});}
    },
    loadingFailed(event) {
      const hop=records.get(event.requestId)?.current;if(!hop)return;
      const row={...safeURL(hop.rawURL),method:hop.method,type:hop.type,phase:stage(),cancelled:!!event.canceled,
        errorKind:event.errorText==='net::ERR_INTERNET_DISCONNECTED'?'offline':event.canceled?'cancelled':'network'};
      hop.failed=true;hop.cancelled=row.cancelled;hop.failureKind=row.errorKind;hop.failurePhase=row.phase;failures.push(row);notify(onFailure,row);
    }
  };
  return {handlers,snapshot() {
    const responses=[],unresponded=[];
    for(const record of records.values())for(const hop of record.hops)if(hop.response)responses.push({...hop.response,method:hop.method,type:hop.type,phase:hop.phase,bodyBytes:hop.bodyBytes,servedFromCache:!!hop.servedFromCache,failed:!!hop.failed,cancelled:!!hop.cancelled,
      ...(hop.failed?{failureKind:hop.failureKind,failurePhase:hop.failurePhase}:{})});
    else unresponded.push({...safeURL(hop.rawURL),method:hop.method,type:hop.type,phase:hop.phase,bodyBytes:hop.bodyBytes,failed:!!hop.failed,cancelled:!!hop.cancelled,
      ...(hop.failed?{failureKind:hop.failureKind,failurePhase:hop.failurePhase}:{})});
    const snapshotIssues=[...issues];
    // An early extra event may still belong to a later redirect hop. Once the
    // current hop has its response, leftovers contradict complete correlation.
    if([...records.values()].some(record=>record.current?.response&&record.extra.length))snapshotIssues.push('unmatched-extra-info');
    return {requests:requests.map(row=>({...row})),responses,unresponded,failures:failures.map(row=>({...row})),issues:snapshotIssues,bodyBytes,pendingExtraInfo};
  }};
}
function mediaNetworkVerdict(snapshot,{appOrigin,workerOrigin}) {
  const api=row=>row.origin===appOrigin&&row.path.startsWith('/api/assets/')&&row.path.endsWith('.mp3');
  const worker=row=>row.origin===workerOrigin&&row.path.startsWith('/media/')&&row.path.endsWith('.mp3');
  const apiRows=snapshot.responses.filter(api),mediaRows=snapshot.responses.filter(worker);
  const apiBodyBytes=apiRows.concat(snapshot.unresponded||[]).filter(api).reduce((sum,row)=>sum+row.bodyBytes,0);
  const failedBeforeResponse=predicate=>(snapshot.unresponded||[]).some(row=>predicate(row)&&row.failed&&row.cancelled!==true);
  const failed=predicate=>(snapshot.failures||[]).some(row=>predicate(row)&&row.cancelled!==true)||failedBeforeResponse(predicate);
  const sound=row=>(!row.failed||row.cancelled===true)&&row.protocolURLMatched&&row.wireStatusMatched===true&&row.wireStatus===row.status&&row.wireStatus!==304&&!row.fromServiceWorker&&!row.fromDiskCache&&!row.servedFromCache&&!row.fromPrefetchCache&&!row.fromEarlyHints&&row.privateNoStore;
  return {apiRedirectsOnly:snapshot.issues.length===0&&!failed(api)&&apiRows.length>0&&apiBodyBytes===0&&apiRows.every(row=>sound(row)&&!row.contentType.startsWith('audio/')&&row.responseKind==='redirect'&&row.status===307&&row.contentLength==='0'&&row.bodyBytes===0),
    workerAudioValid:snapshot.issues.length===0&&!failed(worker)&&mediaRows.some(row=>row.status===206&&row.bodyBytes>0)&&mediaRows.every(row=>sound(row)&&row.responseKind==='final'&&
      [200,206].includes(row.status)&&row.contentType==='audio/mpeg'&&[appOrigin,'null'].includes(row.corsOrigin)),
    apiResponses:apiRows.length,workerResponses:mediaRows.length,apiBodyBytes};
}
async function observeNativeNetwork(page,options) {
  const ledger=createNetworkLedger(options),session=await page.context().newCDPSession(page);
  const events={
    'Network.requestWillBeSent':ledger.handlers.requestWillBeSent,'Network.responseReceived':ledger.handlers.responseReceived,
    'Network.requestServedFromCache':ledger.handlers.requestServedFromCache,
    'Network.responseReceivedExtraInfo':ledger.handlers.responseReceivedExtraInfo,
    'Network.dataReceived':ledger.handlers.dataReceived,'Network.loadingFailed':ledger.handlers.loadingFailed
  };
  for(const [name,handler]of Object.entries(events))session.on(name,handler);
  await session.send('Network.enable');
  return {snapshot:ledger.snapshot,async close(){for(const [name,handler]of Object.entries(events))session.off(name,handler);await session.detach().catch(()=>{});}};
}
module.exports={safeURL,createNetworkLedger,observeNativeNetwork,mediaNetworkVerdict};
