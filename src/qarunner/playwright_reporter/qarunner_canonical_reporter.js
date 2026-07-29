/**
 * qarunner canonical Playwright reporter (M5, T-M5-PLAYWRIGHT-ADAPTER-001).
 *
 * Baked into the Playwright executor image (see Dockerfile.playwright) and
 * loaded via `--reporter=/opt/qarunner-adapter/qarunner_canonical_reporter.js`.
 * Stdlib-only (no package imports): the image has @playwright/test globally but
 * this file must not depend on project node_modules.
 *
 * Writes schema-bounded case-results.json. Control plane parses only that JSON,
 * never raw junit/HTML/trace. stable_case_id = project::file::title.
 *
 * Env: QARUNNER_CASE_RESULTS_PATH — absolute path for the JSON output
 * (defaults to ./case-results.json under cwd if unset).
 */

const fs = require("fs");
const path = require("path");

const SCHEMA_VERSION = "qep.playwright-case-result.v1";
const MAX_MESSAGE_CHARS = 20000;
const ENV_RESULTS_PATH = "QARUNNER_CASE_RESULTS_PATH";

function truncate(message) {
  if (message == null) return null;
  const text = String(message);
  if (text.length <= MAX_MESSAGE_CHARS) return text;
  return text.slice(0, MAX_MESSAGE_CHARS) + "...[truncated]";
}

function stableCaseId(project, file, title) {
  return `${project}::${file}::${title}`;
}

function mapOutcome(status) {
  // Playwright result.status: passed | failed | timedOut | skipped | interrupted
  switch (status) {
    case "passed":
      return "passed";
    case "skipped":
      return "skipped";
    case "timedOut":
    case "interrupted":
      return "error";
    case "failed":
    default:
      return "failed";
  }
}

class QarunnerCanonicalReporter {
  constructor() {
    this._cases = [];
  }

  onTestEnd(test, result) {
    const project = (test.parent && test.parent.project && test.parent.project().name) ||
      (test._projectId) ||
      "default";
    // test.location.file is absolute; prefer relative path from test title path.
    const file =
      (test.location && test.location.file
        ? path.relative(process.cwd(), test.location.file) || test.location.file
        : test.path) || "unknown.spec.ts";
    const title = test.titlePath
      ? test.titlePath().filter(Boolean).slice(1).join(" › ") || test.title
      : test.title;
    const projectName = typeof project === "string" && project ? project : "default";
    const fileName = String(file).replace(/\\/g, "/");
    const titleText = String(title || test.title || "untitled");
    let message = null;
    if (result.errors && result.errors.length) {
      message = truncate(
        result.errors.map((e) => e.message || e.value || String(e)).join("\n")
      );
    }
    this._cases.push({
      stable_case_id: stableCaseId(projectName, fileName, titleText),
      project: projectName,
      file: fileName,
      title: titleText,
      outcome: mapOutcome(result.status),
      duration_ms: Math.max(0, Math.round(Number(result.duration) || 0)),
      message,
    });
  }

  async onEnd() {
    const outPath =
      process.env[ENV_RESULTS_PATH] || path.join(process.cwd(), "case-results.json");
    const payload = {
      schema_version: SCHEMA_VERSION,
      cases: this._cases,
    };
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, JSON.stringify(payload), "utf8");
  }
}

module.exports = QarunnerCanonicalReporter;
// Pure helpers exported for light Node-side unit checks if desired.
module.exports._stableCaseId = stableCaseId;
module.exports._mapOutcome = mapOutcome;
module.exports._truncate = truncate;
module.exports._SCHEMA_VERSION = SCHEMA_VERSION;
