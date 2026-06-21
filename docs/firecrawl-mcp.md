# Firecrawl MCP Server

This project ships a [Firecrawl](https://firecrawl.dev) MCP (Model Context
Protocol) server configuration in [`.mcp.json`](../.mcp.json). It exposes
Firecrawl's scraping, crawling, and extraction tools to MCP-compatible
clients (e.g. Claude Code).

## Setup

The server is configured as a remote (HTTP) MCP endpoint and authenticates
with your Firecrawl API key. The key is read from the `FIRECRAWL_API_KEY`
environment variable so it is never committed to the repository.

1. Get an API key from the [Firecrawl dashboard](https://www.firecrawl.dev/app/api-keys).
2. Export it in your environment:

   ```bash
   export FIRECRAWL_API_KEY="fc-your-api-key"
   ```

3. Start (or restart) your MCP client. It will pick up the `firecrawl`
   server defined in `.mcp.json`:

   ```json
   {
     "mcpServers": {
       "firecrawl": {
         "type": "http",
         "url": "https://mcp.firecrawl.dev/v2/mcp",
         "headers": {
           "Authorization": "Bearer ${FIRECRAWL_API_KEY}"
         }
       }
     }
   }
   ```

## Notes

- Do **not** replace `${FIRECRAWL_API_KEY}` with a literal key in
  `.mcp.json`; keep secrets in the environment.
- See the [Firecrawl MCP docs](https://docs.firecrawl.dev/mcp) for the full
  list of available tools.
