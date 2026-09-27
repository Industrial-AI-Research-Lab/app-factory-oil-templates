import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const client = new Client({ name: "oil-mcp-deploy-probe", version: "1.0.0" });
const timer = setTimeout(() => {
  console.error("FalkorDB MCP connectivity probe timed out");
  process.exit(1);
}, 20_000);

try {
  const transport = new StreamableHTTPClientTransport(
    new URL("http://127.0.0.1:8080/"),
  );
  await client.connect(transport);
  const result = await client.callTool({ name: "list_graphs", arguments: {} });
  if (result.isError) {
    throw new Error("list_graphs returned an MCP error");
  }
  console.log("FalkorDB MCP list_graphs probe passed");
} finally {
  clearTimeout(timer);
  await client.close().catch(() => undefined);
}
