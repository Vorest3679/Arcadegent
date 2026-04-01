MCP server configs live here.

Each `*.json` file is loaded at startup and can use either of these shapes:

```json
{
  "transport": "sse",
  "url": "https://example.com/mcp"
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
