# agent-container updates for agent-browser — 20 November 2026

These changes to the `agent-container` Dockerfile and `docker-compose.yml`
let agent-browser (headless Chrome) run inside the container without the
workarounds needed on 19–20 Nov 2026. That session reproduced the Toowoomba
coordinates bug (see
`docs/specs/spec-tool-map-world-bounds-and-error-fallback-201126.md`).

They address the README's known issue "Chrome/agent-browser — need to find a
better way to set this up within the container", and partly the
port-hosting one.

> **Status: proposed, not yet built.** Everything below comes from the
> specific failures hit in-session. The image couldn't be rebuilt from
> inside the container to verify it. Run the checks in §5 after rebuilding.

## 1. What went wrong in-session, and the workaround used

Running the frontend (Vite, :5173) and a backend (:8000) inside the
container worked: `localhost` is reachable from inside. **Ports were not the
problem.** Getting a browser running was. Each failure below maps to a fix
in §2–§3.

| # | Failure | Root cause | In-session workaround |
|---|---|---|---|
| 1 | `agent-browser` not installed; `npm i -g agent-browser` → `EACCES` | npm's global prefix (`/usr/local`) is root-owned; the session runs as `agent` (uid 1001) | `npm config set prefix ~/.npm-global` and installed there |
| 2 | Chrome exited immediately: `libglib-2.0.so.0: cannot open shared object file` | `node:22-slim` has none of Chrome's runtime libraries | — |
| 3 | `agent-browser install --with-deps` → "requires root privileges or sudo" | No root or sudo in the container, by design. The apt lists are also wiped (`rm -rf /var/lib/apt/lists/*`) | Ran `apt-get update` into a user-owned state dir, `apt-get download`-ed 71 `.deb`s, `dpkg -x`'d them into `~/chromelibs`, and pointed `LD_LIBRARY_PATH` at it |
| 4 | Chrome launched by agent-browser kept dying ("relaunched browser", "CDP response channel closed"), even though `agent-browser doctor`'s launch test passed | Docker blocks Chrome's sandbox for non-root users, and the default `/dev/shm` is only 64 MB. Setting `AGENT_BROWSER_ARGS` didn't visibly fix it in-session; possibly a stale session daemon from the earlier failed launches, unconfirmed | Launched Chrome by hand with `--headless=new --no-sandbox --disable-dev-shm-usage --remote-debugging-port=9333`, then ran agent-browser with `--cdp 9333` |
| 5 | The manually launched Chrome died once when its launching shell exited | Background process tied to the tool's shell | Relaunched with `setsid nohup … & disown` |
| 6 | Header text blank in screenshots | No UI fonts installed | None (cosmetic) |
| 7 | No `curl`, `ps` or `pgrep` | Not in the slim image | Used `python3 urllib` for HTTP checks and `/proc/*/cmdline` to find processes. Matching on `/proc` was what led to an over-broad `kill` that took out unrelated shells. Having `procps` avoids this |

## 2. Dockerfile changes (required)

The changes are minimal and keep the existing structure:

1. **Pin the Debian release** (`node:22-bookworm-slim`) so the Chrome
   library package names stay stable. Debian 13 renamed some of them (e.g.
   `libasound2` → `libasound2t64`). The session container was already
   Debian 12 (bookworm).
2. **Install Chrome's runtime libraries and fonts as root.** This is the
   exact list `agent-browser install --with-deps` printed in-session, plus
   `libglib2.0-0`, the library whose absence caused the first crash.
3. **Add `curl` and `procps`.**
4. **Install `agent-browser` globally as root**, alongside Claude Code.
5. **Download Chrome as the `agent` user** (`agent-browser install` after
   `USER agent`). It then lands in `/home/agent/.agent-browser/`, which is
   baked into the image and outside the `claude-credentials` volume.
6. **Set the Chrome launch flags** via `AGENT_BROWSER_ARGS`.

```dockerfile
FROM node:22-bookworm-slim

# Chrome runtime libraries + fonts for agent-browser: the list printed by
# `agent-browser install --with-deps`, plus libglib2.0-0. Installed here
# because the container has no root/sudo at runtime.
# curl/procps: basic HTTP and process tooling for checking dev servers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    ca-certificates \
    nano \
    curl \
    procps \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libcups2 libdbus-1-3 libdrm2 libgbm1 libxkbcommon0 libxshmfence1 libasound2 \
    libx11-6 libx11-xcb1 libxcb1 libxcb-shm0 libxext6 libxrandr2 libxcomposite1 \
    libxcursor1 libxdamage1 libxfixes3 libxi6 libxrender1 \
    libgtk-3-0 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 libfreetype6 libfontconfig1 \
    fonts-freefont-ttf fonts-noto-color-emoji fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Claude Code refuses --dangerously-skip-permissions when run as root, so
# everything after this point runs as a non-root user.
RUN npm install -g @anthropic-ai/claude-code agent-browser \
    && useradd -m agent

WORKDIR /workspace
RUN chown -R agent:agent /workspace

USER agent
ENV HOME=/home/agent

# Download Chrome into the agent user's home (~/.agent-browser/browsers).
RUN agent-browser install

# Chrome's sandbox can't run as non-root under Docker's default seccomp
# profile; the container itself is the isolation boundary here.
# --disable-dev-shm-usage is belt-and-braces alongside shm_size in compose.
ENV AGENT_BROWSER_ARGS="--no-sandbox,--disable-dev-shm-usage"

CMD ["bash"]
```

