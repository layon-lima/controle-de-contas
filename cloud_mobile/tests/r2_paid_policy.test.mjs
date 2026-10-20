// node --test cloud_mobile/tests/r2_paid_policy.test.mjs
// Simula Cloudflare D1/R2 sem contas reais, rede ou chaves.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const codePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "../src/index.js"
);
const source = readFileSync(codePath, "utf8");
const worker = new Function(
  "Response", "Headers", "URL",
  source.replace("export default {", "const worker = {") + "\nreturn worker;"
)(Response, Headers, URL);

class FakeDB {
  constructor() {
    this.accounts = new Map();
    this.metadata = new Map();
  }
  prepare(sql) {
    const db = this;
    const stmt = sql.replace(/\s+/g, " ").trim();
    let values = [];
    return {
      bind(...data) { values = data; return this; },
      async all() {
        if (stmt.includes("SELECT id,pdf_hash,pdf_name,pdf_pages,payload FROM accounts")) {
          return { results: [...db.accounts.values()].map(row => ({ ...row })) };
        }
        throw Error("Query all nao simulada: " + stmt);
      },
      async first() {
        if (stmt.includes("FROM accounts WHERE id")) return db.accounts.get(values[0]) || null;
        if (stmt.includes("FROM app_meta")) return { value: db.metadata.get(values[0]) || "" };
        throw Error("Query first nao simulada: " + stmt);
      },
      async run() {
        if (stmt.startsWith("INSERT INTO accounts(")) {
          const [id, payload, pdf_hash, pdf_name, pdf_pages, updated_at] = values;
          db.accounts.set(id, { id, payload, pdf_hash, pdf_name, pdf_pages, updated_at });
        } else if (stmt.includes("UPDATE accounts SET pdf_hash=''")) {
          const record = db.accounts.get(values[0]);
          if (record && JSON.parse(record.payload).data_pagamento) {
            record.pdf_hash = "";
            record.pdf_name = "";
            record.pdf_pages = 0;
          }
        } else if (stmt.startsWith("INSERT INTO app_meta")) {
          db.metadata.set(values[0], values[1]);
        } else if (stmt.includes("UPDATE accounts SET pdf_pages=")) {
          const record = db.accounts.get(values[3]);
          if (record) {
            record.pdf_pages = values[0];
            record.pdf_hash = values[1];
            record.pdf_name = values[2];
          }
        } else {
          throw Error("Query run nao simulada: " + stmt);
        }
        return { success: true };
      },
    };
  }
  async batch(statements) {
    for (const statement of statements) await statement.run();
  }
}

class FakeR2 {
  constructor() {
    this.objects = new Map();
    this.failDelete = false;
  }
  async list({ prefix, limit }) {
    return {
      objects: [...this.objects.keys()].filter(key => key.startsWith(prefix))
        .slice(0, limit).map(key => ({ key })),
    };
  }
  async delete(keys) {
    if (this.failDelete) throw Error("Falha temporaria R2");
    for (const key of keys) this.objects.delete(key);
  }
  async put(key, body) {
    this.objects.set(key, {
      body,
      writeHttpMetadata(headers) { headers.set("content-type", "application/pdf"); },
    });
  }
  async get(key) {
    return this.objects.get(key) || null;
  }
}

function fixture() {
  const db = new FakeDB();
  const r2 = new FakeR2();
  const env = { DB: db, PDFS: r2, SYNC_TOKEN: "teste-token", VIEW_TOKEN: "visualizacao" };
  const base = "https://contas-cloud.fazendanovohorizonte.com";
  const originalWindows = new Map([[7, "%PDF-1.4\nORIGINAL WINDOWS"]]);
  async function request(route, method = "GET", body, headers = {}) {
    const options = {
      method,
      headers: {
        "X-Sync-Token": env.SYNC_TOKEN,
        "X-View-Token": env.VIEW_TOKEN,
        ...headers,
      },
    };
    if (body !== undefined) {
      options.body = typeof body === "string" ? body : JSON.stringify(body);
      if (typeof body !== "string") options.headers["Content-Type"] = "application/json";
    }
    const response = await worker.fetch(new Request(base + route, options), env);
    const decoded = await response.json();
    return { status: response.status, data: decoded };
  }
  const open = {
    id: 7, fornecedor: "FORNECEDOR FICTICIO", vencimento: "2027-01-15",
    data_pagamento: null, pdf_hash: "SHA_TESTE", pdf_name: "BOLETO.pdf",
    local_pdf_confirmed: false,
  };
  async function sync(account) {
    return request("/api/sync/accounts", "POST", {
      accounts: [account], synced_at: "2026-10-20T12:00:00",
    });
  }
  function setOriginal() {
    r2.objects.set("accounts/7/original.pdf", { body: "PDF NA NUVEM" });
    r2.objects.set("accounts/7/pages/1.jpg", { body: "JPEG NA NUVEM" });
    const row = db.accounts.get(7);
    row.pdf_pages = 1;
  }
  return { db, r2, env, open, request, sync, setOriginal, originalWindows };
}

