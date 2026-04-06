# Claude Code on Analytics Gateway — Setup Guide

This guide sets up Claude Code on an AG instance with:

- **AWS Bedrock authentication** (pre-configured on AG)
- **ACT3 Mattermost MCP** — search/post to AG Mattermost from Claude Code
- **Google Workspace MCP** — search Gmail, Calendar, and Drive from Claude Code

## Prerequisites

- An AG compute instance (any type — GPU not required for Claude Code)
- Claude Code installed (should be pre-installed on AG instances)
- A Google account (for Google Workspace MCP)

## Step 1: Verify Claude Code Auth

AG instances come pre-configured with AWS Bedrock credentials. Verify it works:

```bash
claude --version
```

The following environment variables should already be set (check with `env | grep -i claude`):

```bash
CLAUDE_CODE_USE_BEDROCK=1
ANTHROPIC_MODEL=us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0
```

If they're set, just run `claude` and it should connect via Bedrock. No login needed.

## Step 2: Install Node.js (for Mattermost MCP)

AG instances don't have Node.js pre-installed. Install to your home directory
(no sudo needed, survives across instances via shared NFS home):

```bash
curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz | tar xJ -C ~/
echo 'export PATH="$HOME/node-v22.14.0-linux-x64/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Verify:

```bash
node --version
npx --version
```

> **Note:** AG has an old Node 12 system package. If you try `sudo apt-get install`
> from nodesource, it will conflict. The home directory install above avoids this.

## Step 3: Set Up Mattermost MCP Server

### 3a. Get your Mattermost access token

1. Open <https://mattermost.act3.analyticsgateway.com>
2. Click your profile icon (top-left) -> **Profile**
3. **Security** -> **Personal Access Tokens** -> **Create Token**
4. Name it (e.g., "claude-code"), copy the token

### 3b. Register the MCP server

```bash
claude mcp add --scope user ACT3_AG \
  -e MCP_MATTERMOST_URL="https://mattermost.act3.analyticsgateway.com" \
  -e MCP_MATTERMOST_TOKEN="<YOUR_MATTERMOST_TOKEN>" \
  -e MCP_MATTERMOST_TEAM_NAME="act3" \
  -- npx -y @dakatan/mcp-mattermost
```

### 3c. Verify

```bash
claude mcp list
```

Start Claude Code and ask "search Mattermost for recent messages about Claude Code."

## Step 4: Set Up Google Workspace MCP Server

This uses the [google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp)
community server. It provides Gmail, Calendar, Drive, Docs, Sheets, and more.

### 4a. Create a Google Cloud project and OAuth credentials

1. Go to <https://console.cloud.google.com/> and create a new project
   (or use an existing one)

2. **Enable APIs** — Go to **APIs & Services** -> **Library** and enable:

   - Gmail API
   - Google Calendar API
   - Google Drive API

   (Optional: Google Docs API, Google Sheets API, Google Slides API, Google Tasks API)

3. **Create an OAuth client** — Go to **Google Auth platform** -> **Clients**:

   - Click **Create Client**
   - Application type: **Desktop app**
   - Name it (e.g., "Claude Code AG")
   - Click **Create**

   This will prompt you to configure the OAuth consent screen if you haven't
   already. Follow the wizard:

4. **Configure branding** — Go to **Google Auth platform** -> **Branding**
   (or click **Get Started** if prompted):

   - Enter app name (e.g., "Claude Code MCP")
   - Select your email as the support email
   - Click **Next**
   - Audience: choose **Internal** if you're in AFResearchLab Google Workspace
     (this lets anyone in the org use it without test user setup).
     Choose **External** only if you're using a personal Google account.
   - Click **Next**
   - Enter your email for project notifications
   - Click **Next**
   - Agree to the Google API Services User Data Policy
   - Click **Create**

5. **Add test users (External only)** — If you chose **External** in step 4,
   go to **Google Auth platform** -> **Audience** (separate page in the left nav):

   - Click **Add users**
   - Enter your Google email address
   - Click **Save**

   > This step is required for External apps in "Testing" status.
   > Without it, the OAuth flow will show "access denied".
   > **Internal** apps skip this step — all org users are authorized automatically.

6. **Copy your credentials** — Go back to **Google Auth platform** -> **Clients**:

   - Click on the client you just created
   - Copy the **Client ID** and **Client Secret**

### 4b. Install uv (Python package runner)

On AG:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### 4c. Authenticate with Google (run on your local machine)

AG is behind a proxy, so Google's OAuth browser redirect won't work directly
on the AG instance. Instead, register the MCP server on your **local machine**
first, let Claude Code trigger the OAuth flow (which opens your browser),
then copy the cached token to AG.

#### Install uv locally (if you don't have it)

- **macOS/Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows (PowerShell):** `irm https://astral.sh/uv/install.ps1 | iex`