**Trade-off of `--no-sandbox`:** if a malicious page exploited Chrome's
renderer, it would get the container's `agent` user rather than a sandboxed
process. That's acceptable given this setup's model, where isolation comes
from the container being a separate box. But avoid browsing untrusted sites
with logged-in sessions from inside it. The alternatives (`cap_add:
SYS_ADMIN`, or a custom seccomp profile allowing user namespaces) give the
whole container more privilege, which is worse for an unattended box.

## 3. docker-compose.yml changes (required)

```yaml
services:
  agent:
    build: .
    volumes:
      - claude-credentials:/home/agent/.claude
    stdin_open: true
    tty: true
    restart: unless-stopped
    command: ["sleep", "infinity"]
    # Chrome uses /dev/shm heavily; Docker's 64 MB default causes renderer
    # crashes on map-heavy pages.
    shm_size: "1gb"
    # Lets Chrome reap its own zombie child processes in a long-running
    # container (the main process is `sleep infinity`, which doesn't).
    init: true

volumes:
  claude-credentials:
    external: true
```

## 4. Optional, related changes

### 4a. Viewing local dev servers from the Mac (README known issue: "Local port hosting")
Not needed for agent-browser, which runs inside the container next to the
servers. Only needed if you want to open the app in the Mac's browser too.

```yaml
    ports:
      - "127.0.0.1:5173:5173"   # Vite
      - "127.0.0.1:8000:8000"   # FastAPI
```

The servers must also listen on all interfaces inside the container:
`npm run dev -- --host 0.0.0.0` and
`uvicorn main:app --host 0.0.0.0 --port 8000`. The `127.0.0.1:` prefix
keeps these ports reachable only from the Mac itself, not the LAN. Add other
ports (e.g. 5057 from the README) the same way.

### 4b. Fallback if agent-browser's own launch still crashes
If §5 step 3 fails after rebuilding, use the setup that worked in-session:
launch Chrome yourself and attach over CDP. Save this as
`/home/agent/bin/chrome-cdp` (e.g. `COPY --chown=agent:agent`) and put it on
`PATH`:

```bash
#!/bin/bash
# Start a detached headless Chrome with a CDP port for agent-browser --cdp.
CHROME=$(ls -d ~/.agent-browser/browsers/chrome-*/chrome | tail -1)
setsid nohup "$CHROME" --headless=new --no-sandbox --disable-dev-shm-usage \
  --remote-debugging-port="${1:-9333}" --user-data-dir="$HOME/.chrome-cdp" \
  --window-size=1280,800 about:blank > "$HOME/.chrome-cdp.log" 2>&1 < /dev/null &
disown
```

Then use `agent-browser --session <name> --cdp 9333 open http://localhost:5173`.

### 4c. Claude Code auto-updates (README known issue)
This has the same root cause as failure #1: global npm packages are
root-owned, so the `agent` user can't update them. To fix both, install
global npm packages as `agent` under a user prefix instead of as root. In
the §2 Dockerfile, drop `@anthropic-ai/claude-code agent-browser` from the
root `npm install -g` line (leaving just `useradd -m agent`), and after
`USER agent` add:

```dockerfile
ENV NPM_CONFIG_PREFIX=/home/agent/.npm-global
ENV PATH=/home/agent/.npm-global/bin:$PATH
RUN npm install -g @anthropic-ai/claude-code agent-browser && agent-browser install
```

This is a separate concern from agent-browser, so treat it as its own
change.

## 5. Rebuild and verify

> **Warning:** changing the Dockerfile means recreating the container
> (`docker compose up -d --build`). That deletes everything that lives only
> in the container's filesystem: clones, venvs, `~/.git-credentials`, and
> unpushed work. Push all branches first. You'll need to re-run the git
> identity and credential setup (README step 3) afterwards. The
> `claude-credentials` volume survives.

```bash
# on the Mac
cd agent-container
docker compose up -d --build
docker exec -it agent-container-agent-1 bash

# inside the container
CHROME=$(ls -d ~/.agent-browser/browsers/chrome-*/chrome | tail -1)
ldd "$CHROME" | grep "not found"            # 1. expect no output
agent-browser doctor                        # 2. expect "Launch test: pass"
S=$(agent-browser session id --scope worktree --prefix check)
agent-browser --session $S open https://example.com
agent-browser --session $S snapshot -i      # 3. expect a heading and a link, not "(no interactive elements)"
agent-browser --session $S screenshot /tmp/check.png   # 4. text should render (fonts)
agent-browser --session $S close
```

**Step 3 is the real test.** In-session, `doctor` passed while real page
loads still failed. If step 3 fails:
1. run `agent-browser close --all`, to clear any stale daemon, and retry;
2. then fall back to §4b.

End-to-end check against this repo: start the backend and `npm run dev` in
`app/frontend`, then `agent-browser open http://localhost:5173` and
`snapshot -i`. Expect "Draw your own area", the query box and the zoom
controls.

## 6. README updates once verified

- Remove "Chrome/agent-browser — need to find a better way…" from Known
  issues, or replace it with a pointer to §4b if the fallback turned out to
  be needed.
- Add a short note: agent-browser is preinstalled. Chrome runs with
  `--no-sandbox` (see the trade-off above). Use `--session <name>` per task.
- If §4a is adopted, document the `--host 0.0.0.0` requirement under
  day-to-day use.
