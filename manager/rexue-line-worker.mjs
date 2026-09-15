/* 熱血少年 LINE receiver v1 — authenticated EXE reader v1.1.
 * Binding: DB -> rexue-line-db
 * Secret: LINE_CHANNEL_SECRET (never paste its value in this file)
 * Variable: ACCEPT_MESSAGES=true only AFTER deployment and cron setup.
 * Cron trigger: 0 * * * * (hourly UTC). Expired rows removed on next run.
 * One-to-one text only. Authenticated latest-200 message reader. No replies.
 * Retention: 365 days from original LINE event timestamp, not redelivery.
 * D1 provider backups/Time Travel have separate retention; a restore must
 * reapply expiry and unsend deletions before any future reader is enabled.
 */
const YEAR = 365 * 24 * 60 * 60 * 1000;
const MAX_BODY = 1024 * 1024;
const SCHEMA = `CREATE TABLE IF NOT EXISTS line_messages_v1 (
  message_key TEXT PRIMARY KEY,
  event_id TEXT,
  sender_id TEXT,
  text_content TEXT,
  sent_at INTEGER NOT NULL,
  received_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
)`;
async function setup(db) {
  await db.batch([
    db.prepare(SCHEMA),
    db.prepare('CREATE INDEX IF NOT EXISTS line_messages_v1_expiry ON line_messages_v1(expires_at)')
  ]);
}
function response(text, status = 200) {
  return new Response(text, {status, headers: {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'
  }});
}
async function readLimited(request) {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  let size = 0;
  const chunks = [];
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) { await reader.cancel(); throw new Error('size'); }
    chunks.push(value);
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 200;
// Short-lived in-memory profile cache; no message contents or credentials stored.
const profiles = new Map();
async function addNames(messages, env) {
  const token = String(env.LINE_CHANNEL_ACCESS_TOKEN || '').trim();
  const ids = [...new Set(messages.map(m => m.sender_id).filter(id => /^U[0-9a-f]{32}$/i.test(id || '')))];
  const names = new Map();
  const pending = [];
  for (const id of ids) {
    const cached = profiles.get(id);
    if (cached && cached.until > Date.now()) names.set(id, cached);
    else pending.push(id);
  }
  // Bound request duration and subrequests. Further names load on the next sync.
  const work = token ? pending.slice(0, 20) : [];
  let cursor = 0;
  await Promise.all(Array.from({length: Math.min(4, work.length)}, async () => {
    while (cursor < work.length) {
      const id = work[cursor++];
      let result = {name: '', state: '暫時無法取得名稱'};
      try {
        const r = await fetch('https://api.line.me/v2/bot/profile/' + encodeURIComponent(id), {
          headers: {Authorization: 'Bearer ' + token}, redirect: 'error',
          signal: AbortSignal.timeout(2000)
        });
        if (r.ok) {
          const p = await r.json();
          if (p.userId === id && typeof p.displayName === 'string' && p.displayName.trim())
            result = {name: p.displayName, state: 'LINE 暱稱'};
        } else if (r.status === 401 || r.status === 403) result.state = '姓名讀取授權未通過';
        else if (r.status === 404) result.state = 'LINE 未提供此用戶名稱';
      } catch {}
      result.until = Date.now() + (result.name ? 15 * 60 * 1000 : 30 * 1000);
      if (profiles.size >= 1000) profiles.delete(profiles.keys().next().value);
      profiles.set(id, result); names.set(id, result);
    }
  }));
  return messages.map(m => ({...m,
    display_name: names.get(m.sender_id)?.name || '',
    name_status: !m.sender_id ? '缺少用戶識別碼' : !token ? '尚未設定姓名讀取授權' : names.get(m.sender_id)?.state || '下次同步補取名稱'
  }));
}
export default {
    async fetch(request, env) {
      const path = new URL(request.url).pathname;

      if (path === "/api/messages" && request.method === "GET") {
        const auth = request.headers.get("authorization") || "";
        const configuredToken = String(env.EXE_API_TOKEN || '').trim();
        const suppliedToken = auth.startsWith('Bearer ') ? auth.slice(7).trim() : '';
        if (!configuredToken || suppliedToken !== configuredToken) {
          return response("Unauthorized", 401);
        }
        try {
          await setup(env.DB);
          const rows = await env.DB.prepare(`
            SELECT message_key, sender_id, text_content, sent_at, received_at, revoked
            FROM line_messages_v1
            WHERE revoked = 0 AND text_content IS NOT NULL AND expires_at > ?
            ORDER BY received_at DESC LIMIT 200
          `).bind(Date.now()).all();
          return new Response(JSON.stringify({ version: "1.3", messages: await addNames(rows.results || [], env) }), {
            headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
          });
        } catch {
          return response("Storage unavailable", 503);
        }
      }
    if (path === '/' && request.method === 'GET') {
      return response('熱血少年：訊息接收程式 v1.3 已部署（含受保護的訊息 API）。此頁不代表 EXE 已完成同步。');
    }
    if (path !== '/webhook') return response('Not found', 404);
    if (request.method !== 'POST') return response('Method not allowed', 405);
    if (!env.LINE_CHANNEL_SECRET || !env.DB) return response('Server configuration missing', 503);
    const signature = request.headers.get('x-line-signature');
    if (!signature || !/^[A-Za-z0-9+/]{43}=$/.test(signature)) return response('Unauthorized', 401);
    let body;
    try { body = await readLimited(request); }
    catch { return response('Request too large or unreadable', 413); }
    try {
      const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(env.LINE_CHANNEL_SECRET),
        {name: 'HMAC', hash: 'SHA-256'}, false, ['verify']);
      const bytes = Uint8Array.from(atob(signature), c => c.charCodeAt(0));
      if (!await crypto.subtle.verify('HMAC', key, bytes, body)) return response('Unauthorized', 401);
    } catch { return response('Unauthorized', 401); }
    let data;
    try { data = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(body)); }
    catch { return response('Invalid JSON', 400); }
    if (!identifier(data?.destination) || !Array.isArray(data.events) || data.events.length > 100) {
      return response('Invalid payload', 400);
    }
    if (data.events.length && env.ACCEPT_MESSAGES !== 'true') return response('Receiver not enabled', 503);
    const now = Date.now();
    const statements = [];
    for (const event of data.events) {
      if (!event || typeof event !== 'object') return response('Invalid event', 400);
      if (event.source?.type !== 'user') continue;
      if (event.type !== 'unsend' && !(event.type === 'message' && event.message?.type === 'text')) continue;
      if (!identifier(event.source.userId) || !identifier(event.webhookEventId) ||
          !Number.isSafeInteger(event.timestamp) || event.timestamp <= 0 || event.timestamp > now + 300000) {
        return response('Invalid event fields', 400);
      }
      const id = event.type === 'unsend' ? event.unsend?.messageId : event.message.id;
      if (!identifier(id)) return response('Invalid message ID', 400);
      const messageKey = JSON.stringify([data.destination, id]);
      if (event.type === 'unsend') {
        // Keep a content-free tombstone, so out-of-order redelivery cannot resurrect text.
        statements.push(env.DB.prepare(`INSERT INTO line_messages_v1
          (message_key,event_id,sender_id,text_content,sent_at,received_at,expires_at,revoked)
          VALUES (?,NULL,NULL,NULL,?,?,?,1)
          ON CONFLICT(message_key) DO UPDATE SET
          event_id=NULL,sender_id=NULL,text_content=NULL,revoked=1,
          expires_at=MAX(line_messages_v1.expires_at,excluded.expires_at)`)
          .bind(messageKey, event.timestamp, now, now + YEAR));
      } else {
        if (typeof event.message.text !== 'string' || event.message.text.length > 20000) return response('Invalid text', 400);
        if (event.timestamp + YEAR <= now) continue;
        statements.push(env.DB.prepare(`INSERT INTO line_messages_v1
          (message_key,event_id,sender_id,text_content,sent_at,received_at,expires_at,revoked)
          VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(message_key) DO NOTHING`)
          .bind(messageKey,event.webhookEventId,event.source.userId,event.message.text,event.timestamp,now,event.timestamp + YEAR));
      }
    }
    try {
      await setup(env.DB);
      statements.push(env.DB.prepare('DELETE FROM line_messages_v1 WHERE expires_at <= ?').bind(now));
      await env.DB.batch(statements);
      return response('OK');
    } catch {
      // Do not log request bodies or secrets; failed writes must not receive HTTP 200.
      return response('Storage unavailable; retry required', 503);
    }
  },
  async scheduled(controller, env, ctx) {
    if (!env.DB) throw new Error('Missing DB');
    await setup(env.DB);
    await env.DB.prepare('DELETE FROM line_messages_v1 WHERE expires_at <= ?').bind(Date.now()).run();
  }
};
