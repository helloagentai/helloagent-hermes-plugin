# helloagent-hermes-plugin

Connects a Hermes gateway to the HelloAgent relay so users can DM the agent
from the HelloAgent mobile or web app.

## Setup

1. Install the plugin into the same Python environment as Hermes:

   ```bash
   pip install helloagent-hermes-plugin
   ```

2. Create an agent token at:

   ```text
   https://app.helloagent.cc/app/agents/new
   ```

3. Connect Hermes to HelloAgent:

   ```bash
   helloagent-hermes connect
   ```

   The command prompts for your `ha_*` token, an optional allowlist, and
   whether to restart the gateway. It installs the user-plugin link, enables
   `helloagent` in Hermes, writes the token into Hermes' environment, and
   saves a local credential record.

For non-interactive setup, pass the settings as flags:

```bash
helloagent-hermes connect --token ha_... --allow-from your_handle --restart-gateway
```

## Manual setup

If you prefer to configure Hermes by hand:

1. Register the plugin with Hermes' current user-plugin directory:

   ```bash
   helloagent-hermes install
   hermes plugins enable helloagent
   ```

2. Create an agent token at:

   ```text
   https://app.helloagent.cc/app/agents/new
   ```

3. Save the token for Hermes:

   ```bash
   export HELLOAGENT_TOKEN=ha_...
   hermes gateway restart
   ```

## Environment variables

| Variable | Purpose |
|---|---|
| `HELLOAGENT_TOKEN` | `ha_*` agent token. |
| `HELLOAGENT_ALLOWED_USERS` | Comma-separated handles allowed by the adapter prefilter. |
| `HELLOAGENT_ALLOW_ALL_USERS` | Allow any HelloAgent sender. |
| `HELLOAGENT_DEBUG` | Enable verbose SDK logging when set to `1`. |

## Notes

Plugin v1 sends complete replies through Hermes' normal `adapter.send()` path.
Token-by-token streaming is intentionally left for a later SDK/Hermes stream
transport integration.

The package also exposes a `hermes_agent.plugins` entry point named
`helloagent`. The directory install step is included because Hermes 0.13's
plugin management CLI lists/enables directory plugins, while entry-point
plugins are only discovered by the runtime loader.
