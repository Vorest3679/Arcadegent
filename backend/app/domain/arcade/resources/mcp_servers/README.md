MCP server configs live in plugin resource directories.

Each `*.json` file is loaded at startup and can use either of these shapes:

```json
{
  "transport": "sse",
  "url": "https://example.com/mcp?key=${config.map.api_key}"
}
```

The file name becomes the server name, for example `fetch.json` -> `fetch`.

Or a standard MCP config fragment:

```json
{
  "mcpServers": {
    "fetch": {
      "transport": "sse",
      "url": "https://example.com/mcp"
    }
  }
}
```

`${config.<path>}` tokens are expanded from the owning plugin's validated
configuration (for example `${config.amap.api_key}`). Process environment
variables are not read here; the host maps environment variables into plugin
config in `plugins.yaml`. An unresolved token is a startup error.

Migrating an older custom directory: replace environment tokens such as
`${AMAP_API_KEY}` with `${config.amap.api_key}`.
