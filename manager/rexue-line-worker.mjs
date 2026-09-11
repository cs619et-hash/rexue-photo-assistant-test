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
            SELECT message_key, text_content, sent_at, received_at, revoked
            FROM line_messages_v1
            WHERE revoked = 0 AND text_content IS NOT NULL AND expires_at > ?
            ORDER BY received_at DESC LIMIT 200
          `).bind(Date.now()).all();
          return new Response(JSON.stringify({ version: "1.1", messages: rows.results || [] }), {
            headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
          });
        } catch {
          return response("Storage unavailable", 503);
        }
      }
    if (path === '/' && request.method === 'GET') {
      return response('熱血少年：訊息接收程式 v1.1 已部署（含受保護的訊息 API）。此頁不代表 EXE 已完成同步。');
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
