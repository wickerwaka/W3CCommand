import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { defineConfig, type Plugin } from "vite";

const ROOT = resolve(import.meta.dirname, "..");
const EMU_OUT = join(ROOT, "emu", "out");
const RAM_SIZE = 0x80000;

/** Dev server only: lists and serves the local emulator RAM captures under emu/out (git-ignored,
 * game memory), so states can be picked without a file dialog. Never part of a build. */
function localStates(): Plugin {
  const list = (): string[] => {
    const out: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir).sort()) {
        const path = join(dir, name);
        const st = statSync(path);
        if (st.isDirectory()) walk(path);
        else if (/\.(bin|sav)$/.test(name) && (st.size === RAM_SIZE || name.endsWith(".sav")) && !/_out\.bin$/.test(name))
          out.push(relative(EMU_OUT, path).split(sep).join("/"));
      }
    };
    try {
      walk(EMU_OUT);
    } catch {
      // no captures yet
    }
    return out;
  };
  return {
    name: "local-states",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__states", (req, res) => {
        const url = new URL(req.url ?? "/", "http://x");
        const name = url.searchParams.get("name");
        if (!name) {
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(list()));
          return;
        }
        const path = resolve(EMU_OUT, name);
        if (!path.startsWith(EMU_OUT + sep) || !list().includes(name)) {
          res.statusCode = 404;
          res.end();
          return;
        }
        res.setHeader("Content-Type", "application/octet-stream");
        res.end(readFileSync(path));
      });
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [localStates()],
});
