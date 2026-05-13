# helloagent-hermes-plugin

Connects a Hermes gateway to the HelloAgent relay so users can DM the agent
from the HelloAgent mobile or web app.

## Setup

1. Install the plugin into the same Python environment as Hermes:

   ```bash
   pip install helloagent-hermes-plugin
   ```

2. Register it with Hermes' current user-plugin directory:

   ```bash
   helloagent-hermes install
   hermes plugins enable helloagent
   ```

3. Create an agent token at:

   ```text
   https://app.helloagent.cc/app/agents/new
   ```

4. Save the token for Hermes:

   ```bash
   export HELLOAGENT_TOKEN=ha_...
   hermes gateway restart
   ```

The setup wizard can also prompt for the token when the plugin is enabled.

For a one-command setup after minting a token, use:

```bash
helloagent-hermes connect --token ha_... --allow-from your_handle --restart-gateway
```

For a quick smoke test, `--allow-all` can be used instead of `--allow-from`.
Prefer an allowlist for normal use.

## Environment variables

| Variable | Purpose |
|---|---|
| `HELLOAGENT_TOKEN` | `ha_*` agent token. |
| `HELLOAGENT_RELAY_URL` | Override relay websocket URL for local development. |
| `HELLOAGENT_API_URL` | REST API base for future control-plane calls. |
| `HELLOAGENT_ALLOWED_USERS` | Comma-separated handles allowed by the adapter prefilter. |
| `HELLOAGENT_ALLOW_ALL_USERS` | Allow any HelloAgent sender. |
| `HELLOAGENT_HOME_CHANNEL` | Default handle for `deliver=helloagent` cron jobs. |
| `HELLOAGENT_DEBUG` | Enable verbose SDK logging when set to `1`. |

## Notes

Plugin v1 sends complete replies through Hermes' normal `adapter.send()` path.
Token-by-token streaming is intentionally left for a later SDK/Hermes stream
transport integration.

The package also exposes a `hermes_agent.plugins` entry point named
`helloagent`. The directory install step is included because Hermes 0.13's
plugin management CLI lists/enables directory plugins, while entry-point
plugins are only discovered by the runtime loader.
