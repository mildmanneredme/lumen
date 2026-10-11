'use strict';
// CDP response URLs identify the actual response hop. Playwright Response.url()
// is taken from its tracked Request and can misattribute native media redirects.
// Raw URLs stay in memory only for exact hop comparison, never in snapshots.
function safeURL(value) {
  try {const u=new URL(value);return {origin:u.origin,path:u.pathname};}
  catch(_){return {origin:'invalid',path:''};}
}
// Active official Chromium NET_ERROR names, checked 2026-10-11:
// https://raw.githubusercontent.com/chromium/chromium/main/net/base/net_error_list.h
// Source SHA256: 8647b49488fbd0856ff03056c459ddfd115a89533637ddb8037637e9c7def243
// Static names only: arbitrary browser messages and URLs never enter evidence.
const NET_ERRORS=new Set([
  'ERR_IO_PENDING','ERR_FAILED','ERR_ABORTED','ERR_INVALID_ARGUMENT','ERR_INVALID_HANDLE','ERR_FILE_NOT_FOUND',
  'ERR_TIMED_OUT','ERR_FILE_TOO_BIG','ERR_UNEXPECTED','ERR_ACCESS_DENIED','ERR_NOT_IMPLEMENTED','ERR_INSUFFICIENT_RESOURCES',
  'ERR_OUT_OF_MEMORY','ERR_UPLOAD_FILE_CHANGED','ERR_SOCKET_NOT_CONNECTED','ERR_FILE_EXISTS','ERR_FILE_PATH_TOO_LONG','ERR_FILE_NO_SPACE',
  'ERR_FILE_VIRUS_INFECTED','ERR_BLOCKED_BY_CLIENT','ERR_NETWORK_CHANGED','ERR_BLOCKED_BY_ADMINISTRATOR','ERR_SOCKET_IS_CONNECTED','ERR_UPLOAD_STREAM_REWIND_NOT_SUPPORTED',
  'ERR_CONTEXT_SHUT_DOWN','ERR_BLOCKED_BY_RESPONSE','ERR_CLEARTEXT_NOT_PERMITTED','ERR_BLOCKED_BY_CSP','ERR_BLOCKED_BY_ORB','ERR_NETWORK_ACCESS_REVOKED',
  'ERR_BLOCKED_BY_FINGERPRINTING_PROTECTION','ERR_BLOCKED_IN_INCOGNITO_BY_ADMINISTRATOR','ERR_LOCAL_NETWORK_PERMISSION_MISSING','ERR_STRICT_ECH_REQUIRED','ERR_CONNECTION_CLOSED','ERR_CONNECTION_RESET',
  'ERR_CONNECTION_REFUSED','ERR_CONNECTION_ABORTED','ERR_CONNECTION_FAILED','ERR_NAME_NOT_RESOLVED','ERR_INTERNET_DISCONNECTED','ERR_SSL_PROTOCOL_ERROR',
  'ERR_ADDRESS_INVALID','ERR_ADDRESS_UNREACHABLE','ERR_SSL_CLIENT_AUTH_CERT_NEEDED','ERR_TUNNEL_CONNECTION_FAILED','ERR_SSL_VERSION_OR_CIPHER_MISMATCH','ERR_SSL_RENEGOTIATION_REQUESTED',
  'ERR_PROXY_AUTH_UNSUPPORTED','ERR_BAD_SSL_CLIENT_AUTH_CERT','ERR_CONNECTION_TIMED_OUT','ERR_HOST_RESOLVER_QUEUE_TOO_LARGE','ERR_SOCKS_CONNECTION_FAILED','ERR_SOCKS_CONNECTION_HOST_UNREACHABLE',
  'ERR_ALPN_NEGOTIATION_FAILED','ERR_SSL_NO_RENEGOTIATION','ERR_WINSOCK_UNEXPECTED_WRITTEN_BYTES','ERR_SSL_DECOMPRESSION_FAILURE_ALERT','ERR_SSL_BAD_RECORD_MAC_ALERT','ERR_PROXY_AUTH_REQUESTED',
  'ERR_PROXY_CONNECTION_FAILED','ERR_MANDATORY_PROXY_CONFIGURATION_FAILED','ERR_PRECONNECT_MAX_SOCKET_LIMIT','ERR_SSL_CLIENT_AUTH_PRIVATE_KEY_ACCESS_DENIED','ERR_SSL_CLIENT_AUTH_CERT_NO_PRIVATE_KEY','ERR_PROXY_CERTIFICATE_INVALID',
  'ERR_NAME_RESOLUTION_FAILED','ERR_NETWORK_ACCESS_DENIED','ERR_TEMPORARILY_THROTTLED','ERR_SSL_CLIENT_AUTH_SIGNATURE_FAILED','ERR_MSG_TOO_BIG','ERR_WS_PROTOCOL_ERROR',
  'ERR_ADDRESS_IN_USE','ERR_SSL_PINNED_KEY_NOT_IN_CERT_CHAIN','ERR_CLIENT_AUTH_CERT_TYPE_UNSUPPORTED','ERR_SSL_DECRYPT_ERROR_ALERT','ERR_WS_THROTTLE_QUEUE_TOO_LARGE','ERR_SSL_SERVER_CERT_CHANGED',
  'ERR_SSL_UNRECOGNIZED_NAME_ALERT','ERR_SOCKET_SET_RECEIVE_BUFFER_SIZE_ERROR','ERR_SOCKET_SET_SEND_BUFFER_SIZE_ERROR','ERR_SOCKET_RECEIVE_BUFFER_SIZE_UNCHANGEABLE','ERR_SOCKET_SEND_BUFFER_SIZE_UNCHANGEABLE','ERR_SSL_CLIENT_AUTH_CERT_BAD_FORMAT',
  'ERR_ICANN_NAME_COLLISION','ERR_SSL_SERVER_CERT_BAD_FORMAT','ERR_CT_STH_PARSING_FAILED','ERR_CT_STH_INCOMPLETE','ERR_UNABLE_TO_REUSE_CONNECTION_FOR_PROXY_AUTH','ERR_CT_CONSISTENCY_PROOF_PARSING_FAILED',
  'ERR_SSL_OBSOLETE_CIPHER','ERR_WS_UPGRADE','ERR_READ_IF_READY_NOT_IMPLEMENTED','ERR_NO_BUFFER_SPACE','ERR_SSL_CLIENT_AUTH_NO_COMMON_ALGORITHMS','ERR_EARLY_DATA_REJECTED',
  'ERR_WRONG_VERSION_ON_EARLY_DATA','ERR_TLS13_DOWNGRADE_DETECTED','ERR_SSL_KEY_USAGE_INCOMPATIBLE','ERR_INVALID_ECH_CONFIG_LIST','ERR_ECH_NOT_NEGOTIATED','ERR_ECH_FALLBACK_CERTIFICATE_INVALID',
  'ERR_PROXY_UNABLE_TO_CONNECT_TO_DESTINATION','ERR_PROXY_DELEGATE_CANCELED_CONNECT_REQUEST','ERR_PROXY_DELEGATE_CANCELED_CONNECT_RESPONSE','ERR_CONTROL_MSG_TOO_BIG','ERR_MULTICAST_NOT_ALLOWED','ERR_CERT_COMMON_NAME_INVALID',
  'ERR_CERT_DATE_INVALID','ERR_CERT_AUTHORITY_INVALID','ERR_CERT_CONTAINS_ERRORS','ERR_CERT_NO_REVOCATION_MECHANISM','ERR_CERT_UNABLE_TO_CHECK_REVOCATION','ERR_CERT_REVOKED',
  'ERR_CERT_INVALID','ERR_CERT_WEAK_SIGNATURE_ALGORITHM','ERR_CERT_NON_UNIQUE_NAME','ERR_CERT_WEAK_KEY','ERR_CERT_NAME_CONSTRAINT_VIOLATION','ERR_CERT_VALIDITY_TOO_LONG',
  'ERR_CERTIFICATE_TRANSPARENCY_REQUIRED','ERR_CERT_KNOWN_INTERCEPTION_BLOCKED','ERR_CERT_SELF_SIGNED_LOCAL_NETWORK','ERR_CERT_END','ERR_INVALID_URL','ERR_DISALLOWED_URL_SCHEME',
  'ERR_UNKNOWN_URL_SCHEME','ERR_INVALID_REDIRECT','ERR_TOO_MANY_REDIRECTS','ERR_UNSAFE_REDIRECT','ERR_UNSAFE_PORT','ERR_INVALID_RESPONSE',
  'ERR_INVALID_CHUNKED_ENCODING','ERR_METHOD_NOT_SUPPORTED','ERR_UNEXPECTED_PROXY_AUTH','ERR_EMPTY_RESPONSE','ERR_RESPONSE_HEADERS_TOO_BIG','ERR_PAC_SCRIPT_FAILED',
  'ERR_REQUEST_RANGE_NOT_SATISFIABLE','ERR_MALFORMED_IDENTITY','ERR_CONTENT_DECODING_FAILED','ERR_NETWORK_IO_SUSPENDED','ERR_NO_SUPPORTED_PROXIES','ERR_HTTP2_PROTOCOL_ERROR',
  'ERR_INVALID_AUTH_CREDENTIALS','ERR_UNSUPPORTED_AUTH_SCHEME','ERR_ENCODING_DETECTION_FAILED','ERR_MISSING_AUTH_CREDENTIALS','ERR_UNEXPECTED_SECURITY_LIBRARY_STATUS','ERR_MISCONFIGURED_AUTH_ENVIRONMENT',
  'ERR_UNDOCUMENTED_SECURITY_LIBRARY_STATUS','ERR_RESPONSE_BODY_TOO_BIG_TO_DRAIN','ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_LENGTH','ERR_INCOMPLETE_HTTP2_HEADERS','ERR_PAC_NOT_IN_DHCP','ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_DISPOSITION',
  'ERR_RESPONSE_HEADERS_MULTIPLE_LOCATION','ERR_HTTP2_SERVER_REFUSED_STREAM','ERR_HTTP2_PING_FAILED','ERR_CONTENT_LENGTH_MISMATCH','ERR_INCOMPLETE_CHUNKED_ENCODING','ERR_QUIC_PROTOCOL_ERROR',
  'ERR_RESPONSE_HEADERS_TRUNCATED','ERR_QUIC_HANDSHAKE_FAILED','ERR_HTTP2_INADEQUATE_TRANSPORT_SECURITY','ERR_HTTP2_FLOW_CONTROL_ERROR','ERR_HTTP2_FRAME_SIZE_ERROR','ERR_HTTP2_COMPRESSION_ERROR',
  'ERR_PROXY_AUTH_REQUESTED_WITH_NO_CONNECTION','ERR_HTTP_1_1_REQUIRED','ERR_PROXY_HTTP_1_1_REQUIRED','ERR_PAC_SCRIPT_TERMINATED','ERR_INVALID_HTTP_RESPONSE','ERR_CONTENT_DECODING_INIT_FAILED',
  'ERR_HTTP2_RST_STREAM_NO_ERROR_RECEIVED','ERR_TOO_MANY_RETRIES','ERR_HTTP2_STREAM_CLOSED','ERR_HTTP_RESPONSE_CODE_FAILURE','ERR_QUIC_CERT_ROOT_NOT_KNOWN','ERR_QUIC_GOAWAY_REQUEST_CAN_BE_RETRIED',
  'ERR_TOO_MANY_ACCEPT_CH_RESTARTS','ERR_INCONSISTENT_IP_ADDRESS_SPACE','ERR_CACHED_IP_ADDRESS_SPACE_BLOCKED_BY_LOCAL_NETWORK_ACCESS_POLICY','ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS','ERR_ZSTD_WINDOW_SIZE_TOO_BIG','ERR_DICTIONARY_LOAD_FAILED',
  'ERR_UNEXPECTED_CONTENT_DICTIONARY_HEADER','ERR_CACHE_MISS','ERR_CACHE_READ_FAILURE','ERR_CACHE_WRITE_FAILURE','ERR_CACHE_OPERATION_NOT_SUPPORTED','ERR_CACHE_OPEN_FAILURE',
  'ERR_CACHE_CREATE_FAILURE','ERR_CACHE_RACE','ERR_CACHE_CHECKSUM_READ_FAILURE','ERR_CACHE_CHECKSUM_MISMATCH','ERR_CACHE_LOCK_TIMEOUT','ERR_CACHE_AUTH_FAILURE_AFTER_READ',
  'ERR_CACHE_ENTRY_NOT_SUITABLE','ERR_CACHE_DOOM_FAILURE','ERR_CACHE_OPEN_OR_CREATE_FAILURE','ERR_CACHE_COMPRESSION_FAILURE','ERR_INSECURE_RESPONSE','ERR_NO_PRIVATE_KEY_FOR_CERT',
  'ERR_ADD_USER_CERT_FAILED','ERR_INVALID_SIGNED_EXCHANGE','ERR_INVALID_WEB_BUNDLE','ERR_TRUST_TOKEN_OPERATION_FAILED','ERR_TRUST_TOKEN_OPERATION_SUCCESS_WITHOUT_SENDING_REQUEST','ERR_HTTPENGINE_PROVIDER_IN_USE',
  'ERR_PKCS12_IMPORT_BAD_PASSWORD','ERR_PKCS12_IMPORT_FAILED','ERR_IMPORT_CA_CERT_NOT_CA','ERR_IMPORT_CERT_ALREADY_EXISTS','ERR_IMPORT_CA_CERT_FAILED','ERR_IMPORT_SERVER_CERT_FAILED',
  'ERR_PKCS12_IMPORT_INVALID_MAC','ERR_PKCS12_IMPORT_INVALID_FILE','ERR_PKCS12_IMPORT_UNSUPPORTED','ERR_KEY_GENERATION_FAILED','ERR_PRIVATE_KEY_EXPORT_FAILED','ERR_SELF_SIGNED_CERT_GENERATION_FAILED',
  'ERR_CERT_DATABASE_CHANGED','ERR_CERT_VERIFIER_CHANGED','ERR_DNS_MALFORMED_RESPONSE','ERR_DNS_SERVER_REQUIRES_TCP','ERR_DNS_TIMED_OUT','ERR_DNS_CACHE_MISS',
  'ERR_DNS_SEARCH_EMPTY','ERR_DNS_SORT_ERROR','ERR_DNS_SECURE_RESOLVER_HOSTNAME_RESOLUTION_FAILED','ERR_DNS_NAME_HTTPS_ONLY','ERR_DNS_REQUEST_CANCELLED','ERR_DNS_NO_MATCHING_SUPPORTED_ALPN',
  'ERR_DNS_SECURE_PROBE_RECORD_INVALID','ERR_DNS_CACHE_INVALIDATION_IN_PROGRESS','ERR_DNS_FORMAT_ERROR','ERR_DNS_SERVER_FAILURE','ERR_DNS_NOT_IMPLEMENTED','ERR_DNS_REFUSED',
  'ERR_DNS_OTHER_FAILURE','ERR_DNS_DIRECT_ONLY','ERR_BLOB_INVALID_CONSTRUCTION_ARGUMENTS','ERR_BLOB_OUT_OF_MEMORY','ERR_BLOB_FILE_WRITE_FAILED','ERR_BLOB_SOURCE_DIED_IN_TRANSIT',
  'ERR_BLOB_DEREFERENCED_WHILE_BUILDING','ERR_BLOB_REFERENCED_BLOB_BROKEN','ERR_BLOB_REFERENCED_FILE_UNAVAILABLE'
]);
const PROTOCOLS=new Set(['http/1.0','http/1.1','h2','h3','quic']);
// Official CDP Network enums; never retain CorsErrorStatus.failedParameter.
const CORS_ERRORS=new Set('DisallowedByMode InvalidResponse WildcardOriginNotAllowed MissingAllowOriginHeader MultipleAllowOriginValues InvalidAllowOriginValue AllowOriginMismatch InvalidAllowCredentials CorsDisabledScheme PreflightInvalidStatus PreflightDisallowedRedirect PreflightWildcardOriginNotAllowed PreflightMissingAllowOriginHeader PreflightMultipleAllowOriginValues PreflightInvalidAllowOriginValue PreflightAllowOriginMismatch PreflightInvalidAllowCredentials PreflightMissingAllowExternal PreflightInvalidAllowExternal InvalidAllowMethodsPreflightResponse InvalidAllowHeadersPreflightResponse MethodDisallowedByPreflightResponse HeaderDisallowedByPreflightResponse RedirectContainsCredentials InsecureLocalNetwork InvalidLocalNetworkAccess NoCorsRedirectModeNotFollow LocalNetworkAccessPermissionDenied'.split(' '));
const BLOCKED_REASONS=new Set('other csp mixed-content origin inspector integrity subresource-filter content-type coep-frame-resource-needs-coep-header coop-sandboxed-iframe-cannot-navigate-to-coop-page corp-not-same-origin corp-not-same-origin-after-defaulted-to-same-origin-by-coep corp-not-same-origin-after-defaulted-to-same-origin-by-dip corp-not-same-origin-after-defaulted-to-same-origin-by-coep-and-dip corp-not-same-site sri-message-signature-mismatch'.split(' '));
function safeFailureDetails(event={}) {
  const code=typeof event.errorText==='string'?/\bnet::(ERR_[A-Z0-9_]+)\b/.exec(event.errorText.slice(0,8192))?.[1]:null;
  const selected=(value,allowed)=>value==null?'none':allowed.has(value)?value:'unknown';
  return {netErrorCode:NET_ERRORS.has(code)?code:'unknown',corsError:selected(event.corsErrorStatus?.corsError,CORS_ERRORS),blockedReason:selected(event.blockedReason,BLOCKED_REASONS)};
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
    return {status:response.status,protocol:PROTOCOLS.has(response.protocol)?response.protocol:response.protocol?'other':'unknown',contentType:/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(type)?type:'',
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
        errorKind:event.errorText==='net::ERR_INTERNET_DISCONNECTED'?'offline':event.canceled?'cancelled':'network',...safeFailureDetails(event)};
      hop.failed=true;hop.cancelled=row.cancelled;hop.failureKind=row.errorKind;hop.failurePhase=row.phase;hop.failureDetails=safeFailureDetails(event);failures.push(row);notify(onFailure,row);
    }
  };
  return {handlers,snapshot() {
    const responses=[],unresponded=[];
    for(const record of records.values())for(const hop of record.hops)if(hop.response)responses.push({...hop.response,method:hop.method,type:hop.type,phase:hop.phase,bodyBytes:hop.bodyBytes,servedFromCache:!!hop.servedFromCache,failed:!!hop.failed,cancelled:!!hop.cancelled,
      ...(hop.failed?{failureKind:hop.failureKind,failurePhase:hop.failurePhase,...hop.failureDetails}:{})});
    else unresponded.push({...safeURL(hop.rawURL),method:hop.method,type:hop.type,phase:hop.phase,bodyBytes:hop.bodyBytes,failed:!!hop.failed,cancelled:!!hop.cancelled,
      unpairedWireStatuses:hop===record.current?[...record.extra]:[],
      ...(hop.failed?{failureKind:hop.failureKind,failurePhase:hop.failurePhase,...hop.failureDetails}:{})});
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
module.exports={safeURL,safeFailureDetails,createNetworkLedger,observeNativeNetwork,mediaNetworkVerdict};
