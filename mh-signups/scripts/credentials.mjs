// Generates the two server secrets. Nothing is written to disk and nothing is sent anywhere.
//   npm run hash-passphrase      prompts (hidden) and prints a bcrypt hash for DASHBOARD_PASSPHRASE_HASH
//   npm run gen-session-secret   prints a random value for SESSION_SECRET
import bcrypt from "bcryptjs";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline";

const [command] = process.argv.slice(2);

function readHidden(prompt) {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stderr, terminal: true });
    rl._writeToOutput = (s) => {
      if (s.includes(prompt)) process.stderr.write(s);
    };
    rl.question(prompt, (answer) => {
      rl.close();
      process.stderr.write("\n");
      resolve(answer);
    });
  });
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8").replace(/\r?\n$/, "");
}

if (command === "session-secret") {
  console.log(randomBytes(48).toString("base64url"));
} else if (command === "hash-passphrase") {
  let passphrase;
  if (process.stdin.isTTY) {
    passphrase = await readHidden("New passphrase: ");
    const again = await readHidden("Repeat passphrase: ");
    if (passphrase !== again) {
      console.error("Passphrases did not match.");
      process.exit(1);
    }
  } else {
    passphrase = await readStdin();
  }
  if (passphrase.length < 12) {
    console.error("Use at least 12 characters.");
    process.exit(1);
  }
  if (Buffer.byteLength(passphrase, "utf8") > 72) {
    console.error("Use at most 72 bytes (bcrypt limit).");
    process.exit(1);
  }
  console.log(await bcrypt.hash(passphrase, 12));
} else {
  console.error("Usage: node scripts/credentials.mjs hash-passphrase | session-secret");
  process.exit(1);
}