test("conta em aberto e solicitada para sincronizacao", async () => {
  const ctx = fixture();
  const result = await ctx.sync(ctx.open);
  assert.equal(result.status, 200);
  assert.deepEqual(result.data.need_pdfs, [7]);
});

test("conta paga so e apagada da nuvem apos confirmar original Windows", async () => {
  const ctx = fixture();
  await ctx.sync(ctx.open);
  ctx.setOriginal();
  const paid = { ...ctx.open, data_pagamento: "2026-10-20", pdf_hash: "" };
  const first = await ctx.sync({ ...paid, local_pdf_confirmed: false });
  assert.equal(first.status, 200);
  assert.equal(ctx.r2.objects.size, 2);
  const hidden = await ctx.request("/api/pdf/7/meta");
  assert.equal(hidden.status, 404);
  const confirmed = await ctx.sync({ ...paid, local_pdf_confirmed: true });
  assert.equal(confirmed.status, 200);
  assert.equal(ctx.r2.objects.size, 0);
  assert.equal(ctx.db.accounts.get(7).pdf_pages, 0);
  assert.equal(ctx.originalWindows.get(7), "%PDF-1.4\nORIGINAL WINDOWS");
});

test("falha na exclusao R2 gera retry e nao apaga arquivo PC", async () => {
  const ctx = fixture();
  await ctx.sync(ctx.open);
  ctx.setOriginal();
  ctx.r2.failDelete = true;
  const paid = { ...ctx.open, data_pagamento: "2026-10-20", local_pdf_confirmed: true };
  const failed = await ctx.sync(paid);
  assert.deepEqual(failed.data.pending_pdf_deletions, [7]);
  assert.equal(ctx.r2.objects.size, 2);
  assert.equal(ctx.db.accounts.get(7).pdf_hash, "SHA_TESTE");
  ctx.r2.failDelete = false;
  const recovered = await ctx.sync(paid);
  assert.deepEqual(recovered.data.pending_pdf_deletions, []);
  assert.equal(ctx.r2.objects.size, 0);
  assert.equal(ctx.db.accounts.get(7).pdf_hash, "");
  assert.ok(ctx.originalWindows.has(7));
});

test("pagamento revertido solicita novamente PDF ao R2", async () => {
  const ctx = fixture();
  await ctx.sync(ctx.open);
  ctx.setOriginal();
  await ctx.sync({ ...ctx.open, data_pagamento: "2026-10-20", local_pdf_confirmed: true });
  const reset = await ctx.sync(ctx.open);
  assert.deepEqual(reset.data.need_pdfs, [7]);
});

test("Worker recusa envio de PDF se a conta foi paga", async () => {
  const ctx = fixture();
  await ctx.sync({ ...ctx.open, data_pagamento: "2026-10-20", local_pdf_confirmed: true });
  const result = await ctx.request(
    "/api/sync/pdf/7", "PUT", "PDF QUE NAO DEVE SUBIR",
    { "Content-Type": "application/pdf" },
  );
  assert.equal(result.status, 409);
  assert.equal(ctx.r2.objects.size, 0);
});

test("mobile lista apenas contas em aberto, inclusive vencidas, sem esconder cadastro pago em D1", async () => {
  const ctx = fixture();
  const pending = { ...ctx.open, id: 8, vencimento: "2026-01-01", pdf_hash: "" };
  const paid = { ...ctx.open, id: 9, data_pagamento: "2026-10-20", local_pdf_confirmed: false };
  const saved = await ctx.request("/api/sync/accounts", "POST", {
    accounts: [ctx.open, pending, paid],
    synced_at: "2026-10-20T12:00:00",
  });
  assert.equal(saved.status, 200);
  assert.equal(ctx.db.accounts.size, 3, "O D1 preserva a conta paga para reconciliação R2.");
  const mobile = await ctx.request("/api/contas");
  assert.equal(mobile.status, 200);
  assert.equal(mobile.data.quantidade, 2);
  assert.deepEqual(mobile.data.contas.map(c => c.id).sort((a,b) => a-b), [7, 8]);
  assert.ok(mobile.data.contas.every(c => c.status !== "Paga"));
  assert.ok(mobile.data.contas.some(c => c.status === "Vencida"));
});
