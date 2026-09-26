// Recall Companion: tells the local Recall app which lines you're looking at in the active editor.
// VS Code hides editor text from accessibility APIs, so Recall can't read code the way it reads other apps.
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const DEBOUNCE_MS = 1500; // send once the view has settled, like Recall's own capture
const MAX_LINES = 400;
const SCHEMES = new Set(["file", "untitled", "vscode-remote"]); // real documents, not output panels or diffs

function ingestFile() {
  const home = process.env.RECALL_HOME || path.join(process.env.LOCALAPPDATA || os.homedir(), "Recall");
  return path.join(home, "ingest.json"); // written by Recall while it runs: {"port", "token"}
}

function visibleSnapshot(editor) {
  const doc = editor.document;
  if (!SCHEMES.has(doc.uri.scheme) || !editor.visibleRanges.length) return null;
  const first = editor.visibleRanges[0].start.line;
  const last = Math.min(editor.visibleRanges[editor.visibleRanges.length - 1].end.line, first + MAX_LINES - 1);
  const lines = [];
  for (let i = first; i <= last && i < doc.lineCount; i++) lines.push(doc.lineAt(i).text);
  const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
  return {
    path: doc.uri.fsPath,
    relative: vscode.workspace.asRelativePath(doc.uri, false),
    workspace: folder ? folder.name : "",
    language: doc.languageId,
    first_line: first + 1,
    lines,
  };
}

function post(snapshot) {
  let conn;
  try {
    conn = JSON.parse(fs.readFileSync(ingestFile(), "utf8"));
  } catch {
    return; // Recall isn't running
  }
  const body = JSON.stringify(snapshot);
  const req = http.request({
    host: "127.0.0.1", port: conn.port, path: "/vscode", method: "POST", timeout: 2000,
    headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body), "X-Recall-Token": conn.token },
  });
  req.on("error", () => {});
  req.on("timeout", () => req.destroy());
  req.end(body);
}

let timer;

function send() {
  const editor = vscode.window.activeTextEditor;
  if (!vscode.window.state.focused || !editor) return; // only what you're actually looking at
  const snapshot = visibleSnapshot(editor);
  if (snapshot && snapshot.lines.length) post(snapshot);
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(send, DEBOUNCE_MS);
}

function activate(context) {
  const isActive = (doc) => vscode.window.activeTextEditor && vscode.window.activeTextEditor.document === doc;
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(schedule),
    vscode.window.onDidChangeTextEditorVisibleRanges((e) => isActive(e.textEditor.document) && schedule()),
    vscode.workspace.onDidChangeTextDocument((e) => isActive(e.document) && schedule()),
    vscode.window.onDidChangeWindowState((state) => state.focused && schedule()),
    { dispose: () => clearTimeout(timer) },
  );
  schedule();
}

module.exports = { activate, deactivate() {}, visibleSnapshot };
