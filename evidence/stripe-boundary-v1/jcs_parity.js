#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");

function canonicalize(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(",")}]`;
  }
  if (typeof value === "object") {
    // ECMAScript Array.sort compares UTF-16 code units, as RFC 8785 requires.
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(",")}}`;
  }
  throw new TypeError(`unsupported JSON value: ${typeof value}`);
}

const vectors = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const result = {};
for (const [name, value] of Object.entries(vectors)) {
  const canonical = canonicalize(value);
  result[name] = {
    canonical,
    sha256: crypto.createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
}
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
