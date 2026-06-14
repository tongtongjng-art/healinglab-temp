// Candidate + editor save API
// Start: pm2 start candidate_api_server.js --name candidate-api
// Nginx should proxy /candidate-api/ to http://127.0.0.1:3021/

const http = require("http");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const ROOT = __dirname;
const CONFIG_PATH = path.join(ROOT, "config.json");

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  } catch (e) {
    return {};
  }
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS"
  });
  res.end(body);
}

function readBody(req, limitBytes) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", chunk => {
      data += chunk;
      if (Buffer.byteLength(data, "utf8") > limitBytes) {
        reject(new Error("body too large"));
        req.destroy();
      }
    });
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });
}

function checkToken(cfg, token) {
  const expectedToken = cfg.candidate_api_token || "";
  return !expectedToken || String(token || "").trim() === expectedToken;
}

function dailyDir(cfg) {
  const upload = cfg.upload || {};
  return path.resolve(upload.remote_dir || "/var/www/html/daily");
}

function safeDate(value) {
  const raw = String(value || "").trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : new Date().toISOString().slice(0, 10);
}

function handleSelectCandidate(req, res, cfg, body) {
  const id = String(body.id || "").trim();
  const token = String(body.token || "").trim();

  if (!id || !/^[a-f0-9]{8,20}$/i.test(id)) {
    return sendJson(res, 400, { ok: false, error: "候选 ID 不合法" });
  }
  if (!checkToken(cfg, token)) {
    return sendJson(res, 403, { ok: false, error: "token 不正确" });
  }

  const script = path.join(ROOT, "server_run_daily.py");
  execFile("python3", [script, "--use-candidate", id], {
    cwd: ROOT,
    timeout: 360000,
    maxBuffer: 1024 * 1024 * 3
  }, (err, stdout, stderr) => {
    if (err) {
      return sendJson(res, 500, {
        ok: false,
        error: "生成失败：" + err.message,
        stdout: (stdout || "").slice(-2000),
        stderr: (stderr || "").slice(-2000)
      });
    }
    return sendJson(res, 200, {
      ok: true,
      message: "生成完成",
      stdout: (stdout || "").slice(-2000)
    });
  });
}

function handleSaveFinal(res, cfg, body) {
  if (!checkToken(cfg, body.token)) {
    return sendJson(res, 403, { ok: false, error: "token 不正确" });
  }

  const html = String(body.html || "");
  if (!html.includes("<!doctype html") && !html.includes("<!DOCTYPE html")) {
    return sendJson(res, 400, { ok: false, error: "保存内容不是完整 HTML" });
  }
  if (html.length < 500 || html.length > 1024 * 1024 * 3) {
    return sendJson(res, 400, { ok: false, error: "HTML 长度不正常" });
  }

  const dir = dailyDir(cfg);
  const archiveDir = path.join(dir, "archive");
  const date = safeDate(body.date);
  fs.mkdirSync(dir, { recursive: true });
  fs.mkdirSync(archiveDir, { recursive: true });

  const latestPath = path.join(dir, "latest.html");
  const indexPath = path.join(dir, "index.html");
  const archivePath = path.join(archiveDir, `day-${date}-edited.html`);

  if (fs.existsSync(latestPath)) {
    fs.copyFileSync(latestPath, path.join(dir, `latest.before_edit_${Date.now()}.html`));
  }

  fs.writeFileSync(latestPath, html, "utf8");
  fs.writeFileSync(indexPath, html, "utf8");
  fs.writeFileSync(archivePath, html, "utf8");

  return sendJson(res, 200, {
    ok: true,
    message: "已保存为正式页",
    latest: "/daily/latest.html",
    archive: `/daily/archive/day-${date}-edited.html`
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    return sendJson(res, 200, { ok: true });
  }
  if (req.method !== "POST") {
    return sendJson(res, 404, { ok: false, error: "not found" });
  }

  const route = req.url.split("?")[0].replace(/\/+$/, "");
  if (route !== "/select-candidate" && route !== "/save-final") {
    return sendJson(res, 404, { ok: false, error: "not found" });
  }

  try {
    const cfg = readConfig();
    const raw = await readBody(req, route === "/save-final" ? 1024 * 1024 * 4 : 1024 * 1024);
    const body = JSON.parse(raw || "{}");

    if (route === "/select-candidate") {
      return handleSelectCandidate(req, res, cfg, body);
    }
    return handleSaveFinal(res, cfg, body);
  } catch (e) {
    return sendJson(res, 500, { ok: false, error: e.message });
  }
});

const cfg = readConfig();
const port = Number(cfg.candidate_api_port || 3021);
server.listen(port, "127.0.0.1", () => {
  console.log("candidate-api listening on 127.0.0.1:" + port);
});