#### Register the MCP server locally

```bash
claude mcp add --scope user google_workspace \
  -e GOOGLE_OAUTH_CLIENT_ID="<YOUR_CLIENT_ID>" \
  -e GOOGLE_OAUTH_CLIENT_SECRET="<YOUR_CLIENT_SECRET>" \
  -- uvx workspace-mcp --tool-tier core --single-user
```

> **Windows note:** If the above doesn't work in PowerShell, use backticks
> instead of backslashes for line continuation, or put it all on one line.

#### Trigger the OAuth flow

Start Claude Code on your local machine:

```bash
claude
```

Then ask it something that requires Google access, e.g.:

```text
what's on my calendar today?
```

This will trigger the MCP server to open your browser for Google sign-in.
Authorize the app. The token is cached locally at:

- **macOS/Linux:** `~/.google_workspace_mcp/credentials/`
- **Windows:** `%USERPROFILE%\.google_workspace_mcp\credentials\`

#### Copy the token to AG

1. On AG, create the target directory:

   ```bash
   mkdir -p ~/.google_workspace_mcp
   ```

2. Drag-and-drop the entire `credentials` folder from your local
   `.google_workspace_mcp` directory into `~/.google_workspace_mcp/` on AG
   using the AG VSCode file explorer.

   To find the folder on your local machine:

   - **macOS/Linux:** open `~/.google_workspace_mcp/` in Finder/Files
   - **Windows:** open `%USERPROFILE%\.google_workspace_mcp\` in File Explorer

The token persists on AG's shared NFS home — you only need to do this once.

### 4d. Register the MCP server

On AG:

```bash
claude mcp add --scope user google_workspace \
  -e GOOGLE_OAUTH_CLIENT_ID="<YOUR_CLIENT_ID>" \
  -e GOOGLE_OAUTH_CLIENT_SECRET="<YOUR_CLIENT_SECRET>" \
  -- uvx workspace-mcp --tool-tier core
```

> **Tool tiers:** `core` gives read/create for Gmail, Calendar, Drive.
> Use `extended` for labels, folders, batch operations. Use `complete` for
> full API access. You can also cherry-pick services:
> `uvx workspace-mcp --tools gmail drive calendar`

### 4e. Verify

Start Claude Code and try:

- "What's on my calendar this week?"
- "Search my Gmail for messages from Jared"
- "Find files in my Drive about MASH"

## Useful: File Download from AG

Need to get files off the AG instance? Use Python's HTTP server:

```bash
cd /path/to/files
python3 -c "import http.server; http.server.HTTPServer(('0.0.0.0', 8888), http.server.SimpleHTTPRequestHandler).serve_forever()" &
```

Port 8888 auto-appears in the VSCode **PORTS** tab. Click the forwarded address
to browse and download files in your browser.

## Useful: VSCode Remote Tunnel

Connect your local VSCode to the AG instance for full remote development:

```bash
~/code tunnel
```

Authenticates via GitHub. On your local VSCode, install the **Remote - Tunnels**
extension, then Command Palette -> **Remote-Tunnels: Connect to Tunnel**.

## Troubleshooting

### Claude Code not connecting to Bedrock

Check that the env vars are set:

```bash
env | grep -i claude
env | grep -i aws
```

You should see `CLAUDE_CODE_USE_BEDROCK=1`, `ANTHROPIC_MODEL`, and `AWS_REGION=us-gov-west-1`.
If missing, check `/etc/profile.d/` or ask your AG admin.

### Mattermost MCP won't connect

Test it manually:

```bash
MCP_MATTERMOST_URL="https://mattermost.act3.analyticsgateway.com" \
MCP_MATTERMOST_TOKEN="<your-token>" \
MCP_MATTERMOST_TEAM_NAME="act3" \
npx -y @dakatan/mcp-mattermost
```

Should start without errors and wait for stdin input (Ctrl+C to exit).

### Google Workspace MCP won't connect

Test it manually:

```bash
GOOGLE_OAUTH_CLIENT_ID="<your-client-id>" \
GOOGLE_OAUTH_CLIENT_SECRET="<your-client-secret>" \
uvx workspace-mcp --tool-tier core
```

If you get auth errors, re-run the OAuth flow on your local machine (step 4c)
and copy the fresh token to AG.

### Google OAuth consent screen says "unverified app"

This is normal for new GCP projects. Click **Advanced** -> **Go to (app name) (unsafe)**
to proceed. The app only accesses your own data.

### npx hangs or fails

Make sure Node.js is installed and on your PATH (`node --version`).
If using the home directory install, open a new terminal or `source ~/.bashrc`.

### Node.js install conflict

If you see `trying to overwrite '/usr/include/node/common.gypi'`, AG has an old
Node 12 system package conflicting. Use the home directory install in Step 2
instead of `apt-get`.
