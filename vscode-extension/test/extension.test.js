// Runs the extension against a fake `vscode` module and a fake Recall endpoint: node --test test/extension.test.js
const assert = require("node:assert");
const fs = require("node:fs");
const http = require("node:http");
const Module = require("node:module");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

function fakeEditor(scheme = "file") {
  return {
    document: {
      uri: { scheme, fsPath: "C:\\demo\\src\\app.py" },
      languageId: "python",
      lineCount: 100,
      lineAt: (i) => ({ text: `line ${i + 1}` }),
    },
    visibleRanges: [{ start: { line: 10 }, end: { line: 14 } }],
  };
}

const listeners = [];
const on = () => (fn) => { listeners.push(fn); return { dispose() {} }; };
const vscode = {
  window: {
    activeTextEditor: fakeEditor(),
    state: { focused: true },
    onDidChangeActiveTextEditor: on(), onDidChangeTextEditorVisibleRanges: on(), onDidChangeWindowState: on(),
  },
  workspace: {
    getWorkspaceFolder: () => ({ name: "demo" }),
    asRelativePath: () => "src/app.py",
    onDidChangeTextDocument: on(),
  },
};
const load = Module._load;
Module._load = function (request, ...rest) {
  return request === "vscode" ? vscode : load.call(this, request, ...rest);
};
const extension = require("../extension.js");

async function fakeRecall() {
  const received = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      received.push({ token: req.headers["x-recall-token"], url: req.url, body: JSON.parse(body) });
      res.writeHead(204).end();
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "recall-ext-"));
  fs.writeFileSync(path.join(home, "ingest.json"), JSON.stringify({ port: server.address().port, token: "t0ken" }));
  process.env.RECALL_HOME = home;
  return { received, close: () => server.close() };
}

const settle = () => new Promise((r) => setTimeout(r, 1800));

test("snapshot holds the visible lines with 1-based numbering", () => {
  const s = extension.visibleSnapshot(fakeEditor());
  assert.deepStrictEqual(
    { path: s.path, relative: s.relative, workspace: s.workspace, language: s.language, first_line: s.first_line },
    { path: "C:\\demo\\src\\app.py", relative: "src/app.py", workspace: "demo", language: "python", first_line: 11 },
  );
  assert.deepStrictEqual(s.lines, ["line 11", "line 12", "line 13", "line 14", "line 15"]);
});

test("output panels and other non-document editors are skipped", () => {
  assert.strictEqual(extension.visibleSnapshot(fakeEditor("output")), null);
});

test("posts the view to Recall once it settles, with the token", async () => {
  const recall = await fakeRecall();
  extension.activate({ subscriptions: [] });
  for (const fn of listeners) fn({ textEditor: vscode.window.activeTextEditor, document: vscode.window.activeTextEditor.document, focused: true });
  await settle();
  recall.close();
  assert.strictEqual(recall.received.length, 1, "a burst of events sends once");
  assert.strictEqual(recall.received[0].token, "t0ken");
  assert.strictEqual(recall.received[0].url, "/vscode");
  assert.strictEqual(recall.received[0].body.first_line, 11);
});

test("nothing is sent while VS Code is in the background", async () => {
  const recall = await fakeRecall();
  vscode.window.state.focused = false;
  listeners[0](vscode.window.activeTextEditor);
  await settle();
  recall.close();
  vscode.window.state.focused = true;
  assert.strictEqual(recall.received.length, 0);
});
