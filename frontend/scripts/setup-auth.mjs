import { randomBytes, scryptSync } from "node:crypto";
import { mkdir, open, unlink } from "node:fs/promises";
import path from "node:path";

const options = { output: ".runtime/auth", user: "admin", origin: "http://localhost:3000", "backend-url": "http://127.0.0.1:8001" };
let internal = false;
for (let index = 2; index < process.argv.length; index += 1) {
  const key = process.argv[index].replace(/^--/, "");
  if (key === "internal") { internal = true; continue; }
  if (!(key in options) || !process.argv[index + 1]) throw new Error("Usage: node scripts/setup-auth.mjs --output DIRECTORY --user USERNAME --origin ORIGIN --backend-url URL [--internal]");
  options[key] = process.argv[++index];
}
if (!/^[a-zA-Z0-9@._+-]{1,200}$/.test(options.user)) throw new Error("Use a simple username (letters, digits, @._+-).");
for (const key of ["origin", "backend-url"]) {
  const url = new URL(options[key]);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error(`${key} must be an HTTP(S) origin without a path.`);
  options[key] = url.origin;
}
if (internal && options["backend-url"] !== "http://127.0.0.1:8001") throw new Error("--internal requires the fixed backend URL http://127.0.0.1:8001.");
if (!internal && options.origin.startsWith("https:") && !options["backend-url"].startsWith("https:")) throw new Error("A production backend requires HTTPS; use --internal only for the combined container.");
const password = randomBytes(24).toString("base64url");
const salt = randomBytes(16);
const hash = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
const env = {
  WORKSPACE_LOGIN_USER: options.user,
  WORKSPACE_PASSWORD_HASH: `scrypt:16384:8:1:${salt.toString("hex")}:${hash.toString("hex")}`,
  WORKSPACE_SESSION_SECRET: randomBytes(48).toString("base64url"),
  WORKSPACE_PUBLIC_ORIGIN: options.origin,
  WORKSPACE_API_KEY: randomBytes(32).toString("base64url"),
  SECRET_KEY: randomBytes(48).toString("base64url"),
  BACKEND_URL: options["backend-url"],
  ...(internal ? { BACKEND_INTERNAL: "true" } : {}),
};
const output = path.resolve(options.output);
await mkdir(output, { recursive: true, mode: 0o700 });
const created = [];
try {
  for (const [name, body] of [
    ["frontend.env", Object.entries(env).map(([key, value]) => `${key}='${value}'`).join("\n") + "\n"],
    ["workspace-login.txt", `Workspace: ${options.origin}\nUsername: ${options.user}\nPassword: ${password}\n\nStore these credentials in your password manager. Do not commit or share this file.\n`],
  ]) {
    const destination = path.join(output, name);
    const handle = await open(destination, "wx", 0o600);
    created.push(destination);
    try { await handle.writeFile(body); } finally { await handle.close(); }
  }
} catch (error) {
  await Promise.all(created.map((file) => unlink(file)));
  throw error;
}
console.log(`Wrote private configuration and login credentials to ${output}. No secrets printed.`);
